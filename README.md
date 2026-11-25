# Kaden

An AI agent for your computer, in a desktop app. It runs commands, works with
files and searches the web, decides on its own only what is safe and asks you about the
rest, and stops at hard spending limits. Works with Anthropic, DeepSeek and
OpenAI-compatible models.

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/images/empty-dark.png">
  <img src="docs/images/empty-light.png" alt="The Kaden desktop app on a new chat">
</picture>

The [desktop app](docs/desktop-app.md), ready for a new chat.

A runtime drives four swappable modules (Core, Memory, Tools, Frontend); see
`src/kaden/api.py` and `SPEC.md`.

## Quick start

Requires Linux, Python 3.12+ and [uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/K5031/Kaden.git
uv tool install --python 3.13 --editable "Kaden[desktop]"
export ANTHROPIC_API_KEY=sk-ant-...   # or enter a key when the app asks for one
kaden
```

Kaden works freely in the folder you choose for a chat; anything outside it gets a quick
automatic check, and Kaden asks you when it is unsure. Choose a folder made for it;
your home folder is too broad to count.

## Documentation

The [documentation](docs/README.md) covers getting started, using Kaden, settings, the
concepts behind it and development.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

GNU Affero General Public License, version 3 or later; see `LICENSE`. Contributions
need a signed [Contributor License Agreement](CLA.md).
