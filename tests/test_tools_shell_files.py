import asyncio
import contextlib
import json
import os
import shutil
from pathlib import Path

import pytest

from kaden.api import InstancePaths, ToolCall
from kaden.tools.shell_files import (
    MAX_OUTPUT_CHARS,
    ShellFilesTools,
    bilibili,
    factory,
    sandbox,
)
from tests.conformance.tools_suite import RecordingContext, ToolCases, ToolsConformance

# Two background children plus a ticking loop: cancellation must reach all of them.
LONG_COMMAND = "sleep 30 & sleep 30 & for i in $(seq 1 100); do echo tick $i; sleep 0.1; done"


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    root = tmp_path / "ws"
    (root / "pkg").mkdir(parents=True)
    (root / "hello.txt").write_text("hello\nworld\n")
    (root / "pkg" / "mod.py").write_text("def answer():\n    return 42\n")
    (root / ".git").mkdir()
    (root / ".git" / "config").write_text("hidden answer\n")
    return root


class TestShellFilesConformance(ToolsConformance):
    @pytest.fixture
    def tools(self, workspace: Path) -> ShellFilesTools:
        return ShellFilesTools(workspace)

    @pytest.fixture
    def cases(self, workspace: Path) -> ToolCases:
        return ToolCases(
            quick=ToolCall("q", "Read", {"file_path": "hello.txt"}),
            failing=ToolCall("f", "Read", {"file_path": "missing.txt"}),
            long_running=ToolCall("l", "Bash", {"command": LONG_COMMAND}),
            extra=(
                ToolCall("s", "Grep", {"pattern": "answer"}),
                ToolCall("ls", "Glob", {"pattern": "**/*"}),
                ToolCall("sh", "Bash", {"command": "echo hi"}),
            ),
            env_probe=ToolCall("e", "Bash", {"command": "printenv KADEN_PERMISSION_CEILING"}),
        )


def ctx(tmp_path: Path, call_id: str = "c", confined: bool = False) -> RecordingContext:
    return RecordingContext(call_id, confined=confined)


async def run(tools: ShellFilesTools, tmp_path: Path, name: str, **args):
    return await tools.execute(ToolCall("c", name, args), ctx(tmp_path))


@pytest.fixture
def tools(workspace: Path) -> ShellFilesTools:
    return ShellFilesTools(workspace)


async def test_shell_success_and_exit_code(tools, tmp_path):
    out = await run(tools, tmp_path, "Bash", command="echo out; echo err >&2")
    assert not out.is_error
    assert "out" in out.content and "err" in out.content
    assert out.content.endswith("[exit code 0]")

    out = await run(tools, tmp_path, "Bash", command="echo nope; exit 3")
    assert out.is_error and "nope" in out.content and "[exit code 3]" in out.content


async def test_shell_runs_in_workspace(tools, workspace, tmp_path):
    out = await run(tools, tmp_path, "Bash", command="pwd")
    assert Path(out.content.splitlines()[0]).resolve() == workspace.resolve()


async def test_the_shell_keeps_its_folder_from_one_command_to_the_next(tools, workspace,
                                                                       tmp_path):
    """Within the workspace and the conversation; a folder outside the workspace is not
    kept, and another conversation starts in the workspace."""
    moved = await run(tools, tmp_path, "Bash", command="cd pkg && echo moved")
    assert moved.content == "moved\n\n[exit code 0]"  # as ever: nothing of the bookkeeping
    here = await run(tools, tmp_path, "Bash", command="pwd")
    assert here.content.splitlines()[0] == str((workspace / "pkg").resolve())
    elsewhere = await tools.execute(ToolCall("c", "Bash", {"command": "pwd"}),
                                    RecordingContext("c", conversation_id="another"))
    assert elsewhere.content.splitlines()[0] == str(workspace.resolve())
    await run(tools, tmp_path, "Bash", command="cd /")
    back = await run(tools, tmp_path, "Bash", command="pwd")
    assert back.content.splitlines()[0] == str(workspace.resolve())
    await run(tools, tmp_path, "Bash", command="cd pkg; false")  # it failed, and moved
    assert (await run(tools, tmp_path, "Bash", command="pwd")).content.splitlines()[0] == (
        str((workspace / "pkg").resolve()))


async def test_shell_truncates_long_output(tools, tmp_path):
    out = await run(tools, tmp_path, "Bash", command="seq 1 200000")
    assert len(out.content) < MAX_OUTPUT_CHARS + 200
    assert out.content.startswith("1\n") and "200000" in out.content
    assert "omitted" in out.content


async def test_shell_streams_progress(tools, tmp_path):
    c = ctx(tmp_path)
    await tools.execute(
        ToolCall("c", "Bash", {"command": "echo one; sleep 0.3; echo two"}), c
    )
    assert [text for _, text in c.reports] == ["one", "two"]


async def test_shell_does_not_hang_on_background_child(tools, tmp_path):
    # The child inherits stdout; the tool must still return once the shell exits.
    out = await asyncio.wait_for(
        run(tools, tmp_path, "Bash", command="sleep 5 & echo started"), 3
    )
    assert "started" in out.content
    await run(tools, tmp_path, "Bash", command="pkill -f 'sleep 5' || true")


