"""The built-in Tools module: shell commands, file operations, the web and a look at the
screen (POSIX only).

Relative paths resolve against the workspace root, which is also the shell's working
directory. Commands inherit the process environment (PERMISSION_CEILING_ENV included);
sandboxed ones without the variables that look like secrets (sandbox_environment).

The workspace is a boundary the runtime can rely on (`confined`): a file call is confined
when its path is inside it, and a shell command when it runs in the bubblewrap sandbox,
where only the workspace is writable and the home directories are hidden. The web tools
(`web.py`) touch nothing on this computer, so they are confined too. Calls that reach
outside still work; the runtime asks the user first.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import io
import os
import re
import secrets
import shutil
import signal
import stat
import subprocess
import threading
import time
from collections.abc import Awaitable, Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, replace
from fnmatch import fnmatch
from pathlib import Path
from typing import IO, Any

import httpx2
import jsonschema
import pathspec
import pypdf
import pypdf.errors

from kadence.api import (
    Attachment,
    Document,
    Image,
    InstancePaths,
    ToolCall,
    ToolContext,
    ToolOutput,
    Tools,
    ToolSpec,
)
from kadence.tools.shell_files import attachments, office, screen, web

SHELL_STALL_TIMEOUT = 600.0  # seconds without output before the runtime may stall-kill
HEARTBEAT_INTERVAL = 0.5  # min seconds between silent heartbeats from file walks
TERMINATE_GRACE = 2.0  # SIGTERM -> SIGKILL delay when a command is cancelled
DRAIN_GRACE = 0.5  # how long to keep reading after the shell exits (background children)
PROGRESS_INTERVAL = 0.2  # min seconds between progress reports
MAX_OUTPUT_CHARS = 30_000  # head + tail kept from command output
MAX_READ_LINES = 2_000
MAX_READ_CHARS = 100_000
PDF_PAGES_PER_READ = 20  # of a PDF in one read_file: each page is also read as an image
MAX_LIST_ENTRIES = 1_000
MAX_SEARCH_MATCHES = 200
MAX_SEARCH_FILE_BYTES = 1_000_000


class ToolError(Exception):
    """Expected failure reported to the model as an is_error output."""


# Set while a call that `confined` judged inside runs (ToolContext.confined): its files must
# really be in the workspace, wherever a link that appeared since the check leads.


def _object(properties: dict[str, Any], required: Sequence[str] = ()) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": list(required),
        "additionalProperties": False,
    }


_STRING = {"type": "string"}

SHELL = ToolSpec(
    name="shell",
    description=(
        "Run a shell command (sh) in the workspace and return its combined stdout and "
        "stderr plus the exit code. Output is truncated to the first and last part when "
        "long. Non-zero exit codes are reported as errors. The folder a command ends in is "
        "where the next one starts, in this conversation, while it is inside the "
        "workspace; elsewhere, the next starts in the workspace."
    ),
    parameters=_object({"command": _STRING, "unsandboxed": {"type": "boolean"}}, ["command"]),
    stall_timeout=SHELL_STALL_TIMEOUT,
)
SANDBOX_NOTE = (
    " Commands run in a sandbox: they can read and write the workspace, read the system "
    "and use the network, but the rest of the home directory and environment variables "
    "holding keys or tokens are hidden, and nothing outside the workspace can be "
    "changed. Set `unsandboxed` to true only when a command needs something outside; "
    "the user is then asked first, so say why."
)
# Variables whose names look like secrets (API keys, tokens, passwords) are left out of a
# sandboxed command's environment: it runs unasked and may use the network.
SECRET_NAME = re.compile(r"KEY|TOKEN|SECRET|PASSW|CREDENTIAL", re.IGNORECASE)
SANDBOX_CHECK_TIMEOUT = 5.0  # seconds for the one test run of the sandbox at startup
FILE_SPECS: tuple[ToolSpec, ...] = (
    ToolSpec(
        name="read_file",
        description=(
            "Read a UTF-8 text file. Optional 1-based start line `offset` and line `limit` "
            f"(default {MAX_READ_LINES}). A picture (PNG, JPEG, GIF, WebP) is shown to you. "
            "A PDF is read as the text of its pages: all of them, or those `pages` names, "
            "such as \"3\" or \"2-5\". Set `view` to look at the pages themselves, for a "
            "scan, a chart or a layout the text does not carry: at most "
            f"{PDF_PAGES_PER_READ} at a time, and each costs far more than its text. "
            "A Word, Excel or "
            "PowerPoint file (.docx, .xlsx, .pptx) is read as its text, by lines like any "
            "text file."
        ),
        parameters=_object(
            {
                "path": _STRING,
                "offset": {"type": "integer", "minimum": 1},
                "limit": {"type": "integer", "minimum": 1},
                "pages": {"type": "string", "description": "For a PDF: a page or a range."},
                "view": {"type": "boolean",
                         "description": "For a PDF: the pages as they look, not their text."},
            },
            ["path"],
        ),
        read_only=True,
    ),
    ToolSpec(
        name="write_file",
        description="Create or overwrite a text file, creating parent directories. A file that is "
            "there must have been read in this conversation, and be unchanged since.",
        parameters=_object({"path": _STRING, "content": _STRING}, ["path", "content"]),
    ),
    ToolSpec(
        name="edit_file",
        description=(
            "Replace an exact string in a file, which must have been read in this "
            "conversation and be unchanged since. `old_string` must occur exactly once unless "
            "`replace_all` is true."
        ),
        parameters=_object(
            {
                "path": _STRING,
                "old_string": {"type": "string", "minLength": 1},
                "new_string": _STRING,
                "replace_all": {"type": "boolean"},
            },
            ["path", "old_string", "new_string"],
        ),
    ),
    ToolSpec(
        name="list_files",
        description=(
            "List files under a directory (default: workspace root), skipping hidden "
            "directories and what .gitignore files leave out. `pattern` is a glob matched "
            "against the file name, or against the relative path when it contains '/'."
        ),
        parameters=_object({"path": _STRING, "pattern": _STRING}),
        read_only=True,
    ),
    ToolSpec(
        name="search_files",
        description=(
            "Search text files under a directory for a Python regular expression. Returns "
            "`path:line: text` matches, with `context` lines before and after each as "
            "`path-line- text`; `output` \"files\" lists only the files that match and "
            "\"count\" how many lines match in each. `glob` filters file names. Hidden "
            "directories, what .gitignore files leave out, binary files and files over 1 MB "
            "are skipped."
        ),
        parameters=_object(
            {"pattern": {"type": "string", "minLength": 1}, "path": _STRING, "glob": _STRING,
             "ignore_case": {"type": "boolean"},
             "context": {"type": "integer", "minimum": 0, "maximum": 10},
             "output": {"enum": ["content", "files", "count"]}},
            ["pattern"],
        ),
        read_only=True,
    ),
)


def _page_range(pages: str | None, total: int) -> tuple[int, int]:
    """The first and last page `pages` names ("3", "2-5"; None: all of them)."""
    if pages is None:
        return 1, total
    first, _, last = pages.strip().partition("-")
    try:
        start, end = int(first), int(last or first)
    except ValueError:
        raise ToolError(f'pages is a page or a range, such as "3" or "2-5", not {pages!r}') \
            from None
    if not 1 <= start <= end <= total:
        raise ToolError(f"pages {pages} is not within the PDF's {total} pages")
    return start, end


def factory(options: dict[str, Any], paths: InstancePaths) -> ShellFilesTools:
    """Options: root (default: the current directory), shell_timeout, shell_stall_timeout."""
    options = dict(options)
    return ShellFilesTools(Path(options.pop("root", Path.cwd())), **options)


factory.display_name = "Shell, files and web"  # type: ignore[attr-defined]


def sandbox_environment(environ: Mapping[str, str]) -> dict[str, str]:
    return {k: v for k, v in environ.items() if not SECRET_NAME.search(k)}


def _sandbox(bwrap: str, root: Path, home: Path) -> list[str]:
    """The bubblewrap command line that confines a shell command to `root`.

    The whole system is visible read-only, so installed programs run; home directories,
    /tmp and the session's runtime directory are replaced by empty ones, and only the
    workspace is bound back, writable. The network is left as it is."""
    hidden = [p for p in (Path("/home"), Path("/root"), Path("/run/user"), Path("/tmp"), home)
              if p.is_dir()]
    command = [bwrap, "--ro-bind", "/", "/"]
    for path in hidden:
        command += ["--tmpfs", str(path)]
    return command + ["--dir", str(home), "--bind", str(root), str(root), "--dev", "/dev",
                      "--proc", "/proc", "--unshare-pid", "--die-with-parent",
                      "--chdir", str(root)]


def _bwrap(root: Path, home: Path) -> str | None:
    """bubblewrap, if a sandbox works here (not when it is missing, user namespaces are
    disabled, or Kadence itself runs in a sandbox). Blocking: run it where module
    factories run, off the event loop."""
    bwrap = shutil.which("bwrap")
    if bwrap is None:
        return None
    try:
        works = subprocess.run([*_sandbox(bwrap, root, home), "true"], check=False,
                               capture_output=True, timeout=SANDBOX_CHECK_TIMEOUT
                               ).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        works = False
    return bwrap if works else None


class ShellFilesTools(Tools):
    """`shell_timeout`: hard limit per command (None = runtime default, math.inf = none).
    `shell_stall_timeout`: seconds without output before the command counts as hung."""

    def __init__(
        self,
        root: Path,
        *,
        shell_timeout: float | None = None,
        shell_stall_timeout: float | None = SHELL_STALL_TIMEOUT,
        web_tools: web.Web | None = None,
        screen_tools: screen.Screen | None = None,
    ) -> None:
        self._default = Path(root).expanduser().resolve()
        self._bwrap = _bwrap(self._default, Path.home().resolve())
        self._shell_spec = replace(SHELL, timeout=shell_timeout,
                                   stall_timeout=shell_stall_timeout)
        self._web = web_tools or web.Web()
        self._screen = screen_tools or screen.Screen.found()
        # Each conversation's files as it last read or wrote them: (conversation, file)
        # to what _version says of it.
        self._seen: dict[tuple[str, Path], tuple[int, int, int]] = {}
        self._folders: dict[str, Path] = {}  # where each conversation's shell is now
        # The line a command ends with to say where it ended; never part of its output.
        self._where = f"\x1b[kadence-folder-{secrets.token_hex(8)}]"
        self._set_root(self._default)
        self._handlers: dict[str, Callable[[dict[str, Any], ToolContext], Awaitable[str]]] = {
            "shell": self._shell,
            "read_file": self._read_file,
            "write_file": self._write_file,
            "edit_file": self._edit_file,
            "list_files": self._list_files,
            "search_files": self._search_files,
            **{name: self._web_call(name) for name in web.NAMES},
            screen.NAME: self._screenshot,
        }

    def _set_root(self, root: Path) -> None:
        self.root = root
        home = Path.home().resolve()
        # A workspace that is or holds the home directory (or is /) is no boundary.
        self._bounded = root != home and root not in home.parents
        self._sandbox = (_sandbox(self._bwrap, root, home)
                         if self._bwrap is not None and self._bounded else None)
        shell = self._shell_spec
        if self._sandbox is not None:
            shell = replace(shell, description=shell.description + SANDBOX_NOTE)
        self._spec_list = (shell, *FILE_SPECS, *web.SPECS, *self._screen.specs)
        self._specs = {spec.name: spec for spec in self._spec_list}

    async def list_tools(self) -> Sequence[ToolSpec]:
        return self._spec_list

    async def use_folder(self, folder: Path | None) -> None:
        root = self._default if folder is None else Path(folder).expanduser().resolve()
        await asyncio.to_thread(root.mkdir, parents=True, exist_ok=True)
        self._set_root(root)

    async def confined(self, call: ToolCall) -> bool:
        if call.name in web.NAMES:
            return True
        if not self._bounded or call.name not in self._handlers or call.name == screen.NAME:
            return False
        if call.name == "shell":
            return self._sandbox is not None and call.arguments.get("unsandboxed") is not True
        path = call.arguments.get("path", ".")
        return isinstance(path, str) and await asyncio.to_thread(self._inside, path)

    def _inside(self, raw: str) -> bool:
        """Whether the path, with symlinks followed, is in the workspace."""
        return self._path(raw).resolve().is_relative_to(self.root)

    async def attach(self, path: Path) -> Attachment:
        """A relative path is taken from the work folder. Reading is the user's act, so
        the file may lie outside it."""
        return await asyncio.to_thread(attachments.read, self._path(str(path)))

    async def execute(self, call: ToolCall, ctx: ToolContext) -> ToolOutput:
        spec = self._specs.get(call.name)
        if spec is None:
            return ToolOutput(f"Unknown tool: {call.name}", is_error=True)
        try:
            jsonschema.validate(call.arguments, spec.parameters)
        except jsonschema.ValidationError as e:
            return ToolOutput(f"Invalid arguments: {e.message}", is_error=True)
        try:
            result = await self._handlers[call.name](call.arguments, ctx)
            return result if isinstance(result, ToolOutput) else ToolOutput(result)
        except (ToolError, web.WebError, screen.ScreenError) as e:
            return ToolOutput(str(e), is_error=True)
        except httpx2.HTTPError as e:
            return ToolOutput(f"The page could not be read: {type(e).__name__}: {e}",
                              is_error=True)
        except OSError as e:
            return ToolOutput(f"{type(e).__name__}: {e}", is_error=True)

    async def _screenshot(self, args: dict[str, Any], ctx: ToolContext) -> ToolOutput:
        return await self._screen.run()

    def _web_call(self, name: str) -> Callable[[dict[str, Any], ToolContext], Awaitable[str]]:
        async def call(args: dict[str, Any], ctx: ToolContext) -> str:
            return await self._web.run(name, args)

        return call

    def _path(self, raw: str | None) -> Path:
        path = Path(raw or ".").expanduser()
        return path if path.is_absolute() else self.root / path

    def _outside(self, path: Path) -> ToolError:
        return ToolError(f"{self._display(path)} leads outside the work folder; set it up "
                         "again inside the folder or ask the user")

    def _within(self, path: Path, ctx: ToolContext) -> Path:
        """Where the path leads, links followed. For a call `confined` let through, that
        must be in the workspace now, whatever stood there when it was judged."""
        resolved = path.resolve()
        if ctx.confined and not resolved.is_relative_to(self.root):
            raise self._outside(path)
        return resolved

    def _open(self, path: Path, flags: int, ctx: ToolContext) -> int:
        """os.open, for a call `confined` allowed only if the file opened is in the
        workspace: checked on the open file, so a link swapped in after the check (or
        between this check and the read or write) cannot lead outside."""
        if not ctx.confined:
            return os.open(path, flags, 0o644)
        fd = os.open(self._within(path, ctx), flags | os.O_NOFOLLOW, 0o644)
        try:
            inside = Path(os.readlink(f"/proc/self/fd/{fd}")).is_relative_to(self.root)
        except OSError:
            os.close(fd)
            raise
        if not inside:
            os.close(fd)
            raise self._outside(path)
        return fd

    def _open_folder(self, path: Path, ctx: ToolContext) -> int:
        """The folder, open: what is walked is what was opened, whatever is put at its
        path meanwhile. For a call `confined` let through it must be in the workspace."""
        try:
            return self._open(path, os.O_RDONLY | os.O_DIRECTORY, ctx)
        except NotADirectoryError:
            raise ToolError(f"{self._display(path)} is not a directory") from None

    def _ignored_above(self, base: Path) -> list[_Rules]:
        """The .gitignore files of the folders from the workspace down to `base`, above
        it, as they apply to what is under `base`. Blocking."""
        try:
            below = base.resolve().relative_to(self.root)
        except (ValueError, OSError):
            return []  # outside the workspace: only its own .gitignore files count
        rules = []
        folder = self.root
        for depth, part in enumerate((None, *below.parts)):
            if part is not None:
                folder = folder / part
            if depth == len(below.parts):
                break  # base's own is read as it is walked
            spec = _gitignore((folder / ".gitignore").read_text
                              if (folder / ".gitignore").is_file() else None)
            if spec is not None:
                rules.append(_Rules("", "/".join(below.parts[depth:]), spec))
        return rules

    def _display(self, path: Path) -> str:
        try:
            return path.relative_to(self.root).as_posix()
        except ValueError:
            return str(path)

    # ── shell ────────────────────────────────────────────────────────────

    async def _shell(self, args: dict[str, Any], ctx: ToolContext) -> str:
        # Our own pipe rather than PIPE: asyncio's Process.wait() also waits for its pipes
        # to close, which never happens while a backgrounded child holds stdout.
        read_fd, write_fd = os.pipe()
        folder = self._folders.get(ctx.conversation_id, self.root)
        if not await asyncio.to_thread(folder.is_dir):
            folder = self.root
        # The command, then where it ended, on a line of its own; its exit code stays.
        script = (f"{args['command']}\n__kadence_status=$?\n"
                  f"printf '\\n%s%s\\n' '{self._where}' \"$(pwd -P)\"\n"
                  "exit $__kadence_status\n")
        try:
            sandbox = [] if self._sandbox is None or args.get("unsandboxed") else [
                *self._sandbox[:-1], str(folder)]  # its --chdir
            proc = await asyncio.create_subprocess_exec(
                *sandbox, "sh", "-c", script,
                env=sandbox_environment(os.environ) if sandbox else None,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=write_fd,
                stderr=asyncio.subprocess.STDOUT,
                cwd=folder,
                start_new_session=True,  # own process group, so cancellation reaches children
            )
        except BaseException:
            os.close(read_fd)
            raise
        finally:
            os.close(write_fd)
        stream = asyncio.StreamReader()
        transport, _ = await asyncio.get_running_loop().connect_read_pipe(
            lambda: asyncio.StreamReaderProtocol(stream), os.fdopen(read_fd, "rb", 0)
        )
        output = _OutputBuffer(MAX_OUTPUT_CHARS)
        reader = asyncio.create_task(_pump(stream, output, ctx, self._where))
        try:
            code = await proc.wait()
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(asyncio.shield(reader), DRAIN_GRACE)
        except asyncio.CancelledError:
            await _terminate_group(proc)
            raise
        finally:
            reader.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await reader
            transport.close()
        text = self._ended_in(output.text(), ctx)
        if code != 0:
            raise ToolError(f"{text}\n[exit code {code}]".lstrip())
        return f"{text}\n[exit code 0]".lstrip()

    def _ended_in(self, text: str, ctx: ToolContext) -> str:
        """The output without the line that says where the command ended, which is kept
        for the conversation's next command if it is in the workspace."""
        at = text.rfind("\n" + self._where)
        if at == -1:
            return text  # it ended before saying: by exit, or killed
        end = text.find("\n", at + 1)
        where = Path(text[at + 1 + len(self._where): end if end != -1 else None])
        if where.is_relative_to(self.root):
            self._folders[ctx.conversation_id] = where
        else:
            self._folders.pop(ctx.conversation_id, None)
        return text[:at] + (text[end + 1:] if end != -1 else "")

    # ── files ────────────────────────────────────────────────────────────

    async def _read_file(self, args: dict[str, Any], ctx: ToolContext) -> str | ToolOutput:
        path = self._path(args["path"])
        offset = args.get("offset", 1)
        limit = args.get("limit", MAX_READ_LINES)
        if await asyncio.to_thread(path.is_dir):
            raise ToolError(f"{self._display(path)} is a directory")
        await asyncio.to_thread(self._saw, path, ctx)
        if path.suffix.lower() in attachments.IMAGE_TYPES:
            return await asyncio.to_thread(self._read_image, path, ctx)
        if path.suffix.lower() == ".pdf":
            return await asyncio.to_thread(self._read_pdf, path, args.get("pages"),
                                           args.get("view") is True, ctx)

        def source() -> IO[str]:
            """The file as text; a Word, Excel or PowerPoint file converted first."""
            if path.suffix.lower() not in office.KINDS:
                return open(self._open(path, os.O_RDONLY, ctx), encoding="utf-8", errors="replace")
            with open(self._open(path, os.O_RDONLY, ctx), "rb") as raw:
                try:
                    return io.StringIO(office.to_text(raw, path.suffix) + "\n")
                except ValueError as e:
                    raise ToolError(f"{self._display(path)}: {e}") from e

        def read() -> str:
            """Reads only up to what it returns: the file may be huge or endless."""
            selected: list[str] = []
            size, notes = 0, []
            with source() as f:
                lines = iter(lambda: f.readline(MAX_READ_CHARS + 1), "")
                for number, line in enumerate(lines, 1):
                    if number < offset:
                        continue
                    if number >= offset + limit:
                        notes.append(f"[more lines follow; use offset {number} to read on]")
                        break
                    if size + len(line) > MAX_READ_CHARS:
                        selected.append(line[: MAX_READ_CHARS - size])
                        notes.append(f"[truncated at {MAX_READ_CHARS} characters]")
                        break
                    selected.append(line)
                    size += len(line)
            text = "".join(selected)
            return "\n".join([text, *notes]) if notes else text

        return await asyncio.to_thread(read)

    def _read_image(self, path: Path, ctx: ToolContext) -> ToolOutput:
        """The picture itself, for the model to look at."""
        with open(self._open(path, os.O_RDONLY, ctx), "rb") as f:
            if os.fstat(f.fileno()).st_size > attachments.MAX_IMAGE_BYTES:
                raise ToolError(f"{self._display(path)} is too large to look at: pictures "
                                f"up to {attachments.MAX_IMAGE_BYTES // 2 ** 20} MB can be.")
            raw = f.read()
        image = Image(attachments.IMAGE_TYPES[path.suffix.lower()],
                      base64.b64encode(raw).decode(), path.name)
        return ToolOutput(f"[Image {self._display(path)}]", attachments=(image,))

    def _read_pdf(self, path: Path, pages: str | None, view: bool,
                  ctx: ToolContext) -> ToolOutput:
        """A PDF's pages, all or those `pages` names: their text, each under its number, up
        to MAX_READ_CHARS; with `view`, at most PDF_PAGES_PER_READ of the pages themselves
        as a document of their own, which a model that reads PDFs sees as they look."""
        with open(self._open(path, os.O_RDONLY, ctx), "rb") as f:
            try:
                reader = pypdf.PdfReader(f)
                total = len(reader.pages)
                first, last = _page_range(pages, total)
                head = f"PDF, {total} page{'s' if total != 1 else ''}"
                if not view:
                    return ToolOutput(self._pdf_text(reader, first, last, head))
                more = last - first + 1 > PDF_PAGES_PER_READ
                last = min(last, first + PDF_PAGES_PER_READ - 1)
                writer = pypdf.PdfWriter()
                texts = []
                for number in range(first, last + 1):
                    page = reader.pages[number - 1]
                    writer.add_page(page)
                    texts.append(f"[Page {number}]\n{page.extract_text().strip()}".rstrip())
                data = io.BytesIO()
                writer.write(data)
            except (pypdf.errors.PyPdfError, KeyError) as e:
                raise ToolError(f"{self._display(path)} cannot be read as a PDF: {e}") from e
        if (first, last) != (1, total):
            head += f"; pages {first}-{last} attached"
        if more:
            head += f'; use pages "{last + 1}-{min(total, last + PDF_PAGES_PER_READ)}" to read on'
        if data.tell() > attachments.MAX_PDF_BYTES:
            raise ToolError(f"These pages of {self._display(path)} are too large to look at; "
                            "ask for fewer, or read their text.")
        document = Document(str(path), base64.b64encode(data.getvalue()).decode(),
                            "\n\n".join(texts)[:MAX_READ_CHARS], last - first + 1)
        return ToolOutput(f"[{head}]", attachments=(document,))

    @staticmethod
    def _pdf_text(reader: pypdf.PdfReader, first: int, last: int, head: str) -> str:
        parts, size = [f"[{head}]"], 0
        for number in range(first, last + 1):
            text = reader.pages[number - 1].extract_text().strip()
            if size and size + len(text) > MAX_READ_CHARS:
                parts.append(f'[more pages follow; use pages "{number}-{last}" to read on]')
                break
            parts.append(f"[Page {number}]\n{text[:MAX_READ_CHARS]}".rstrip())
            size += len(text)
        if not size:
            parts.append("[No text could be read from these pages: they may be scans. Read "
                         "them with view true to look at them.]")
        return "\n".join(parts)

    def _as_read(self, path: Path, ctx: ToolContext) -> None:
        """Refuse to change a file that is there unless the conversation read it and it
        is as it was then: a change made meanwhile (by the user, a command) would be
        overwritten unseen. Blocking."""
        try:
            now = _version(path)
        except FileNotFoundError:
            return  # a new file
        seen = self._seen.get((ctx.conversation_id, path.resolve()))
        if seen is None:
            raise ToolError(f"Read {self._display(path)} first: you have not read it in "
                            "this conversation.")
        if seen != now:
            raise ToolError(f"{self._display(path)} changed since you read it; read it "
                            "again before changing it.")

    def _saw(self, path: Path, ctx: ToolContext) -> None:
        """The conversation knows the file as it is now. Blocking."""
        try:
            self._seen[(ctx.conversation_id, path.resolve())] = _version(path)
        except OSError:
            pass

    async def _write_file(self, args: dict[str, Any], ctx: ToolContext) -> str:
        path = self._path(args["path"])
        content: str = args["content"]

        def write() -> None:
            existed = os.path.lexists(path)
            self._within(path.parent, ctx).mkdir(parents=True, exist_ok=True)
            with open(self._open(path, os.O_WRONLY | os.O_CREAT, ctx), "w", encoding="utf-8") as f:
                if existed:  # checked once it is open: where it leads is refused first
                    self._as_read(path, ctx)
                f.truncate()
                f.write(content)
            self._saw(path, ctx)

        await asyncio.to_thread(write)
        return f"Wrote {len(content)} characters to {self._display(path)}"

    async def _edit_file(self, args: dict[str, Any], ctx: ToolContext) -> str:
        path = self._path(args["path"])
        old, new = args["old_string"], args["new_string"]
        replace_all = args.get("replace_all", False)

        def edit() -> int:
            with open(self._open(path, os.O_RDWR, ctx), "r+", encoding="utf-8") as f:
                self._as_read(path, ctx)
                text = f.read()
                count = text.count(old)
                if count == 1 or (count > 1 and replace_all):
                    f.seek(0)
                    f.truncate()
                    f.write(text.replace(old, new))
            if count == 0:
                raise ToolError(f"old_string not found in {self._display(path)}")
            if count > 1 and not replace_all:
                raise ToolError(
                    f"old_string occurs {count} times in {self._display(path)}; "
                    "add context to make it unique or set replace_all"
                )
            self._saw(path, ctx)
            return count

        count = await asyncio.to_thread(edit)
        return f"Replaced {count} occurrence(s) in {self._display(path)}"

    async def _list_files(self, args: dict[str, Any], ctx: ToolContext) -> str:
        base = self._path(args.get("path"))
        pattern = args.get("pattern", "*")

        def list_(cancelled: threading.Event) -> list[str]:
            found = []
            folder = self._open_folder(base, ctx)
            try:
                for rel, name, _ in _walk(folder, cancelled, ctx, self._ignored_above(base)):
                    if fnmatch(rel if "/" in pattern else name, pattern):
                        found.append(rel)
                        if len(found) > MAX_LIST_ENTRIES:
                            break
            finally:
                os.close(folder)
            return found

        found = await _cancellable_thread(list_)
        lines = sorted(found[:MAX_LIST_ENTRIES])
        if len(found) > MAX_LIST_ENTRIES:
            lines.append(f"[truncated at {MAX_LIST_ENTRIES} entries]")
        return "\n".join(lines) if lines else "No files found"

    async def _search_files(self, args: dict[str, Any], ctx: ToolContext) -> str:
        base = self._path(args.get("path"))
        name_glob = args.get("glob")
        output, context = args.get("output", "content"), args.get("context", 0)
        try:
            regex = re.compile(args["pattern"],
                               re.IGNORECASE if args.get("ignore_case") else 0)
        except re.error as e:
            raise ToolError(f"Invalid regular expression: {e}") from None

        def search(cancelled: threading.Event) -> list[str]:
            matches: list[str] = []
            folder = self._open_folder(base, ctx)
            try:
                for rel, name, within in _walk(folder, cancelled, ctx,
                                               self._ignored_above(base)):
                    if name_glob and not fnmatch(name, name_glob):
                        continue
                    if (data := _regular_file(name, within)) is None or b"\0" in data[:8192]:
                        continue
                    lines = data.decode("utf-8", "replace").splitlines()
                    hits = [n for n, line in enumerate(lines, 1) if regex.search(line)]
                    if not hits:
                        continue
                    if output == "files":
                        matches.append(rel)
                    elif output == "count":
                        matches.append(f"{rel}:{len(hits)}")
                    else:
                        matches += _with_context(rel, lines, hits, context)
                    if len(matches) > MAX_SEARCH_MATCHES:
                        return matches
            finally:
                os.close(folder)
            return matches

        matches = await _cancellable_thread(search)
        lines = matches[:MAX_SEARCH_MATCHES]
        if len(matches) > MAX_SEARCH_MATCHES:
            lines.append(f"[truncated at {MAX_SEARCH_MATCHES} matches]")
        return "\n".join(lines) if lines else "No matches"


