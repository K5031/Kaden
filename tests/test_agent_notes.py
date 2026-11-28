"""Agent Notes keep the format AGENTS.md gives them: the header, the status their folder
says, the sections of that folder, and links that resolve."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
NOTES = ROOT / ".agents" / "notes"
SECTIONS = {
    "proposed": ("Problem", "Proposal", "Alternatives considered", "Acceptance criteria",
                 "Risks"),
    "implemented": ("Problem", "Decision", "Alternatives considered", "Consequences"),
    "rejected": ("Problem", "Alternatives considered"),
}
NOT_IN_IMPLEMENTED = ("Proposal", "Acceptance criteria", "Risks", "Plan",
                      "Implementation plan")
LINK = re.compile(r"\[[^\]]*\]\(([^)\s]+)\)")


def notes() -> list[Path]:
    return sorted(NOTES.rglob("*.md"))


def headings(text: str) -> list[str]:
    return [line[3:].strip() for line in text.splitlines() if line.startswith("## ")]


@pytest.mark.parametrize("note", notes(), ids=lambda p: f"{p.parent.name}/{p.name}")
def test_a_note_has_its_folders_header_and_sections(note: Path) -> None:
    folder, text = note.parent.name, note.read_text()
    assert note.parent.parent == NOTES and folder in SECTIONS, "not in a status folder"
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}-[a-z0-9-]+\.md", note.name)
    lines = text.splitlines()
    assert lines[0].startswith("# Agent Note: ")
    assert lines[2].startswith(f"Status: {folder} — ")
    found = headings(text)
    assert [h for h in found if h in SECTIONS[folder]] == list(SECTIONS[folder])
    if folder == "implemented":
        assert not set(found) & set(NOT_IN_IMPLEMENTED)


def documents() -> list[Path]:
    return sorted([*(ROOT / ".agents").rglob("*.md"), *(ROOT / "docs").rglob("*.md"),
                   ROOT / "AGENTS.md", ROOT / "SPEC.md", ROOT / "README.md"])


@pytest.mark.parametrize("document", documents(), ids=lambda p: str(p.relative_to(ROOT)))
def test_every_relative_link_resolves(document: Path) -> None:
    # Examples in code: blocks first, or a fence would read as an empty span.
    text = re.sub(r"```.*?```", "", document.read_text(), flags=re.DOTALL)
    text = re.sub(r"`[^`\n]*`", "", text)
    broken = []
    for target in LINK.findall(text):
        path = target.split("#")[0]
        if not path or re.match(r"[a-z]+:", path):
            continue
        if not (document.parent / path).exists():
            broken.append(target)
    assert not broken
