"""The sandbox a shell command runs in without asking: bubblewrap on Linux, Seatbelt
(`sandbox-exec`) on macOS, behind one interface. What it lets a command reach is the
table in .agents/notes/implemented/2026-11-26-a-sandbox-on-each-system.md: the work
folder (read and write), its own temporary folder, the files the user attached (read),
the system (read) and the network; not the home folders, /tmp, the login session's
services (the keychain among them) or the local services' sockets."""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
import sys
import tempfile
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Protocol

CHECK_TIMEOUT = 5.0  # seconds for the one test run of the sandbox at startup


class Sandbox(Protocol):
    def wrap(self, command: Sequence[str], *, root: Path, tmp: Path,
             granted: Sequence[Path]) -> list[str]:
        """`command` as it runs confined, started in a folder inside `root`: it reads and
        writes `root` and `tmp` (an empty folder made for it; its TMPDIR), reads `granted`
        and the system, and reaches nothing else of the user's. Paths are resolved.
        Blocking: it may look at the file system."""
        ...


def find() -> Sandbox | None:
    """This system's sandbox, if it works here: tried once by running `true` in it, so a
    missing program, user namespaces turned off, or Kaden running in a sandbox already
    give None. Blocking: run it where module factories run, off the event loop."""
    home = Path.home().resolve()
    candidate: Sandbox | None = None
    if sys.platform == "darwin" and (program := shutil.which("sandbox-exec")):
        candidate = Seatbelt(program, home)
    elif sys.platform.startswith("linux") and (program := shutil.which("bwrap")):
        candidate = Bubblewrap(program, home)
    return candidate if candidate is not None and _works(candidate) else None


def _works(candidate: Sandbox) -> bool:
    with tempfile.TemporaryDirectory(prefix="kaden-sandbox-check-") as folder:
        root, tmp = Path(folder).resolve() / "root", Path(folder).resolve() / "tmp"
        root.mkdir()
        tmp.mkdir()
        try:
            return subprocess.run(candidate.wrap(["true"], root=root, tmp=tmp, granted=()),
                                  cwd=root, check=False, capture_output=True,
                                  timeout=CHECK_TIMEOUT).returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            return False


# ── Linux ─────────────────────────────────────────────────────────────

class Bubblewrap:
    """The whole system visible read-only; home folders, /tmp and the login session's
    runtime folder replaced by empty ones; every socket file under /run covered by
    /dev/null, so connect() is refused; the work folder and tmp bound back, writable."""

    def __init__(self, program: str, home: Path, *, run: Path = Path("/run")) -> None:
        self._program, self._home, self._run = program, home, run

    def wrap(self, command: Sequence[str], *, root: Path, tmp: Path,
             granted: Sequence[Path]) -> list[str]:
        hidden = [p for p in (Path("/home"), Path("/root"), self._run / "user", Path("/tmp"),
                              self._home) if p.is_dir()]
        args = [self._program, "--ro-bind", "/", "/"]
        for path in hidden:
            args += ["--tmpfs", str(path)]
        for path in local_sockets(self._run):
            args += ["--ro-bind", "/dev/null", str(path)]
        args += ["--dir", str(self._home), "--bind", str(root), str(root),
                 "--bind", str(tmp), str(tmp)]
        for path in granted:  # the work folder's own are there already
            if not path.is_relative_to(root):
                args += ["--ro-bind-try", str(path), str(path)]
        return [*args, "--dev", "/dev", "--proc", "/proc", "--unshare-pid",
                "--die-with-parent", *command]


def local_sockets(run: Path) -> list[Path]:
    """The socket files under `run` (/run, where local services keep theirs), on its own
    file system (a disk mounted below is not walked), leaving out `run`/user, which the
    sandbox hides whole, and folders the user cannot list."""
    try:
        device = run.stat().st_dev
    except OSError:
        return []
    found: list[Path] = []
    folders = [run]
    while folders:
        try:
            entries = list(os.scandir(folders.pop()))
        except OSError:
            continue
        for entry in entries:
            try:
                info = entry.stat(follow_symlinks=False)
            except OSError:
                continue
            path = Path(entry.path)
            if stat.S_ISSOCK(info.st_mode):
                found.append(path)
            elif stat.S_ISDIR(info.st_mode) and info.st_dev == device \
                    and path != run / "user":
                folders.append(path)
    return sorted(found)


