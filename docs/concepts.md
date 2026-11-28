# Concepts

## Conversations

Every chat with Kaden is a conversation. You can have as many as you like, go back to any
of them, and start a new one whenever the agent is not busy. Kaden names each
conversation after its first message; you can rename it.

- **New conversation**: a fresh start. The agent sees nothing of your other chats except
  [long-term memory](#memory).
- **Continue in a new conversation**: a new conversation that knows where an earlier one left off. Kaden
  writes a short summary of the earlier one into the new one, then leaves the earlier one
  as it was. Use it when a conversation has grown long or wandered off.
- **Archive** hides a conversation from the list and from the agent's searches. You can
  bring it back.
- **Delete** erases a conversation for good. See [Deleting](#deleting).

The agent sees when each of your messages was sent, so it knows the date and time and how
long ago you last wrote. The conversation shows your messages as you typed them.

## Background tasks

A tool that runs longer than about 10 seconds moves to the background. A shell command is
the exception: it runs until it ends, for up to 2 minutes unless the agent allows it longer
(at most 10), unless the agent starts it in the background, as it does for a server or a
long build. In the background, the agent gets on with other work, or tells you it is
waiting. When the tasks it started have all ended, the
agent is woken with the results and carries on by itself, so you don't have to ask whether
it is done. It can check on a task or cancel it, and so can you (**Cancel** on its card). A task that runs
over an hour (unless the tool allows longer) ends as timed out.

Apart from [reminders](#the-plan-and-reminders), that is the only time the agent acts
without a message from you. Permissions and the spending limit apply as always, and after Kaden restarts it
waits for you before doing anything.

When you stop the agent or quit, running tasks are cancelled. If one does not stop in time,
it is marked as abandoned: it may still be running, and the agent is told to check before
assuming anything.

## The plan and reminders

- **The plan** is a list of steps, each to do, doing, done or dropped, with an optional
  due time and note. The agent always has it in view, and you can edit it. Any
  conversation can have one (see [The plan](using-kaden.md#the-plan)).
- **A step that falls due wakes the agent** (see [Reminders](using-kaden.md#reminders)):
  it is how you ask Kaden to do something later.
- **Standing work of its own** is [Continuous](#continuous): a life that goes on by
  itself. Your chats answer you, and act on their own only for a reminder or work they
  started.

## Continuous

Besides your chats, Kaden can live continuously: someone with a life of their own, not
an assistant doing your work. The **Continuous** switch at the top right of the window
turns the whole window into their view, and back to your chats; **☰** in their view is
the same menu as in your chats. Kaden opens again on whichever you had open when you
closed it.

- **You say who they are.** The first time, you give them a name and, if you like, who
  they are: personality, interests, how they talk. Leave that empty and they find out for
  themselves. Their settings (the gear in their view) change it.
- **They live on their own.** They do their own things with their tools (reading,
  looking things up, writing, watching Bilibili, projects of their own). After a turn
  they rest until something wakes them: you writing to them, something they watch
  happening (a channel they follow posting, a Bilibili message), their own background
  work finishing, or a time they chose themselves (up to a day ahead). Kaden never wakes
  them just because time passed.
- **They don't wait long on you.** A permission or a question they put to you closes by
  itself after 3 minutes without an answer, and they go on without it.
- **You can see what they are up to.** While they do something, the line at the top of
  their view says what (say, "Search the web for fox legends"), and **Files** next to
  their name opens their folder: they know you can see it, and what they keep there is up
  to them.
- **They talk to you when they want to.** What they say stays in their view. While you
  are in your chats, a dot on the **Continuous** switch says they said something, and a
  notification shows it when the window is not in front; opening their view clears it.
- **They are apart from your chats, both ways.** They never see your conversations or
  their memory, and your chats never see their life. They know what you tell them, and
  have a folder of their own to work in. What you always or never allow in their view is
  theirs: your chats' saved answers do not apply to them, nor theirs to your chats.
- **They keep going.** They start living the first time you open their view, and from
  then on start with Kaden, carrying on after a restart. Switching back to your chats does
  not pause them. Closing the window keeps Kaden running in the tray; only Quit stops
  them.
- **Their own time costs at most a daily amount**, US$1 unless you change it (the gear
  in their view), on your cheaper model (Settings →
  Model for memory upkeep). Talking with you does not count toward it, and is answered
  with your chosen model. Once a day's share is spent, they rest until the next day and
  still answer you. Your spending limit in Settings covers it too.
- **Voice** talks with whatever is on screen, so you can talk with them too.

## Sub-sessions

The agent can hand a self-contained task to a sub-session: a separate agent with its own
context, working in the same folder with the same tools and settings, as Claude Code's
subagents do. The task is its first message, and it works until its turn ends; its last
reply, whole, comes back as the result of that step. It is not done while background work
it started still runs: one left running a server or a watcher keeps working, and the
step waits, until it or you stop it. The agent either waits for it or lets it run in the background while
the conversation goes on. Several can run at once.

- **You decide on the agent's own ideas.** When you asked for the work, the sub-session
  starts at once. When it is the agent's idea, it is a
  [proposal](using-kaden.md#proposals) you approve or dismiss.
- **It does not go on by itself.** Once its turn has ended, it waits: if it needs
  something, it says so in its reply, and the agent answers it or gives it more to do.
- **The agent hears back.** When a sub-session stops working (it is done, or answered you)
  and the agent was not already waiting for it, the agent gets a short
  notice in its conversation, which wakes it.
- **It can work on its own branch.** In a git repository, the agent can give a sub-session
  a worktree of its own (`isolation: "worktree"`, as in Claude Code): a separate checkout in
  `.claude/worktrees/` of the repository, on a new branch from the last commit, so its
  changes don't mix with yours or another sub-session's. When it ends, a worktree it left
  unchanged is removed with its branch; one it changed is kept, and its path and branch come
  back with its reply. Deleting the sub-session keeps a changed worktree: the code is yours.
  Outside a git repository the agent is told it can't.
- **A spending limit is up to you.** A sub-session has none unless you set a limit per
  sub-session in Settings, or the agent gives one a limit of its own. With yours set, a
  sub-session gets that much unless the agent gives it less, and never more; it is not
  counted in your own limit. To change or remove one sub-session's limit, even after it
  ran out, right-click it: **Change spending limit…** (0 for none), then **Resume** it.
- **It asks you for approvals.** A step that needs your OK waits for you, as in your own
  conversation; the sidebar says the sub-session needs your approval. If it needs a
  decision, its reply says so, and the agent can answer it or ask you.
- **You can open it.** The desktop app lists each conversation's sub-sessions under it in
  the sidebar. Click one to open it in place of the conversation: its messages and steps,
  its approvals, Stop, and a message box that talks to it. Click the conversation to go
  back; the sub-session keeps working.
- **It is kept.** Quitting Kaden stops sub-sessions that are working, but they stay listed,
  and **Resume** lets one that was stopped or failed carry on with its task. Kaden never resumes one by itself after a
  restart, so a crash cannot quietly repeat an action. Opening one that is not running
  starts it again on its own conversation. Their work is in `sessions/` in the data folder.
- **Right-click one** to open, resume, stop, rename, archive or delete it.
- **They go with their conversation.** Deleting a conversation stops and deletes its
  sub-sessions too; archiving it hides them with it.

## Memory

Kaden keeps every message of every conversation until you delete the conversation.

- When a conversation fills about 90% of the model's context, its older half is
  **summarized** so the recent part fits. Continuous counts its context as 150K tokens
  at most, so its conversation, which never ends, is summarized before each step gets
  expensive (`compact_window` in [Settings](settings.md#configtoml)). Long tool output (command output, web pages) is
  shortened for the summary, so summaries stay cheap. The originals stay saved, in full.
- The summary ends with a list of the **topics covered so far**, one line each. Details
  fade from the summary as it is rewritten, but the list keeps every topic, so the agent
  knows what it can search for. The chat shows
  "Compacting conversation…" while this runs, then "Conversation compacted".
- The agent can **search** a conversation's full history, including summarized parts, and
  your other conversations that are not archived.
- **Long-term memory** is a set of notes shared by all your conversations: **About you**
  (who you are, how you like answers) and one note per project, person or topic, each with
  a one-line description. Every conversation sees About you and the list of notes; the
  agent opens a note when it is relevant, so memory stays cheap as it grows.
- **Recall:** before the agent answers your message (or, in Continuous, something that
  woke them, such as a Bilibili message), the cheaper upkeep model picks up to five notes
  that look relevant from their titles and descriptions, and the agent gets their full
  text with the message, as Claude Code does. A note is not brought up again while it is
  still in view and unchanged.
- After each answer, Kaden's cheaper upkeep model reads the exchange and saves what will
  matter later (decisions, plans, lasting facts) into the notes. It skips small talk, news
  of recent work and anything Kaden can read again in your files, and rewrites a note that
  has gone out of date, so you do not have to tidy memory yourself. A note you or the
  agent edit while it reads keeps that edit.
  When it saves something, a line under the answer says so ("Memory updated: Trip"), with
  a link to Memory.
  The agent also saves when you ask it to remember something, and deletes a note when you
  say it is no longer true. It never keeps passwords or keys on purpose. You can read, add,
  edit and **delete** notes in Memory. Notes stay when the conversation that wrote them is
  deleted. Deleting a note does not delete the messages it came from.
- Kaden can keep long-term memory **per project, as files** instead, the way Claude
  Code does; see [Installed modules](settings.md#installed-modules).

## Skills

A skill is a set of written instructions for one kind of task, which the agent reads only
when that task comes up. Every conversation sees the list of skills (a name and one line
each), so having many costs little.

- **Use one** by asking for the task in your own words, or by writing its name as a
  message: `/consolidate-memory`. Anything after the name is passed to the skill.
- **Kaden comes with** `consolidate-memory`: the agent rereads its long-term memory,
  merges duplicates, fixes or deletes what is out of date and tells you what it changed.
  It runs only when you ask.
- **And with** `coding`: how to work on code (read the project first, small changes in its
  style, tests, debugging, careful git). The agent loads it by itself when a task is about
  code; `/coding` loads it on purpose.
- **Add your own** as a folder with a `SKILL.md` file:

  ```markdown
  ---
  name: release-notes
  description: Write release notes from the git log, in this project's format.
  ---

  1. Read the commits since the last tag...
  ```

  The name is lowercase letters, digits and hyphens. Put the folder in `~/.kaden/skills/`
  to have the skill everywhere, or in a project's `.claude/skills/` to have it in chats
  that work in that folder. A project's skill replaces yours of the same name, and yours
  replaces Kaden's. The format is Claude Code's, so its skills work as they are. A new or
  changed skill is picked up with your next message.
- A project's skills are instructions from that project, so open only folders you trust.

## Deleting

Deleting a conversation erases its messages, its summary and its search entries. Memory
notes it wrote stay, so Kaden still knows what it learned; to remove one too, delete it in
Memory. Deleting a note erases it with its earlier versions. Kaden overwrites the erased content in its database file, so it
cannot be recovered from there. It cannot be undone.

Deletion only reaches Kaden's database. It does not reach:

- copies outside Kaden's data folder, such as backups of your computer;
- what the AI provider already received to answer each message; the provider keeps that
  according to its own retention policy.

Kaden's activity log (`events.jsonl` in the data folder) holds nothing to delete: it
records what Kaden did and when, never what was said, what a tool was asked or what it
returned.

To tidy the list without losing anything, archive instead.

## Budgets

A budget limits how much Kaden may use: money, tokens, working time or tool calls. Each
limit is a running total for the Kaden data folder, across all its conversations. When one
is reached, Kaden stops and refuses to continue until the limit is raised. See
[Settings](settings.md).

## Providers and models

A provider is the company whose AI models Kaden uses: Anthropic (Claude), DeepSeek or
OpenAI. Each has several models; larger ones are smarter and cost more. Kaden can use a
cheaper model for memory upkeep than for the conversation itself.
