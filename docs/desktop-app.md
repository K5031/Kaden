# Desktop app

The desktop app is Kaden's window: a conversation list on the left, the conversation
in the middle, and a message box at the bottom.

![The desktop app, light](images/main-light.png)

It follows your computer's light or dark setting, and switches when you change it. To
keep it light or dark whatever the computer is set to, choose **☰ → View → Theme**; the
choice is remembered.

![The desktop app, dark](images/main-dark.png)

### Your own themes

A theme of your own, or one someone shared, is a JSON file in the `themes` folder of
Kaden's data folder (`~/.kaden/themes/`). It shows in **☰ → View → Theme** by its name,
and saving the file restyles the app at once.

```json
{
  "name": "Fox Den",
  "base": "dark",
  "overrides": {"accent": "#FF8A3D", "bg": "#1E1612", "code.keyword": "#FFB37A"}
}
```

Every field is optional. `base` is `light` or `dark` (dark when left out): the theme starts
from it, and `overrides` changes the colours you name, as `#rrggbb` or `#rgb`. A name or
colour Kaden does not know is skipped, and the theme's menu item says which.

| Name | What it colours |
|---|---|
| `bg` | The conversation, dialogs, menus, tooltips and plain buttons |
| `side` | The sidebar |
| `card` | Step groups and cards, a button or a conversation under the pointer, disabled fields |
| `field` | Text fields and drop-downs |
| `composer` | The message box |
| `bubble` | Your messages, and a code block's Copy under the pointer |
| `select` | Code in replies (its background); small buttons, the title bar's and Settings' list under the pointer or chosen |
| `line` | Hairlines, outlines, dividers, progress bars and pressed buttons |
| `track` | A switch that is off, scroll bars, the thinking rule, the message box while typing |
| `text`, `muted` | Text, and secondary text (hints, times, disabled text) |
| `accent`, `accent_hover` | Main buttons, links, focus and selected text, and a main button under the pointer |
| `on_accent` | Text on `accent` |
| `accent_soft`, `accent_text` | The chosen item (the open conversation, a menu or list choice) and its text; a main button you cannot use yet |
| `ok`, `warning`, `error` | Success, warnings and errors; `error` also delete buttons and the window's close button under the pointer |
| `error_bg`, `error_text` | An error banner and its text |
| `code.keyword`, `code.string`, `code.comment`, `code.number`, `code.name`, `code.builtin` | Kinds of words in code in replies |

## Install

Ready-made downloads (a `.dmg` for macOS, a Flatpak and an AppImage for Linux) are not
published yet. Until then, install Kaden with its `desktop` extra from a checkout (see
[Getting started](getting-started.md) for cloning):

```bash
uv tool install --python 3.13 --editable "Kaden[desktop]"
```

Then start it:

```bash
kaden
```

The window opens on its own and the terminal is free again. `kd` and `kaden-desktop` do
the same. To run the app without installing:

```bash
cd Kaden
uv run --python 3.13 --extra desktop kaden
```

On Linux, choose your name at the bottom left → **Add to App Menu** once the app is open. Kaden then appears in
your app menu with its own icon and starts without a terminal. The menu item is there only
while the app menu has no entry for this install.

The app is tested on Python 3.13. The Qt libraries it uses are not fully compatible with
Python 3.15 yet. Without the `desktop` extra the app cannot start. On a computer without a
display, `kaden` says so and exits; see
[Driving Kaden from a program](headless.md) for running it without a screen.

### A natural voice

With an NVIDIA GPU (about 5 GB of its memory free), Kaden can speak with a more natural
voice, in voices copied from recordings, and understand you better. It needs Python 3.14 and a few GB more of
libraries (PyTorch):

```bash
uv tool install --python 3.14 --editable "Kaden[voice-gpu]"
```

