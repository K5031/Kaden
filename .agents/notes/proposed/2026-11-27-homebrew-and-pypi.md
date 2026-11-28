# Agent Note: Homebrew and PyPI

Status: proposed — each release is published to PyPI from the release workflow, and a tap of the user's own (`K5031/homebrew-kaden`) installs it with Homebrew's Python application formula, Qt and numpy from Homebrew's own formulae

## Problem

On a Mac, people install command-line apps with Homebrew. Kaden has no Homebrew formula
and no PyPI package. A Mac user must clone the repository and install uv
(see [releases](../implemented/2026-11-25-releases.md), which ships Linux packages only).

A Homebrew formula for a Python app is built from PyPI's files, so PyPI comes first.

## Proposal

### PyPI, from the release workflow

- The release workflow (`.github/workflows/release.yml`) gains a `pypi` job after
  `version`. It runs `uv build` (sdist and wheel) and `uv publish`, with PyPI's trusted
  publishing: the job has `id-token: write`, and no token is stored. The PyPI project
  `kaden` is created with this repository and workflow as its trusted publisher (a step
  the user does once on pypi.org).
- The package is the same `kaden` with its `desktop` extra, so
  `uv tool install "kaden[desktop]"` also works on Linux and macOS.
- Run by hand, the workflow builds but does not publish, as for the Linux packages.

### The tap

- A repository `K5031/homebrew-kaden` with `Formula/kaden.rb`. `brew install
  k5031/kaden/kaden` finds it with no `brew tap` first.
