"""Briefcase app module: the bundle runs `python -m kaden_desktop`.

The package's one program starts the window launcher, or, run as `kaden` or `kd` (links
the package adds), the command's launcher, which opens the app or runs headless."""

import sys
from pathlib import Path

from kaden_desktop.commands import launcher_for

launcher_for(Path(sys.argv[0]).name)()
