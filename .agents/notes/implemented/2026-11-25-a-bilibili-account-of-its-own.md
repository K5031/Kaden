# Agent Note: A Bilibili account of its own

Status: implemented — the life (Continuous) logs into a Bilibili account of its own by a QR code, follows channels, is woken by their new videos, and messages people

## Problem

Nothing happens in the life's world unless it goes looking or the user writes: it starts
every activity from its own head, in one burst. The user wants input to reach it from the
world, starting with Bilibili, where it can watch videos as a visitor
([watching Bilibili](2026-11-23-watching-bilibili.md)). A visitor cannot follow a channel,
and Bilibili's channel pages are the part most likely to be closed to visitors. The user
wants the life to have an account of its own, and to be able to message them on it.

## Decision

### The account is the instance's, and the user makes it

- The user creates the account on Bilibili (it needs a phone number and a verification
  code, which only the user handles).
- A login belongs to one Kaden instance: the one whose tools logged in, in practice the
  life's (`<data dir>/life`). The user's chats could log in an account of their own the
  same way. Nothing done with the life's account acts as the user, which is what the
  visitor-only rule of [watching Bilibili](2026-11-23-watching-bilibili.md) protects.
  The visitor tools (`bilibili.py`) never use a login.

### Logging in without a password, as a tool

