"""Which launcher the bundle's program runs, by the name it was started as."""

from __future__ import annotations

from collections.abc import Callable

COMMAND_NAMES = ("kadence", "kd")  # the commands the package links to the program


def launcher_for(name: str) -> Callable[[], None]:
    if name in COMMAND_NAMES:
        from kadence.__main__ import main
    else:
        from kadence.window import main
    return main