async def test_read_file_reads_a_pdf_as_the_text_of_its_pages(tools, workspace, tmp_path):
    from tests.pdf import make_pdf

    (workspace / "report.pdf").write_bytes(make_pdf(
        ["Sales rose in March", "Costs fell in April", "Outlook for May"]))
    whole = await run(tools, tmp_path, "Read", file_path="report.pdf")
    assert whole.content.startswith("[PDF, 3 pages]\n[Page 1]\nSales rose in March")
    assert "[Page 3]\nOutlook for May" in whole.content
    assert not whole.attachments  # text is what it costs: the pages come only when asked for
    some = await run(tools, tmp_path, "Read", file_path="report.pdf", pages="2-3")
    assert "[Page 1]" not in some.content and "[Page 2]\nCosts fell in April" in some.content
    one = await run(tools, tmp_path, "Read", file_path="report.pdf", pages="3")
    assert one.content == "[PDF, 3 pages]\n[Page 3]\nOutlook for May"
    for wrong in ("0", "4", "3-2", "two"):
        out = await run(tools, tmp_path, "Read", file_path="report.pdf", pages=wrong)
        assert out.is_error and "pages" in out.content
    (workspace / "broken.pdf").write_bytes(b"%PDF-1.4 not really")
    assert (await run(tools, tmp_path, "Read", file_path="broken.pdf")).is_error


async def test_read_file_shows_a_pdfs_pages_when_asked_to(tools, workspace, tmp_path):
    """For a scan, a chart or a layout: the pages themselves, a few at a time."""
    from tests.pdf import make_pdf

    (workspace / "report.pdf").write_bytes(make_pdf(
        ["Sales rose in March", "Costs fell in April", "Outlook for May"]))
    part = await run(tools, tmp_path, "Read", file_path="report.pdf", pages="2-3", view=True)
    [document] = part.attachments
    assert part.content == "[PDF, 3 pages; pages 2-3 attached]" and document.pages == 2
    assert document.text == "[Page 2]\nCosts fell in April\n\n[Page 3]\nOutlook for May"
    whole = await run(tools, tmp_path, "Read", file_path="report.pdf", view=True)
    assert whole.content == "[PDF, 3 pages]" and whole.attachments[0].pages == 3
    assert whole.attachments[0].data != document.data  # only the pages asked for
    (workspace / "long.pdf").write_bytes(make_pdf([f"Page text {n}" for n in range(1, 46)]))
    first = await run(tools, tmp_path, "Read", file_path="long.pdf", view=True)
    assert first.attachments[0].pages == 20
    assert first.content == '[PDF, 45 pages; pages 1-20 attached; use pages "21-40" to read on]'


async def test_read_file_says_when_a_pdf_has_no_text_to_read(tools, workspace, tmp_path):
    from tests.pdf import make_pdf

    (workspace / "scan.pdf").write_bytes(make_pdf(["", ""]))
    out = await run(tools, tmp_path, "Read", file_path="scan.pdf")
    assert "No text could be read" in out.content and "view" in out.content


async def test_read_file_shows_a_picture(tools, workspace, tmp_path):
    from tests.test_attachments import PNG

    (workspace / "shot.png").write_bytes(PNG)
    out = await run(tools, tmp_path, "Read", file_path="shot.png")
    assert out.content == "[Image shot.png]" and out.attachments[0].media_type == "image/png"
    (workspace / "huge.png").write_bytes(b"\x89PNG" + b"\x00" * 5 * 1024 * 1024)
    huge = await run(tools, tmp_path, "Read", file_path="huge.png")
    assert huge.is_error and "too large" in huge.content


async def test_read_file_offset_and_limit(tools, workspace, tmp_path):
    (workspace / "lines.txt").write_text("".join(f"{i}\n" for i in range(1, 11)))
    out = await run(tools, tmp_path, "Read", file_path="lines.txt", offset=3, limit=2)
    assert out.content.startswith("     3\t3\n     4\t4\n")
    assert "use offset 5 to read on" in out.content


async def test_read_numbers_its_lines_as_cat_n_does(tools, tmp_path):
    out = await run(tools, tmp_path, "Read", file_path="hello.txt")
    assert out.content == "     1\thello\n     2\tworld\n"


async def test_read_file_stops_at_its_limits_without_reading_everything(tools, workspace,
                                                                         tmp_path):
    (workspace / "zero").touch()
    os.truncate(workspace / "zero", 2 ** 40)  # a terabyte without line breaks, all a hole
    out = await asyncio.wait_for(run(tools, tmp_path, "Read", file_path="zero"), 5)
    assert "[truncated at" in out.content


def outside_secret(tmp_path: Path) -> Path:
    secret = tmp_path / "outside" / "id_key"
    secret.parent.mkdir()
    secret.write_text("PRIVATE KEY\n")
    return secret


async def test_search_skips_links_and_special_files(tools, workspace, tmp_path):
    (workspace / "key.txt").symlink_to(outside_secret(tmp_path))
    (workspace / "zero").symlink_to("/dev/zero")
    out = await asyncio.wait_for(run(tools, tmp_path, "Grep", pattern="."), 5)
    assert "PRIVATE KEY" not in out.content and "key.txt" not in out.content