class _OutputBuffer:
    """Keeps the head and tail of a stream within a character budget."""

    def __init__(self, limit: int) -> None:
        self._half = limit // 2
        self._head = ""
        self._tail = ""
        self._dropped = 0

    def append(self, text: str) -> None:
        if len(self._head) < self._half:
            take = self._half - len(self._head)
            self._head += text[:take]
            text = text[take:]
        self._tail += text
        if len(self._tail) > self._half:
            self._dropped += len(self._tail) - self._half
            self._tail = self._tail[-self._half :]

    def text(self) -> str:
        if self._dropped:
            return f"{self._head}\n[... {self._dropped} characters omitted ...]\n{self._tail}"
        return self._head + self._tail


async def _pump(stream: asyncio.StreamReader, out: _OutputBuffer, ctx: ToolContext,
                hidden: str = "") -> None:
    """Keep what the command writes, and show its newest line as progress; a line that
    starts with `hidden` is never shown."""
    last_report = 0.0
    pending: str | None = None
    while chunk := await stream.read(65536):
        text = chunk.decode("utf-8", "replace")
        out.append(text)
        lines = [line for line in re.split(r"[\r\n]", text)
                 if line.strip() and not (hidden and line.startswith(hidden))]
        if lines:
            pending = lines[-1][:200]
        now = time.monotonic()
        if pending is not None and now - last_report >= PROGRESS_INTERVAL:
            ctx.progress(pending)
            pending, last_report = None, now
    if pending is not None:
        ctx.progress(pending)