- **`BiliLogin`** (`tools/shell_files/bili_account.py`), a tool of the built-in Tools
  module, does the whole login, so nothing outside that module knows Bilibili. Without a
  login it asks Bilibili's web QR login for a code (`passport.bilibili.com/x/passport-login/
  web/qrcode/generate`) and returns it at once as a picture (drawn with `segno`), which the
  step's card in the window shows. The user scans it with the Bilibili app, switched to
  the life's account, and confirms. The module polls (`…/poll`, every 2 s, `POLL_S`) in the
  background for the code's 3 minutes (`CODE_S`). Calling `BiliLogin` again says whether
  it worked and which account it is; `log_out` forgets the login. No password passes
  through Kaden.
- A tool returns only when it finishes, so the code comes back at once and the wait is
  the module's.
- The login (Bilibili's cookies `SESSDATA`, `bili_jct`, `DedeUserID`, `DedeUserID__ckMd5`,
  `sid`, `buvid3`, and its `refresh_token`) is kept in the system keychain (service
  `kaden`, as API keys are) under `bilibili-login:<data dir>` (`login_name`). A file in the
  data folder, `bilibili-account` (no secret in it), says there is a login, and only then is
  the keychain asked: sub-sessions and a fleet's agents, which never log in, never touch
  it.
- `BiliLogin` is deferred (`ToolSpec.deferred`), as Claude Code defers its rarely used
  built-ins: named in the system prompt, described once loaded with ToolSearch or called.
- A step's card shows a tool's pictures (`ToolResult.attachments`, the desktop's
  `ToolItem.pictures`), for any tool that returns them.
- Cookies never appear in tool output (the archive keeps everything) or on a command line.

### What the account adds

The account's tools are offered only while the instance has a login (checked each time
the tools are listed, so a login reaches the life on its next turn); until then only
`BiliLogin` is.

| Tool | What it does |
|---|---|
| `BiliFollow` | Follow, or with `follow: false` unfollow, a channel (uploader id or space link); deferred |
| `BiliFollowing` | The channels the account follows; deferred |
| `BiliFeed` | New videos from followed channels since it last looked |
| `BiliWatchFeed` | A [standing watch](2026-11-25-standing-watches.md): told of each burst of new videos, until stopped |
| `BiliMessage` | Send a private message to an account by its UID or a link to its space page |
| `BiliMessages` | Messages people sent the account since it last looked, with who sent them |
| `BiliWatchMessages` | A watch of the inbox, kept by the module while logged in |

- `BiliVideo` (a visitor tool) shows the uploader's id, so a channel can be followed from a
  video.
- `BiliWatchFeed` reads the account's feed (`x/polymer/web-dynamic/v1/feed/all`, video
  posts) every 10 minutes (`FEED_CHECK_S`), up to 3 pages while a whole page is new, and
  finishes 5 minutes (`BATCH_S`) after the first new video, with everything new; the
  runtime delivers that as a notice that wakes the life, and watches again, also after a
  restart. A watch rides out a feed that fails to load; a login that is gone ends it.
- A video is new when it was not delivered and was posted after the last look, less an
  hour (`LATE_S`: Bilibili may list a video late, and a new follow's past uploads are not
  news). What was delivered is kept by post id in `bilibili-feed.json` (the newest 1000),
  marked only once the outcome is in the conversation (`ToolOutput.confirm`). The first
  look shows the latest 10. `BiliFeed` delivers the same way.
- `BiliWatchMessages` is kept by the module (`Tools.standing_watches`) whenever the
  instance is logged in. It reads the inbox (`api.vc.bilibili.com …/get_sessions`, then
  `…/fetch_session_msgs`) every 30 s (`MESSAGE_CHECK_S`), or every 5 s (`MESSAGE_ACTIVE_S`)
  for 5 minutes after a message either way. Whose messages wake it is the user's tool
  setting `bilibili_wake` ([tool settings](2026-11-25-tool-settings.md)): from followed
  accounts (the default), from anyone, or from no one, which keeps no watch.
- A message's wake counts as the life's own time, within its daily amount: anyone can write
  to an account, and a stranger's messages must not spend beyond it. The user's message
  on a spent day reaches it the next day; their message in the app always does at once.
- What was delivered is kept by sender and message number (`msg_seqno`) in
  `bilibili-messages.json`, marked only once the outcome is in the conversation. The first
  look shows the latest 10.
- Following goes through `x/relation/modify` (with the csrf from `bili_jct`), the follow
  list through `x/relation/followings` (paged), sending through `…/web_im/v1/web_im/
  send_msg`.
- A refusal from Bilibili (a follow refused, a check against automated accounts) is
  reported as it is; the tools do not retry it. When the login stops working, the tools
  say so and that `BiliLogin` gives a new code.
- Nothing is posted in public: no comments, danmaku or likes. Following is the one thing
  others can see, and `BiliFollow` says so.

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
  dialog, passed to its tools as an option). It held the life to the user's account, but
  put one website in who the life is, and the tool worked only once set up. The user wants
  the tool general.
- **The user's messages waking it as the user's.** Their Bilibili message would reach it on
  a spent day too, but the tool cannot tell the user from anyone else, and a stranger's
  messages would spend outside the daily amount.
- **A general accounts mechanism in the contract** (modules declare accounts, Settings
  logs in). Complete, but machinery for one account; worth it once a second module needs
  a login.
- **A password login.** Bilibili's password login goes through a captcha (geetest);
  QR login needs neither a password nor a captcha.
- **Watching channels in the runtime, outside tool calls.** New runtime machinery for one
  kind of input; a watch tool and [standing watches](2026-11-25-standing-watches.md) do it
  for any module.

## Consequences

- The life follows channels, wakes on their new videos in one wake per burst, and talks
  with people, the user included, on Bilibili; without a login everything works as for a
  visitor.
- **No refresh of the login.** Bilibili can ask for a refresh before the cookies expire
  (`x/passport-login/web/cookie/info`); its refresh flow needs RSA-OAEP. The
  `refresh_token` is kept, but the flow is not built: when the login stops working the user
  scans a new code.
- **Bilibili's checks against automated accounts** may restrict the account; new accounts
  following through the API are a known trigger. Following is public.
- **The account is as safe as the keychain:** the life's shell could read the entry and
  act as the account beyond these tools, as it could an API key. The sandbox hides the
  keychain's bus; without it, nothing does.
- **Cost:** each delivered batch or message is a wake on the life's full context; a busy
  follow list or inbox spends the daily amount sooner, and once it is spent, wakes wait for
  the next day.
- Bilibili limits messages to an account that does not follow back (often one until it is
  answered); the user following the life's account avoids that.