@pytest.mark.parametrize("name, args", [
    ("Read", {}),
    ("Write", {"content": "pwned"}),
    ("Edit", {"old_string": "PRIVATE", "new_string": "pwned"}),
])
async def test_a_path_swapped_for_a_link_after_the_check_is_refused(tools, workspace, tmp_path,
                                                                    name, args):
    secret = outside_secret(tmp_path)
    call = ToolCall("c", name, {"file_path": "notes.txt", **args})
    (workspace / "notes.txt").write_text("PRIVATE notes\n")
    judged = await tools.confined(call)
    assert judged
    (workspace / "notes.txt").unlink()
    (workspace / "notes.txt").symlink_to(secret)  # swapped between the check and the run
    out = await tools.execute(call, ctx(tmp_path, confined=judged))  # as the runtime does
    assert out.is_error and "outside the work folder" in out.content
    assert secret.read_text() == "PRIVATE KEY\n" and "PRIVATE KEY" not in out.content


@pytest.mark.parametrize("name, args", [
    ("Glob", {"path": "sub", "pattern": "**/*"}),
    ("Grep", {"path": "sub", "pattern": "PRIVATE"}),
    ("Write", {"file_path": "sub/new/file.txt", "content": "pwned"}),
])
async def test_a_folder_swapped_for_a_link_after_the_check_is_refused(tools, workspace, tmp_path,
                                                                      name, args):
    """The call was let through for a folder inside the work folder; what stands there
    when it runs leads outside, and nothing outside is read, listed or made."""
    secret = outside_secret(tmp_path)
    call = ToolCall("c", name, args)
    (workspace / "sub").mkdir()
    judged = await tools.confined(call)
    assert judged
    (workspace / "sub").rmdir()
    (workspace / "sub").symlink_to(secret.parent, target_is_directory=True)
    before = sorted(p.name for p in secret.parent.iterdir())
    out = await tools.execute(call, ctx(tmp_path, confined=judged))
    assert out.is_error and "outside the work folder" in out.content
    assert "PRIVATE KEY" not in out.content and secret.name not in out.content
    assert sorted(p.name for p in secret.parent.iterdir()) == before  # nothing was made there


async def test_a_call_that_was_not_confined_may_still_reach_outside(tools, tmp_path):
    secret = outside_secret(tmp_path)  # the user was asked and allowed it
    out = await run(tools, tmp_path, "Read", file_path=str(secret))
    assert out.content == "     1\tPRIVATE KEY\n"


async def test_read_directory_is_error(tools, tmp_path):
    assert (await run(tools, tmp_path, "Read", file_path="pkg")).is_error


async def test_write_creates_parents(tools, workspace, tmp_path):
    out = await run(tools, tmp_path, "Write", file_path="a/b/c.txt", content="x")
    assert not out.is_error
    assert (workspace / "a/b/c.txt").read_text() == "x"


async def test_edit_file(tools, workspace, tmp_path):
    (workspace / "e.txt").write_text("foo bar foo")
    await run(tools, tmp_path, "Read", file_path="e.txt")  # a file is changed as it was read
    out = await run(tools, tmp_path, "Edit", file_path="e.txt", old_string="foo", new_string="x")
    assert out.is_error and "2 times" in out.content
    out = await run(tools, tmp_path, "Edit", file_path="e.txt", old_string="bar", new_string="B")
    assert not out.is_error
    out = await run(
        tools, tmp_path, "Edit",
        file_path="e.txt", old_string="foo", new_string="F", replace_all=True,
    )
    assert not out.is_error
    assert (workspace / "e.txt").read_text() == "F B F"
    out = await run(tools, tmp_path, "Edit", file_path="e.txt", old_string="zzz", new_string="")
    assert out.is_error and "not found" in out.content


async def test_glob_matches_paths_newest_first(tools, workspace, tmp_path):
    os.utime(workspace / "hello.txt", (1, 1))
    out = await run(tools, tmp_path, "Glob", pattern="**/*")
    assert out.content.splitlines() == ["pkg/mod.py", "hello.txt"]
    assert (await run(tools, tmp_path, "Glob", pattern="**/*.py")).content == "pkg/mod.py"
    assert (await run(tools, tmp_path, "Glob", pattern="*.py")).content == "No files found"
    assert (await run(tools, tmp_path, "Glob", pattern="*.txt")).content == "hello.txt"
    both = await run(tools, tmp_path, "Glob", pattern="**/*.{py,txt}")
    assert sorted(both.content.splitlines()) == ["hello.txt", "pkg/mod.py"]
    assert (await run(tools, tmp_path, "Glob", pattern="*.py", path="pkg")).content == "mod.py"


async def test_search_files_skips_hidden(tools, tmp_path):
    out = await run(tools, tmp_path, "Grep", pattern="answer", output_mode="content")
    assert out.content == "pkg/mod.py:1: def answer():"
    assert (await run(tools, tmp_path, "Grep", pattern="answer")).content == "pkg/mod.py"
    out = await run(tools, tmp_path, "Grep", pattern="(")
    assert out.is_error


