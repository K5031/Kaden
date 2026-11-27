"""The built-in Tools module: shell commands, file operations, the web and a look at the
screen (POSIX only).

Relative paths resolve against the workspace root, which is also the shell's working
directory. Commands inherit the process environment (PERMISSION_CEILING_ENV included);
sandboxed ones without the variables that look like secrets (sandbox_environment).

The workspace is a boundary the runtime can rely on (`confined`): a file call is confined
when its path is inside it, and a shell command when it runs in the sandbox (`sandbox.py`:
bubblewrap on Linux, Seatbelt on macOS), where only the workspace is writable and the home
directories are hidden. The web tools
(`web.py`) touch nothing on this computer, so they are confined too. Calls that reach
outside still work; the runtime asks the user first.
"""

from __future__ import annotations

import asyncio
import base64
import bisect
import codecs
import contextlib
import io
import json
import math
import os
import re
import secrets
import shutil
import signal
import stat
import tempfile
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

from kaden.api import (
    DESCRIPTION_ARG,
    DESCRIPTION_SCHEMA,
    Attachment,
    Document,
    Image,
    InstancePaths,
    StandingWatch,
    ToolCall,
    ToolContext,
    ToolOutput,
    Tools,
    ToolSetting,
    ToolSpec,
    foreground_timeout,
)
from kaden.tools.shell_files import (
    attachments,
    bili_account,
    bilibili,
    office,
    sandbox,
    screen,
    web,
)

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
# Grep's `type`: ripgrep's names for the common file types.
FILE_TYPES: dict[str, tuple[str, ...]] = {
    "py": ("*.py", "*.pyi"), "js": ("*.js", "*.mjs", "*.cjs", "*.jsx"),
    "ts": ("*.ts", "*.tsx", "*.mts", "*.cts"), "rust": ("*.rs",), "go": ("*.go",),
    "java": ("*.java",), "c": ("*.c", "*.h"),
    "cpp": ("*.cpp", "*.cc", "*.cxx", "*.hpp", "*.hh", "*.hxx", "*.h"),
    "md": ("*.md", "*.markdown"), "json": ("*.json",), "yaml": ("*.yaml", "*.yml"),
    "toml": ("*.toml",), "html": ("*.html", "*.htm"), "css": ("*.css", "*.scss", "*.sass"),
    "sh": ("*.sh", "*.bash", "*.zsh"), "rb": ("*.rb",), "php": ("*.php",),
    "swift": ("*.swift",), "kotlin": ("*.kt", "*.kts"), "cs": ("*.cs",), "sql": ("*.sql",),
    "txt": ("*.txt",), "xml": ("*.xml",),
}


class ToolError(Exception):
    """Expected failure reported to the model as an is_error output."""



def _object(properties: dict[str, Any], required: Sequence[str] = ()) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": list(required),
        "additionalProperties": False,
    }


_STRING = {"type": "string"}

