"""The window launcher, which `kaden` hands over to and the desktop bundle runs.

It reads config.toml and settings.toml like `kaden`, and runs the saved frontend when it
is a window frontend (else the desktop app), each on the event loop its factory names. The
Tools start in the folder it is started in, or the saved work folder; each chat is then
given a folder of its own. Restart now starts the instance again in this process, or on
another loop when the next frontend needs one. Nothing here assumes a GUI toolkit.
"""

from __future__ import annotations

import argparse
import os
from collections.abc import Sequence
from pathlib import Path

from kaden.__main__ import DEFAULT_DATA_DIR, Launch, run_frontends
from kaden.api import PermissionPolicy
from kaden.runtime.modules import WINDOW


def parse_args(argv: Sequence[str] | None = None) -> Launch:
    parser = argparse.ArgumentParser(prog="kaden-desktop", description=__doc__)
    parser.add_argument("--data-dir", type=Path,
                        default=Path(os.environ.get("KADEN_DATA_DIR", DEFAULT_DATA_DIR)),
                        help="instance data directory (default: $KADEN_DATA_DIR or ~/.kaden)")
    parser.add_argument("--config", type=Path, help="config file (default: <data dir>/config.toml)")
    parser.add_argument("--conversation", help="open this conversation first")
    parser.add_argument("--model", help="model id (default: the first the Core lists)")
    parser.add_argument("--maintenance-model", help="cheaper model for memory maintenance")
    parser.add_argument("--permissions", choices=[p.value for p in PermissionPolicy],
                        help="tool permission policy (default: auto)")
    return Launch(**vars(parser.parse_args(argv)))


def main(argv: Sequence[str] | None = None) -> None:
    run_frontends(parse_args(argv), WINDOW)


if __name__ == "__main__":
    main()
