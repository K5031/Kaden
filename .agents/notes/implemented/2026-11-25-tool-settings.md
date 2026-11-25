# Agent Note: Tool settings

Status: implemented — a Tools module declares its settings; Settings shows them on a Tools page, per Kaden, as Claude Desktop shows an extension's

## Problem

A Tools module has no setting the user can see. Its factory takes options from
`config.toml`, which the user never opens, so every choice a tool needs from the user ends
up as a field written into the window for that one tool. The first was "Your Bilibili UID"
in the life's identity dialog, which the user asked to remove: it put one website into who
the life is, and every next service would add another field.

The next need is already here: the life's Bilibili inbox wakes it for anyone's message,
and a stranger can wake it at will. The user wants to say whose messages wake it, and that
is a setting of the Bilibili tools, for the life's Kaden only.

Two more things are off today: Settings opened in the life's view shows the user's chats'
settings, not the life's; and the app rewrites the life's `settings.toml` from the chats'
on every start, so a setting of the life's own would not last.

## Decision

### A module declares its settings

- A Tools factory may carry `user_config`: a sequence of `ToolSetting`s (`api.py`), each
  with a name, a title, a description, a type and a default. The idea is Claude Desktop's
  extensions' (`user_config` in their manifest), not their format. Types: `boolean`,
  `string`, and `choice` (its values and their labels). Numbers, lists and secrets come
  with the first tool that needs one.
- `load_config` reads the declaration from the factory of the Tools module being built
  (`modules.declared_settings`), an id or an import path alike, and keeps it in
  `ModuleState.tool_settings`.
- The values are saved with the instance's other settings, in `settings.toml` under
  `[tool_settings]`. Only the values the module being built declares, and of the declared
  type, are given to its factory, over the same names from `config.toml`
  (`ModuleState.tool_values`): a value left from another module never reaches a factory
  that would not take it. The factory routes each to where it is used.

### The contract

- `Settings.tool_settings`: the declared settings, each with its saved value.
  `SettingsUpdate.tool_settings`: names to values (`Mapping[str, Any]`, so the headless
  channel carries a string as a string), None for back to the default, merged into the
  saved ones and checked against the declaration in the runtime (an unknown name, or a
  value of another type, is a ValueError). Saved at once; applied at the next start
  (`restart_needed`). Spec 16.4.

### Settings

- **The chats:** a **Tools** page in Settings, after Instructions, when the Tools module
  declares settings (`ToolSettingRows`: a switch, a field, options), applied at the next
  start as module settings are (Restart now).
- **The life:** in its view, the gear and ☰ → Settings open **Settings · <name>**
  (`LifeSettingsDialog`), its tool settings only; its provider, models, limits and
  instructions follow the user's chats and its life, as the app writes them. Saving sends
  them to the life's runtime and restarts its process (`Life.restart`), since the life's
  runtime cannot restart itself over its channel.
- **Lasting:** when the app writes the life's `settings.toml` from the chats' on start, the
  life's own `[tool_settings]` replace the chats' (none if it has none).
- A declared setting is the instance's: the chats' page shows it too, and there it applies
  to what the chats do.

### Bilibili: whose messages wake the life

- The built-in Tools module declares `bilibili_wake`, a `choice`: from followed accounts
  (the default), from anyone, from no one. With "from no one" the inbox is not watched
  (`Account.standing` names nothing); `BiliMessages` still reads it.
- "From followed accounts": the inbox list (`get_sessions`) gives each conversation and
  its unread count; only conversations with followed accounts are read and can fire.
  Others are not fetched (a stranger cannot make it read more), stay unread for
  `BiliMessages`, and the next firing says how many conversations are waiting. Whom it
  follows is read in pages and kept for 10 minutes.

## Alternatives considered

- **A field in the window for each tool's need** (the UID field). What was built first; the
  user rejected it: it is not general, and it shows a service to someone who does not use
  it.
- **Only the follow list, no settings.** No new machinery, and "from accounts it follows"
  is the default here too; but the next tool with a choice and no natural place for it
  (an email filter, a channel list, an endpoint) would bring the same problem back.
- **Options in `config.toml` only.** Already possible, but the user never sees the file.
- **Settings per tool call** (arguments the life passes). The life would decide what
  protects the user; these are the user's.

## Consequences

- A Tools module gets settings the user sees without any change to the window; one that
  declares none looks as before.
- A stranger's Bilibili message no longer wakes the life by default; it waits for
  `BiliMessages`, and the life hears it is there with the next message it is woken for.
- The life's settings dialog shows only its tools' settings; a user looking there for its
  model or limits finds them in the chats' Settings, as the dialog says.
- A new follow wakes it within 10 minutes, not at once.
- Tool settings apply only after a restart: the chats' need Restart now; the life's
  restart on saving.
- Values of a module that is not the running one are dropped when this one's are saved.