SHELL = ToolSpec(
    name="Bash",
    description=(
        "Run a shell command (sh) in the workspace and return its combined stdout and "
        "stderr plus the exit code. Output is truncated to the first and last part when "
        "long. Non-zero exit codes are reported as errors. The folder a command ends in is "
        "where the next one starts, in this conversation, while it is inside the "
        "workspace; elsewhere, the next starts in the workspace. A command runs until it "
        "ends or its `timeout` (milliseconds; 120000 unless set, at most 600000). Set "
        "`run_in_background` for one that runs long, such as a server or a long build: "
        "you get on with other work and are told its outcome when it ends."
    ),
    parameters=_object({
        "command": _STRING,
        "timeout": {"type": "integer", "minimum": 1, "maximum": 600_000},
        "run_in_background": {"type": "boolean"},
        "dangerouslyDisableSandbox": {"type": "boolean"},
        DESCRIPTION_ARG: DESCRIPTION_SCHEMA,
    }, ["command"]),
    stall_timeout=SHELL_STALL_TIMEOUT,
    foreground=True,
)
SANDBOX_NOTE = (
    " Commands run in a sandbox: they can read and write the workspace, read the system "
    "and use the network, but the rest of the home directory, local services and "
    "environment variables holding keys or tokens are hidden, and nothing outside the "
    "workspace can be changed. Set `dangerouslyDisableSandbox` to true only when a command needs something outside; "
    "the user is then asked first, so say why."
)
# Variables whose names look like secrets (API keys, tokens, passwords) are left out of a
# sandboxed command's environment: it runs unasked and may use the network.
SECRET_NAME = re.compile(r"KEY|TOKEN|SECRET|PASSW|CREDENTIAL", re.IGNORECASE)
FILE_SPECS: tuple[ToolSpec, ...] = (
    ToolSpec(
        name="Read",
        description=(
            "Read a UTF-8 text file, its lines numbered as `cat -n` numbers them (the number, "
            "a tab, the line); the number and tab are not part of the file. Optional 1-based "
            f"start line `offset` and line `limit` (default {MAX_READ_LINES}). A picture (PNG, JPEG, GIF, WebP) is shown to you. "
            "A PDF is read as the text of its pages: all of them, or those `pages` names, "
            "such as \"3\" or \"2-5\". Set `view` to look at the pages themselves, for a "
            "scan, a chart or a layout the text does not carry: at most "
            f"{PDF_PAGES_PER_READ} at a time, and each costs far more than its text. "
            "A Jupyter notebook is read as its cells with their ids and outputs. "
            "A Word, Excel or "
            "PowerPoint file (.docx, .xlsx, .pptx) is read as its text, by lines like any "
            "text file."
        ),
        parameters=_object(
            {
                "file_path": _STRING,
                "offset": {"type": "integer", "minimum": 1},
                "limit": {"type": "integer", "minimum": 1},
                "pages": {"type": "string", "description": "For a PDF: a page or a range."},
                "view": {"type": "boolean",
                         "description": "For a PDF: the pages as they look, not their text."},
            },
            ["file_path"],
        ),
        read_only=True,
    ),
    ToolSpec(
        name="Write",
        description="Create or overwrite a text file, creating parent directories. A file that is "
            "there must have been read in this conversation, and be unchanged since.",
        parameters=_object({"file_path": _STRING, "content": _STRING}, ["file_path", "content"]),
    ),
    ToolSpec(
        name="Edit",
        description=(
            "Replace an exact string in a file, which must have been read in this "
            "conversation and be unchanged since. `old_string` must occur exactly once unless "
            "`replace_all` is true."
        ),
        parameters=_object(
            {
                "file_path": _STRING,
                "old_string": {"type": "string", "minLength": 1},
                "new_string": _STRING,
                "replace_all": {"type": "boolean"},
            },
            ["file_path", "old_string", "new_string"],
        ),
    ),
    ToolSpec(
        name="Glob",
        description=(
            "List the files under a directory (default: workspace root) whose path relative "
            "to it matches a glob `pattern`, the most recently changed first: `*` and `?` "
            "stay within one folder, `**/` is any number of folders, `{a,b}` either. "
            "Hidden directories and what .gitignore files leave out are skipped."
        ),
        parameters=_object({"pattern": {"type": "string", "minLength": 1}, "path": _STRING},
                           ["pattern"]),
        read_only=True,
    ),
    ToolSpec(
        name="Grep",
        description=(
            "Search text files under a directory for a Python regular expression. "
            "`output_mode` \"files_with_matches\" (the default) lists the files that match, "
            "\"count\" how many lines match in each, \"content\" the matching lines as "
            "`path:line: text`, with `-A` lines after, `-B` before or `-C` around each as "
            "`path-line- text`. `-i` ignores case, `glob` filters file names and `type` "
            "file types, `multiline` lets a match span lines, `head_limit` keeps the first "
            "entries. Hidden directories, what .gitignore files leave out, "
            "binary files and files over 1 MB are skipped."
        ),
        parameters=_object(
            {"pattern": {"type": "string", "minLength": 1}, "path": _STRING, "glob": _STRING,
             "type": {"type": "string", "description": "A file type, as ripgrep's --type: "
                      "py, js, ts, rust, go, java, c, cpp, md, json, yaml, toml, html, css, "
                      "sh, rb, php, swift, kotlin, cs, sql, txt, xml."},
             "output_mode": {"enum": ["files_with_matches", "count", "content"]},
             "-i": {"type": "boolean"},
             **{flag: {"type": "integer", "minimum": 0, "maximum": 10}
                for flag in ("-A", "-B", "-C")},
             "head_limit": {"type": "integer", "minimum": 1},
             "multiline": {"type": "boolean"}},
            ["pattern"],
        ),
        read_only=True,
    ),
    ToolSpec(
        name="NotebookEdit",
        description=(
            "Replace, insert or delete one cell of a Jupyter notebook (.ipynb), which must "
            "have been read in this conversation and be unchanged since. `cell_id` is the id "
            "Read shows in `<cell id=\"...\">`; it is needed to replace or delete. "
            "`edit_mode` is replace unless set; insert puts a new cell after `cell_id` (or "
            "first without one) and needs `cell_type`. A replaced code cell loses its "
            "outputs."
        ),
        parameters=_object(
            {"notebook_path": _STRING, "cell_id": _STRING, "new_source": _STRING,
             "cell_type": {"enum": ["code", "markdown"]},
             "edit_mode": {"enum": ["replace", "insert", "delete"]}},
            ["notebook_path", "new_source"],
        ),
        deferred=True,  # notebooks are rare, as Claude Code defers it
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
    """Options: root (default: the current directory), shell_timeout, shell_stall_timeout,
    bilibili_wake (a setting of the user's: user_config)."""
    options = dict(options)
    account = bili_account.Account(paths.data_dir,
                                   wake=options.pop("bilibili_wake", "follows"))
    return ShellFilesTools(Path(options.pop("root", Path.cwd())), account=account, **options)


factory.display_name = "Shell, files and web"  # type: ignore[attr-defined]
factory.work_folder_option = "root"  # type: ignore[attr-defined]
factory.user_config = (  # type: ignore[attr-defined]
    ToolSetting("bilibili_wake", "Woken by Bilibili messages", "choice", "follows",
                "Whose messages start a turn. The rest wait unread.",
                dict(bili_account.WAKE)),
)


def sandbox_environment(environ: Mapping[str, str]) -> dict[str, str]:
    return {k: v for k, v in environ.items() if not SECRET_NAME.search(k)}


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
        bilibili_tools: bilibili.Bilibili | None = None,
        screen_tools: screen.Screen | None = None,
        account: bili_account.Account | None = None,
    ) -> None:
        """`account`: the instance's own Bilibili account (its data folder's); none without."""
        self._default = Path(root).expanduser().resolve()
        self._found = sandbox.find()  # this system's, if it works here
        # Files the user attached: the sandbox shows each, read-only, where it is.
        self._attached: dict[Path, None] = {}
        self._shell_spec = replace(SHELL, timeout=shell_timeout,
                                   stall_timeout=shell_stall_timeout)
        self._web = web_tools or web.Web()
        self._bili = bilibili_tools or bilibili.Bilibili()
        self._screen = screen_tools or screen.Screen(screen.find())
        self._account = account
        # Each conversation's files as it last read or wrote them: (conversation, file)
        # to what _version says of it.
        self._seen: dict[tuple[str, Path], tuple[int, int, int]] = {}
        self._folders: dict[str, Path] = {}  # where each conversation's shell is now
        # The line a command ends with to say where it ended; never part of its output.
        self._where = f"\x1b[kaden-folder-{secrets.token_hex(8)}]"
        self._set_root(self._default)
        self._handlers: dict[str, Callable[[dict[str, Any], ToolContext], Awaitable[str]]] = {
            "Bash": self._shell,
            "Read": self._read_file,
            "Write": self._write_file,
            "Edit": self._edit_file,
            "Glob": self._list_files,
            "Grep": self._search_files,
            "NotebookEdit": self._notebook_edit,
        }

    def _set_root(self, root: Path) -> None:
        self.root = root
        home = Path.home().resolve()
        # A workspace that is or holds the home directory (or is /) is no boundary.
        self._bounded = root != home and root not in home.parents
        self._sandbox = self._found if self._bounded else None
        shell = self._shell_spec
        if self._sandbox is not None:
            shell = replace(shell, description=shell.description + SANDBOX_NOTE)
        self._spec_list = (shell, *FILE_SPECS, *web.SPECS, *self._bili.specs,
                           *self._screen.specs)
        self._specs = {spec.name: spec for spec in self._spec_list}

    async def list_tools(self) -> Sequence[ToolSpec]:
        if self._account is None:
            return self._spec_list
        return (*self._spec_list, *await self._account.specs())  # with a login, more

    async def standing_watches(self) -> Sequence[StandingWatch]:
        return await self._account.standing() if self._account is not None else ()

    async def aclose(self) -> None:
        await self._bili.aclose()
        if self._account is not None:
            await self._account.aclose()

    async def use_folder(self, folder: Path | None) -> None:
        root = self._default if folder is None else Path(folder).expanduser().resolve()
        await asyncio.to_thread(root.mkdir, parents=True, exist_ok=True)
        self._set_root(root)

    async def confined(self, call: ToolCall) -> bool:
        if call.name in web.NAMES or call.name in bilibili.NAMES:
            return True
        if call.name in bili_account.NAMES:  # reading the account's feed and follows only
            return call.name in bili_account.READ_ONLY
        if not self._bounded or call.name not in self._handlers:  # the screen too
            return False
        if call.name == SHELL.name:
            return (self._sandbox is not None
                    and call.arguments.get("dangerouslyDisableSandbox") is not True)
        args = call.arguments
        path = args.get("file_path", args.get("notebook_path", args.get("path", ".")))
        return isinstance(path, str) and await asyncio.to_thread(self._inside, path)

    def _inside(self, raw: str) -> bool:
        """Whether the path, with symlinks followed, is in the workspace."""
        return self._path(raw).resolve().is_relative_to(self.root)

    async def attach(self, path: Path) -> Attachment:
        """A relative path is taken from the work folder. Reading is the user's act, so
        the file may lie outside it, and a sandboxed command can read it from then on."""
        block = await asyncio.to_thread(attachments.read, self._path(str(path)))
        self._attached[self._path(str(path)).resolve()] = None
        return block

    async def execute(self, call: ToolCall, ctx: ToolContext) -> ToolOutput:
        if call.name in bili_account.NAMES and self._account is not None:
            return await self._account_call(call)
        spec = self._specs.get(call.name)
        if spec is None:
            return ToolOutput(f"Unknown tool: {call.name}", is_error=True)
        try:
            jsonschema.validate(call.arguments, spec.parameters)
        except jsonschema.ValidationError as e:
            return ToolOutput(f"Invalid arguments: {e.message}", is_error=True)
        try:
            result = await self._run(call, ctx)
            return result if isinstance(result, ToolOutput) else ToolOutput(result)
        except (ToolError, web.WebError, screen.ScreenError, bilibili.BiliError) as e:
            return ToolOutput(str(e), is_error=True)
        except httpx2.HTTPError as e:
            return ToolOutput(f"The page could not be read: {type(e).__name__}: {e}",
                              is_error=True)
        except OSError as e:
            return ToolOutput(f"{type(e).__name__}: {e}", is_error=True)

    async def _run(self, call: ToolCall, ctx: ToolContext) -> str | ToolOutput:
        if call.name in web.NAMES:
            return await self._web.run(call.name, call.arguments)
        if call.name in bilibili.NAMES:
            return await self._bili.run(call.name, call.arguments)
        if call.name == screen.NAME:
            return await self._screen.run()
        return await self._handlers[call.name](call.arguments, ctx)

    async def _account_call(self, call: ToolCall) -> ToolOutput:
        assert self._account is not None
        spec = next((s for s in await self._account.specs() if s.name == call.name), None)
        if spec is None:
            return ToolOutput("Not logged in to Bilibili: BiliLogin gives a code to log in.",
                              is_error=True)
        try:
            jsonschema.validate(call.arguments, spec.parameters)
        except jsonschema.ValidationError as e:
            return ToolOutput(f"Invalid arguments: {e.message}", is_error=True)
        try:
            return await self._account.run(call.name, call.arguments)
        except httpx2.HTTPError as e:
            return ToolOutput(f"Bilibili could not be reached: {type(e).__name__}: {e}",
                              is_error=True)

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
        """os.open of a regular file (or, with O_DIRECTORY, a folder): a pipe or a device
        is refused rather than waited on. For a call `confined` allowed, the file is
        reached from the workspace one folder at a time, none of them a link, so a link
        swapped in after the check (or during the open) cannot lead outside."""
        folder = flags & os.O_DIRECTORY
        flags |= 0 if folder else os.O_NONBLOCK  # opening a pipe would wait for a writer
        if ctx.confined:
            fd = self._open_inside(path, self._within(path, ctx), flags)
        else:
            fd = os.open(path, flags, 0o644)
        if not folder:
            try:
                if not stat.S_ISREG(os.fstat(fd).st_mode):
                    raise ToolError(f"{self._display(path)} is not a regular file")
                os.set_blocking(fd, True)
            except BaseException:
                os.close(fd)
                raise
        return fd

    def _open_inside(self, path: Path, resolved: Path, flags: int) -> int:
        """`resolved`, a path in the workspace without links, opened from the workspace
        down, each step refusing a link (O_NOFOLLOW): what is opened is in the workspace,
        whatever is swapped in meanwhile."""
        *folders, last = ("", *resolved.relative_to(self.root).parts)
        fd = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY)
        try:
            for name in folders[1:]:
                step = self._step(path, name, os.O_RDONLY | os.O_DIRECTORY, fd)
                os.close(fd)
                fd = step
            if not last:  # the workspace itself
                return fd if flags & os.O_DIRECTORY else self._step(path, ".", flags, fd)
            opened = self._step(path, last, flags, fd)
        except BaseException:
            os.close(fd)
            raise
        os.close(fd)
        return opened

    def _step(self, path: Path, name: str, flags: int, within: int) -> int:
        try:
            return os.open(name, flags | os.O_NOFOLLOW, 0o644, dir_fd=within)
        except OSError:
            with contextlib.suppress(OSError):
                if stat.S_ISLNK(os.stat(name, dir_fd=within, follow_symlinks=False).st_mode):
                    raise self._outside(path) from None
            raise

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
        if self._sandbox is None or args.get("dangerouslyDisableSandbox"):
            return await self._command(args, ctx, None)
        tmp = Path(await asyncio.to_thread(tempfile.mkdtemp, prefix="kaden-sandbox-")).resolve()
        try:
            return await self._command(args, ctx, tmp)
        finally:
            await asyncio.to_thread(shutil.rmtree, tmp, True)

    async def _command(self, args: dict[str, Any], ctx: ToolContext, tmp: Path | None) -> str:
        """The command, in the sandbox with `tmp` as its temporary folder; without one,
        as the user."""
        # Our own pipe rather than PIPE: asyncio's Process.wait() also waits for its pipes
        # to close, which never happens while a backgrounded child holds stdout.
        read_fd, write_fd = os.pipe()
        folder = self._folders.get(ctx.conversation_id, self.root)
        if not await asyncio.to_thread(folder.is_dir):
            folder = self.root
        # The command, then where it ended, on a line of its own; its exit code stays.
        script = (f"{args['command']}\n__kaden_status=$?\n"
                  f"printf '\\n%s%s\\n' '{self._where}' \"$(pwd -P)\"\n"
                  "exit $__kaden_status\n")
        try:
            command = ["sh", "-c", script]
            if tmp is not None:
                assert self._sandbox is not None
                command = await asyncio.to_thread(self._sandbox.wrap, command, root=self.root,
                                                  tmp=tmp, granted=list(self._attached))
            proc = await asyncio.create_subprocess_exec(
                *command,
                env=None if tmp is None else {**sandbox_environment(os.environ),
                                              "TMPDIR": str(tmp)},
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
        limit = None if args.get("run_in_background") else foreground_timeout(args)
        try:
            try:
                code = await asyncio.wait_for(proc.wait(), limit)
            except TimeoutError:
                await _terminate_group(proc)
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(asyncio.shield(reader), DRAIN_GRACE)
                text = self._ended_in(output.text(), ctx)
                raise ToolError(f"{text}\n[timed out after {limit:g} s; set timeout for "
                                "longer, or run_in_background]".lstrip()) from None
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
        path = self._path(args["file_path"])
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
        if path.suffix.lower() == ".ipynb":
            return await asyncio.to_thread(self._read_notebook, path, offset, limit, ctx)

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
                        selected.append(f"{number:>6}\t{line[: MAX_READ_CHARS - size]}")
                        notes.append(f"[truncated at {MAX_READ_CHARS} characters]")
                        break
                    selected.append(f"{number:>6}\t{line}")
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

    def _notebook(self, path: Path, ctx: ToolContext) -> dict[str, Any]:
        with open(self._open(path, os.O_RDONLY, ctx), encoding="utf-8") as f:
            try:
                book = json.load(f)
            except json.JSONDecodeError as e:
                raise ToolError(f"{self._display(path)} is not a notebook: {e}") from None
        if not isinstance(book, dict) or not isinstance(book.get("cells"), list):
            raise ToolError(f"{self._display(path)} is not a notebook: it has no cells")
        return book

    def _read_notebook(self, path: Path, offset: int, limit: int, ctx: ToolContext) -> str:
        """A notebook as its cells, each with its id, type (unless code), source and
        outputs, as Claude Code's Read shows one: `limit` cells from the `offset`th, as
        many as fit in MAX_READ_CHARS, with a note on where to read on."""
        cells = self._notebook(path, ctx)["cells"]
        parts: list[str] = []
        size, end = 0, min(len(cells), offset - 1 + limit)
        for n in range(offset - 1, end):
            cell = cells[n]
            cell_id = _cell_id(cell, n)
            lines = [f'<cell id="{cell_id}">']
            if cell.get("cell_type", "code") != "code":
                lines.append(f"<cell_type>{cell.get('cell_type')}</cell_type>")
            lines.append(_joined(cell.get("source", "")).rstrip("\n"))
            lines += [f"[output] {text}" for text in map(_output_text, cell.get("outputs", ()))
                      if text]
            lines.append(f'</cell id="{cell_id}">')
            part = "\n".join(lines) + "\n"
            if parts and size + len(part) > MAX_READ_CHARS:
                end = n
                break
            parts.append(part[:MAX_READ_CHARS])
            size += len(part)
        if end < len(cells):
            parts.append(f"[{len(cells) - end} more cells; use offset {end + 1} to read on]")
        return "".join(parts)

    async def _notebook_edit(self, args: dict[str, Any], ctx: ToolContext) -> str:
        path = self._path(args["notebook_path"])
        mode = args.get("edit_mode", "replace")
        cell_id, source, kind = args.get("cell_id"), args["new_source"], args.get("cell_type")

        def edit() -> str:
            with open(self._open(path, os.O_RDWR, ctx), "r+", encoding="utf-8",
                      newline="") as f:
                self._as_read(path, ctx)
                raw = f.read()
                try:
                    book = json.loads(raw)
                except json.JSONDecodeError as e:
                    raise ToolError(f"{self._display(path)} is not a notebook: {e}") from None
                cells = book.get("cells")
                if not isinstance(cells, list):
                    raise ToolError(f"{self._display(path)} is not a notebook: it has no cells")
                ids = [_cell_id(c, n) for n, c in enumerate(cells)]
                if cell_id is not None and cell_id not in ids:
                    raise ToolError(f"There is no cell {cell_id!r} in {self._display(path)}")
                at = ids.index(cell_id) if cell_id is not None else None
                if mode == "insert":
                    if kind is None:
                        raise ToolError("Inserting a cell needs its cell_type")
                    new: dict[str, Any] = {"cell_type": kind, "id": secrets.token_hex(4),
                                           "metadata": {}, "source": source}
                    if kind == "code":
                        new |= {"execution_count": None, "outputs": []}
                    cells.insert(0 if at is None else at + 1, new)
                    done = f"Inserted cell {new['id']}"
                elif at is None:
                    raise ToolError(f"{mode} needs the cell_id of the cell")
                elif mode == "delete":
                    del cells[at]
                    done = f"Deleted cell {cell_id}"
                else:
                    cell = cells[at]
                    cell["source"] = source
                    if kind is not None and kind != cell.get("cell_type"):
                        cell["cell_type"] = kind
                        if kind == "markdown":
                            cell.pop("outputs", None)
                            cell.pop("execution_count", None)
                    if cell.get("cell_type") == "code":
                        cell["outputs"], cell["execution_count"] = [], None
                    done = f"Replaced cell {cell_id}"
                text = json.dumps(book, indent=1, ensure_ascii=False) + "\n"
                f.seek(0)
                f.truncate()
                f.write(text.replace("\n", "\r\n") if _crlf(raw) else text)
            self._saw(path, ctx)
            return f"{done} in {self._display(path)}"

        return await asyncio.to_thread(edit)

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
        path = self._path(args["file_path"])
        content: str = args["content"]

        def write() -> None:
            existed = os.path.lexists(path)
            self._within(path.parent, ctx).mkdir(parents=True, exist_ok=True)
            with open(self._open(path, os.O_WRONLY | os.O_CREAT, ctx), "w", encoding="utf-8",
                      newline="") as f:
                if existed:  # checked once it is open: where it leads is refused first
                    self._as_read(path, ctx)
                f.truncate()
                f.write(content)
            self._saw(path, ctx)

        await asyncio.to_thread(write)
        return f"Wrote {len(content)} characters to {self._display(path)}"

    async def _edit_file(self, args: dict[str, Any], ctx: ToolContext) -> str:
        path = self._path(args["file_path"])
        old, new = args["old_string"], args["new_string"]
        replace_all = args.get("replace_all", False)

        def edit() -> int:
            with open(self._open(path, os.O_RDWR, ctx), "r+", encoding="utf-8",
                      newline="") as f:
                self._as_read(path, ctx)
                text = f.read()
                crlf = _crlf(text)  # edited with LF, as Read shows it, and written back
                if crlf:
                    text = text.replace("\r\n", "\n")
                find, put = (old.replace("\r\n", "\n"), new.replace("\r\n", "\n")) if crlf \
                    else (old, new)
                count = text.count(find)
                if count == 1 or (count > 1 and replace_all):
                    text = text.replace(find, put)
                    f.seek(0)
                    f.truncate()
                    f.write(text.replace("\n", "\r\n") if crlf else text)
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
        pattern = _glob(args["pattern"])

        def list_(cancelled: threading.Event) -> list[tuple[float, str]]:
            found = []
            folder = self._open_folder(base, ctx)
            try:
                for rel, name, within in _walk(folder, cancelled, ctx,
                                               self._ignored_above(base)):
                    if pattern.fullmatch(rel):
                        try:
                            changed = os.stat(name, dir_fd=within,
                                              follow_symlinks=False).st_mtime
                        except OSError:  # gone since it was listed
                            continue
                        found.append((-changed, rel))
            finally:
                os.close(folder)
            return found

        found = await _cancellable_thread(list_)
        lines = [rel for _, rel in sorted(found)[:MAX_LIST_ENTRIES]]
        if len(found) > MAX_LIST_ENTRIES:
            lines.append(f"[truncated at {MAX_LIST_ENTRIES} entries]")
        return "\n".join(lines) if lines else "No files found"

    async def _search_files(self, args: dict[str, Any], ctx: ToolContext) -> str:
        base = self._path(args.get("path"))
        name_glob = args.get("glob")
        output = args.get("output_mode", "files_with_matches")
        around = args.get("-C", 0)
        before, after = args.get("-B", around), args.get("-A", around)
        limit = min(args.get("head_limit", MAX_SEARCH_MATCHES), MAX_SEARCH_MATCHES)
        kind = args.get("type")
        if kind is not None and kind not in FILE_TYPES:
            raise ToolError(f"Unknown type {kind!r}; the types are: {', '.join(FILE_TYPES)}")
        name_globs = FILE_TYPES[kind] if kind is not None else ()
        multiline = args.get("multiline") is True
        flags = (re.IGNORECASE if args.get("-i") else 0) | (re.DOTALL | re.MULTILINE
                                                             if multiline else 0)
        try:
            regex = re.compile(args["pattern"], flags)
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
                    if name_globs and not any(fnmatch(name, g) for g in name_globs):
                        continue
                    if (data := _regular_file(name, within)) is None or b"\0" in data[:8192]:
                        continue
                    text = data.decode("utf-8", "replace")
                    lines = text.splitlines()
                    hits = (_spanned(regex, text) if multiline else
                            [n for n, line in enumerate(lines, 1) if regex.search(line)])
                    if not hits:
                        continue
                    if output == "files_with_matches":
                        matches.append(rel)
                    elif output == "count":
                        matches.append(f"{rel}:{len(hits)}")
                    else:
                        matches += _with_context(rel, lines, hits, before, after)
                    if len(matches) > limit:
                        return matches
            finally:
                os.close(folder)
            return matches

        matches = await _cancellable_thread(search)
        lines = matches[:limit]
        if len(matches) > limit and limit < args.get("head_limit", math.inf):
            lines.append(f"[truncated at {limit} entries]")
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
    decoder = codecs.getincrementaldecoder("utf-8")("replace")  # a read may end mid-character
    while chunk := await stream.read(65536):
        text = decoder.decode(chunk)
        out.append(text)
        lines = [line for line in re.split(r"[\r\n]", text)
                 if line.strip() and not (hidden and line.startswith(hidden))]
        if lines:
            pending = lines[-1][:200]
        now = time.monotonic()
        if pending is not None and now - last_report >= PROGRESS_INTERVAL:
            ctx.progress(pending)
            pending, last_report = None, now
    out.append(decoder.decode(b"", final=True))
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
                  before: int, after: int) -> list[str]:
    """The lines that match, as `path:line: text`, each with `before` and `after` lines
    around it as `path-line- text`; "--" between groups that do not touch."""
    if not before and not after:
        return [f"{rel}:{n}: {lines[n - 1][:500]}" for n in hits]
    out: list[str] = []
    shown = set(hits)
    last = 0
    for n in hits:
        first, end = max(1, n - before), min(len(lines), n + after)
        if last and first > last + 1:
            out.append("--")
        for m in range(max(first, last + 1), end + 1):
            sep = ":" if m in shown else "-"
            out.append(f"{rel}{sep}{m}{sep} {lines[m - 1][:500]}")
        last = max(last, end)
    return out