async def test_listing_and_searching_leave_out_what_git_ignores(tools, workspace, tmp_path):
    """As git sees the project: the .gitignore files from the work folder down to where
    the search starts, and those below it, each for its own folder."""
    (workspace / ".gitignore").write_text("node_modules/\n*.log\n")
    (workspace / "node_modules" / "lib").mkdir(parents=True)
    (workspace / "node_modules" / "lib" / "index.js").write_text("answer = 1\n")
    (workspace / "debug.log").write_text("answer in a log\n")
    (workspace / "pkg" / ".gitignore").write_text("generated.py\n")
    (workspace / "pkg" / "generated.py").write_text("def answer(): pass\n")
    listed = (await run(tools, tmp_path, "Glob", pattern="**/*")).content.splitlines()
    assert sorted(listed) == [".gitignore", "hello.txt", "pkg/.gitignore", "pkg/mod.py"]
    found = await run(tools, tmp_path, "Grep", pattern="answer", output_mode="content")
    assert found.content == "pkg/mod.py:1: def answer():"
    inside = await run(tools, tmp_path, "Grep", pattern="answer", path="pkg",
                       output_mode="content")
    assert inside.content == "mod.py:1: def answer():"  # pkg's own .gitignore holds here


async def test_search_can_ignore_case_show_context_and_list_only_files(tools, workspace,
                                                                        tmp_path):
    (workspace / "notes.md").write_text("one\nTODO: first\nthree\nfour\ntodo: second\n")
    content = {"output_mode": "content"}
    assert (await run(tools, tmp_path, "Grep", pattern="todo", **content)).content == (
        "notes.md:5: todo: second")
    found = await run(tools, tmp_path, "Grep", pattern="todo", **content, **{"-i": True})
    assert found.content == "notes.md:2: TODO: first\nnotes.md:5: todo: second"
    around = await run(tools, tmp_path, "Grep", pattern="first", **content, **{"-C": 1})
    assert around.content == "notes.md-1- one\nnotes.md:2: TODO: first\nnotes.md-3- three"
    after = await run(tools, tmp_path, "Grep", pattern="first", **content, **{"-A": 2})
    assert after.content == "notes.md:2: TODO: first\nnotes.md-3- three\nnotes.md-4- four"
    before = await run(tools, tmp_path, "Grep", pattern="second", **content, **{"-B": 1})
    assert before.content == "notes.md-4- four\nnotes.md:5: todo: second"
    files = await run(tools, tmp_path, "Grep", pattern="o")
    assert files.content == "hello.txt\nnotes.md"
    counts = await run(tools, tmp_path, "Grep", pattern="o", output_mode="count",
                       **{"-i": True})
    assert counts.content == "hello.txt:2\nnotes.md:4"
    first = await run(tools, tmp_path, "Grep", pattern="o", **content, head_limit=2)
    assert first.content == "hello.txt:1: hello\nhello.txt:2: world"


async def test_a_file_is_changed_only_as_it_was_read(tools, workspace, tmp_path):
    """A file that is there is written or edited only once the conversation has read it,
    and only while it is as it was then: a change made meanwhile is not overwritten."""
    target = workspace / "hello.txt"
    unread = await run(tools, tmp_path, "Edit", file_path="hello.txt", old_string="world",
                       new_string="there")
    assert unread.is_error and "Read hello.txt first" in unread.content
    assert (await run(tools, tmp_path, "Write", file_path="hello.txt", content="x")).is_error
    assert target.read_text() == "hello\nworld\n"

    await run(tools, tmp_path, "Read", file_path="hello.txt")
    assert not (await run(tools, tmp_path, "Edit", file_path="hello.txt", old_string="world",
                          new_string="there")).is_error
    assert not (await run(tools, tmp_path, "Edit", file_path="hello.txt", old_string="there",
                          new_string="all")).is_error  # its own change is known
    target.write_text("hello\nchanged by the user\n")
    os.utime(target, ns=(1, 1))  # as a change in the same instant would not be told apart
    stale = await run(tools, tmp_path, "Edit", file_path="hello.txt", old_string="hello",
                      new_string="bye")
    assert stale.is_error and "changed since" in stale.content
    assert target.read_text() == "hello\nchanged by the user\n"

    other = await tools.execute(ToolCall("c", "Edit", {
        "file_path": "hello.txt", "old_string": "hello", "new_string": "bye"}),
        RecordingContext("c", conversation_id="another"))
    assert other.is_error and "Read hello.txt first" in other.content  # each its own
    new = await run(tools, tmp_path, "Write", file_path="fresh.txt", content="new\n")
    assert not new.is_error  # a new file needs nothing read


# ── confined to the work folder ──

SANDBOX = pytest.mark.skipif(sandbox.find() is None,
                             reason="needs a sandbox (bubblewrap, Seatbelt)")


async def confined(tools: ShellFilesTools, name: str, **args) -> bool:
    return await tools.confined(ToolCall("c", name, args))


async def test_file_calls_are_confined_by_path(tools, workspace, tmp_path):
    outside = tmp_path / "outside.txt"
    outside.write_text("x")
    (workspace / "link").symlink_to(outside)
    assert await confined(tools, "Read", file_path="hello.txt")
    assert await confined(tools, "Read", file_path=str(workspace / "hello.txt"))
    assert await confined(tools, "Write", file_path="new/dir/file.txt", content="x")
    assert await confined(tools, "Glob")
    assert await confined(tools, "Grep", pattern="x", path="pkg")
    assert not await confined(tools, "Read", file_path=str(outside))
    assert not await confined(tools, "Read", file_path="../outside.txt")
    assert not await confined(tools, "Read", file_path="link")  # a symlink leading out
    assert not await confined(tools, "Glob", path=str(tmp_path))
    assert not await confined(tools, "Read", file_path=3)
    assert not await confined(tools, "nope")


