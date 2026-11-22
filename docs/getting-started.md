# Getting started

## What you need

- Linux. macOS should work but is untested; Windows is not supported.
- Python 3.12 or newer (the app is tested on 3.13), and [uv](https://docs.astral.sh/uv/).
- An API key from one provider: [Anthropic](https://platform.claude.com/) (Claude),
  [DeepSeek](https://platform.deepseek.com/) or [OpenAI](https://platform.openai.com/).

## Install

```bash
git clone https://github.com/K5031/Kadence.git
uv tool install --python 3.13 --editable "Kadence[desktop]"
```

This puts the `kadence` command on your PATH, and `kd`, a shorter name for the same thing.
The `desktop` extra installs what the [desktop app](desktop-app.md) needs; without it,
Kadence can only be [driven by another program](headless.md).

**Updating.** `git pull` in the `Kadence` folder updates the code. When an update adds a
library, install again with `--reinstall` added, then restart Kadence:

```bash
uv tool install --python 3.13 --editable "Kadence[desktop]" --reinstall
```

Without uv, use a virtual environment (systems such as Arch refuse a system-wide
`pip install`):

```bash
python -m venv ~/.venvs/kadence
~/.venvs/kadence/bin/pip install "kadence[desktop] @ git+https://github.com/K5031/Kadence.git"
~/.venvs/kadence/bin/kadence
```

## Add your API key

Either export it in your shell profile (`~/.bashrc`, `~/.zshrc`):

```bash
export ANTHROPIC_API_KEY=sk-ant-...     # or DEEPSEEK_API_KEY / OPENAI_API_KEY
```

or start Kadence without one: a welcome window asks for your provider and the key, which is
stored in your system's keychain (see [First start](desktop-app.md#first-start)).
Kadence uses Claude unless you choose another provider.

## First run

```bash
kadence
```

The [desktop app](desktop-app.md) opens on a new chat. Each chat works in one folder, its
**work folder**: click **Choose folder** and pick one made for it, such as
`~/kadence-work`. By default the agent does what it likes inside that folder, and a quick
automatic check decides about anything outside it, asking you when it is unsure (see
[Permissions](settings.md#permissions)).

Type a message and press Enter. The agent can run commands, work with files and search the
web; when it wants to do something it should not decide alone, it asks you first.

Next: [Using Kadence](using-kadence.md).

## Spending

Every message costs money with your provider. Set a spending limit in Settings (the gear
at the bottom left) before longer sessions; Kadence stops when it is reached. See
[Settings](settings.md).