def _spanned(regex: re.Pattern[str], text: str) -> list[int]:
    """The numbers of the lines the matches of a multiline pattern cover."""
    starts = [0, *(i + 1 for i, c in enumerate(text) if c == "\n")]
    hits: set[int] = set()
    for m in regex.finditer(text):
        first = bisect.bisect_right(starts, m.start())
        last = bisect.bisect_right(starts, max(m.start(), m.end() - 1))
        hits.update(range(first, last + 1))
    return sorted(n for n in hits if n <= len(text.splitlines()))


def _glob(pattern: str) -> re.Pattern[str]:
    """A glob as a regular expression over a relative path: `*` and `?` within one folder,
    `**/` any number of folders, `**` anything, `[...]` a set, `{a,b}` either."""
    out, i, depth = [], 0, 0
    while i < len(pattern):
        c = pattern[i]
        if pattern.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
            continue
        if pattern.startswith("**", i):
            out.append(".*")
            i += 2
            continue
        if c == "*":
            out.append("[^/]*")
        elif c == "?":
            out.append("[^/]")
        elif c == "[" and (end := pattern.find("]", i + 2)) != -1:
            body = pattern[i + 1:end].replace("\\", "\\\\")
            out.append("[^" + body[1:] + "]" if body[0] in "!^" else "[" + body + "]")
            i = end + 1
            continue
        elif c == "{":
            out.append("(?:")
            depth += 1
        elif c == "}" and depth:
            out.append(")")
            depth -= 1
        elif c == "," and depth:
            out.append("|")
        else:
            out.append(re.escape(c))
        i += 1
    if depth:
        raise ToolError(f"Unclosed {{ in the pattern {pattern!r}")
    return re.compile("".join(out), re.DOTALL)


def _cell_id(cell: dict[str, Any], n: int) -> str:
    """A cell's id; one without (a notebook older than nbformat 4.5) by its place."""
    return str(cell.get("id") or f"cell-{n}")


def _joined(source: Any) -> str:
    return "".join(source) if isinstance(source, list) else str(source)


def _output_text(output: dict[str, Any]) -> str:
    """What a cell's output shows as text: a stream, a result's text, an error's name."""
    if "text" in output:
        return _joined(output["text"]).strip()
    data = output.get("data", {})
    if "text/plain" in data:
        return _joined(data["text/plain"]).strip()
    if output.get("output_type") == "error":
        return f"{output.get('ename', 'Error')}: {output.get('evalue', '')}"
    return "[image]" if any(k.startswith("image/") for k in data) else ""


def _crlf(text: str) -> bool:
    """Whether every line ends with CR LF; a file that mixes endings is edited as it is."""
    return "\r\n" in text and text.count("\n") == text.count("\r\n")


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