async def _terminate_group(proc: asyncio.subprocess.Process) -> None:
    """SIGTERM the command's process group, then SIGKILL whatever is left."""
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(proc.pid, signal.SIGTERM)
    with contextlib.suppress(TimeoutError, asyncio.CancelledError):
        await asyncio.wait_for(proc.wait(), TERMINATE_GRACE)
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(proc.pid, signal.SIGKILL)
    with contextlib.suppress(asyncio.CancelledError):
        await proc.wait()


@dataclass(frozen=True)
class _Rules:
    """A .gitignore file's patterns, for the paths under `scope` (from where the walk
    starts; "" for all), each seen from the file's own folder as `offset` + the rest."""

    scope: str
    offset: str
    spec: pathspec.GitIgnoreSpec

    def ignore(self, rel: str, folder: bool) -> bool:
        if self.scope and not rel.startswith(self.scope + "/"):
            return False
        rest = rel[len(self.scope) + 1:] if self.scope else rel
        path = f"{self.offset}/{rest}" if self.offset else rest
        return self.spec.match_file(path + ("/" if folder else ""))


def _gitignore(read: Callable[[], str] | None) -> pathspec.GitIgnoreSpec | None:
    if read is None:
        return None
    try:
        return pathspec.GitIgnoreSpec.from_lines(read().splitlines())
    except (OSError, ValueError):
        return None


