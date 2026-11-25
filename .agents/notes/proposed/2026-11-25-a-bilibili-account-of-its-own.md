# Agent Note: A Bilibili account of its own

Status: proposed — the life (Continuous) logs into a Bilibili account of its own, follows channels on it, and is woken by their new videos

## Problem

Nothing happens in the life's world unless it goes looking or the user writes: it starts
every activity from its own head, in one burst. The user wants input to reach it from the
world ([Following things](../../todo.md)), starting with Bilibili, where it can already
watch videos as a visitor ([watching Bilibili](../implemented/2026-11-23-watching-bilibili.md)).
A visitor cannot follow a channel, and Bilibili's channel pages are the part most likely
to be closed to visitors. The user wants the life to have an account of its own.

## Proposal

### The account is the life's, and the user makes it

- The user creates the account on Bilibili (it needs a phone number and a verification
  code, which only the user handles) and may let the life choose its name and avatar.
- The account belongs to one Kaden instance: the life's (`<data dir>/life`). The user's
  own chats do not use it, so nothing done with it acts as the user, which is what the
  visitor-only rule of [watching Bilibili](../implemented/2026-11-23-watching-bilibili.md)
  protects.

### Logging in without a password, as a tool

- **`BiliLogin`**, a tool of the built-in Tools module, does the whole login, so nothing
  outside that module knows Bilibili: without a login it asks Bilibili's web QR login for
  a code (`passport.bilibili.com/x/passport-login/web/qrcode/generate`) and returns it at
  once as a picture, which the window shows in the step's card; the user scans it with
  the Bilibili app, switched to the life's account, and confirms. The module then polls
  (`…/poll` every 2 s: not scanned, scanned, expired, logged in) in the background for
  the code's 3 minutes. Calling `BiliLogin` again says whether it worked, and who the
  account is; `log_out` forgets the login. No password passes through Kaden. Checked
  2026-11-25: generate answers a visitor, poll says "not scanned" (86101).
- A tool returns only when it finishes, so the code cannot be shown while the same call
  waits for the scan: the code comes back at once and the wait is the module's.
- On success Bilibili sets its login cookies (`SESSDATA`, `bili_jct`, `DedeUserID`) and
  gives a `refresh_token`. The module keeps them in the system keychain (service `kaden`,
  as API keys are), under a name made from its instance's data folder (the factory's
  `paths.data_dir`), so a login belongs to one Kaden instance: the life's, when the life
  logs in. The user's chats could log in an account of their own the same way. A file
  in the data folder (`bilibili-account`, no secret in it) says there is a login, and only
  then is the keychain asked: sub-sessions and a fleet's agents, which never log in, never
  touch it.
- `BiliLogin` is a one-off, so it is deferred as Claude Code defers its rarely used
  built-ins (`ToolSpec.deferred`): named in the system prompt, described once loaded with
  ToolSearch or called.
- The QR code is drawn with `segno` (pure Python, no dependencies, the established
  library for it), added to the core dependencies, as the Tools module draws it.
- **Tool cards show a tool's pictures.** A tool's pictures (`ToolResult.attachments`)
  reach the model today, and the window shows nothing of them. The card shows them under
  its row, small; a general change, useful for any tool that returns pictures
  (`BiliFrames`, screenshots).
