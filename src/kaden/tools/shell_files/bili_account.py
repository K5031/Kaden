"""A Bilibili account of an instance's own: logging in by a QR code the user scans with the
Bilibili app, following channels, and the feed of their new videos. The login is kept in
the system keychain under a name made from the instance's data folder, so it is that
instance's alone (the life's, when the life logs in). Login cookies never appear in what
a tool returns. The visitor tools (bilibili.py) do not use the login."""

from __future__ import annotations

import asyncio
import base64
import contextlib
import io
import json
import logging
import math
import re
import time
import urllib.parse
import uuid
from pathlib import Path
from typing import Any, Protocol

import httpx2
import keyring
import keyring.errors
import segno

from kaden.api import KEYCHAIN_SERVICE, Image, StandingWatch, ToolOutput, ToolSpec
from kaden.tools.shell_files.bilibili import API, HEADERS, TIMEOUT, BiliError

PASSPORT = "https://passport.bilibili.com/x/passport-login/web/qrcode"
VC = "https://api.vc.bilibili.com"  # private messages
CODE_S = 180.0  # how long a login code can be scanned
POLL_S = 2.0
FEED_CHECK_S = 600.0  # while waiting for new videos: how often the feed is read
BATCH_S = 300.0  # after the first new video, how long to wait for more of the same burst
FIRST_LOOK = 10  # videos shown the first time the feed is read
FEED_PAGES = 3  # read while a whole page is new, at most
DELIVERED_KEPT = 1000  # posts remembered as delivered, the newest
FEED_FILE = "bilibili-feed.json"  # in the data folder: the posts delivered, and when
# A video is new when it was not delivered and was posted after the last look, less this
# much: Bilibili may list a video a while after its time. A new follow's past uploads are
# older than that, and are not news.
LATE_S = 3600.0
MESSAGE_CHECK_S = 30.0  # while watching the inbox and no one is talking: how often it is read
MESSAGE_ACTIVE_S = 5.0  # how often while a conversation is going
ACTIVE_FOR_S = 300.0  # a conversation is going this long after a message either way
MESSAGES_FILE = "bilibili-messages.json"  # in the data folder: the last delivered, by sender
FIRST_MESSAGES = 10  # messages shown the first time
FOLLOWS_S = 600.0  # whom it follows, read again after this
FOLLOWS_PAGE, FOLLOWS_PAGES = 50, 40  # accounts per page; pages read at most
# Whose messages wake it (the tool setting bilibili_wake), and their labels.
WAKE = {"follows": "From followed accounts", "anyone": "From anyone",
        "nobody": "From no one"}
# In the data folder while it has a login (no secret in it): only then is the keychain
# asked, so the many instances that never log in (sub-sessions, a fleet's agents) never do.
MARKER = "bilibili-account"
COOKIES = ("SESSDATA", "bili_jct", "DedeUserID", "DedeUserID__ckMd5", "sid", "buvid3")
SCANNED, EXPIRED = 86090, 86038
_ID = re.compile(r"(?:uid\s*[:：]?\s*)?([1-9]\d{0,15})", re.IGNORECASE)
_SPACE_PATH = re.compile(r"/(?:space/)?([1-9]\d{0,15})(?:/.*)?")

logger = logging.getLogger(__name__)

LOGIN = ToolSpec(
    name="BiliLogin",
    description=(
        "Your own Bilibili account. Without a login it gives a QR code, shown to the user, "
        "to scan with the Bilibili app on the account's phone; call it again to see whether "
        "it worked. With one it says which account it is. log_out forgets the login. Once "
        "logged in, you can follow channels and read their new videos."),
    parameters={"type": "object", "properties": {"log_out": {"type": "boolean"}},
                "additionalProperties": False},
    read_only=False, deferred=True)  # a one-off: named, and described once it is wanted
_CHANNEL_ARG = {"type": "string", "description": (
    "The channel: its uploader id (BiliVideo shows it), or a link to its space page")}