def _walk(folder: int, cancelled: threading.Event, ctx: ToolContext,
          rules: Sequence[_Rules] = ()) -> Iterator[tuple[str, str, int]]:
    """The files under the open folder, skipping hidden directories and what the
    .gitignore files (`rules` from above it, and each folder's own) leave out: each its
    path from there, its name and the open directory it is in. Links to directories are
    not followed, and nothing is reached by a path that could be swapped meanwhile. Sends
    silent heartbeats as it goes."""
    rules = list(rules)
    last_beat = 0.0
    for dirpath, dirnames, filenames, within in os.fwalk(".", dir_fd=folder):
        if cancelled.is_set():
            return
        if (now := time.monotonic()) - last_beat >= HEARTBEAT_INTERVAL:
            ctx.progress("")
            last_beat = now
        under = dirpath[2:]  # "." or "./a/b"
        if (".gitignore" in filenames and (text := _regular_file(".gitignore", within))
                and (spec := _gitignore(lambda text=text: text.decode("utf-8", "replace")))):
            rules.append(_Rules(under, "", spec))
        dirnames[:] = sorted(d for d in dirnames if not d.startswith(".")
                             and not _ignored(rules, f"{under}/{d}" if under else d, True))
        for name in sorted(filenames):
            rel = f"{under}/{name}" if under else name
            if not _ignored(rules, rel, False):
                yield rel, name, within