# ── macOS ─────────────────────────────────────────────────────────────

# The system services a confined command may look up: who a user is, logging,
# notifications, certificate trust, DNS, the network's configuration, system
# preferences and fonts. Not the Keychain (SecurityServer), the pasteboard, LaunchServices
# or Apple events, through which `open` and `osascript` start programs outside.
MACH_SERVICES = (
    "com.apple.system.opendirectoryd.libinfo",
    "com.apple.system.opendirectoryd.membership",
    "com.apple.system.logger",
    "com.apple.logd",
    "com.apple.system.notification_center",
    "com.apple.trustd",
    "com.apple.trustd.agent",
    "com.apple.dnssd.service",
    "com.apple.SystemConfiguration.configd",
    "com.apple.SystemConfiguration.DNSConfiguration",
    "com.apple.cfprefsd.daemon",
    "com.apple.FontObjectsServer",
    "com.apple.fonts",
)
# Writable devices: the null and zero devices, terminals and the descriptors of /dev/fd.
DEVICES = ('(literal "/dev/null")', '(literal "/dev/zero")', '(literal "/dev/tty")',
           '(literal "/dev/dtracehelper")', '(regex #"^/dev/ttys[0-9]+$")',
           '(regex #"^/dev/fd/")')
DNS_SOCKET = "/private/var/run/mDNSResponder"


class Seatbelt:
    """`sandbox-exec` with a profile made for each command: everything denied but what
    the table allows. In a profile a later rule wins over an earlier one."""

    def __init__(self, program: str, home: Path) -> None:
        self._program, self._home = program, home

    def wrap(self, command: Sequence[str], *, root: Path, tmp: Path,
             granted: Sequence[Path]) -> list[str]:
        return [self._program, "-p", self.profile(root=root, tmp=tmp, granted=granted),
                *command]

    def profile(self, *, root: Path, tmp: Path, granted: Sequence[Path]) -> str:
        hidden = (Path("/Users"), Path("/private/var/root"), self._home, Path("/private/tmp"),
                  Path("/private/var/folders"))
        # Each folder on the way to what is shown: its metadata only, so paths resolve.
        ancestors = sorted({str(a) for p in (root, tmp, *granted) for a in p.parents})
        lines = [
            "(version 1)",
            "(deny default)",
            "(allow process-exec* process-fork)",
            "(allow signal (target same-sandbox))",
            "(allow process-info* (target same-sandbox))",
            "(allow sysctl-read)",
            "(allow ipc-posix-shm* ipc-posix-sem)",
            "(allow pseudo-tty)",
            "(allow file-ioctl)",
            "(allow file-read*)",
            f"(deny file-read* file-write* {_filters('subpath', hidden)})",
            f"(allow file-read-metadata {_filters('literal', ancestors)})",
            f"(allow file-read* file-write* {_filters('subpath', (root, tmp))})",
        ]
        if granted:
            lines.append(f"(allow file-read* {_filters('literal', granted)})")
        lines += [
            f"(allow file-write* {' '.join(DEVICES)})",
            f"(allow mach-lookup {_filters('global-name', MACH_SERVICES)})",
            "(allow system-socket)",
            '(allow network-outbound (remote ip "*:*"))',
            '(allow network-inbound (local ip "*:*"))',
            '(allow network-bind (local ip "*:*"))',
            f'(allow network-outbound (remote unix-socket (path-literal "{DNS_SOCKET}")))',
        ]
        return "\n".join(lines) + "\n"


def _filters(kind: str, values: Iterable[object]) -> str:
    return " ".join(f'({kind} "{_quoted(str(v))}")' for v in values)


def _quoted(text: str) -> str:
    return text.replace("\\", "\\\\").replace('"', '\\"')