ACCOUNT_SPECS: tuple[ToolSpec, ...] = (
    ToolSpec(
        name="BiliFollow",
        description=("Follow a Bilibili channel with your account, or with follow false, "
                     "stop following it. Others can see whom an account follows."),
        parameters={"type": "object", "properties": {
            "channel": _CHANNEL_ARG, "follow": {"type": "boolean"}},
            "required": ["channel"], "additionalProperties": False},
        read_only=False, deferred=True),
    ToolSpec(
        name="BiliFollowing",
        description="The Bilibili channels your account follows.",
        parameters={"type": "object", "properties": {}, "additionalProperties": False},
        read_only=True, deferred=True),
    ToolSpec(
        name="BiliFeed",
        description=("New videos from the channels your account follows, since you last "
                     "looked (the first time, the latest few)."),
        parameters={"type": "object", "properties": {}, "additionalProperties": False},
        read_only=True),
    ToolSpec(
        name="BiliWatchFeed",
        description=(
            "Watch the channels your account follows: each time they post, you are told what "
            "is new (a burst of posts together), until you stop it."),
        parameters={"type": "object", "properties": {}, "additionalProperties": False},
        read_only=True, timeout=math.inf, watch=True),
)
ACCOUNT_NAMES = frozenset(s.name for s in ACCOUNT_SPECS)
# Private messages, with anyone; the account's inbox is watched for the life while it is
# logged in, as its own time (anyone can write to an account).
MESSAGE_SPECS: tuple[ToolSpec, ...] = (
    ToolSpec(
        name="BiliMessage",
        description=("Send a private message on Bilibili, to an account by its UID or a link "
                     "to its space page."),
        parameters={"type": "object", "properties": {
            "to": {"type": "string", "description": "Their UID, or a link to their space page"},
            "text": {"type": "string", "minLength": 1, "maxLength": 1000}},
            "required": ["to", "text"], "additionalProperties": False},
        read_only=False),
    ToolSpec(
        name="BiliMessages",
        description=("Private messages to your Bilibili account since you last looked, with "
                     "who sent them (the first time, the latest few)."),
        parameters={"type": "object", "properties": {}, "additionalProperties": False},
        read_only=True),
    ToolSpec(
        name="BiliWatchMessages",
        description=("Watch your Bilibili account's private messages. It is kept running "
                     "while you are logged in: you are told when someone writes."),
        parameters={"type": "object", "properties": {}, "additionalProperties": False},
        read_only=True, timeout=math.inf, watch=True),
)
MESSAGE_NAMES = frozenset(s.name for s in MESSAGE_SPECS)
NAMES = ACCOUNT_NAMES | MESSAGE_NAMES | {LOGIN.name}


class LoginGone(BiliError):
    """Bilibili no longer takes the login."""


class Store(Protocol):
    """Where the login is kept; blocking."""

    def get(self, name: str) -> str | None: ...

    def set(self, name: str, value: str) -> None: ...

    def delete(self, name: str) -> None: ...


class Keychain:
    """The system keychain, under Kaden's service."""

    def get(self, name: str) -> str | None:
        return keyring.get_password(KEYCHAIN_SERVICE, name)

    def set(self, name: str, value: str) -> None:
        keyring.set_password(KEYCHAIN_SERVICE, name, value)

    def delete(self, name: str) -> None:
        with contextlib.suppress(keyring.errors.PasswordDeleteError):
            keyring.delete_password(KEYCHAIN_SERVICE, name)


def login_name(data_dir: Path) -> str:
    """The keychain name of the Bilibili login of the instance in `data_dir`."""
    return f"bilibili-login:{data_dir.expanduser().resolve()}"