- Cookies never appear in tool output (the archive keeps everything) or on a command line
  (not in ffmpeg's `-headers`).

### What the account adds

Tools in the built-in Tools module, beside the visitor ones, offered to an instance that
has a login (it checks for one off the event loop and lists its tools each cycle, so a
login reaches the life on its next turn):

| Tool | What it does |
|---|---|
| `BiliFollow` | Follow or unfollow a channel (uploader id or channel link) |
| `BiliFollowing` | The channels the account follows |
| `BiliFeed` | New videos from followed channels since it last looked |
| `BiliWatchFeed` | Watch them: told of each burst of new videos, until stopped ([standing watches](../implemented/2026-11-25-standing-watches.md)) |

- `BiliVideo` also shows the uploader's id, so a channel can be followed from a video.
- `BiliWatchFeed` is a [standing watch](../implemented/2026-11-25-standing-watches.md):
  each of its calls checks the account's feed (the dynamics feed, video posts) every 10
  minutes and finishes when there is something new, 5 minutes after the first new video,
  with everything new; the runtime delivers it as a notice that wakes the life, and
  watches again, also after a restart.
- What has been delivered is kept in the data folder by post id, not by time (two posts in
  the same second, one listed late, or the newest deleted would otherwise be lost or come
  back), and a watch's outcome is marked delivered only once it is in the conversation.
  `BiliFeed` delivers too. The first look shows the latest few. A watch rides out a feed
  that fails to load; a login that is gone ends it.
- A refusal from Bilibili (a follow refused, a check against automated accounts) is
  reported as it is; the tools do not retry it.
- The visitor tools stay visitors: browsing is not tied to the account.
- Nothing is posted in public: no comments, danmaku or likes. Following is the one thing
  others can see (the channel gains a follower; the following list may be public).

### Private messages

The user wants the life to message them on Bilibili, and asked for a general tool: one
that would message anyone, with nothing set up for it.

- **`BiliMessage`** sends a private message to an account by its UID or a link to its space
  page. The life learns the user's UID as it learns anything: the user tells it.
- **`BiliMessages`** gives the messages people sent the account since it last looked, with
  who sent them; **`BiliWatchMessages`** is a watch the module keeps whenever the life is
  logged in ([standing watches](../implemented/2026-11-25-standing-watches.md)), so a
  message wakes it within a minute; by default only from accounts it follows, a setting of
  the user's ([tool settings](../implemented/2026-11-25-tool-settings.md)).
- A message counts as the life's own time, within its daily amount: anyone can write to an
  account, and a stranger's messages must not spend beyond it. The user's message on a
  spent day reaches it the next day; their message in the app always does at once.
- What has been delivered is kept by sender and message number (`msg_seqno`) in the data
  folder, marked only once the watch's outcome is in the conversation.
- Bilibili limits messages to an account that does not follow back (often one until it
  is answered); the user following the life's account avoids that.
- Unverified until the life is logged in: sending (`api.vc.bilibili.com/web_im/v1/web_im/
  send_msg`, with the account's csrf), the inbox (`…/session_svr/v1/session_svr/
  get_sessions`) and reading a conversation (`…/svr_sync/v1/svr_sync/fetch_session_msgs`).

## Alternatives considered

- **The channel's video list as a visitor** (`x/space/wbi/arc/search`). No account, but
  the endpoint most likely to answer a visitor with Bilibili's check against automated
  visitors, which Kaden does not get around. Not tried, since the user wants an account.
- **The user's own account.** Already logged in, but the life would act as the user, and
  the user would see its follows in their own feed.
- **The window logs in** (a QR code in the life's identity dialog, the login written to the
  keychain for the Tools module to read). The window would know Bilibili's login, and a
  frontend would hand a Tools module a secret outside the contract (contract issue P6).
- **Messages with the user's account only** (a UID the user set in the life's identity
  dialog, passed to its tools as an option). Built first: it held the life to the user's
  account, but put one website in who the life is, and a tool that worked only once set
  up. The user wants the tool general.
- **The user's messages waking it as the user's.** Their Bilibili message would reach it on
  a spent day too, but the tool cannot tell the user from anyone else, and a stranger's
  messages would spend outside the daily amount.
- **A general accounts mechanism in the contract** (modules declare accounts, Settings
  logs in). Complete, but machinery for one account; worth it once a second module needs
  a login.
- **A password login.** Bilibili's password login goes through a captcha (geetest);
  QR login needs neither a password nor a captcha.
- **Watching channels in the runtime, outside tool calls.** Follows would survive restarts
  by themselves, but it is new runtime machinery for one kind of input. A background tool
  call already wakes the life when it finishes; a watch that a restart ends is seen as
  ended, and the life can start it again.

## Acceptance criteria

- Asked to log in, the life calls `BiliLogin`, the code shows in the step's card, the user
  scans it, and the life's next `BiliLogin` says it is logged in; `log_out` forgets it; the
  user's chats never get the life's account.
- The life can follow a channel it found in a video, list its follows, and is woken by a
  followed channel's new video, with everything new from a burst in one wake.
- Without a login, everything works as now.

## Risks

- **Unverified until there is an account:** the feed (`x/polymer/web-dynamic/v1/feed/all`,
  `type=video`, with `offset` and `update_baseline`), follow (`x/relation/modify`, `act`
  1 or 2, `csrf` from `bili_jct`) and following (`x/relation/followings`, paged) calls need
  a login, so they are built from Bilibili's documented web API and tried once the user has
  made the account. Logged-in calls may need the visitor cookies (`buvid3`, `buvid4`) or a
  `bili_ticket`; if Bilibili also wants browser fingerprint parameters (`dm_img_*`), that
  is posing as a browser, and the design stops there.
- **Expiry:** Bilibili can ask for a refresh before the cookies expire
  (`x/passport-login/web/cookie/info` says `refresh`); its refresh flow needs RSA-OAEP, a
  crypto dependency. Not built at first: the `refresh_token` is kept, and when the login
  stops working the tools say so and the user scans again.
- **Bilibili's checks against automated accounts** may restrict the account; new
  accounts following through the API are a known trigger. Following is also public by
  default.
- **The account is as safe as the keychain:** the life's shell could read the entry and
  act as the account beyond these tools (as it could an API key). The sandbox (bwrap)
  hides the keychain's bus; without it, nothing does.
- **Cost:** each delivered batch is a wake on the life's full context; a busy follow list
  spends the daily amount sooner, and once it is spent, wakes wait for the next day.
