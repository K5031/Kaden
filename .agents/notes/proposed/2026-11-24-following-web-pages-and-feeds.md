# Agent Note: Following web pages and feeds

Status: proposed — two watch tools in the built-in module, a page's main text and a feed's new entries, with repositories followed through GitHub's own feeds

## Problem

[Continuous](../implemented/2026-11-23-continuous.md) is woken from outside only by what
it can watch ([standing watches](../implemented/2026-11-25-standing-watches.md)). The
only sources today are Bilibili's: the channels its account follows, and its inbox. Anything
else it cares about, a blog, a project's releases, a page that changes, it can only check
by waking on a timer and looking. That spends its own time on looks that mostly find
nothing, and it misses changes between looks.

The todo item asks for web pages (compared by extracted content, not HTML), feeds, and
repositories, after a few days of logs.

### What the logs show (2026-11-23 to 11-27)

- The inbox watch (`w1`) fires a few times a day, as people write. In one 40-minute
  stretch it fired about 1,500 times, from a fault in the Bilibili tools: a watch that
  misjudges what is new costs a wake per firing, which matters more for watches the agent
  starts itself.
- The feed watch (`w2`) fired 4 times in two days, at least an hour apart: a burst
  arrives as one wake, as intended.
- The life called `WebFetch` once in four days. It spends its time on Bilibili (messages,
  follows, videos) and its own notes. Nothing yet shows it wants to follow other sites.

## Proposal

### Two watch tools, in the built-in Tools module

Both go in `tools/shell_files/web.py`, beside `WebFetch`, whose public-address check and
fetch they reuse: a watch reaches only what `WebFetch` could. Both are `ToolSpec.watch`
(read-only, `timeout=math.inf`) and `deferred`, since only a life uses them often.

| Tool | Arguments | Fires when |
|---|---|---|
| `WatchPage` | `url`, `every_minutes` (default 60, at least 15) | the page's main text (trafilatura, as `WebFetch` reads it) differs from what was last delivered. The firing is a unified diff of that text, cut at a few thousand characters, with the page's address |
| `WatchFeed` | `url`, `every_minutes` (default 30, at least 15) | an RSS or Atom feed (feedparser) has entries not delivered before. The firing lists them: title, link, date, the summary's first lines. Like `BiliWatchFeed`, it waits 5 minutes after the first new entry, so a burst comes as one wake |

- **First look is not news.** The first time a watch looks, it records what is there and
  fires nothing, as `BiliWatchFeed` does.
- **Delivered only when delivered.** What a firing gives is marked seen in its
  `ToolOutput.confirm`, so a crash gives it again. State is one small JSON file per watch
  in the module's data folder, named by a hash of the conversation
  (`ToolContext.conversation_id`), the tool and the normalized URL. Watches belong to a
  conversation, so two conversations following one page each get every change. The
  interval is left out of the key, so changing only how often a page is checked keeps
  what was seen.
- **Polite polling.** The fetch sends `If-None-Match` / `If-Modified-Since` from the last
  answer, so an unchanged feed costs a 304 (feedparser documents this as `etag` and
  `modified`; here httpx2 sends the headers, and feedparser only parses the body).
- **Whitespace-only changes** to a page's text are not a change. Nothing cleverer: what
  counts as a change for a given page is the agent's to judge when it reads the diff.
- **Errors.** A page that is gone (404, 410) or a body that is not a feed ends the watch
  (`retry=False`). Timeouts, 5xx and 429 errors are tried again with the runtime's
  doubling back-off.

### Repositories are feeds

GitHub publishes Atom feeds of a repository's releases (`/releases.atom`), tags
(`/tags.atom`) and a branch's commits (`/commits/<branch>.atom`), so `WatchFeed` follows a
repository with no GitHub tool and no API key. `WatchFeed`'s description says so in one
sentence, as `BiliVideo`'s says what links it takes. GitHub has no feed for issues or
pull requests; those stay with `gh` in Bash.

### What stays the same

- The runtime and `api.py` do not change: the watch contract already covers these.
  Watches the life starts count as its own time, within its daily amount.
