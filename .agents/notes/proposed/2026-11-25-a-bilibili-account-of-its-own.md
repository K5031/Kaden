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

### Logging in without a password

- In the life's identity dialog (✎ next to its name), **Bilibili: Log in…** shows a QR
  code from Bilibili's web QR login (`passport.bilibili.com/x/passport-login/web/qrcode/
  generate`, then `…/poll` every 2 s: not scanned, scanned, expired, or logged in). The
  user scans it with the Bilibili app, switched to the life's account. No password passes
  through Kaden. The code lasts about 3 minutes. Checked 2026-11-25: generate answers a
  visitor, poll says "not scanned" (86101).
- On success Bilibili sets its login cookies (`SESSDATA`, `bili_jct`, `DedeUserID`) and
  gives a `refresh_token`. They are kept in the system keychain (service `kaden`, as API
  keys are), under a name that carries the instance's data folder, so only the life's
  tools read them. The dialog then shows the account's name and **Log out**.
- The QR code is drawn with `segno` (pure Python, no dependencies), added to the desktop
  extra.
- The login is the frontend's (it shows the code and stores the result), as the life's
  data folder already is (the app writes its `life.json` and settings); the tools read the
  keychain entry, under a name defined once in `api.py` from the data folder. A frontend
  writing what a Tools module reads is not in the contract yet: recorded in
  `.agents/contract-issues.md`.
- Cookies never appear in tool output (the archive keeps everything) or on a command line
  (not in ffmpeg's `-headers`).

### What the account adds

Tools in the built-in Tools module, beside the visitor ones, offered to an instance that
has a login (the module is given its data folder, checks for a login off the event loop,
and lists its tools each cycle, so a login made while the life runs reaches it on its next
turn):

| Tool | What it does |
|---|---|
| `BiliFollow` | Follow or unfollow a channel (uploader id or channel link) |
| `BiliFollowing` | The channels the account follows |
| `BiliFeed` | New videos from followed channels since it last looked; with `wait`, it waits for them in the background |

- `BiliVideo` also shows the uploader's id, so a channel can be followed from a video.
- `BiliFeed` with `wait` is background work: it checks the account's feed (the dynamics
  feed, video posts) every 10 minutes and finishes only when there is something new,
  which wakes the life as any finished background work does. Its spec has no timeout
  (the default of an hour would end it, and wake the life, every hour with nothing new).
  After the first new video it waits 5 more minutes and delivers everything new in one
  wake.
- One mark of what has been delivered, kept in the data folder: a restart does not deliver
  anything again, and `BiliFeed` without `wait` moves the same mark. The first look ever
  sets it to now instead of delivering the feed's history. One watch at a time: a second
  `wait` while one runs answers that it is already waiting, with its operation id.
- A refusal from Bilibili (a follow refused, a check against automated accounts) is
  reported as it is; the tools do not retry it.
- The visitor tools stay visitors: browsing is not tied to the account.
- Nothing is posted: no comments, danmaku, likes or messages. Following is the one thing
  others can see (the channel gains a follower; the following list may be public).

## Alternatives considered

- **The channel's video list as a visitor** (`x/space/wbi/arc/search`). No account, but
  the endpoint most likely to answer a visitor with Bilibili's check against automated
  visitors, which Kaden does not get around. Not tried, since the user wants an account.
- **The user's own account.** Already logged in, but the life would act as the user, and
  the user would see its follows in their own feed.
- **A password login.** Bilibili's password login goes through a captcha (geetest);
  QR login needs neither a password nor a captcha.
- **Watching channels in the runtime, outside tool calls.** Follows would survive restarts
  by themselves, but it is new runtime machinery for one kind of input. A background tool
  call already wakes the life when it finishes; a watch that a restart ends is seen as
  ended, and the life can start it again.

## Acceptance criteria

- The user scans a code in the identity dialog and the life is logged in; Log out forgets
  it; the user's chats never get the account's tools.
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