@SANDBOX
async def test_a_sandboxed_command_cannot_leave_the_workspace(tools, workspace, tmp_path):
    assert await confined(tools, "Bash", command="true")
    assert not await confined(tools, "Bash", command="true", dangerouslyDisableSandbox=True)
    outside = tmp_path / "outside.txt"
    services = "".join(f"test -S '{path}' && echo 'SOCKET {path}'; "
                       for path in sandbox.local_sockets(Path("/run")))
    out = await run(tools, tmp_path, "Bash", command=(
        f"cat hello.txt; echo in > inside.txt; echo out > {outside}; "
        'echo "home:$(ls -A ~ 2>/dev/null | wc -l | tr -d " ")"; '
        "test -e /run/user/$(id -u)/bus && echo BUS; "
        f"test -S /run/dbus/system_bus_socket && echo SYSTEM; {services}true"))
    assert "hello" in out.content and "home:0" in out.content and "BUS" not in out.content
    assert "SYSTEM" not in out.content and "SOCKET" not in out.content
    assert (workspace / "inside.txt").read_text() == "in\n"
    assert not outside.exists()

    out = await run(tools, tmp_path, "Bash", command=f"echo out > {outside}", dangerouslyDisableSandbox=True)
    assert not out.is_error and outside.read_text() == "out\n"


@SANDBOX
async def test_a_file_the_user_attached_is_readable_in_the_sandbox(tools, workspace, tmp_path):
    """Attaching is the user's act, so the sandbox shows that one file, read-only, where it
    is: the agent can copy a picture it was sent. Its neighbours stay hidden."""
    sent, other = tmp_path / "fox.png", tmp_path / "other.png"
    sent.write_bytes(b"\x89PNG fox")
    other.write_bytes(b"\x89PNG other")
    copy = f"cp {sent} kept.png"
    out = await run(tools, tmp_path, "Bash", command=copy)
    assert out.is_error or not (workspace / "kept.png").exists()  # not yet: /tmp is hidden
    await tools.attach(sent)
    out = await run(tools, tmp_path, "Bash", command=(
        f"{copy}; cat {other} || echo HIDDEN; echo x >> {sent} || echo READ-ONLY"))
    assert (workspace / "kept.png").read_bytes() == b"\x89PNG fox"
    assert "HIDDEN" in out.content and "READ-ONLY" in out.content
    assert sent.read_bytes() == b"\x89PNG fox"


async def test_without_a_sandbox_the_shell_is_not_confined(workspace, tmp_path, monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda name: None)
    tools = ShellFilesTools(workspace)
    assert not await confined(tools, "Bash", command="true")
    assert await confined(tools, "Read", file_path="hello.txt")
    outside = tmp_path / "outside.txt"
    out = await run(tools, tmp_path, "Bash", command=f"echo out > {outside}")
    assert not out.is_error and outside.exists()  # runs as before, after the user allowed it


async def test_a_work_folder_that_holds_the_home_folder_confines_nothing(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / "project").mkdir(parents=True)
    (home / "notes.txt").write_text("x")
    monkeypatch.setenv("HOME", str(home))
    for root in (home, tmp_path):  # the home folder itself, and a folder that holds it
        tools = ShellFilesTools(root)
        assert not await confined(tools, "Read", file_path=str(home / "notes.txt"))
        assert not await confined(tools, "Bash", command="true")
    assert await confined(ShellFilesTools(home / "project"), "Glob")


async def test_read_only_flags(tools):
    flags = {spec.name: spec.read_only for spec in await tools.list_tools()}
    flags.pop("screenshot", None)  # offered where Spectacle is installed (test_tools_screen)
    assert all(flags.pop(name, True) for name in bilibili.NAMES)  # Kaden's own: reading
    assert flags == {
        "Bash": False,
        "Read": True,
        "Write": False,
        "Edit": False,
        "Glob": True,
        "Grep": True,
        "NotebookEdit": False,
        "WebSearch": True,
        "WebFetch": True,
    }
    assert [spec.name for spec in await tools.list_tools() if spec.foreground] == ["Bash"]
    stall = {spec.name: spec.stall_timeout for spec in await tools.list_tools()}
    assert stall["Bash"] and all(v is None for k, v in stall.items() if k != "Bash")


async def test_the_tools_take_claude_codes_parameters(tools):
    params = {spec.name: set(spec.parameters["properties"]) for spec in await tools.list_tools()
              if spec.name not in bilibili.NAMES}  # Kaden's own (test_bilibili)
    params.pop("screenshot", None)
    assert params == {
        "Bash": {"command", "timeout", "run_in_background", "dangerouslyDisableSandbox"},
        "Read": {"file_path", "offset", "limit", "pages", "view"},
        "Write": {"file_path", "content"},
        "Edit": {"file_path", "old_string", "new_string", "replace_all"},
        "Glob": {"pattern", "path"},
        "Grep": {"pattern", "path", "glob", "type", "output_mode", "-i", "-A", "-B", "-C",
                 "head_limit", "multiline"},
        "WebSearch": {"query", "max_results", "allowed_domains", "blocked_domains"},
        "WebFetch": {"url", "prompt", "start"},
        "NotebookEdit": {"notebook_path", "cell_id", "new_source", "cell_type", "edit_mode"},
    }


