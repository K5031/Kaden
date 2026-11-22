"""The commands an install puts on the PATH."""

from __future__ import annotations

import tomllib
from pathlib import Path


def test_kd_is_a_short_name_for_kaden():
    project = tomllib.loads((Path(__file__).parents[1] / "pyproject.toml").read_text())
    scripts = project["project"]["scripts"]
    assert scripts["kd"] == scripts["kaden"] == "kaden.__main__:main"


def test_the_bundle_starts_the_terminal_app_as_kaden_or_kd():
    import kaden.__main__
    import kaden.window
    from kaden_desktop.commands import launcher_for

    assert launcher_for("kaden") is kaden.__main__.main
    assert launcher_for("kd") is kaden.__main__.main
    assert launcher_for("kaden-desktop") is kaden.window.main