class Account:
    """The account tools of the instance in `data_dir`. `transport` and `store` are
    replaced in tests, and so are the waits."""

    def __init__(self, data_dir: Path, *, transport: httpx2.AsyncBaseTransport | None = None,
                 store: Store | None = None,
                 poll_s: float = POLL_S, code_s: float = CODE_S,
                 feed_check_s: float = FEED_CHECK_S, batch_s: float = BATCH_S,
                 message_check_s: float = MESSAGE_CHECK_S,
                 message_active_s: float = MESSAGE_ACTIVE_S, wake: str = "follows") -> None:
        if wake not in WAKE:
            raise ValueError(f"bilibili_wake is one of {', '.join(WAKE)}, not {wake!r}")
        self._wake = wake
        self._follows: tuple[float, set[int]] | None = None  # when read, and whom
        self._data_dir, self._name = data_dir, login_name(data_dir)
        self._names: dict[int, str] = {}  # accounts' names, by UID, as read
        self._message_check_s, self._message_active_s = message_check_s, message_active_s
        self._talked_at = float("-inf")  # the last message sent or read, either way
        self._transport = transport
        self._store = store or Keychain()
        self._poll_s, self._code_s = poll_s, code_s
        self._feed_check_s, self._batch_s = feed_check_s, batch_s
        self._login: dict[str, str] | None = None
        self._loaded = False
        self._client: httpx2.AsyncClient | None = None  # the account's, with its login
        self._retired: list[httpx2.AsyncClient] = []  # closed at the end: calls may be in them
        self._saving = asyncio.Lock()  # a login kept, or forgotten, one at a time
        self._generation = 0  # moves on at each log out: a login kept after it is undone
        self._pending: asyncio.Task[None] | None = None  # a code waiting to be scanned
        self._pending_state = 0
        self._pending_since = 0.0
        self._outcome = ""  # why the last code did not log in, said with the next one

    async def standing(self) -> list[StandingWatch]:
        """The account's inbox, watched while logged in, as the life's own time, unless
        no one's messages wake it."""
        if self._wake == "nobody" or not await self._logged_in():
            return []
        return [StandingWatch("BiliWatchMessages")]

    async def specs(self) -> tuple[ToolSpec, ...]:
        if not await self._logged_in():
            return (LOGIN,)
        return (LOGIN, *ACCOUNT_SPECS, *MESSAGE_SPECS)

    async def run(self, name: str, args: dict[str, Any]) -> ToolOutput:
        try:
            match name:
                case "BiliLogin":
                    return await self.log_in(args)
                case "BiliFollow":
                    return ToolOutput(await self.follow(args))
                case "BiliFollowing":
                    return ToolOutput(await self.following())
                case "BiliMessage":
                    return ToolOutput(await self.message(args))
                case "BiliMessages":
                    return ToolOutput(await self.messages(args))
                case "BiliWatchFeed":
                    return await self.watch_feed()
                case "BiliWatchMessages":
                    return await self.watch_messages()
                case _:
                    return ToolOutput(await self.feed(args))
        except BiliError as e:
            return ToolOutput(str(e), is_error=True)

    # ── Logging in ────────────────────────────────────────────────────────

    async def log_in(self, args: dict[str, Any]) -> ToolOutput:
        if args.get("log_out"):
            await self._log_out()
            return ToolOutput("Logged out of Bilibili; the login is forgotten.")
        if await self._logged_in():
            try:
                me = await self._call("GET", f"{API}/x/web-interface/nav")
            except LoginGone:
                pass  # forgotten: a new code follows
            else:
                return ToolOutput(f"Logged in as {me.get('uname')} (id {me.get('mid')}).")
        if self._pending is not None and not self._pending.done():
            left = max(0, round(self._code_s - (time.monotonic() - self._pending_since)))
            if self._pending_state == SCANNED:
                return ToolOutput("The code was scanned; waiting for the login to be "
                                  f"confirmed in the Bilibili app ({left} s left).")
            return ToolOutput(f"Waiting for the code to be scanned ({left} s left).")
        why, self._outcome = self._outcome, ""
        return await self._new_code(why)

    async def _new_code(self, why: str) -> ToolOutput:
        async with self._visitor() as client:
            body = (await client.get(f"{PASSPORT}/generate")).json()
        if body.get("code") != 0:
            raise BiliError(f"Bilibili refused a login code: {body.get('message')}")
        url, key = body["data"]["url"], body["data"]["qrcode_key"]
        picture = io.BytesIO()
        segno.make(url, error="m").save(picture, kind="png", scale=8, border=4)
        self._pending_state, self._pending_since = 0, time.monotonic()
        self._pending = asyncio.create_task(self._wait_for_scan(key, self._generation))
        minutes = round(self._code_s / 60)
        return ToolOutput(
            f"{why}Here is a login code, shown to the user: they scan it with the Bilibili "
            f"app, switched to the account, and confirm, within {minutes} minutes. Call "
            "BiliLogin again to see whether it worked.",
            attachments=[Image("image/png", base64.b64encode(picture.getvalue()).decode(),
                               "bilibili-login.png")])

    async def _wait_for_scan(self, key: str, generation: int) -> None:
        """Poll until the code is scanned and confirmed, expires, or runs out; a poll that
        fails is tried again. What happened is said with the next BiliLogin."""
        deadline = time.monotonic() + self._code_s
        async with self._visitor() as client:
            while time.monotonic() < deadline:
                try:
                    response = await client.get(f"{PASSPORT}/poll", params={"qrcode_key": key})
                    response.raise_for_status()
                    data = response.json().get("data") or {}
                except (httpx2.HTTPError, ValueError):
                    await asyncio.sleep(self._poll_s)
                    continue
                state = data.get("code")
                if state == 0:
                    login = {c.name: c.value for c in client.cookies.jar if c.name in COOKIES}
                    login["refresh_token"] = data.get("refresh_token", "")
                    # Kept whole even if this task is cancelled meanwhile: a log out waits
                    # for it, then undoes it.
                    await asyncio.shield(asyncio.ensure_future(self._keep(login, generation)))
                    return
                if state == EXPIRED:
                    break
                self._pending_state = state
                await asyncio.sleep(self._poll_s)
        self._outcome = "The last code expired before it was scanned. "

    async def _keep(self, login: dict[str, str], generation: int) -> None:
        async with self._saving:
            if generation != self._generation:  # logged out meanwhile
                return
            try:
                await asyncio.to_thread(self._write_login, json.dumps(login))
            except Exception as e:  # said with the next BiliLogin
                logger.exception("the Bilibili login could not be kept")
                self._outcome = f"The code was scanned, but the login could not be kept: {e}. "
                return
            self._login, self._loaded = login, True

    async def _log_out(self) -> None:
        self._generation += 1
        if self._pending is not None:
            self._pending.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._pending
            self._pending = None
        async with self._saving:  # after a login being kept, if one is
            await asyncio.to_thread(self._drop)
            self._login, self._loaded = None, True
            self._retire()

    async def _forget(self, login: dict[str, str] | None) -> None:
        """Bilibili no longer takes `login`: it is dropped, so its tools and the inbox
        watch go with it, unless another was kept meanwhile. A code being scanned stays."""
        async with self._saving:
            if self._login is not login or login is None:
                return
            try:
                await asyncio.to_thread(self._drop)
            except Exception:  # forgotten here all the same: Bilibili refuses it
                logger.exception("the Bilibili login could not be forgotten")
            self._login = None
            self._retire()
            self._outcome = "The login stopped working. "

    async def _logged_in(self) -> bool:
        if not self._loaded:
            try:
                saved = await asyncio.to_thread(self._saved)
                self._login = json.loads(saved) if saved else None
            except Exception:  # a keychain that fails or holds nonsense: no login
                logger.exception("the Bilibili login could not be read")
                self._login = None
            self._loaded = True
        return self._login is not None

    def _saved(self) -> str | None:
        return self._store.get(self._name) if (self._data_dir / MARKER).exists() else None

    def _write_login(self, login: str) -> None:
        self._store.set(self._name, login)
        self._data_dir.mkdir(parents=True, exist_ok=True)
        (self._data_dir / MARKER).write_text("Logged in to Bilibili; the login is in the "
                                             "system keychain.\n")

    def _drop(self) -> None:
        self._store.delete(self._name)
        (self._data_dir / MARKER).unlink(missing_ok=True)

    def _retire(self) -> None:
        if self._client is not None:
            self._retired.append(self._client)
            self._client = None

    # ── What the account does ─────────────────────────────────────────────

    async def follow(self, args: dict[str, Any]) -> str:
        mid = _channel(args["channel"])
        follow = args.get("follow", True)
        await self._account()  # logged in, with the account's csrf token
        assert self._login is not None
        await self._call("POST", f"{API}/x/relation/modify", data={
            "fid": mid, "act": 1 if follow else 2, "re_src": 11,
            "csrf": self._login.get("bili_jct", "")})
        return (f"Following channel {mid}." if follow else f"No longer following channel {mid}.")

    async def following(self) -> str:
        await self._account()
        assert self._login is not None
        data = await self._call("GET", f"{API}/x/relation/followings", params={
            "vmid": self._login.get("DedeUserID", ""), "pn": 1, "ps": 50})
        channels = data.get("list") or []
        if not channels:
            return "Your account follows no channels yet."
        lines = [f"{c['uname']} ({c['mid']})" + (f": {c['sign']}" if c.get("sign") else "")
                 for c in channels]
        return f"Following {data.get('total', len(lines))}:\n" + "\n".join(lines)

    async def feed(self, args: dict[str, Any]) -> str:
        return await self._look()

    async def watch_feed(self) -> ToolOutput:
        """Until a followed channel posts, then the burst's wait; what it gives is marked
        delivered only once confirmed (ToolOutput.confirm), so a crash gives it again. The
        first time, what the feed holds is noted, not given: it is not news."""
        try:
            if (record := await asyncio.to_thread(self._read_feed)) is None:
                looked, there = time.time(), await self._videos()
                await asyncio.to_thread(self._remember, there, looked)
                record = ([v["id"] for v in there], looked)
            known, since = record
            while not await self._new_since(known, since):
                await asyncio.sleep(self._feed_check_s)
            await asyncio.sleep(self._batch_s)  # the rest of the burst
            looked, videos = time.time(), await self._videos()
        except LoginGone as e:
            return ToolOutput(str(e), is_error=True, retry=False)
        # Against what was delivered when it began: a look meanwhile does not empty it.
        return ToolOutput(_listed("New videos", _fresh(videos, known, since)),
                          confirm=lambda: self._remember(videos, looked))

    async def _new_since(self, known: list[str], since: float) -> bool:
        """Something new since the wait began; a failure to read the feed is tried again
        at the next check, unless the login is gone."""
        try:
            return bool(_fresh(await self._videos(), known, since))
        except LoginGone:
            raise
        except (BiliError, httpx2.HTTPError, ValueError) as e:
            logger.warning("reading the Bilibili feed failed, trying again later: %s", e)
            return False

    async def _look(self) -> str:
        """What is new, which then is delivered; the first time, the latest few."""
        looked, videos = time.time(), await self._videos()
        record = await asyncio.to_thread(self._read_feed)
        if record is None:
            shown = videos[:FIRST_LOOK]
            heading = "The latest videos from the channels you follow"
        else:
            shown, heading = _fresh(videos, *record), "New videos"
        await asyncio.to_thread(self._remember, videos, looked)
        return _listed(heading, shown)

    def _remember(self, videos: list[dict[str, Any]], looked: float) -> None:
        """These posts as delivered, with those before, the newest kept, and when they were
        looked at. Blocks."""
        known, since = self._read_feed() or ([], 0.0)
        ids = [v["id"] for v in videos]
        merged = ids + [i for i in known if i not in set(ids)]
        self._data_dir.mkdir(parents=True, exist_ok=True)
        (self._data_dir / FEED_FILE).write_text(json.dumps(
            {"delivered": merged[:DELIVERED_KEPT], "since": max(since, looked)}))

    async def _videos(self) -> list[dict[str, Any]]:
        """The feed's videos, newest first: its first page, and the next while a whole page
        is new."""
        known = set((await asyncio.to_thread(self._read_feed) or ([], 0.0))[0])
        videos: list[dict[str, Any]] = []
        offset = ""
        for _ in range(FEED_PAGES):
            data = await self._call("GET", f"{API}/x/polymer/web-dynamic/v1/feed/all",
                                    params={"type": "video", "timezone_offset": -480,
                                            **({"offset": offset} if offset else {})})
            page = [v for item in data.get("items") or [] if (v := _video(item))]
            videos += page
            offset = str(data.get("offset") or "")
            if not data.get("has_more") or not offset or not known \
                    or any(v["id"] in known for v in page):
                break
        return videos

    def _read_feed(self) -> tuple[list[str], float] | None:
        """The posts delivered and when the feed was last looked at; None before a look."""
        try:
            data = json.loads((self._data_dir / FEED_FILE).read_text())
            return [str(i) for i in data["delivered"]], float(data["since"])
        except (OSError, ValueError, KeyError, TypeError):
            return None

    # ── Private messages ──────────────────────────────────────────────────

    async def message(self, args: dict[str, Any]) -> str:
        to = _channel(args["to"])
        await self._account()
        assert self._login is not None
        csrf = self._login.get("bili_jct", "")
        await self._call("POST", f"{VC}/web_im/v1/web_im/send_msg", data={
            "msg[sender_uid]": self._login.get("DedeUserID", ""),
            "msg[receiver_id]": to, "msg[receiver_type]": 1, "msg[msg_type]": 1,
            "msg[msg_status]": 0, "msg[content]": json.dumps({"content": args["text"]}),
            "msg[timestamp]": int(time.time()), "msg[new_face_version]": 0,
            "msg[dev_id]": str(uuid.uuid4()).upper(), "from_firework": 0, "build": 0,
            "mobi_app": "web", "csrf_token": csrf, "csrf": csrf})
        self._talked_at = time.monotonic()
        return f"Sent to {await self._name_of(to)} ({to})."

    async def messages(self, args: dict[str, Any]) -> str:
        marks = await asyncio.to_thread(self._read_message_marks)
        written = await self._incoming(marks)
        if marks is None:  # the first look: the whole inbox is read from now
            await asyncio.to_thread(self._write_message_marks, await self._inbox_marks())
        elif written:
            await asyncio.to_thread(self._mark_messages, written)
        if not written:
            return "Nothing new in your Bilibili messages."
        self._talked_at = time.monotonic()
        return await self._messages_text(written)

    async def watch_messages(self) -> ToolOutput:
        """Until someone writes; marked delivered only once confirmed (ToolOutput.confirm)."""
        marks = await asyncio.to_thread(self._read_message_marks)
        waiting: list[int] = []
        try:
            if marks is None:  # the first look: what is there already is not news
                marks = await self._inbox_marks()
                await asyncio.to_thread(self._write_message_marks, marks)
            while not (written := await self._incoming_or_none(marks, waiting)):
                await asyncio.sleep(self._message_wait())
            text = await self._messages_text(written)
        except LoginGone as e:
            return ToolOutput(str(e), is_error=True, retry=False)
        if waiting:
            n = len(waiting)
            text += (f"\n{n} more conversation{'s' if n > 1 else ''} waiting, from accounts "
                     "you don't follow (BiliMessages reads them).")
        self._talked_at = time.monotonic()
        return ToolOutput(text, confirm=lambda: self._mark_messages(written))

    def _message_wait(self) -> float:
        """Read often while a conversation is going, seldom when no one is talking."""
        talking = time.monotonic() - self._talked_at < ACTIVE_FOR_S
        return self._message_active_s if talking else self._message_check_s

    async def _incoming_or_none(self, marks: dict[str, int] | None, waiting: list[int]
                                ) -> list[dict[str, Any]]:
        """As _incoming, from those whose messages wake it; `waiting` is set to the others
        with something new. A failure to read is tried again at the next check, unless the
        login is gone."""
        try:
            only = await self._followed() if self._wake == "follows" else None
            return await self._incoming(marks, only, waiting)
        except LoginGone:
            raise
        except (BiliError, httpx2.HTTPError, ValueError) as e:
            logger.warning("reading Bilibili messages failed, trying again later: %s", e)
            return []

    async def _inbox_marks(self) -> dict[str, int]:
        """Each conversation's latest message number: the whole inbox, as read."""
        return {str(talker): seq for talker, seq, _ in await self._sessions()}

    async def _sessions(self) -> list[tuple[int, int, int]]:
        """The inbox's conversations: (talker, latest message number, unread count)."""
        data = await self._call("GET", f"{VC}/session_svr/v1/session_svr/get_sessions",
                                params={"session_type": 1, "group_fold": 1, "unfollow_fold": 0,
                                        "sort_rule": 2, "build": 0, "mobi_app": "web"})
        return [(int(s.get("talker_id") or 0), int((s.get("last_msg") or {}).get("msg_seqno")
                                                   or 0), int(s.get("unread_count") or 0))
                for s in data.get("session_list") or [] if s.get("talker_id")]

    async def _followed(self) -> set[int]:
        """The UIDs the account follows, read again once FOLLOWS_S old."""
        if self._follows is not None and time.monotonic() - self._follows[0] < FOLLOWS_S:
            return self._follows[1]
        await self._account()
        assert self._login is not None
        found: set[int] = set()
        for page in range(1, FOLLOWS_PAGES + 1):
            data = await self._call("GET", f"{API}/x/relation/followings", params={
                "vmid": self._login.get("DedeUserID", ""), "pn": page, "ps": FOLLOWS_PAGE})
            listed = data.get("list") or []
            found.update(int(c["mid"]) for c in listed if c.get("mid"))
            if len(listed) < FOLLOWS_PAGE or len(found) >= int(data.get("total") or 0):
                break
        self._follows = (time.monotonic(), found)
        return found

    async def _incoming(self, marks: dict[str, int] | None, only: set[int] | None = None,
                        waiting: list[int] | None = None) -> list[dict[str, Any]]:
        """Others' text messages past each sender's mark, oldest first; the first time (no
        marks), the latest few. With `only`, conversations with others are not read: those
        with something new are put in `waiting`."""
        await self._account()
        assert self._login is not None
        me = int(self._login.get("DedeUserID") or 0)
        written = []
        for talker, last, unread in await self._sessions():
            mark = (marks or {}).get(str(talker), 0)
            if last <= mark:
                continue
            if only is not None and talker not in only:
                if waiting is not None and unread and talker not in waiting:
                    waiting.append(talker)
                continue
            got = await self._call("GET", f"{VC}/svr_sync/v1/svr_sync/fetch_session_msgs",
                                   params={"talker_id": talker, "session_type": 1, "size": 20})
            for m in got.get("messages") or []:
                if m.get("sender_uid") in (me, None) or m.get("msg_type") != 1 \
                        or int(m.get("msg_seqno") or 0) <= mark:
                    continue
                try:
                    text = json.loads(m.get("content") or "{}").get("content", "")
                except ValueError:
                    continue
                written.append({"from": talker, "seq": int(m["msg_seqno"]),
                                "at": int(m.get("timestamp") or 0), "text": text})
        written.sort(key=lambda m: (m["at"], m["seq"]))
        return written[-FIRST_MESSAGES:] if marks is None else written

    async def _messages_text(self, written: list[dict[str, Any]]) -> str:
        lines = []
        for m in written:
            when = time.strftime("%Y-%m-%d %H:%M", time.localtime(m["at"]))
            lines.append(f"- {when}, {await self._name_of(m['from'])} ({m['from']}): {m['text']}")
        return "New messages on Bilibili:\n" + "\n".join(lines)

    async def _name_of(self, uid: int) -> str:
        if uid not in self._names:
            try:
                card = await self._call("GET", f"{API}/x/web-interface/card", params={"mid": uid})
                self._names[uid] = (card.get("card") or {}).get("name") or f"UID {uid}"
            except (BiliError, httpx2.HTTPError, ValueError):
                return f"UID {uid}"
        return self._names[uid]

    def _mark_messages(self, written: list[dict[str, Any]]) -> None:
        """These as delivered, each sender's mark moved past them. Blocks."""
        marks = self._read_message_marks() or {}
        for m in written:
            marks[str(m["from"])] = max(marks.get(str(m["from"]), 0), m["seq"])
        self._write_message_marks(marks)

    def _read_message_marks(self) -> dict[str, int] | None:
        try:
            marks = json.loads((self._data_dir / MESSAGES_FILE).read_text())["delivered"]
        except (OSError, ValueError, KeyError, TypeError):
            return None
        return {str(k): int(v) for k, v in marks.items()} if isinstance(marks, dict) else None

    def _write_message_marks(self, marks: dict[str, int]) -> None:
        self._data_dir.mkdir(parents=True, exist_ok=True)
        (self._data_dir / MESSAGES_FILE).write_text(json.dumps({"delivered": marks}))

    # ── Calls ─────────────────────────────────────────────────────────────

    async def _call(self, method: str, url: str, *, params: dict[str, Any] | None = None,
                    data: dict[str, Any] | None = None) -> Any:
        """Bilibili's answer's data; its refusal, as it is, otherwise: never retried."""
        client = await self._account()
        login = self._login
        response = await client.request(method, url, params=params, data=data)
        response.raise_for_status()
        body = response.json()
        if body.get("code") == -101:
            await self._forget(login)
            raise LoginGone("The Bilibili login stopped working: BiliLogin gives a new code "
                            "to log in again.")
        if body.get("code") != 0:
            raise BiliError(f"Bilibili refused: {body.get('message') or body.get('code')}")
        return body.get("data") or {}

    async def _account(self) -> httpx2.AsyncClient:
        if not await self._logged_in():
            raise BiliError("Not logged in to Bilibili: BiliLogin gives a code to log in.")
        if self._client is None:
            assert self._login is not None
            cookies = {k: v for k, v in self._login.items() if k in COOKIES}
            self._client = httpx2.AsyncClient(transport=self._transport, headers=HEADERS,
                                              cookies=cookies, timeout=TIMEOUT)
        return self._client

    def _visitor(self) -> httpx2.AsyncClient:
        """A visitor's session for one login: its cookies become the login's."""
        return httpx2.AsyncClient(transport=self._transport, headers=HEADERS,
                                  follow_redirects=True, timeout=TIMEOUT)

    async def aclose(self) -> None:
        if self._pending is not None:
            self._pending.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._pending
        self._retire()
        for client in self._retired:
            await client.aclose()
        self._retired.clear()