async def test_file_walks_send_heartbeats_from_their_thread(tools, tmp_path):
    for name, args in (("Grep", {"pattern": "answer"}), ("Glob", {"pattern": "**/*"})):
        c = ctx(tmp_path)
        out = await tools.execute(ToolCall("c", name, args), c)
        assert not out.is_error
        assert c.heartbeats >= 1 and c.foreign_thread_calls >= 1 and c.reports == []


async def test_timeouts_are_configurable(workspace):
    import math

    specs = {s.name: s for s in await ShellFilesTools(workspace).list_tools()}
    assert specs["Bash"].timeout is None and specs["Bash"].stall_timeout == 600
    tools = ShellFilesTools(workspace, shell_timeout=math.inf, shell_stall_timeout=None)
    shell = {s.name: s for s in await tools.list_tools()}["Bash"]
    assert shell.timeout == math.inf and shell.stall_timeout is None


async def test_factory(workspace, tmp_path, monkeypatch):
    tools = factory({"root": str(workspace), "shell_timeout": 60}, InstancePaths(tmp_path))
    assert isinstance(tools, ShellFilesTools) and tools.root == workspace.resolve()
    monkeypatch.chdir(workspace / "pkg")
    assert factory({}, InstancePaths(tmp_path)).root == (workspace / "pkg").resolve()


async def test_use_folder_moves_every_tool_to_that_folder(tools, workspace, tmp_path):
    other = tmp_path / "other" / "project"
    await tools.use_folder(other)  # created when missing
    assert other.is_dir() and tools.root == other.resolve()
    await run(tools, tmp_path, "Write", file_path="notes.txt", content="here")
    assert (other / "notes.txt").read_text() == "here"
    assert await confined(tools, "Read", file_path="notes.txt")
    assert not await confined(tools, "Read", file_path=str(workspace / "hello.txt"))
    out = await run(tools, tmp_path, "Bash", command="pwd")
    assert out.content.startswith(str(other.resolve()))
    await tools.use_folder(None)  # back to the folder the module was built with
    assert tools.root == workspace.resolve()
    assert (await run(tools, tmp_path, "Read", file_path="hello.txt")).content == (
        "     1\thello\n     2\tworld\n")


@SANDBOX
async def test_the_sandbox_follows_the_folder(tools, workspace, tmp_path):
    other = tmp_path / "other"
    await tools.use_folder(other)
    assert await confined(tools, "Bash", command="true")
    out = await run(tools, tmp_path, "Bash", command=(
        f"echo in > inside.txt; echo out > {workspace / 'escaped.txt'}; true"))
    assert not out.is_error
    assert (other / "inside.txt").exists() and not (workspace / "escaped.txt").exists()


