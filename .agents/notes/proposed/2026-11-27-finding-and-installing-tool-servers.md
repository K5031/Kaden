# Agent Note: Finding and installing tool servers

Status: proposed — a Browse on Settings → Tool servers that lists the official MCP Registry and installs a server in one click

## Problem

Adding a tool server means knowing it exists, finding its command line (`npx -y …`) or
URL, and typing that and its variables into Settings → Tool servers. A user who is not a
developer cannot do that, and does not know what servers there are. Claude Desktop has a
directory of connectors and extensions with one-click install that asks only for a
server's keys.

## Proposal

- **Source: the official MCP Registry** (`registry.modelcontextprotocol.io`), not a
  catalogue of Kaden's own. It is an unopinionated list of public servers in a standard
  `server.json` format, and it is meant to be read by clients and aggregators. It is in
  preview: breaking changes and data resets may happen.
  - Read: `GET /v0.1/servers?search=<words>&limit=<n>&cursor=<c>`. Checked on 2026-11-27:
    it returns `servers[].server` (name, title, description, version, websiteUrl,
    repository, `packages[]`, `remotes[]`), `_meta["io.modelcontextprotocol.registry/official"]`
    (status, isLatest) and `metadata.nextCursor`.
  - Of the 100 entries the API listed first, 93 offered only a remote URL, 6 an npm
    package and 1 a PyPI package. Some offered neither, so they cannot be installed.
- **Who publishes it.** The registry checks a server name's namespace when the server is
  published: `io.github.<user>/…` through that GitHub account, and `com.example/…` through
  that domain (DNS or HTTP). Browse shows that as "by github.com/<user>" or "by
  example.com", with the repository link. The registry checks nothing else, and Kaden
  vouches for nothing.
- **What can be installed.** An entry is installable when Kaden can run it:
  - an npm package: `npx -y <id>@<version>`;
  - a PyPI package: `uvx <id>==<version>`;
  - an OCI image: `docker run -i --rm -e NAME … <image>`, one `-e NAME` per variable,
    because docker does not pass the environment it gets on to the container;
  - a remote that needs no header.

  The version is the one listed, so a server does not change under the user. The
  package's `runtimeHint` (`npx`, `uvx`, `docker`) picks the runner when it gives one.
- **Its arguments.** A package lists `runtimeArguments` (for the runner, such as `-y`) and
  `packageArguments` (for the server), each `positional` or `named`, with a fixed `value`
  or one the user gives (`valueHint`, `isRequired`, `default`, `choices`, `isRepeated`,
  and `{name}` placeholders filled from `variables`). For example, a filesystem server
  needs the folders it may reach, one argument repeated. A named argument's `name`
  includes its dashes and is written `<name>=<value>`, as the schema describes it
  (`--flag={value}`).
  - Fixed values go into the command as listed.
  - Required ones without a value, and optional ones with a description, are asked for in
    the same form as the variables, prefilled with their `default`, as a choice where
    `choices` is given, and as a list where repeated.
  - Values are quoted with `shlex.join`, since `McpServer.command` is a command line. The
    MCP module splits it again and starts the program without a shell, which the
    schema's warning about command injection recommends. An entry
  whose runner is missing (`shutil.which`) shows what to install ("Needs Node.js") and no
  Install button.
- **One click.** Install builds an `McpServer` and saves it through
  `update_settings(mcp_servers=…)`, as Add… does today:
  - The name is taken from the last part of the registry name, cut to 32 allowed
    characters, with a number added when it is already taken.
  - It asks only for the package's arguments as above, and its `environmentVariables`
    that are `isRequired` or `isSecret`, with their descriptions and defaults. Variable
    values go to the keychain, as today. Arguments are part of the command, so a secret
    passed as an argument would be kept in settings.toml. An entry with a secret argument
    is not installable.
  - Then the existing "Restart now" notice appears. Remove is the existing Remove…,
    which also erases the keys.
- **Already installed.** An entry whose command or URL matches a saved server shows as
  installed.
- **No new permission prompts.** Installing is the user's own act. Each tool call of the
  server is reviewed or asked about as any tool server's already is (never read-only,
  never confined).
- **Where the code lives.** The registry is read by a small client that frontends share,
  `kaden/frontends/registry.py`, on `httpx2` (already a dependency). Nothing else knows
  the registry: it is a frontend's way of filling a setting. The runtime is unchanged.

