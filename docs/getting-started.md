# Getting started

## What you need

- Linux. macOS should work but is untested; Windows is not supported.
- An API key from one provider: [Anthropic](https://platform.claude.com/) (Claude),
  [DeepSeek](https://platform.deepseek.com/) or [OpenAI](https://platform.openai.com/).

## Install

On Ubuntu 24.04 and 26.04, Fedora 44 and 45 and Arch, download the package for your
system from the [latest release](https://github.com/K5031/Kaden/releases/latest) and
install it:

| Your system | Download | Install |
|---|---|---|
| Ubuntu 24.04 or 26.04 | the `.deb` named for it (`noble`, `resolute`) | open it, or `sudo apt install ./kaden-desktop_*.deb` |
| Fedora 44 or 45 | the `.rpm` named for it (`fc44`, `fc45`) | open it, or `sudo dnf install ./kaden-desktop-*.rpm` |
| Arch | the `.pkg.tar.zst` | `sudo pacman -U kaden-desktop-*.pkg.tar.zst` |

Kaden is then in your apps, and `kaden` (or `kd`) starts it from a terminal. A new
release is installed the same way, over the old one.

### From source

On other systems, to follow changes before they are released, or for the
[GPU voice](using-kaden.md), install from source. This needs Python 3.12 or newer (the
app is tested on 3.13) and [uv](https://docs.astral.sh/uv/):

```bash
git clone https://github.com/K5031/Kaden.git
uv tool install --python 3.13 --editable "Kaden[desktop]"
```

This puts the `kaden` command on your PATH, and `kd`, a shorter name for the same thing.
The `desktop` extra installs what the [desktop app](desktop-app.md) needs; without it,
Kaden can only be [driven by another program](headless.md).

**Updating.** `git pull` in the `Kaden` folder updates the code. When an update adds a
library, install again with `--reinstall` added, then restart Kaden:

```bash
uv tool install --python 3.13 --editable "Kaden[desktop]" --reinstall
```

Without uv, use a virtual environment (systems such as Arch refuse a system-wide
`pip install`):

```bash
python -m venv ~/.venvs/kaden
~/.venvs/kaden/bin/pip install "kaden[desktop] @ git+https://github.com/K5031/Kaden.git"
~/.venvs/kaden/bin/kaden
```

## Add your API key

Either export it in your shell profile (`~/.bashrc`, `~/.zshrc`):

```bash
export ANTHROPIC_API_KEY=sk-ant-...     # or DEEPSEEK_API_KEY / OPENAI_API_KEY
```

or start Kaden without one: a welcome window asks for your provider and the key, which is
stored in your system's keychain (see [First start](desktop-app.md#first-start)).
Kaden uses Claude unless you choose another provider.

## First run

```bash
kaden
```

The [desktop app](desktop-app.md) opens on a new chat. Each chat works in one folder, its
**work folder**: click **Choose folder** and pick one made for it, such as
`~/kaden-work`. By default the agent does what it likes inside that folder, and a quick
automatic check decides about anything outside it, asking you when it is unsure (see
[Permissions](settings.md#permissions)).

Type a message and press Enter. The agent can run commands, work with files and search the
web; when it wants to do something it should not decide alone, it asks you first.

Next: [Using Kaden](using-kaden.md).

## Spending

Every message costs money with your provider. Set a spending limit in Settings (the gear
at the bottom left) before longer sessions; Kaden stops when it is reached. See
[Settings](settings.md).
