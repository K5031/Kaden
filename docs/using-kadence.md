# Using Kadence

## Starting

`kadence` (or `kd`, or Kadence in your app menu) opens the [desktop app](desktop-app.md) on
your most recent conversation. The command returns at once; the app runs on its own. It
needs a display: without one, `kadence` says so and exits.

| Option | Does |
|---|---|
| `--conversation ID` | Open this conversation first |
| `--data-dir DIR`, `--config FILE` | Use another data folder or config file |
| `--show-config` | Print the settings Kadence would use, then exit |
| `--headless` | Run without a screen, for [another program to drive](headless.md) |
| `--model ID`, `--maintenance-model ID` | Override the models for this run |
| `--permissions auto\|allow_workspace\|allow_read_only\|ask\|deny` | Override the [permission level](settings.md#permissions) for this run |

This page says what Kadence does; [Desktop app](desktop-app.md#using-it) lists where each
thing is in the window.

## Talking to the agent

Type a message and press Enter. While the agent works you can keep typing; your messages
are queued and delivered when it is ready. Its reasoning, replies and tool calls appear in
the conversation as they happen. Each step reads as a short sentence the agent writes
("Read your notes"), with the exact input below it. The steps between two replies, and the
agent's thinking between them, fold into one line that shows the step under way, then how
many there were and any that failed ("4 steps · 1 failed"); click it to see them all.

### Talking with Kadence

You can talk instead of typing. Click the sound-wave button in the message box:

- **You talk, Kadence answers aloud.** It sends what you said once you have been silent for
  about a second, and reads the reply while it is still being written. The conversation
  is on screen as usual, and you can type in between.
- **Only words are read.** Code, tables, links and symbols stay on screen.
- **Esc stops the reading** and Kadence listens again. You cannot talk over it: while it
  speaks, it does not listen.
- **The agent knows you are talking**, so it answers briefly, in plain sentences, without
  emoji, kaomoji or symbols, until you type again.
- **Speech runs on your computer.** What you say is not sent to a speech service; only
  the text goes to the model, as when you type. The first time, Kadence asks to download
  the speech models (about 830 MB) into its data folder.
- **English only** for now.

### Attaching files

Click **+** in the message box, drop files on the window or paste them, then send. A
message can be attachments only.

- **A picture** (PNG, JPEG, GIF or WebP, up to 5 MB) is shown to the model, so you can ask
  about a screenshot or a photo. With a model that cannot see pictures (DeepSeek), the
  agent is only told that one was attached.
- **A PDF** (up to 20 MB and 600 pages) goes to Claude whole: it reads the text and sees
  every page, so tables, charts and scans work. Other providers get the text of its pages.
- **A Word, Excel or PowerPoint file** (.docx, .xlsx, .pptx) goes as its text: a
  document's headings, paragraphs and tables, a workbook's sheets row by row, a deck's
  slides with their notes. Formatting, pictures and charts in them are not included.
- **A text file** (up to 200 kB) goes with its content and its path, so the agent can read
  it at once and knows where it is, also when it is outside the work folder.
- **Any other file** (binary, or larger) goes by its path only; the agent opens it with
  its tools if it needs to.

Attachments are kept with the conversation and shown again when you reopen it. A picture
or a PDF adds to what every later message of that conversation costs; a PDF is charged
by the page.

The agent can also open these by itself when it comes across them, with its file tool:

- **A picture** is shown to the model, so it can look at a screenshot or a diagram in your
  project.
- **A PDF** is read as the text of its pages, all of them or a range. When the text is
  not enough (a scan, a chart, a layout), the agent can ask to look at the pages
  themselves, up to 20 at a time. That costs far more, and only Claude sees them; other
  providers get the text again.
- **A Word, Excel or PowerPoint file** is read as its text.

In the list of steps, such a result names what it returned (`[image: chart.png]`).

## Approving actions

When the agent wants to use a tool that the permission level does not allow on its own,
Kadence asks. The question starts with what the agent says the step does ("Remove the old
build folder"), followed by its exact input. That sentence is the agent's own words: if it
doesn't match the input, trust the input.

| Answer | Effect |
|---|---|
| Allow once | Runs this one call |
| Allow for this conversation | Runs this tool without asking again in this conversation, until you quit |
| Deny | The agent is told the call was refused |

The permission level is set in [Settings](settings.md#permissions).

### Plan mode

For a bigger change, have the agent plan first. Choose **Plan mode** in the permission
picker under the message box (it then reads "Plan mode"), or the agent turns it on itself.
In plan mode the agent only looks around: it reads and searches, and anything that would
change something is refused. When its plan is ready, a card above the message box shows it:

| Answer | Effect |
|---|---|
| Approve | Plan mode ends and the agent carries out the plan |
| Approve and… | The same, after setting the permission level you pick |
| Keep planning | The agent stays in plan mode and waits for you |

Or type what to change: the agent revises the plan. Turning plan mode off in the picker
approves a plan that waits. Plan mode lasts until the plan is approved, you turn it off,
or you switch conversations.

### Proposals

When the agent has an idea of its own (tidying a folder, handing a side task to a
sub-session), it proposes it instead of doing it. A card shows what it would do and why,
with **Approve** and **Dismiss**. The agent carries on meanwhile. Approve runs exactly that step, without asking again, and the agent is
told the result; Dismiss tells it you said no. Proposals still open when you quit, stop the
agent or switch conversations are withdrawn.

### The plan

For a task with several steps, the agent writes a plan first and ticks the steps off as it
works: ✓ done, ◐ under way, ○ still to do, and struck through for steps it dropped. A quick
question gets no plan.

- **The checklist is part of the conversation.** It sits under the newest message while
  the agent works and is gone when the agent stops.
- **The agent finishes its plan before it stops.** If it ends a turn with steps open, Kadence
  sends it back once to do them or close them. When the turn ends, Kadence ticks off any
  step still open. A step stays open only when it waits for a reminder's time, for
  background work, or for its next check-in when it runs continuously.

### Reminders

Ask Kadence to remind you of something, or to do something later ("remind me at 9 to call
Sam", "check the build again in ten minutes"). It gives a step of its plan a due time, and
is woken when the time comes, without a message from you. Next to the conversation's title,
**Reminder at 09:00** shows while one is waiting.

- **The conversation has to be open in Kadence** at that time. A reminder that came due
  while it was closed, or while Kadence was not running, is not sent late by itself: the
  agent sees it with your next message.
- **Stop** puts reminders off until your next message.
- For work that keeps going on its own, have the conversation
  [run continuously](concepts.md#running-continuously) instead.

### Questions

When the agent needs your choice to go on (which database, which approach), it asks and
waits. A card above the message box shows each question with its choices: pick one, or
several where it says you may, and press **Send**. Or type your answer instead. The agent
carries on with your answers in the same turn.

### The wrap-up card

When the agent needs you to decide something, it ends its turn with a card above the message
box: the question, up to three buttons with its recommendation first, and sometimes a short
recap of what is done and what is not. Other answers have no card.

- Most buttons send their text as your message, as if you had typed it.
- One button may do the thing itself ("Yes, push" runs the push). Kadence runs exactly what
  the agent proposed, and the agent picks up from the result.
- Type any other answer instead, or close the card with ✕. Once you send anything, the card
  goes away, and a button that would have done something is withdrawn.
- The card comes back if you reopen the conversation before answering. After a restart a
  button that would have done something just sends its text.

## Keys

| Key | Does |
|---|---|
| Enter | Send the message; Shift+Enter starts a new line |
| Esc | Stop the agent; running tools are cancelled. While Kadence reads a reply aloud, stop the reading |
| Ctrl+N | New chat |
| Ctrl+, | Open Settings |
| Ctrl + and Ctrl −, Ctrl+0 | Make everything larger or smaller; back to normal |
| F2, Delete | Rename or delete the conversation selected in the list |
| F1 | Open this documentation |
| Ctrl+Q | Quit |

A message that is only a [skill](concepts.md#skills)'s name after a slash, such as
`/consolidate-memory`, runs that skill.

## Conversations and memory

The sidebar lists your conversations, grouped by folder; **☰ → View → Show archived
conversations** includes archived ones. A new chat is listed once you send its first
message, and is not kept if you leave it before. Click one to open it. Right-click one to
rename it, archive it to hide it, delete it for good, or **Continue in a new
conversation**, which starts a new one from where that one left off. Deleting asks first
and cannot be undone; deleting the open conversation opens a new chat in its place. You
can only switch conversations while the agent is not working. See
[Deleting](concepts.md#deleting) for what deletion does and does not reach.

**Settings → Memory** shows long-term memory: About you and the notes Kadence keeps across
conversations (see [Memory](concepts.md#memory)). Kadence writes them after each answer, and
the agent saves, reads or deletes one when it needs to, which shows as a step. You can add,
edit or delete notes yourself; About you can be emptied but not deleted. Deleting a note
does not delete the messages it came from.

## Folders

Each conversation works in one folder: its commands run there and its files are read and
written there. A new chat asks for its folder before its first message: pick a recent one
or choose another (see [Using it](desktop-app.md#using-it)). The conversation keeps that
folder when you come back to it; hover its title to see it. **Continue in a new
conversation** keeps the earlier conversation's folder.

## The web

The agent can search the web and read web pages, for anything recent or that it is not
sure of. It links the pages its answer relies on.

With Claude as the AI provider, searches run on Anthropic's side: reliable, with the
sources listed under the answer. Each search costs about 1 cent on your Anthropic account,
plus the tokens of the pages it reads, and counts toward your spending limit. If web search
is switched off for your Anthropic account, Kadence uses its own. With other providers,
Kadence's own search goes to DuckDuckGo and other search engines. Pages are fetched from the
sites themselves. Anything the agent has
read can end up in a search or an address, so keep secrets out of what you share with it.
It reads public pages only, not pages on your computer or your local network. Some sites
refuse programs that read them (Kadence says so and looks elsewhere), and search engines
sometimes turn searches away for a while; Kadence tries a search again before it gives up.

## Looking at your screen

Ask the agent to look at your screen ("what does this error on my screen mean?") and it
takes a screenshot of your whole desktop and sees it, also when you talk to it.

- **It asks first**, like any action outside the work folder. At the permission level
  Auto it looks without asking when your message asks for it.
- **Everything on screen is sent to the model**: close what you do not want it to see.
- **It only looks.** It cannot click or type on your desktop.
- **KDE Plasma only** for now: it uses Spectacle, KDE's screenshot program. On other
  desktops the agent does not have this tool.

A screenshot costs about as much as a page of text each time, and stays in the
conversation like an attached picture.

## More tools (MCP)

Tool servers give Kadence more tools: GitHub, a database, your calendar, and many more.
They use MCP, the Model Context Protocol, which most AI apps support; servers are listed on
their makers' sites and at github.com/modelcontextprotocol/servers.

To add one, open **Settings → Tool servers** and give it:

- a **name**, such as `github`; its tools appear as `mcp__github__…`;
- a **command** to start it (`npx -y @modelcontextprotocol/server-github`) or its **URL**;
- any **variables** it needs, one per line as `NAME=value`, such as an access token. Values
  are kept in your system's keychain, never in a file; leave a value empty to keep it.

When your servers offer many tools, the agent sees them by name and loads the ones it
needs as it goes, so that they do not make every request larger.

Servers start with the next start of Kadence: press **Restart now**. A server that fails to
start is left out; the others still work.

A tool server is someone else's program running with your permissions, so add only ones
you trust. Kadence treats each of its actions as reaching outside the work folder: it checks
or asks before it runs (see [Permissions](settings.md#permissions)).

## Long-running tools

A tool that takes longer than about 10 seconds moves to the background: the agent can keep
working, and the result arrives in the conversation when it finishes. A shell command runs
until it ends (at most 10 minutes) unless the agent starts it in the background. Each running tool
has a card with **Cancel**.

## Quitting

Ctrl+Q, or closing the window, quits. Tools still running are cancelled; your
conversations are saved and can be continued later.