async def test_use_folder_on_a_folder_that_holds_home_confines_nothing(tools, tmp_path,
                                                                       monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    await tools.use_folder(home)
    assert not await confined(tools, "Read", file_path="x.txt")
    assert not await confined(tools, "Bash", command="true")


async def test_use_folder_fails_for_a_path_that_cannot_be_a_folder(tools, workspace):
    with pytest.raises(OSError):
        await tools.use_folder(workspace / "hello.txt" / "sub")
    assert tools.root == workspace.resolve()


def test_secret_variables_are_left_out_of_the_sandbox():
    from kaden.tools.shell_files import sandbox_environment

    env = sandbox_environment({
        "ANTHROPIC_API_KEY": "k", "DEEPSEEK_API_KEY": "k", "GITHUB_TOKEN": "t",
        "AWS_SECRET_ACCESS_KEY": "s", "DB_PASSWORD": "p", "MY_CREDENTIALS": "c",
        "PATH": "/usr/bin", "HOME": "/home/u", "LANG": "C.UTF-8",
        "KADEN_PERMISSION_CEILING": "auto", "ANTHROPIC_BASE_URL": "https://x",
    })
    assert env == {"PATH": "/usr/bin", "HOME": "/home/u", "LANG": "C.UTF-8",
                   "KADEN_PERMISSION_CEILING": "auto", "ANTHROPIC_BASE_URL": "https://x"}


@SANDBOX
async def test_a_sandboxed_command_cannot_read_keys_from_the_environment(
        tools, tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-secret")
    monkeypatch.setenv("KADEN_PERMISSION_CEILING", "auto")
    probe = 'echo "key=${ANTHROPIC_API_KEY:-none} ceiling=$KADEN_PERMISSION_CEILING"'
    out = await run(tools, tmp_path, "Bash", command=probe)
    assert "key=none ceiling=auto" in out.content
    out = await run(tools, tmp_path, "Bash", command=probe, dangerouslyDisableSandbox=True)
    assert "key=sk-secret" in out.content  # asked for first, so it runs as the user would


async def test_no_tool_declares_its_own_description(tools):
    """The runtime adds the description to every tool (api.DESCRIPTION_ARG)."""
    from kaden.api import DESCRIPTION_ARG

    for spec in await tools.list_tools():
        assert DESCRIPTION_ARG not in spec.parameters["properties"], spec.name


async def test_judging_a_call_leaves_nothing_behind(tools, workspace):
    """The verdict travels with the call (ToolContext.confined): one that never runs (say,
    denied) is not remembered anywhere."""
    before = dict(vars(tools))
    assert await tools.confined(ToolCall("c", "Read", {"file_path": "notes.txt"}))
    assert vars(tools) == before


async def test_grep_can_match_across_lines_and_by_file_type(tools, workspace, tmp_path):
    (workspace / "notes.md").write_text("def answer():\n    return 42\n")
    one_line = await run(tools, tmp_path, "Grep", pattern=r"answer\(\):\s+return")
    assert one_line.content == "No matches"
    across = await run(tools, tmp_path, "Grep", pattern=r"answer\(\):\s+return",
                       multiline=True, output_mode="content")
    assert across.content.startswith("notes.md:1: def answer():\nnotes.md:2:     return 42")
    typed = await run(tools, tmp_path, "Grep", pattern="answer", type="py")
    assert typed.content == "pkg/mod.py"
    assert (await run(tools, tmp_path, "Grep", pattern="answer", type="nope")).is_error


async def test_a_command_past_its_timeout_returns_what_it_wrote(tools, tmp_path):
    out = await asyncio.wait_for(run(tools, tmp_path, "Bash", command="echo started; sleep 30",
                                     timeout=300), 10)
    assert out.is_error and "started" in out.content and "timed out" in out.content


def notebook(path: Path) -> None:
    path.write_text(json.dumps({
        "cells": [
            {"cell_type": "markdown", "id": "intro", "metadata": {}, "source": ["# Sales\n"]},
            {"cell_type": "code", "id": "load", "metadata": {}, "execution_count": 1,
             "source": ["import pandas\n", "df = pandas.read_csv('s.csv')"],
             "outputs": [{"output_type": "stream", "name": "stdout", "text": ["ok\n"]}]}],
        "metadata": {}, "nbformat": 4, "nbformat_minor": 5}))


async def test_read_shows_a_notebook_as_its_cells(tools, workspace, tmp_path):
    notebook(workspace / "sales.ipynb")
    out = await run(tools, tmp_path, "Read", file_path="sales.ipynb")
    assert out.content == (
        '<cell id="intro">\n<cell_type>markdown</cell_type>\n# Sales\n</cell id="intro">\n'
        '<cell id="load">\nimport pandas\ndf = pandas.read_csv(\'s.csv\')\n'
        '[output] ok\n</cell id="load">\n')


async def test_notebook_edit_replaces_inserts_and_deletes_cells(tools, workspace, tmp_path):
    path = workspace / "sales.ipynb"
    notebook(path)
    unread = await run(tools, tmp_path, "NotebookEdit", notebook_path="sales.ipynb",
                       cell_id="load", new_source="x = 1")
    assert unread.is_error and "read" in unread.content
    await run(tools, tmp_path, "Read", file_path="sales.ipynb")
    out = await run(tools, tmp_path, "NotebookEdit", notebook_path="sales.ipynb",
                    cell_id="load", new_source="x = 1")
    assert not out.is_error, out.content
    cells = json.loads(path.read_text())["cells"]
    assert cells[1]["source"] == "x = 1" and cells[1]["outputs"] == []
    assert cells[1]["execution_count"] is None
    await run(tools, tmp_path, "NotebookEdit", notebook_path="sales.ipynb", cell_id="intro",
              new_source="Notes", cell_type="markdown", edit_mode="insert")
    cells = json.loads(path.read_text())["cells"]
    assert [c["cell_type"] for c in cells] == ["markdown", "markdown", "code"]
    assert cells[1]["source"] == "Notes" and cells[1]["id"] not in ("intro", "load")
    await run(tools, tmp_path, "NotebookEdit", notebook_path="sales.ipynb", cell_id="intro",
              new_source="", edit_mode="delete")
    assert [c.get("id") for c in json.loads(path.read_text())["cells"]][1:] == ["load"]
    missing = await run(tools, tmp_path, "NotebookEdit", notebook_path="sales.ipynb",
                        cell_id="gone", new_source="y")
    assert missing.is_error and "gone" in missing.content
    no_type = await run(tools, tmp_path, "NotebookEdit", notebook_path="sales.ipynb",
                        new_source="y", edit_mode="insert")
    assert no_type.is_error and "cell_type" in no_type.content


async def test_a_long_notebook_is_read_by_cells(tools, workspace, tmp_path, monkeypatch):
    from kaden.tools import shell_files

    monkeypatch.setattr(shell_files, "MAX_READ_CHARS", 120)
    path = workspace / "long.ipynb"
    path.write_text(json.dumps({"cells": [
        {"cell_type": "code", "id": f"c{n}", "metadata": {}, "source": f"x = {n}  # " + "y" * 30,
         "outputs": [], "execution_count": None} for n in range(1, 6)],
        "metadata": {}, "nbformat": 4, "nbformat_minor": 5}))
    first = await run(tools, tmp_path, "Read", file_path="long.ipynb")
    assert first.content.startswith('<cell id="c1">') and '<cell id="c5">' not in first.content
    assert "use offset" in first.content
    later = await run(tools, tmp_path, "Read", file_path="long.ipynb", offset=4, limit=1)
    assert later.content.startswith('<cell id="c4">') and '<cell id="c5">' not in later.content
    assert "use offset 5" in later.content


async def test_glob_keeps_the_newest_when_there_are_too_many(tools, workspace, tmp_path,
                                                             monkeypatch):
    from kaden.tools import shell_files

    monkeypatch.setattr(shell_files, "MAX_LIST_ENTRIES", 2)
    for n, name in enumerate(["a.md", "b.md", "c.md", "z.md"]):
        (workspace / name).write_text(name)
        os.utime(workspace / name, (100 + n, 100 + n))
    out = await run(tools, tmp_path, "Glob", pattern="*.md")
    assert out.content.splitlines()[:2] == ["z.md", "c.md"]
    assert "truncated" in out.content


# ── on any system ──

def without_proc(monkeypatch) -> None:
    """As on macOS, which has no /proc."""
    real = os.readlink

    def readlink(path, *args, **kwargs):
        if str(path).startswith("/proc/"):
            raise FileNotFoundError(path)
        return real(path, *args, **kwargs)

    monkeypatch.setattr(os, "readlink", readlink)


async def test_confined_file_calls_work_without_proc(tools, workspace, tmp_path, monkeypatch):
    without_proc(monkeypatch)
    notebook(workspace / "sales.ipynb")

    async def confined_run(name: str, **args) -> str:
        call = ToolCall("c", name, args)
        assert await tools.confined(call)
        out = await tools.execute(call, ctx(tmp_path, confined=True))
        assert not out.is_error, out.content
        return out.content

    assert "hello" in await confined_run("Read", file_path="hello.txt")
    await confined_run("Write", file_path="new/note.txt", content="one\n")
    await confined_run("Edit", file_path="hello.txt", old_string="world", new_string="there")
    await confined_run("Read", file_path="sales.ipynb")
    await confined_run("NotebookEdit", notebook_path="sales.ipynb", cell_id="load",
                       new_source="x = 1")
    assert "pkg/mod.py" in await confined_run("Glob", pattern="**/*.py")
    assert "pkg/mod.py" in await confined_run("Grep", pattern="answer")
    assert (workspace / "new/note.txt").read_text() == "one\n"
    assert (workspace / "hello.txt").read_text() == "hello\nthere\n"


@pytest.mark.parametrize("confined_call", [False, True])
async def test_a_pipe_or_device_is_refused_not_waited_on(tools, workspace, tmp_path,
                                                         confined_call):
    pipe = workspace / "pipe"
    os.mkfifo(pipe)  # nothing ever writes to it
    try:
        for path in ("pipe",) if confined_call else ("pipe", "/dev/zero"):
            out = await asyncio.wait_for(tools.execute(ToolCall(
                "c", "Read", {"file_path": path}), ctx(tmp_path, confined=confined_call)), 5)
            assert out.is_error and "not a regular file" in out.content
    finally:  # a reader left waiting for a writer is let go
        with contextlib.suppress(OSError):
            os.close(os.open(pipe, os.O_WRONLY | os.O_NONBLOCK))


async def test_an_edit_keeps_windows_line_endings(tools, workspace, tmp_path):
    path = workspace / "crlf.txt"
    path.write_bytes(b"one\r\ntwo\r\nthree\r\n")
    assert (await run(tools, tmp_path, "Read", file_path="crlf.txt")).content == (
        "     1\tone\n     2\ttwo\n     3\tthree\n")
    out = await run(tools, tmp_path, "Edit", file_path="crlf.txt", old_string="one\ntwo",
                    new_string="1\n2")
    assert not out.is_error, out.content
    assert path.read_bytes() == b"1\r\n2\r\nthree\r\n"
    out = await run(tools, tmp_path, "Edit", file_path="crlf.txt", old_string="2\r\nthree",
                    new_string="3")
    assert not out.is_error, out.content
    assert path.read_bytes() == b"1\r\n3\r\n"


async def test_an_edit_leaves_mixed_line_endings_as_they_are(tools, workspace, tmp_path):
    path = workspace / "mixed.txt"
    path.write_bytes(b"a\r\nb\nc\r\n")
    await run(tools, tmp_path, "Read", file_path="mixed.txt")
    out = await run(tools, tmp_path, "Edit", file_path="mixed.txt", old_string="b",
                    new_string="B")
    assert not out.is_error, out.content
    assert path.read_bytes() == b"a\r\nB\nc\r\n"


async def test_a_notebook_edit_keeps_windows_line_endings(tools, workspace, tmp_path):
    path = workspace / "sales.ipynb"
    notebook(path)
    path.write_bytes(json.dumps(json.loads(path.read_text()), indent=1).replace(
        "\n", "\r\n").encode())
    await run(tools, tmp_path, "Read", file_path="sales.ipynb")
    out = await run(tools, tmp_path, "NotebookEdit", notebook_path="sales.ipynb",
                    cell_id="load", new_source="x = 1")
    assert not out.is_error, out.content
    raw = path.read_bytes()
    assert raw.count(b"\n") == raw.count(b"\r\n") > 1
    assert json.loads(raw)["cells"][1]["source"] == "x = 1"


async def test_a_character_split_between_reads_of_the_output_is_kept_whole():
    from kaden.tools.shell_files import _OutputBuffer, _pump

    stream, out = asyncio.StreamReader(), _OutputBuffer(1000)
    pumping = asyncio.create_task(_pump(stream, out, RecordingContext("c")))
    encoded = "naïve 好".encode()
    for byte in encoded:
        stream.feed_data(bytes([byte]))
        await asyncio.sleep(0)
    stream.feed_eof()
    await pumping
    assert out.text() == "naïve 好"
