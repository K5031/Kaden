"""A project's instruction files, as Claude Code reads them: CLAUDE.md, .claude/CLAUDE.md
and CLAUDE.local.md in the work folder and every folder above it, AGENTS.md only where
there is none of those, @imports, and a size limit."""

from __future__ import annotations

from kaden.runtime import instructions


def found_in(folder: object) -> list[instructions.File]:
    return instructions.read(folder)[0]  # type: ignore[arg-type]


def test_the_files_of_the_folder_and_those_above_it_nearest_last(tmp_path):
    project = tmp_path / "repo"
    work = project / "app"
    (work / ".claude").mkdir(parents=True)
    (tmp_path / "CLAUDE.md").write_text("outer")
    (project / "CLAUDE.md").write_text("project")
    (work / "CLAUDE.local.md").write_text("mine")
    (work / ".claude" / "CLAUDE.md").write_text("shared")
    (work / "CLAUDE.md").write_text("app")
    (work / "AGENTS.md").write_text("for other agents")  # CLAUDE.md is there: not read
    found = found_in(work)
    assert [f.text for f in found] == ["outer", "project", "app", "shared", "mine"]


def test_agents_md_where_there_is_no_claude_md(tmp_path):
    (tmp_path / "AGENTS.md").write_text("rules")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "AGENTS.md").write_text("more")
    assert [f.text for f in found_in(tmp_path / "sub")] == ["rules", "more"]
    (tmp_path / "CLAUDE.md").mkdir()  # a folder of that name is not a file
    assert [f.text for f in found_in(tmp_path / "sub")] == ["rules", "more"]


def test_imports_from_the_importing_file_four_hops_once_each(tmp_path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "CLAUDE.md").write_text("Read @AGENTS.md and @docs/a.md, not `@code.md`.\n"
                                        "```\n@fenced.md\n```\n")
    (tmp_path / "AGENTS.md").write_text("agents")
    (tmp_path / "docs" / "a.md").write_text("a @b.md")
    (tmp_path / "docs" / "b.md").write_text("b @c.md")
    (tmp_path / "docs" / "c.md").write_text("c @d.md")
    (tmp_path / "docs" / "d.md").write_text("d @e.md")
    (tmp_path / "docs" / "e.md").write_text("e: five hops away")
    (tmp_path / "code.md").write_text("in a code span")
    (tmp_path / "fenced.md").write_text("in a block")
    texts = [f.text for f in found_in(tmp_path)]
    # Each import before the file that imports it, as Claude Code expands them.
    assert texts.index("agents") < texts.index(texts[-1]) and texts[-1].startswith("Read @")
    assert "a @b.md" in texts and "d @e.md" in texts
    assert "e: five hops away" not in texts  # past four hops
    assert "in a code span" not in texts and "in a block" not in texts
    (tmp_path / "docs" / "b.md").write_text("b @a.md")  # a cycle
    assert [f.text for f in found_in(tmp_path)].count("a @b.md") == 1


def test_a_long_file_gives_its_start_and_says_where_the_rest_is(tmp_path):
    (tmp_path / "CLAUDE.md").write_text("x" * (instructions.MAX_CHARS + 500))
    [found] = found_in(tmp_path)
    assert found.text.startswith("x" * 100) and len(found.text) < instructions.MAX_CHARS + 200
    assert str(tmp_path / "CLAUDE.md") in found.text  # read the rest there


def test_the_prompt_section_names_each_file(tmp_path):
    (tmp_path / "CLAUDE.md").write_text("Use tabs.")
    section = instructions.prompt(found_in(tmp_path))
    assert section.startswith("## Project instructions")
    assert str(tmp_path / "CLAUDE.md") in section and "Use tabs." in section
    assert instructions.prompt([]) == ""


def test_no_folder_no_instructions():
    assert instructions.read(None) == ([], [])


def test_agents_md_in_claude_too(tmp_path):
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude" / "AGENTS.md").write_text("hidden rules")
    assert [f.text for f in found_in(tmp_path)] == ["hidden rules"]


def test_an_import_is_read_only_from_the_instruction_files_own_folder(tmp_path):
    """A cloned repository must not bring the user's keys or environment into every
    request: no import from outside the folder its file is in, nor from /proc."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (tmp_path / "secret.txt").write_text("API_KEY=sk-123")
    (repo / "CLAUDE.md").write_text("@../secret.txt @/proc/self/environ @~/.ssh/id_rsa")
    found, skipped = instructions.read(repo)
    assert [f.text for f in found] == ["@../secret.txt @/proc/self/environ @~/.ssh/id_rsa"]
    assert any("secret.txt" in s and "outside" in s for s in skipped)
    assert not any("sk-123" in f.text for f in found)


def test_a_bad_import_is_skipped_and_said_never_raised(tmp_path):
    (tmp_path / "logo.png").write_bytes(b"\x89PNG\x00@\x00\x00 binary")
    (tmp_path / "CLAUDE.md").write_text("@logo.png @~nosuchuser_kaden/x")
    found, skipped = instructions.read(tmp_path)
    assert [f.path.name for f in found] == ["CLAUDE.md"]  # the rest still read
    assert any("logo.png" in s and "not text" in s for s in skipped)  # no binary junk
    assert any("nosuchuser" in s for s in skipped)


def test_a_quoted_path_is_not_an_import(tmp_path):
    (tmp_path / "a.md").write_text("imported")
    (tmp_path / "CLAUDE.md").write_text('Say "@a.md" or \'@a.md\' aloud.')
    assert [f.text for f in found_in(tmp_path)] == ['Say "@a.md" or \'@a.md\' aloud.']


def test_the_cut_notice_is_not_read_for_imports(tmp_path):
    folder = tmp_path / "a b"  # the notice's path: "…/a b/CLAUDE.md", " b/…" is no import
    (folder / "@b").mkdir(parents=True)
    (tmp_path / "x").mkdir()
    (folder / "CLAUDE.md").write_text("x" * (instructions.MAX_CHARS + 10) + " @trap.md")
    (folder / "trap.md").write_text("past the cut")
    [found] = found_in(folder)
    assert found.text.endswith(f"the rest is in {folder / 'CLAUDE.md'}.]")  # trap not read


def test_an_instruction_file_that_links_outside_is_not_read(tmp_path):
    """The file itself may be a link: where it leads is checked as an import is."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (tmp_path / "id_rsa").write_text("PRIVATE KEY")
    (repo / "CLAUDE.md").symlink_to(tmp_path / "id_rsa")
    (repo / "CLAUDE.local.md").symlink_to("/proc/self/environ")
    found, skipped = instructions.read(repo)
    assert found == [] and len(skipped) == 2
    inside = repo / "rules.md"
    inside.write_text("in the project")
    (repo / "CLAUDE.md").unlink()
    (repo / "CLAUDE.md").symlink_to(inside)  # a link within the project is fine
    assert [f.text for f in instructions.read(repo)[0]] == ["in the project"]