## Alternatives considered

- **A catalogue of Kaden's own** (a vetted list in the repo). Kaden could vouch for each
  server, but someone has to keep it current; the todo says no.
- **Only servers that Kaden or Anthropic vouches for.** Claude's directory reviews
  connectors: an automatic policy scan for a community listing, and a tested "Verified"
  tier. Kaden has no review process, and copying Claude's list would mean reading a
  directory that has no public API (unverified).
- **Let the agent install servers** (Claude Code's agent can run `claude mcp add`). The
  agent could already write `settings.toml` through the shell, which is protected. The
  todo asks for a place for the user.
- **`.mcpb` bundles** (Claude Desktop's extension format: a zip with a manifest whose
  `user_config` marks `sensitive` values for the OS keychain). They need a reader and
  unpacker of their own. The registry also lists `mcpb` packages, which could be a later
  package type.

## Implementation plan

Contract changes: none for stdio servers. Remotes that need headers need
`McpServer.headers`, with values in the keychain like `env`, and the MCP module sending
them. That is left out of the first version (see the open questions).

Files:
- `src/kaden/frontends/registry.py`: `search(words, cursor) -> Page` and
  `install_plan(entry) -> Plan | Unavailable`. A Plan is the name, the runner and its
  fixed arguments, the arguments and variables to ask for, and `build(answers) ->
  McpServer`. It holds no Qt.
- `src/kaden/frontends/desktop/settings.py`: a Browse… button on the Tool servers page
  opens a `BrowseDialog` with a search box, results (title, publisher, description,
  Install or Installed or "Needs …"), and a variables form on Install.
- `docs/settings.md` (Tool servers), `.agents/decisions.md` (a registry row: API version,
  page size, runner commands).

Tests first:
- `tests/test_registry.py`, with recorded registry JSON and no network:
  - the parsing, and only the latest version kept;
  - the command per package type, `-e NAME` per variable for docker;
  - fixed `runtimeArguments` and `packageArguments` in order, named ones as
    `<name>=<value>`, `{placeholders}` filled, repeated ones once per value, required
    ones asked for, defaults prefilled, values quoted (`;rm -rf ~` stays one argument);
  - a secret argument makes the entry not installable;
  - a missing runner gives Unavailable;
  - remotes with required headers are not installable;
  - names cut, made unique and allowed;
  - only required or secret variables asked for.
- `tests/test_desktop_window.py`:
  - Install saves the expected `McpServer` and shows the restart notice;
  - a saved server shows as installed.

Steps:
1. Write `registry.py` and its tests.
2. Add BrowseDialog and its tests.
3. Update the docs and decisions.

Open questions for the user:
- **What "certified" means.** Recommended: show every installable entry with its checked
  publisher (the registry's namespace check) and repository, and add no badge of Kaden's
  own. The alternatives are a short list Kaden vouches for, or a filter to publishers
  that prove a domain rather than only a GitHub account.
- **Remote servers that need a key or OAuth.** Most entries are remote. A header key
  needs `McpServer.headers`, a small contract change. OAuth needs a browser sign-in flow
  and token refresh, which the `mcp` SDK may provide (unverified). Should either be in the
  first version?
- **Updates.** Should a newer version of an installed server be shown and installed by a
  click, or stay pinned until the user installs it again?

## Acceptance criteria

- From Settings → Tool servers → Browse, a search finds registry servers, each with its
  title, description, checked publisher and repository.
- Installing an npm or PyPI server asks only for its required or secret variables, saves
  it, and its tools are offered after Restart now.
- A server whose runner is missing says what is needed and cannot be installed.
- Nothing about the registry is in the runtime or api.py.

## Risks

- **The registry is in preview** and may change its API or reset its data. Kaden's client
  is small and pinned to one API version, and a failure shows "The directory can't be
  reached".
- **The list is unvetted.** There are near-duplicate entries and spam, such as two
  publishers listing the same remote. A checked namespace says who published an entry,
  not that the server is safe. The permission review of every call is what protects the
  user.
- **Most entries are remote-only**, so without headers or OAuth much of the registry shows
  as not installable.
- **`npx` and `uvx` fetch code at the first start.** That start can take longer than the
  30 s connect timeout, and then the server is left out once.