Without the extra, or on Python 3.13, Kaden speaks with its usual voices. See
[Talking with Kaden](using-kaden.md#talking-with-kaden).

## First start

When no API key is saved, a welcome window asks for three things:

1. **Your AI provider**: Claude, DeepSeek or OpenAI. **Get a key** opens the provider's
   website.
2. **Your API key.** Kaden keeps it in your computer's keychain. If your computer has no
   keychain, set the environment variable named in the message instead
   (`ANTHROPIC_API_KEY`, `DEEPSEEK_API_KEY` or `OPENAI_API_KEY`).
3. **A spending limit.** Optional.

Kaden then restarts with your provider. If the provider refuses the key, the message says
so; enter another one in Settings and choose **Restart now**. If Kaden cannot start for
another reason, the Settings window opens with the message.

## Using it

| To | Do this |
|---|---|
| Move, resize or maximize the window | Drag the conversation's title bar or the top of the sidebar; double-click it to maximize. Drag any edge to resize. Minimize, maximize and close are at the top right. |
| Find the menus | **☰** at the top of the sidebar: new chat, set a duty, stop, view (zoom, theme, archived conversations) and model. Your name at the bottom left: settings, memory, tool servers, help and quit; the gear next to it opens Settings. Shortcuts work without opening a menu. |
| Send a message | Type it and press **Enter**. **Shift+Enter** starts a new line. |
| Attach a file | Click **+** in the message box, drop it anywhere on the window, or paste it (a copied picture or screenshot works too). It waits above the message box, a picture with a small preview, until you send; ✕ takes it off. See [Attaching files](using-kaden.md#attaching-files). |
| Talk instead of typing, or hear the replies | Click the microphone button in the message box to talk: Kaden sends what you said when you stop talking. Click the speaker button to hear each reply read aloud. Use either or both; the message box shows whether it is listening, thinking or speaking. **Esc** stops the reading. Click a button again to turn it off. See [Talking with Kaden](using-kaden.md#talking-with-kaden). |
| Run a skill | Send its name as a message: `/consolidate-memory`. See [Skills](concepts.md#skills). |
| Stop Kaden | Press the round button in the message box (it shows a square while Kaden works) or **Esc**. |
| Answer a permission request | Choose **Allow once**, **Allow for this conversation**, **Always allow** or **Deny** on the card above the message box; **Never allow** is under ▾ beside Deny. In Continuous's view there is no "for this conversation". |
| Answer with a click | When Kaden needs a decision, a card above the message box asks the question and offers buttons, Kaden's pick first. A click sends that answer, or does what it says. ✕ closes the card. See [The wrap-up card](using-kaden.md#the-wrap-up-card). |
| Answer a proposal | When Kaden proposes something of its own, a card above the message box says what and why: **Approve** runs it, **Dismiss** declines. See [Proposals](using-kaden.md#proposals). |
| Cancel one task | Press **Cancel** on its card. |
| See what a tool did | Steps between two replies fold into one line, with a chevron after it: the step under way while Kaden works ("Search the web for flights to Oslo"), with how long it has been running once that passes a few seconds ("running · 12s"), then the last step and any that failed ("Read main.py · 1 failed"). Click it to see each step, in a row of its own, and the thinking between them; point at a step to see the tool and its exact input, and click it for the result. A step always reads as words: the agent's own sentence, else what the tool does ("Read main.py", "Searched for “TODO”"), never its raw input. |
| See the model's reasoning | **Thinking…** shows under the conversation as soon as Kaden starts on its next step, counting the seconds (**Thinking… 3s**). Once the reasoning arrives it shows above the reply, the count going on, then **Thought for 4s** when it is over: click it to read it. |
| Copy a reply | Point at it: a copy button and how long ago it came appear under it. |
| Copy code from a reply | Click the copy button at the top right of the code block. |
| Start over | **New chat** (**Ctrl+N**). Nothing is added to the list until you send your first message. |
| Choose a chat's folder | A new chat needs a folder before its first message: click **Choose folder** in the middle and pick a recent one or **Choose folder…**. Sending without one opens the same menu. Any time: **☰ → New chat in another folder…**. See [Folders](using-kaden.md#folders). |
| Go back to a conversation | Click it in the list. |
| Rename, archive or delete one | Right-click it (or select it and press the Menu key): **Rename…** (**F2**), **Archive**, **Delete…** (**Delete**). Turn on **☰ → View → Show archived conversations** to see hidden ones. |
| Pick up where one left off | Right-click it: **Continue in a new conversation**. |
| See what Kaden remembers | **Settings → Memory** (or your name at the bottom left → **Memory…**): About you and the notes it keeps across conversations; add, edit or delete them. |
| Give Kaden more tools | **Settings → Tool servers**: add GitHub, a database and the like. See [More tools](using-kaden.md#more-tools-mcp). |
| Open work Kaden handed off | Its sub-sessions are listed under the conversation, with a dot while they work: click one to open it there, talk to it, approve its steps or stop it. Click the conversation to go back. Right-click one to resume, change its spending limit, rename, archive or delete it; the limit for new ones is in **Settings**. See [Sub-sessions](concepts.md#sub-sessions). |
| Fold a project | Conversations are grouped by their folder; click a folder's name to fold or unfold it. |
| Switch to Continuous | The **Continuous** switch at the top right: the first time, name them and say who they are; then the window becomes their view, without the chat list. Switch it off to go back to your chats: they live on either way. **☰** at its top left is the usual menu, and the gear at the bottom left opens their own settings: their name and who they are, their daily amount, what you said always or never to in their view, and their tools' settings (a change there restarts them). Their model and limits follow your Settings. **Files** next to their name opens their folder, with whatever they keep there. See [Continuous](concepts.md#continuous). |
| Give a conversation standing work | **☰ → Set a duty…**: what to keep doing, for how long, how often to check in, and the plan; **Turn off** ends it. See [Duties](concepts.md#duties). |
| Change the model | Click the model's name under the message box, or **☰ → Model**. |
| Change what Kaden may do on its own | Click the mode under the message box (Auto, Work folder, Read only, Ask first, No tools). Letting it do more asks you first. See [Permissions](settings.md#permissions). |
| Change settings | The gear at the bottom left, or **Ctrl+,**. Settings has sections down the left: General, Spending, Permissions, Instructions, Tools (when the tools have settings), Memory and Tool servers. Tool settings apply after a restart. |
| Make everything larger or smaller | **Ctrl +** and **Ctrl −** (also in **☰ → View**); **Ctrl+0** goes back to normal. Kaden remembers the size. |

Each conversation in the list shows its name, when it was last used, and the start of its
first message. Until Kaden names a conversation, the start of its first message is its
name. A new chat joins the list with your first message; if you leave it before writing
anything, it is not kept.

Messages you send while Kaden works are queued and shown greyed until Kaden reads them.
You can switch conversations only while Kaden is not working on anything; renaming,
archiving and deleting work any time.

**Delete…** asks first: deleting erases the conversation and its summary, and cannot be
undone. What Kaden remembered from it stays in Memory, where you can forget it too. To only hide a conversation, choose **Archive**. Deleting the open
conversation opens a new chat in its place, once Kaden is not working. See [Deleting](concepts.md#deleting) for what
deletion does not reach.

Hover the conversation's title to see its folder. What Kaden has spent, across all
conversations, is at the top of the menu under your name; when you pass 80% of your
spending limit, a notice in the conversation says so. Under the message box are the permission mode and the model. A conversation with a duty shows when it checks in next, next to its title.

At start the app opens maximized and reopens your most recent conversation, or the one
named with `kaden --conversation ID`.

## Settings

See [Settings](settings.md) for what each setting does. Raising or removing the spending
limit, or letting Kaden do more without asking, asks you to confirm first. The provider,
key and modules apply when Kaden restarts: **Restart now** in the Settings window does
that.

## Good to know

- One Kaden uses a data folder (`~/.kaden`) at a time. Opening the app again brings the
  open window forward; when another Kaden has the folder, such as one
  [driven by a program](headless.md), the app says so once and closes.
- Closing the window stops running tasks; your conversations are saved.