def test_imports_come_depth_first_before_their_file(tmp_path):
    (tmp_path / "CLAUDE.md").write_text("top @a.md")
    (tmp_path / "a.md").write_text("a @b.md")
    (tmp_path / "b.md").write_text("b")
    assert [f.text for f in found_in(tmp_path)] == ["b", "a @b.md", "top @a.md"]


def test_only_an_at_after_a_space_or_a_lines_start_is_an_import(tmp_path):
    (tmp_path / "types.md").write_text("imported")
    (tmp_path / "CLAUDE.md").write_text("see node_modules/@types.md and (@types.md)")
    assert [f.text for f in found_in(tmp_path)] == [
        "see node_modules/@types.md and (@types.md)"]


def test_all_of_them_together_are_cut_at_a_total(tmp_path):
    """A repository importing many files cannot make every request huge."""
    names = [f"part{i}.md" for i in range(5)]
    for name in names:
        (tmp_path / name).write_text("y" * 30_000)
    (tmp_path / "CLAUDE.md").write_text(" ".join(f"@{n}" for n in names))
    found, skipped = instructions.read(tmp_path)
    total = sum(len(f.text.split("\n\n[Cut at")[0]) for f in found)
    assert total <= instructions.TOTAL_CHARS
    assert any("characters for all of them" in s for s in skipped)


def test_an_instruction_file_of_another_user_is_not_read(tmp_path, monkeypatch):
    """A shared folder above the work (/tmp) may hold one another user put there."""
    import os

    (tmp_path / "CLAUDE.md").write_text("planted")
    monkeypatch.setattr(os, "getuid", lambda: 12345)  # as if the file were someone else's
    found, skipped = instructions.read(tmp_path)
    assert found == [] and any("another user's" in s for s in skipped)


def test_an_absolute_import_is_refused_before_the_filesystem_is_asked(tmp_path, monkeypatch):
    """@/net/host/x would make an automounter reach that host just to look."""
    from pathlib import Path

    asked: list[str] = []
    real = Path.resolve

    def resolve(self: Path, strict: bool = False) -> Path:
        asked.append(str(self))
        return real(self, strict)

    (tmp_path / "CLAUDE.md").write_text("@/net/attacker.example/x")
    monkeypatch.setattr(Path, "resolve", resolve)
    _, skipped = instructions.read(tmp_path)
    assert not any("attacker" in a for a in asked) and any("attacker" in s for s in skipped)


def test_the_environment_says_the_folder_the_repository_and_the_platform(tmp_path):
    """As Claude Code's environment block: where the work is, whether git tracks it and on
    which branch, and on what system."""
    import platform

    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    (repo / ".git" / "HEAD").write_text("ref: refs/heads/feature/x\n")
    work = repo / "src"
    work.mkdir()
    section = instructions.environment(work)
    assert section.startswith("## Environment")
    assert f"Work folder: {work}" in section
    assert "Git repository: yes, on branch feature/x" in section
    assert platform.system() in section
    (repo / ".git" / "HEAD").write_text("0123456789abcdef0123456789abcdef01234567\n")
    assert "detached at 0123456" in instructions.environment(work)
    plain = tmp_path / "plain"
    plain.mkdir()
    assert "Git repository: no" in instructions.environment(plain)
    assert instructions.environment(None) == ""


def test_a_worktree_finds_its_branch_through_its_git_file(tmp_path):
    gitdir = tmp_path / "main" / ".git" / "worktrees" / "w"
    gitdir.mkdir(parents=True)
    (gitdir / "HEAD").write_text("ref: refs/heads/claude/fix\n")
    tree = tmp_path / "w"
    tree.mkdir()
    (tree / ".git").write_text(f"gitdir: {gitdir}\n")
    assert "on branch claude/fix" in instructions.environment(tree)


def test_a_submodules_relative_gitdir_and_odd_heads(tmp_path):
    modules = tmp_path / "parent" / ".git" / "modules" / "lib"
    modules.mkdir(parents=True)
    (modules / "HEAD").write_text("ref: refs/heads/main\n")
    lib = tmp_path / "parent" / "lib"
    lib.mkdir()
    (lib / ".git").write_text("gitdir: ../.git/modules/lib\n")
    assert "on branch main" in instructions.environment(lib)
    (modules / "HEAD").write_text("ref: refs/heads/.invalid\n")  # a reftable repository
    assert "Git repository: yes\n" in instructions.environment(lib) + "\n"
    (lib / ".git").write_text("junk")  # git refuses it; no parent's branch either
    assert "Git repository: yes\n" in instructions.environment(lib) + "\n"