def _ignored(rules: Sequence[_Rules], rel: str, folder: bool) -> bool:
    return any(rule.ignore(rel, folder) for rule in rules)


def _with_context(rel: str, lines: Sequence[str], hits: Sequence[int],
                  context: int) -> list[str]:
    """The lines that match, as `path:line: text`, each with `context` lines around it as
    `path-line- text`; "--" between groups that do not touch."""
    if not context:
        return [f"{rel}:{n}: {lines[n - 1][:500]}" for n in hits]
    out: list[str] = []
    shown = set(hits)
    last = 0
    for n in hits:
        first, end = max(1, n - context), min(len(lines), n + context)
        if last and first > last + 1:
            out.append("--")
        for m in range(max(first, last + 1), end + 1):
            sep = ":" if m in shown else "-"
            out.append(f"{rel}{sep}{m}{sep} {lines[m - 1][:500]}")
        last = max(last, end)
    return out


def _version(path: Path) -> tuple[int, int, int]:
    """What tells one state of a file from the next: its size, its time of change and
    its inode, which a file replaced by another (an editor's save) changes."""
    info = path.stat()
    return info.st_size, info.st_mtime_ns, info.st_ino


def _regular_file(name: str, within: int) -> bytes | None:
    """What an ordinary file in the open directory holds; None for a link, a special
    file, one too large to search, or one that cannot be read."""
    try:
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=within)
    except OSError:
        return None
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_SEARCH_FILE_BYTES:
            return None
        with open(fd, "rb", closefd=False) as f:
            return f.read()
    except OSError:
        return None
    finally:
        os.close(fd)


async def _cancellable_thread[T](fn: Callable[[threading.Event], T]) -> T:
    """Run fn in a thread; on cancellation, signal it to stop instead of leaking work."""
    cancelled = threading.Event()
    try:
        return await asyncio.to_thread(fn, cancelled)
    except asyncio.CancelledError:
        cancelled.set()
        raise