def _video(item: dict[str, Any]) -> dict[str, Any] | None:
    modules = item.get("modules") or {}
    author = modules.get("module_author") or {}
    archive = ((modules.get("module_dynamic") or {}).get("major") or {}).get("archive")
    if not archive:
        return None
    return {"id": str(item.get("id_str") or archive.get("bvid", "")),
            "title": archive.get("title", ""), "length": archive.get("duration_text", ""),
            "author": author.get("name", ""), "mid": author.get("mid", ""),
            "at": float(author.get("pub_ts") or 0),
            "bvid": archive.get("bvid", ""), "desc": (archive.get("desc") or "").strip()[:200]}


def _fresh(videos: list[dict[str, Any]], known: list[str], since: float
           ) -> list[dict[str, Any]]:
    """Those not delivered and posted since the last look (LATE_S)."""
    seen = set(known)
    return [v for v in videos if v["id"] not in seen and v["at"] >= since - LATE_S]


def _listed(heading: str, videos: list[dict[str, Any]]) -> str:
    if not videos:
        return "Nothing new from the channels you follow."
    return f"{heading}:\n" + "\n".join(
        f"- {v['title']} ({v['length']}), by {v['author']} ({v['mid']}): {v['bvid']}"
        + (f"\n  {v['desc']}" if v["desc"] else "") for v in videos)


def _channel(raw: str) -> int:
    """A channel's uploader id, from the id itself (`UID:` before it too) or a link to its
    space page (`space.bilibili.com/<id>`, `m.bilibili.com/space/<id>`)."""
    text = raw.strip()
    if found := _ID.fullmatch(text):
        return int(found.group(1))
    link = urllib.parse.urlsplit(text if "//" in text else f"https://{text}")
    host = (link.hostname or "").lower()
    if host in ("space.bilibili.com", "m.bilibili.com") and \
            (found := _SPACE_PATH.fullmatch(link.path)) and \
            (host == "space.bilibili.com" or link.path.startswith("/space/")):
        return int(found.group(1))
    raise BiliError(f"Not a channel: {raw!r}. Give its uploader id or its space link.")
