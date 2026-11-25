# Agent Note: Watching Bilibili

Status: implemented — Kaden's built-in tools read Bilibili as a viewer does, without logging in: a video's details and subtitles, its danmaku, its comments, and frames of it to look at

## Problem

[Continuous](2026-11-23-continuous.md) has its own time and
little to do with it: the web search and a page reader. The user wants it to watch videos,
as people on Bilibili set agents to ("养鱼", raising a fish), and to have that ability, not
a routine for using it.

## Decision

Four tools in the built-in Tools module (`tools/shell_files/bilibili.py`), beside
`WebSearch` and `WebFetch`, for every agent:

| Tool | What it gives |
|---|---|
| `BiliVideo` | A video's details (title, uploader, description, length, counts, parts) and its subtitles where it has any open to visitors |
| `BiliDanmaku` | Its danmaku, each at its moment in the video |
| `BiliComments` | Its top comments, with their likes and top replies |
| `BiliFrames` | Pictures of it at the moments asked for, as images |

- **No search of its own.** Bilibili answers a visitor's search with its check against
  automated visitors (`v_voucher`, a captcha); passing it means posing as a browser
  (forged fingerprints and tickets), which Kaden does not do. Videos are found with
  `WebSearch` (`site:bilibili.com …`) and links the user gives; the tools take a BV id or
  any link to a video.
- **As a visitor:** no login, so nothing it does can act as the user, and subtitles that
  Bilibili gives logged-in viewers only are missing. A life may get an account of its
  own: [a Bilibili account of its own](../proposed/2026-11-25-a-bilibili-account-of-its-own.md).
- **Light:** Bilibili's own web API over the HTTP client Kaden has (`httpx2`), with its
  request signing (WBI) and a visitor's cookie; frames are cut from the video's stream by
  the system's `ffmpeg`, offered only where it is installed. No new library.
- Read-only network calls: the permission system treats them as the web tools are.

## Alternatives considered

- **Agent-Reach** (114k stars, a command-line tool over yt-dlp). Widely maintained and
  broad, but Bilibili as text only, no frames; the user chose Kaden's own.
- **bilibili-skill and other tool servers.** Complete, but unvetted (no users) or heavy,
  and a tool server a life would need its own settings for.
- **yt-dlp in the shell.** Nothing to build, but no search, no comments, and every agent
  working it out each time.

## Consequences

- Any agent, a life included, can take in a Bilibili video as a viewer does:
  what is said where subtitles are open, the danmaku, the comments, and pictures of it.
- Bilibili changes its API and its checks against automated visitors; when it does,
  these tools break until Kaden follows, as there is no wider project to fix them.
- Many videos have no subtitles open to visitors, and finding videos goes through a web
  search rather than Bilibili's own.