- The formula is Homebrew's standard Python application: `include
  Language::Python::Virtualenv`, `depends_on "python@3.14"`, a `resource` per Python
  dependency (sdists, as Homebrew's pip helper builds without binary wheels), and
  `virtualenv_install_with_resources`. That helper installs each resource and Kaden itself
  without resolving dependencies (`std_pip_args`), so what Kaden's metadata requires by
  name is not checked at install: the formula's own list is what gets installed.
- It installs plain `kaden`, not `kaden[desktop]`. The extra names `pyside6-essentials`,
  `pyside6-addons` and `sherpa-onnx`, which have no sdist Homebrew can build
  (`pyside6-*`, `shiboken6`, `sherpa-onnx-core` ship wheels only, per `uv.lock`), and
  Homebrew's `pyside` formula does not provide those distribution names. The desktop's
  pure-Python needs (`qasync`, `pygments`) are listed as resources by hand, and PySide6
  comes from `depends_on "pyside"` (Qt 6 and PySide6, bottled for Apple Silicon), whose
  modules the virtualenv sees through Homebrew's site-packages (unverified: the
  formula may need a `.pth` file for it, as homebrew-core formulae that use `pyside` do).
  Kaden checks no distribution metadata at run time, only imports, so this works.
- `brew update-python-resources` is run with `--exclude-packages` for what comes from
  formulae, and the hand-listed desktop resources are kept.
- Compiled dependencies of the base install, from `uv.lock`: `numpy`, `pillow`,
  `cryptography`, `cffi`, `pyyaml`, `lxml`, `regex`, `charset-normalizer` (pure wheel
  fallback), and Rust ones `pydantic-core`, `rpds-py`, `jiter` and `primp` (from `ddgs`;
  Rust plus BoringSSL, so also `cmake` and `go` at build time, unverified). Those Homebrew
  has formulae for (`numpy`, `pillow`, `cryptography`, `libyaml`-backed `pyyaml`;
  unverified each) are `depends_on`; the rest build from sdist, with
  `depends_on "rust" => :build` and `libxml2`/`libxslt` for `lxml`, as homebrew-core's
  Python apps do. `primp` is the hardest: if it does not build, `ddgs` is left out: it is imported only
  when Kaden's own web search runs (`tools/shell_files/web.py`), so that tool fails with
  an import error while the provider's web search still works. Saying so plainly is a
  small code change.
- The resources are generated, not written: `brew update-python-resources kaden` after
  the version and checksum change. A workflow in the tap does that when a release is
  published (a `repository_dispatch` from Kaden's release workflow), opens a pull
  request, and runs `brew test-bot` on macOS.
- `test do` runs `kaden --help` and checks the version.
- `kaden`, `kd` and `kaden-desktop` are installed. The desktop app starts from the
  terminal. A Launchpad entry comes with the cask, later.

### Not now

- **The voice.** `sherpa-onnx` ships binary wheels. Building it from its sdist builds
  onnxruntime (CMake, a long build, and whether it works under Homebrew is unverified). The
  formula installs without the voice. Turning voice on then says it needs
  `uv tool install "kaden[desktop]"` (today a missing `sherpa_onnx` fails at import when
  the voice starts; that message is a small code change). If the build turns out to work, the voice goes in.
- **A cask** for a `.dmg`: once one is signed (see [macOS, the rest](2026-11-25-macos-the-rest.md)).
- **homebrew-core.** It requires a notable project (stars, users) and builds from source
  on its own CI. The user's tap first.

## Alternatives considered

- **The formula installs with `uv tool install` or pip with wheels.** One line, all wheels
  (voice included), and fast. But Homebrew's rules and `brew audit` expect resources with
  checksums, and an install that downloads at install time cannot be reproduced or cached
  as a bottle. In a private tap it would work (unverified whether Homebrew's build sandbox
  allows the network), but it is the pattern Homebrew asks formulae not to use.
- **PySide6 as a resource.** Its sdist builds Qt bindings against a Qt install. Homebrew's
  `pyside` formula already has that, bottled.
- **No PyPI, the formula builds from the GitHub tag's tarball.** Possible for Kaden itself,
  but its dependencies still come from PyPI, and PyPI is wanted anyway (`uv tool install`).
- **A cask only (the `.dmg`).** The usual way for a GUI app, but it waits for signing.
  Unsigned casks trip Gatekeeper on every update.

## Acceptance criteria

- Pushing a version tag publishes `kaden` to PyPI with no token in the repository's
  secrets, and `uv tool install "kaden[desktop]"` from PyPI starts the app on Linux.
- On a Mac with Homebrew, `brew install k5031/kaden/kaden` installs from bottles where
  Homebrew has them, and `kaden --help` and `kaden-desktop` run.
- A new release opens a pull request in the tap with the new resources, and
  `brew test-bot` passes on it.

## Risks

- Python 3.14 versus Homebrew's: if `pyside` and `numpy` are built for another
  `python@3.x`, the formula must use that one (unverified which `pyside` uses today).
- The resource list is long (Kaden's dependencies pull in about 80 packages). A
  dependency with a broken sdist breaks the formula until it is pinned.
- Rust builds make an install from source slow when no bottle exists. A tap has no
  bottles unless its CI uploads them (`brew test-bot` with `brew pr-pull`, unverified for
  a user tap).
- A PyPI name, once published, is held by the project: the first release should be one
  the user is happy to have public.

## Implementation plan

Files: `.github/workflows/release.yml` (a `pypi` job; a dispatch to the tap),
`pyproject.toml` (sdist includes; the classifiers PyPI shows), `README.md` and
`docs/getting-started.md` (install with `uv tool` and Homebrew), and a new repository
`K5031/homebrew-kaden` (`Formula/kaden.rb`, `.github/workflows/update.yml`,
`.github/workflows/tests.yml`).

1. `uv build` locally, then install the sdist and the wheel in clean venvs and run
   `kaden --help` (a test in `tests/test_packaging.py` that builds and checks the sdist
   holds what the wheel does).
2. The `pypi` job, tried on TestPyPI first (trusted publishing works there too).
3. The tap repository with a formula written by `brew create --python` and
   `brew update-python-resources`, installed on a Mac (the runner, then the user's).
4. The dispatch from Kaden's release to the tap's update workflow.
5. Docs.

Open questions for the user:
- Create the PyPI project `kaden` now (it holds the name), or at the first release?
- Without the voice in the Homebrew install for now: fine?
- Create the `K5031/homebrew-kaden` repository (it must be made by you, on GitHub)?
