# Agent Note: A sandbox on each system

Status: implemented — the shell's sandbox is behind one interface, bubblewrap on Linux and Seatbelt on macOS, with the local services' sockets out of reach

## Problem

[Free in the work folder](2026-11-10-free-in-the-work-folder.md) lets a
sandboxed command run without asking. Its sandbox has two gaps:

- **Linux: local services stay reachable.** The whole system is bound read-only, and a
  read-only bind does not stop `connect()` on a socket file. A command that runs unasked
  can reach `/run/docker.sock` (root on the machine, in effect), the system D-Bus
  (`/run/dbus/system_bus_socket`: polkit lets an active session power off, mount disks,
  change the network), the smartcard daemon, systemd's varlink services, an SSH agent
  under `/run`.
- **macOS: no sandbox at all.** bubblewrap is Linux only, so every command asks there.

Each system's way of confining a process is its own, and the code that uses the sandbox
should not need to know which one runs.

## Decision

A `Sandbox` protocol in `tools/shell_files/sandbox.py`, what the shell needs and not how a
system does it:

```python
class Sandbox(Protocol):
    def wrap(self, command, *, root, tmp, granted) -> list[str]: ...
```

`command` run so that it reads and writes `root` and `tmp` (an empty folder made for
that command by `ShellFilesTools._shell`, removed after it, its `TMPDIR`), reads `granted`
(files the user attached) and the system, and reaches nothing else listed in the table.
It starts in the folder the process is started in, inside `root`. `find()` is called once
when the module is built, off the event loop: it picks the backend this system has and
**probes** it by running `true` in it; a backend that fails the probe (missing,
user namespaces off, nested in another sandbox, a profile the system refuses) is `None`,
and commands ask. `sys.platform` appears only in `find()`.

The table of the note above, per backend:

| | Linux: bubblewrap | macOS: Seatbelt (`sandbox-exec -p PROFILE`) |
|---|---|---|
| Work folder | `--bind`, read and write | `file-read*`, `file-write*` in it |
| System | `--ro-bind / /` | `file-read*` everywhere not hidden below |
| Home folders | `--tmpfs` over `/home`, `/root`, the user's home | `/Users`, `/var/root`, the user's home not readable; their ancestors' metadata only, so paths resolve |
| `/tmp` | `--tmpfs /tmp`; the command's own `tmp` bound | `/private/tmp` and `/private/var/folders` hidden; the command's own `tmp` |
| Login session's services | `--tmpfs /run/user` (session bus, keychain) | `mach-lookup` denied except a short list of system services (directory lookups, logging, notifications, certificate trust, DNS-SD, preferences daemon, network configuration, fonts); not the Keychain (`com.apple.SecurityServer`), pasteboard, LaunchServices or Apple events |
| Local services | every socket file under `/run` covered with `/dev/null` (`connect()` is refused) | unix sockets denied except mDNSResponder's (DNS) |
| Writes | the work folder and `tmp` only | the work folder, `tmp` and `/dev/null`-like devices only |
| Environment | secret-looking names removed, `TMPDIR=tmp` | the same |
| Processes | `--unshare-pid` | signals and process info only within the sandbox |
| Network | as outside | as outside (IP) |

The `/run` sockets are found each time a command is wrapped (`sandbox.local_sockets`, in
a thread), so a service
started after Kaden (Docker) is covered too. The walk stays on `/run`'s own filesystem
(a mounted disk under `/run/media` is not walked) and skips `/run/user` (hidden whole) and
folders the user cannot list, whose sockets the user cannot find either.

**Abstract sockets are accepted as a gap.** Linux abstract unix sockets (X11's
`@/tmp/.X11-unix/X0`, some D-Bus setups) live in the network namespace, not the
filesystem; only `--unshare-net` closes them, and that takes the network away, which
the note above declined. A sandboxed command can therefore talk to an X server that
accepts the user without a cookie (XWayland with `si:localuser`). macOS has no abstract
sockets.

## Alternatives considered

- **Close the Linux gap only, macOS keeps asking.** Less code, but macOS users get a
  prompt for every command, the case the work-folder level exists to avoid.
- **Hide only `/run/docker.sock` and `/run/dbus` by name.** Covers the two known cases,
  misses the rest (`/run/pcscd`, `/run/ssh-unix-local`, systemd's varlink sockets,
  `/run/podman`), and each new service would be a new hole.
- **Hide all of `/run`.** Simplest, but `/etc/resolv.conf` often links into `/run`
  (systemd-resolved, NetworkManager) and NixOS keeps its whole system there; DNS or
  every program would break.
- **A seccomp filter refusing `socket(AF_UNIX)`**, as Claude Code's Linux sandbox does.
  Closes abstract sockets too, but needs a BPF program per architecture of our own (or a
  binding to libseccomp that is not on PyPI in maintained form), and breaks programs that
  use unix sockets internally.
- **`--unshare-net` with a proxy for the network.** The strongest; it needs a proxy of
  our own and breaks programs that ignore proxy variables.
- **A denylist of mach services on macOS** (everything but the Keychain). Fewer programs
  break, but `open` (LaunchServices) and `osascript` (Apple events) start programs outside
  the sandbox, and the pasteboard is the user's clipboard: an allowlist is the only one
  that stays shut as macOS adds services.
- **Landlock, or a container.** Declined in the note above for the same reasons.

## Consequences

- **macOS commands run unasked in the work folder**, as on Linux.
- **Tests.** The boundary test (`test_a_sandboxed_command_cannot_leave_the_workspace`:
  writing outside, the home folder, the session bus, the system bus, every socket under
  `/run`) runs against whichever backend `find()` returns. The Seatbelt profile is tested
  row by row as text (`tests/test_sandbox.py`) and `find()`'s choice with fakes, on any
  system; the profile itself runs only on the `macos-latest` CI job, where `find()` must
  return Seatbelt (a profile macOS refuses fails the probe there).
- **A wrong profile is a security bug** (as for bubblewrap), and no one has run it on a
  Mac by hand. A profile macOS refuses fails the probe and leaves commands asking, which
  is safe.
- **Hard links on macOS are unchecked.** A command could link a file of the user's that
  is visible (outside the hidden folders, such as one under a user-owned `/usr/local`)
  into the work folder and write it through the link; on Linux the work folder is a bind
  mount of its own and the link fails.
- **A socket that goes away** between the walk and bubblewrap's start makes that command
  fail; it is run again.
- **Programs that need a mach service not on the list fail** in the sandbox on macOS;
  the agent asks to run them outside, as for a tool in the home folder.
- `sandbox-exec` is deprecated by Apple, though Chrome, Claude Code, Codex and Gemini CLI
  rely on it; if it goes, the probe fails and commands ask.
- Programs on macOS that ignore `TMPDIR` and write to `/tmp` fail in the sandbox.