- No standing watches of the module's own: what to follow is the agent's choice, as Bilibili
  follows are.
- Permissions treat them as `WebFetch`: read-only network calls.

## Alternatives considered

- **A repository tool on GitHub's REST API.** It covers issues and pull requests, but
  without a key it is limited to 60 requests an hour, shared by every repository watched
  (GitHub's docs). Its documented exemption for unchanged (304) answers is stated for
  authenticated requests. It would also need a GitHub token setting. The feeds need
  neither, for the releases and commits that are most of what is worth following.
- **changedetection.io** (a self-hosted service that watches pages and calls a webhook).
  Mature, with visual selectors and filters, but it is a second program to install and
  run, and Kaden has no inbound webhook to receive it.
- **An MCP server for feeds or page monitoring.** Nothing to build, but a server's watch
  would have to be a tool that waits, and MCP tools cannot declare `ToolSpec.watch`, so
  the runtime could not keep it as a watch.
- **The life writes its own check script** (Bash, a timer, a file of what it saw). It
  works today, with no code, but every life works it out again. A failure goes unnoticed
  until it looks, and a firing is not delivered-when-delivered.
- **Comparing HTML, or hashes only.** HTML changes on every load (ads, tokens, counters);
  a hash says that something changed, not what. The todo already ruled out HTML.
- **A judge on each change** (the maintenance model decides whether a diff matters, given
  an `about` argument). It would cut noisy firings, but it adds a model call per change
  and a second opinion between the agent and what it chose to follow. Left for a page
  that proves noisy.

## Acceptance criteria

- `WatchPage` fires once per real change of a page's main text, with a diff, and not on
  a whitespace-only change or on its first look. It fires again after a crash before
  confirmation.
- `WatchFeed` fires with only the new entries, a burst as one firing, and nothing on its
  first look. A 304 makes no firing.
- Neither reaches a private or local address. A 404 ends the watch with the reason; a 503
  is retried.
- A life that calls `WatchFeed` on `https://github.com/<owner>/<repo>/releases.atom` is
  woken by the next release.

## Risks

- **Noisy pages.** Rotating text inside the main content (dates, "trending" lists) makes a
  page fire on every look. The interval and the daily amount bound the cost. The agent
  can stop such a watch; a judge is the next step if that is not enough.
- **Some sites refuse programs** (403, bot checks), as for `WebFetch`. Such a watch ends
  with the reason rather than retrying forever.
- **State files outlive their watch:** a stopped watch's file stays (the module is not
  told of `TaskStop`). They are small; a later watch on the same address starts from it,
  which is what the agent would want.
- Unverified: whether GitHub's Atom feeds count toward any rate limit, and how often
  they refresh.

## Implementation plan

1. **Tests first,** in `tests/test_tools_web.py` with `httpx2.MockTransport` and a fake
   resolver (as the `WebFetch` tests do), with the check interval and the burst wait
   injected:
   - page: the first look records and gives nothing; a changed body gives a diff; a
     whitespace change gives nothing; nothing is marked seen before `confirm`; 404 ends
     the watch, 503 retries; a private address is refused;
   - feed: the first look gives nothing; new entries come in one firing after the burst
     wait; 304 and unchanged feeds give nothing; a non-feed body ends the watch;
   - two conversations watching one URL keep separate state; changing only
     `every_minutes` keeps it.
2. Add the `feedparser` dependency (6.0.14 on PyPI, Python >= 3.10).
3. In `web.py`: a `fetch_conditional` shared with `fetch`, then `watch_page` and
   `watch_feed` returning `ToolOutput` with `confirm` and `retry`; the specs; dispatch in
   `shell_files/__init__.py`.
4. `docs/`: the tools page lists both, with the GitHub feed addresses.
5. Move this note to `implemented/`. `SPEC.md` names no built-in tools, so it does not
   change.

## Open questions for the user

- Build it now, or wait for the life to want it? The logs show no demand yet.
- Default intervals of 60 minutes for pages and 30 for feeds: fine, or should the agent
  have to choose?
- Should a life get a starter set of feeds the user picks (as standing watches), or is
  every follow its own choice, as on Bilibili?
