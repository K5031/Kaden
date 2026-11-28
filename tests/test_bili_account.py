"""A Bilibili account of an instance's own: logging in by a QR code the user scans, the
login kept per data folder, following channels, and being woken by their new videos
(against a stand-in that answers as Bilibili does)."""

from __future__ import annotations

import asyncio
import base64
import json
import time
from pathlib import Path
from typing import Any

import httpx2
import pytest

from kaden.api import Image
from kaden.tools.shell_files.bili_account import NAMES, Account

SESSDATA = "secret-sessdata"


class Store:
    """The keychain, in memory."""

    def __init__(self) -> None:
        self.saved: dict[str, str] = {}

    def get(self, name: str) -> str | None:
        return self.saved.get(name)

    def set(self, name: str, value: str) -> None:
        self.saved[name] = value

    def delete(self, name: str) -> None:
        self.saved.pop(name, None)


class Bilibili:
    """Bilibili's web API as a logged-in user sees it, with a feed to add to."""

    def __init__(self) -> None:
        self.polls = [86101, 86090, 0]  # not scanned, scanned, logged in
        self.feed: list[dict[str, Any]] = []
        self.follows: dict[int, str] = {}
        self.refuse_follow = False
        self.asked: list[httpx2.Request] = []
        self.page = 20  # feed items per page
        self.fail_poll = 0  # polls to answer with a server error first
        self.fail_feed = 0  # feed reads to answer with a server error first
        self.messages: list[dict[str, Any]] = []  # the conversation with the user, oldest first
        self.sent: list[dict[str, str]] = []

    def video(self, n: int, author: str = "Nature Notes", mid: int = 77,
              at: int | None = None) -> None:
        self.feed.insert(0, {"id_str": str(1000 + n), "modules": {
            "module_author": {"name": author, "mid": mid,
                              "pub_ts": int(time.time()) + n if at is None else at},
            "module_dynamic": {"major": {"archive": {
                "bvid": f"BV1video{n:04d}", "title": f"Lyrebirds {n}",
                "duration_text": "05:12", "desc": "They copy chainsaws."}}}}})

    def say(self, sender: int, text: str, receiver: int | None = None) -> None:
        """A message in the conversation: from the life (42) to the user, or back."""
        self.messages.append({"msg_seqno": len(self.messages) + 1, "sender_uid": sender,
                              "receiver_id": receiver or (900 if sender == 42 else 42),
                              "msg_type": 1, "timestamp": 1_700_000_000 + len(self.messages),
                              "content": json.dumps({"content": text})})

    def answer(self, request: httpx2.Request) -> httpx2.Response:
        self.asked.append(request)
        path = request.url.path
        logged_in = SESSDATA in request.headers.get("cookie", "")
        if request.url.host == "www.bilibili.com":
            return httpx2.Response(200, text="<html></html>",
                                   headers={"set-cookie": "buvid3=visitor; Domain=.bilibili.com"})
        if path.endswith("/qrcode/generate"):
            return httpx2.Response(200, json={"code": 0, "data": {
                "url": "https://account.bilibili.com/h5/scan?key=k1", "qrcode_key": "k1"}})
        if path.endswith("/qrcode/poll"):
            if self.fail_poll:
                self.fail_poll -= 1
                return httpx2.Response(502)
            code = self.polls.pop(0) if len(self.polls) > 1 else self.polls[0]
            headers = [("set-cookie", f"SESSDATA={SESSDATA}; Domain=.bilibili.com; Path=/"),
                       ("set-cookie", "bili_jct=csrf123; Domain=.bilibili.com; Path=/"),
                       ("set-cookie", "DedeUserID=42; Domain=.bilibili.com; Path=/")
                       ] if code == 0 else []
            return httpx2.Response(200, headers=headers, json={"code": 0, "data": {
                "code": code, "message": "", "refresh_token": "r1" if code == 0 else ""}})
        if not logged_in:
            return httpx2.Response(200, json={"code": -101, "message": "账号未登录"})
        if path == "/x/web-interface/nav":
            return httpx2.Response(200, json={"code": 0, "data": {
                "isLogin": True, "uname": "Ahri", "mid": 42}})
        if path == "/x/polymer/web-dynamic/v1/feed/all":
            if self.fail_feed:
                self.fail_feed -= 1
                return httpx2.Response(502)
            start = int(request.url.params.get("offset") or 0)
            items = self.feed[start:start + self.page]
            more = start + self.page < len(self.feed)
            return httpx2.Response(200, json={"code": 0, "data": {
                "items": items, "has_more": more, "offset": str(start + self.page)}})
        if path == "/x/relation/followings":
            page, size = int(request.url.params["pn"]), int(request.url.params["ps"])
            follows = list(self.follows.items())[(page - 1) * size:page * size]
            return httpx2.Response(200, json={"code": 0, "data": {"total": len(self.follows),
                "list": [{"mid": m, "uname": n, "sign": ""} for m, n in follows]}})
        if path == "/web_im/v1/web_im/send_msg":
            form = dict(httpx2.QueryParams(request.content.decode()))
            if form.get("csrf") != "csrf123":
                return httpx2.Response(200, json={"code": -111, "message": "csrf 校验失败"})
            self.sent.append(form)
            self.say(42, json.loads(form["msg[content]"])["content"])
            return httpx2.Response(200, json={"code": 0, "message": "0", "data": {}})
        if path == "/session_svr/v1/session_svr/get_sessions":
            talkers: dict[int, dict[str, Any]] = {}
            for m in self.messages:
                talker = m["receiver_id"] if m["sender_uid"] == 42 else m["sender_uid"]
                talkers[talker] = m
            return httpx2.Response(200, json={"code": 0, "data": {"session_list": [
                {"talker_id": t, "session_type": 1, "last_msg": last,
                 "unread_count": int(last["sender_uid"] != 42)}
                for t, last in reversed(list(talkers.items()))]}})
        if path == "/x/web-interface/card":
            mid = int(request.url.params["mid"])
            return httpx2.Response(200, json={"code": 0, "data": {"card": {
                "mid": str(mid), "name": {900: "K5", 123: "a stranger"}.get(mid, f"user{mid}")}}})
        if path == "/svr_sync/v1/svr_sync/fetch_session_msgs":
            talker = int(request.url.params["talker_id"])
            mine = [m for m in self.messages if talker in (m["sender_uid"], m["receiver_id"])]
            return httpx2.Response(200, json={"code": 0, "data": {
                "messages": list(reversed(mine))[:20]}})  # newest first, as Bilibili gives them
        if path == "/x/relation/modify":
            form = dict(httpx2.QueryParams(request.content.decode()))
            if form.get("csrf") != "csrf123":
                return httpx2.Response(200, json={"code": -111, "message": "csrf 校验失败"})
            if self.refuse_follow:
                return httpx2.Response(200, json={"code": 22015, "message": "账号异常"})
            mid = int(form["fid"])
            if form["act"] == "1":
                self.follows[mid] = f"channel {mid}"
            else:
                self.follows.pop(mid, None)
            return httpx2.Response(200, json={"code": 0, "message": "0"})
        return httpx2.Response(404)


def account(folder: Path, store: Store, site: Bilibili, **kwargs: Any) -> Account:
    return Account(folder, transport=httpx2.MockTransport(site.answer), store=store,
                   poll_s=0.01, **kwargs)


async def logged_in(folder: Path, store: Store, site: Bilibili, **kwargs: Any) -> Account:
    acct = account(folder, store, site, **kwargs)
    await acct.run("BiliLogin", {})
    for _ in range(300):
        if "Logged in as Ahri" in (await acct.run("BiliLogin", {})).content:
            return acct
        await asyncio.sleep(0.01)
    raise AssertionError("never logged in")


def names(specs: Any) -> set[str]:
    return {s.name for s in specs}


async def test_logging_in_by_a_code_the_user_scans(tmp_path):
    store, site = Store(), Bilibili()
    acct = account(tmp_path / "life", store, site)
    assert names(await acct.specs()) == {"BiliLogin"}  # nothing of an account yet
    first = await acct.run("BiliLogin", {})
    [code] = first.attachments
    assert isinstance(code, Image) and code.media_type == "image/png"
    assert base64.b64decode(code.data).startswith(b"\x89PNG")
    assert "scan" in first.content.lower()
    for _ in range(300):
        said = (await acct.run("BiliLogin", {})).content
        if "Logged in as Ahri" in said:
            break
        await asyncio.sleep(0.01)
    assert "Logged in as Ahri" in said
    assert names(await acct.specs()) == NAMES  # the account's tools come with it
    [saved] = store.saved.values()
    assert SESSDATA in saved and SESSDATA not in first.content + said  # never in output
    await acct.aclose()


async def test_a_login_is_its_instances_own(tmp_path):
    store, site = Store(), Bilibili()
    life = await logged_in(tmp_path / "life", store, site)
    chats = account(tmp_path / "chats", store, site)
    assert names(await chats.specs()) == {"BiliLogin"}  # the user's chats: no account
    again = account(tmp_path / "life", store, site)  # the life after a restart
    assert "Logged in as Ahri" in (await again.run("BiliLogin", {})).content
    logged_out = await again.run("BiliLogin", {"log_out": True})
    assert "Logged out" in logged_out.content and not store.saved
    assert names(await again.specs()) == {"BiliLogin"}
    for each in (life, chats, again):
        await each.aclose()


async def test_an_expired_code_gives_a_new_one(tmp_path):
    store, site = Store(), Bilibili()
    site.polls = [86038]  # expired before it was scanned
    acct = account(tmp_path / "life", store, site)
    await acct.run("BiliLogin", {})
    await asyncio.sleep(0.05)
    again = await acct.run("BiliLogin", {})
    assert "expired" in again.content.lower() and len(again.attachments) == 1
    await acct.aclose()


async def test_following_a_channel_and_listing_follows(tmp_path):
    store, site = Store(), Bilibili()
    acct = await logged_in(tmp_path / "life", store, site)
    said = await acct.run("BiliFollow", {"channel": "https://space.bilibili.com/77?spm=x"})
    assert "Following" in said.content and site.follows == {77: "channel 77"}
    assert "channel 77" in (await acct.run("BiliFollowing", {})).content
    await acct.run("BiliFollow", {"channel": "77", "follow": False})
    assert site.follows == {}
    site.refuse_follow = True  # Bilibili's check against automated accounts
    refused = await acct.run("BiliFollow", {"channel": "77"})
    assert refused.is_error and "账号异常" in refused.content
    assert sum(r.url.path == "/x/relation/modify" for r in site.asked) == 3  # not retried
    await acct.aclose()


async def test_the_feed_says_whats_new_once_and_from_now_the_first_time(tmp_path):
    store, site = Store(), Bilibili()
    for n in range(15):
        site.video(n)
    acct = await logged_in(tmp_path / "life", store, site)
    first = (await acct.run("BiliFeed", {})).content
    assert "Lyrebirds 14" in first and "Lyrebirds 4" not in first  # the latest, not all
    assert "Nothing new" in (await acct.run("BiliFeed", {})).content
    site.video(15)
    later = (await acct.run("BiliFeed", {})).content
    assert "Lyrebirds 15" in later and "Lyrebirds 14" not in later
    assert "BV1video0015" in later and "Nature Notes (77)" in later
    restarted = account(tmp_path / "life", store, site)  # what was delivered stays delivered
    assert "Nothing new" in (await restarted.run("BiliFeed", {})).content
    await acct.aclose()
    await restarted.aclose()


async def test_watching_the_feed_gives_a_burst_in_one_delivered_once_confirmed(tmp_path):
    store, site = Store(), Bilibili()
    site.video(0)
    acct = await logged_in(tmp_path / "life", store, site, feed_check_s=0.02, batch_s=0.1)
    await acct.run("BiliFeed", {})  # the first look: from now on
    watching = asyncio.create_task(acct.run("BiliWatchFeed", {}))
    await asyncio.sleep(0.05)
    assert not watching.done()
    site.video(1)
    await asyncio.sleep(0.04)
    site.video(2)  # the same burst
    fired = await asyncio.wait_for(watching, 2)
    assert "Lyrebirds 1" in fired.content and "Lyrebirds 2" in fired.content
    # Not yet in the conversation: given again if asked before it is confirmed.
    assert "Lyrebirds 1" in (await acct.run("BiliWatchFeed", {})).content
    assert fired.confirm is not None
    fired.confirm()
    assert "Nothing new" in (await acct.run("BiliFeed", {})).content
    await acct.aclose()


def test_the_watches_are_watches_and_following_is_not_read_only(tmp_path):
    from kaden.tools.shell_files.bili_account import ACCOUNT_SPECS, MESSAGE_SPECS

    specs = {s.name: s for s in (*ACCOUNT_SPECS, *MESSAGE_SPECS)}
    assert specs["BiliWatchFeed"].watch and specs["BiliWatchMessages"].watch
    assert specs["BiliWatchFeed"].read_only and specs["BiliWatchMessages"].read_only
    assert not specs["BiliFeed"].watch
    from kaden.tools.shell_files.bili_account import LOGIN

    assert LOGIN.deferred  # named in every request, described only once wanted
    assert not specs["BiliFollow"].read_only and specs["BiliFollowing"].read_only
    # A message sent is the agent's last word: no recap of it follows.
    assert specs["BiliMessage"].ends_turn and not specs["BiliMessages"].ends_turn


@pytest.mark.parametrize("channel", ["77", "https://space.bilibili.com/77/video", "UID:77"])
async def test_a_channel_is_its_id_or_a_link_to_it(tmp_path, channel):
    store, site = Store(), Bilibili()
    acct = await logged_in(tmp_path / "life", store, site)
    await acct.run("BiliFollow", {"channel": channel})
    assert 77 in site.follows
    await acct.aclose()


async def test_the_built_in_tools_offer_the_account_of_their_instance(tmp_path):
    from types import SimpleNamespace

    from kaden.api import InstancePaths, ToolCall
    from kaden.tools.shell_files import ShellFilesTools, factory

    store, site = Store(), Bilibili()
    tools = ShellFilesTools(tmp_path / "work", account=account(tmp_path / "life", store, site))
    offered = {s.name for s in await tools.list_tools()}
    assert "BiliLogin" in offered and "BiliFeed" not in offered
    ctx = SimpleNamespace(call_id="c1", conversation_id="c", confined=False,
                          progress=lambda text: None)
    code = await tools.execute(ToolCall("c1", "BiliLogin", {}), ctx)
    assert len(code.attachments) == 1 and not code.is_error
    assert not await tools.confined(ToolCall("c2", "BiliFollow", {"channel": "77"}))
    assert await tools.confined(ToolCall("c3", "BiliFeed", {}))  # reading only
    bad = await tools.execute(ToolCall("c4", "BiliFollow", {"channel": "77", "x": 1}), ctx)
    assert bad.is_error  # checked against its spec as every tool is
    await tools.aclose()
    # The factory gives the module its instance's data folder.
    built = factory({"root": str(tmp_path / "work")}, InstancePaths(tmp_path / "life"))
    assert "BiliLogin" in {s.name for s in await built.list_tools()}
    await built.aclose()


async def test_an_instance_that_never_logged_in_never_asks_the_keychain(tmp_path):
    """Sub-sessions, a fleet's agents and tests list their tools often; only an instance
    that has logged in looks in the keychain."""

    class Untouchable(Store):
        def get(self, name: str) -> str | None:
            raise AssertionError("the keychain was asked")

    acct = account(tmp_path / "unit", Untouchable(), Bilibili())
    assert names(await acct.specs()) == {"BiliLogin"}
    await acct.aclose()
    store, site = Store(), Bilibili()
    life = await logged_in(tmp_path / "life", store, site)
    await life.run("BiliLogin", {"log_out": True})
    assert not list((tmp_path / "life").iterdir())  # logged out: no marker left
    await life.aclose()


async def test_a_keychain_fault_leaves_the_other_tools_and_says_logged_out(tmp_path):
    """A keychain that fails, or holds something unreadable, costs the account's tools,
    never the shell and files."""
    from kaden.tools.shell_files import ShellFilesTools

    class Broken(Store):
        def get(self, name: str) -> str | None:
            raise RuntimeError("org.freedesktop.DBus.Error.ServiceUnknown")

    folder = tmp_path / "life"
    folder.mkdir()
    (folder / "bilibili-account").write_text("x")  # it had logged in
    for store in (Broken(), Store()):
        store.saved = {}
        if not isinstance(store, Broken):
            from kaden.tools.shell_files.bili_account import login_name

            store.saved[login_name(folder)] = "{not json"
        acct = account(folder, store, Bilibili())
        assert names(await acct.specs()) == {"BiliLogin"}
        tools = ShellFilesTools(tmp_path / "work", account=account(folder, store, Bilibili()))
        assert {"Bash", "Read", "BiliLogin"} <= {s.name for s in await tools.list_tools()}
        await acct.aclose()
        await tools.aclose()


async def test_a_scan_survives_a_blip_and_a_lost_one_says_why(tmp_path):
    store, site = Store(), Bilibili()
    site.fail_poll = 2  # Bilibili stumbles twice while the user scans
    acct = await logged_in(tmp_path / "life", store, site)
    await acct.aclose()

    class Locked(Store):
        def set(self, name: str, value: str) -> None:
            raise RuntimeError("the keychain is locked")

    acct = account(tmp_path / "other", Locked(), Bilibili())
    await acct.run("BiliLogin", {})
    for _ in range(300):
        said = (await acct.run("BiliLogin", {})).content
        if "could not be kept" in said:
            break
        await asyncio.sleep(0.01)
    assert "could not be kept" in said and "the keychain is locked" in said
    await acct.aclose()


async def test_logging_out_while_a_login_is_being_kept_stays_logged_out(tmp_path):
    import threading

    release = threading.Event()
    saving = threading.Event()

    class Slow(Store):
        def set(self, name: str, value: str) -> None:
            saving.set()
            release.wait(5)
            super().set(name, value)

    store, site = Slow(), Bilibili()
    acct = account(tmp_path / "life", store, site)
    await acct.run("BiliLogin", {})
    for _ in range(300):
        if saving.is_set():
            break
        await asyncio.sleep(0.01)
    assert saving.is_set()
    out = asyncio.create_task(acct.run("BiliLogin", {"log_out": True}))
    await asyncio.sleep(0.05)
    release.set()
    await out
    assert not store.saved and not (tmp_path / "life" / "bilibili-account").exists()
    assert names(await acct.specs()) == {"BiliLogin"}
    await acct.aclose()


async def test_videos_are_told_apart_by_their_post_not_their_time(tmp_path):
    """Two posted in the same second, one listed late with an earlier time: each is
    delivered once; a deleted one does not bring old ones back."""
    store, site = Store(), Bilibili()
    now = int(time.time())
    site.video(0, at=now - 100)
    acct = await logged_in(tmp_path / "life", store, site)
    await acct.run("BiliFeed", {})
    site.video(1, at=now + 5)
    site.video(2, at=now + 5)  # the same second
    site.video(3, at=now - 1800)  # listed late, with an older time
    later = (await acct.run("BiliFeed", {})).content
    assert all(f"Lyrebirds {n}" in later for n in (1, 2, 3))
    site.feed.pop(0)  # the newest is deleted
    assert "Nothing new" in (await acct.run("BiliFeed", {})).content
    await acct.aclose()


async def test_a_busy_feed_is_read_past_its_first_page(tmp_path):
    store, site = Store(), Bilibili()
    site.page = 3
    site.video(0)
    acct = await logged_in(tmp_path / "life", store, site)
    await acct.run("BiliFeed", {})
    for n in range(1, 8):  # more new ones than a page holds
        site.video(n)
    later = (await acct.run("BiliFeed", {})).content
    assert all(f"Lyrebirds {n} (" in later for n in range(1, 8))
    await acct.aclose()


async def test_a_watch_rides_out_a_blip_and_keeps_its_own_videos(tmp_path):
    store, site = Store(), Bilibili()
    site.video(0)
    acct = await logged_in(tmp_path / "life", store, site, feed_check_s=0.02, batch_s=0.15)
    await acct.run("BiliFeed", {})
    site.fail_feed = 2  # Bilibili stumbles while it watches
    watching = asyncio.create_task(acct.run("BiliWatchFeed", {}))
    await asyncio.sleep(0.1)
    assert not watching.done()
    site.video(1)
    await asyncio.sleep(0.08)
    await acct.run("BiliFeed", {})  # a look of its own during the burst's wait
    fired = await asyncio.wait_for(watching, 2)
    assert not fired.is_error and "Lyrebirds 1" in fired.content
    await acct.aclose()


async def test_a_watch_whose_login_is_gone_cannot_pass(tmp_path):
    store, site = Store(), Bilibili()
    acct = await logged_in(tmp_path / "life", store, site, feed_check_s=0.02)
    site.feed = []
    for name in ("BiliWatchFeed", "BiliWatchMessages"):
        acct._login = {"SESSDATA": "gone"}  # Bilibili ended the login: its calls answer -101
        acct._retire()
        out = await asyncio.wait_for(acct.run(name, {}), 2)
        assert out.is_error and not out.retry and "stopped working" in out.content
    await acct.aclose()


@pytest.mark.parametrize("channel", ["12abc", "fluid 42", "0", "a cat video"])
async def test_what_is_not_a_channel_is_refused(tmp_path, channel):
    store, site = Store(), Bilibili()
    acct = await logged_in(tmp_path / "life", store, site)
    refused = await acct.run("BiliFollow", {"channel": channel})
    assert refused.is_error and not site.follows
    await acct.aclose()


async def test_a_phones_link_to_a_channel_is_followed(tmp_path):
    store, site = Store(), Bilibili()
    acct = await logged_in(tmp_path / "life", store, site)
    await acct.run("BiliFollow", {"channel": "https://m.bilibili.com/space/77"})
    assert 77 in site.follows
    await acct.aclose()


async def test_messages_come_with_a_login_and_the_inbox_is_watched_as_its_own(tmp_path):
    """Logged in, it can message and read messages; its inbox is watched for it, as its
    own time: anyone can write to an account, so a stranger's message must not spend
    beyond its daily amount."""
    store, site = Store(), Bilibili()
    acct = await logged_in(tmp_path / "a", store, site)
    assert {"BiliMessage", "BiliMessages", "BiliWatchMessages"} <= names(await acct.specs())
    [kept] = await acct.standing()
    assert kept.name == "BiliWatchMessages" and not kept.as_user
    signed_out = account(tmp_path / "c", Store(), site)
    assert names(await signed_out.specs()) == {"BiliLogin"} and not await signed_out.standing()
    for each in (acct, signed_out):
        await each.aclose()


async def test_a_message_goes_to_whoever_it_names(tmp_path):
    store, site = Store(), Bilibili()
    acct = await logged_in(tmp_path / "life", store, site)
    said = await acct.run("BiliMessage", {"to": "https://space.bilibili.com/900",
                                          "text": "I found a lyrebird that copies chainsaws"})
    assert not said.is_error and "Sent to K5 (900)" in said.content
    [form] = site.sent
    assert form["msg[receiver_id]"] == "900" and form["msg[sender_uid]"] == "42"
    assert json.loads(form["msg[content]"]) == {
        "content": "I found a lyrebird that copies chainsaws"}
    refused = await acct.run("BiliMessage", {"to": "K5", "text": "hi"})
    assert refused.is_error and len(site.sent) == 1  # a UID or a link, not a name
    await acct.aclose()


async def test_messages_from_anyone_are_read_once_and_its_own_not(tmp_path):
    store, site = Store(), Bilibili()
    site.say(900, "hey Ahri")
    acct = await logged_in(tmp_path / "life", store, site)
    first = (await acct.run("BiliMessages", {})).content
    assert "K5 (900): hey Ahri" in first
    assert "Nothing new" in (await acct.run("BiliMessages", {})).content
    await acct.run("BiliMessage", {"to": "900", "text": "hi K5"})  # its own: not news to it
    site.say(900, "what are you up to?")
    site.say(123, "do you like foxes?", receiver=42)
    later = (await acct.run("BiliMessages", {})).content
    assert "K5 (900): what are you up to?" in later
    assert "a stranger (123): do you like foxes?" in later and "hi K5" not in later
    await acct.aclose()


async def test_watching_the_inbox_fires_when_someone_writes(tmp_path):
    store, site = Store(), Bilibili()
    site.follows[900] = "K5"
    acct = await logged_in(tmp_path / "life", store, site, message_check_s=0.02)
    await acct.run("BiliMessages", {})
    watching = asyncio.create_task(acct.run("BiliWatchMessages", {}))
    await asyncio.sleep(0.05)
    assert not watching.done()
    site.say(900, "ok I'm back")
    fired = await asyncio.wait_for(watching, 2)
    assert "ok I'm back" in fired.content and fired.confirm is not None
    fired.confirm()
    assert "Nothing new" in (await acct.run("BiliMessages", {})).content
    await acct.aclose()


def read_conversations(site: Bilibili) -> list[int]:
    return [int(r.url.params["talker_id"]) for r in site.asked
            if r.url.path.endswith("/fetch_session_msgs")]


async def test_by_default_only_accounts_it_follows_wake_it(tmp_path):
    """A stranger's conversation is not even read: it waits for BiliMessages, and the
    next firing says it is there."""
    store, site = Store(), Bilibili()
    site.follows[900] = "K5"
    acct = await logged_in(tmp_path / "life", store, site, message_check_s=0.02)
    await acct.run("BiliMessages", {})
    watching = asyncio.create_task(acct.run("BiliWatchMessages", {}))
    site.say(123, "buy followers cheap")
    await asyncio.sleep(0.1)
    assert not watching.done() and 123 not in read_conversations(site)
    site.say(900, "you there?")
    fired = await asyncio.wait_for(watching, 2)
    assert "you there?" in fired.content and "buy followers" not in fired.content
    assert "1 more conversation" in fired.content
    fired.confirm()
    assert "buy followers cheap" in (await acct.run("BiliMessages", {})).content
    await acct.aclose()


async def test_from_anyone_a_stranger_wakes_it_and_from_no_one_nothing_does(tmp_path):
    store, site = Store(), Bilibili()
    acct = await logged_in(tmp_path / "a", store, site, message_check_s=0.02, wake="anyone")
    await acct.run("BiliMessages", {})
    watching = asyncio.create_task(acct.run("BiliWatchMessages", {}))
    site.say(123, "hello stranger")
    assert "hello stranger" in (await asyncio.wait_for(watching, 2)).content
    quiet = await logged_in(tmp_path / "b", Store(), site, wake="nobody")
    assert await quiet.standing() == []
    assert "BiliMessages" in names(await quiet.specs())  # it can still read them
    for each in (acct, quiet):
        await each.aclose()


async def test_whom_it_follows_is_read_in_pages_and_kept_a_while(tmp_path):
    store, site = Store(), Bilibili()
    for mid in range(1000, 1120):
        site.follows[mid] = f"channel {mid}"
    site.follows[900] = "K5"  # on the third page
    acct = await logged_in(tmp_path / "life", store, site, message_check_s=0.02)
    await acct.run("BiliMessages", {})
    site.say(900, "page three")
    fired = await asyncio.wait_for(acct.run("BiliWatchMessages", {}), 2)
    assert "page three" in fired.content
    fired.confirm()
    pages = len([r for r in site.asked if r.url.path == "/x/relation/followings"])
    site.say(900, "again")
    await asyncio.wait_for(acct.run("BiliWatchMessages", {}), 2)
    assert len([r for r in site.asked if r.url.path == "/x/relation/followings"]) == pages
    await acct.aclose()


def test_the_built_in_tools_declare_whose_messages_wake_it(tmp_path):
    from kaden.api import InstancePaths
    from kaden.tools.shell_files import factory

    [wake] = factory.user_config  # type: ignore[attr-defined]
    assert wake.name == "bilibili_wake" and wake.type == "choice"
    assert wake.default == "follows" and set(wake.choices) == {"follows", "anyone", "nobody"}
    paths = InstancePaths(data_dir=tmp_path)
    tools = factory({"root": str(tmp_path), "bilibili_wake": "nobody"}, paths)
    assert tools._account is not None and tools._account._wake == "nobody"


def history(site: Bilibili) -> None:
    """An inbox with years in it: follow notes and alerts from many accounts."""
    for n in range(14):
        site.say(5000 + n, f"thanks for the follow #{n}")
    site.say(900, "hey Ahri")


async def test_the_first_look_counts_the_whole_inbox_as_read(tmp_path):
    store, site = Store(), Bilibili()
    history(site)
    acct = await logged_in(tmp_path / "life", store, site)
    first = (await acct.run("BiliMessages", {})).content
    assert "hey Ahri" in first and "#0" not in first  # the latest few
    assert "Nothing new" in (await acct.run("BiliMessages", {})).content  # the rest too
    await acct.aclose()


async def test_a_first_watch_wakes_it_only_for_what_comes_after(tmp_path):
    store, site = Store(), Bilibili()
    history(site)
    site.follows[900] = "K5"
    acct = await logged_in(tmp_path / "life", store, site, message_check_s=0.02,
                           wake="anyone")
    watching = asyncio.create_task(acct.run("BiliWatchMessages", {}))
    await asyncio.sleep(0.1)
    assert not watching.done()  # what was there is not news
    site.say(900, "you there?")
    fired = await asyncio.wait_for(watching, 2)
    assert "you there?" in fired.content
    assert "hey Ahri" not in fired.content and "thanks for the follow" not in fired.content
    await acct.aclose()


async def test_a_first_watch_of_the_feed_wakes_it_only_for_what_comes_after(tmp_path):
    """What the feed holds when it first watches is not news."""
    store, site = Store(), Bilibili()
    for n in range(15):
        site.video(n, at=int(time.time()) - 86_400 * (15 - n))  # the last two weeks
    acct = await logged_in(tmp_path / "life", store, site, feed_check_s=0.02, batch_s=0.05)
    watching = asyncio.create_task(acct.run("BiliWatchFeed", {}))
    await asyncio.sleep(0.1)
    assert not watching.done()
    site.video(15)
    fired = await asyncio.wait_for(watching, 2)
    assert "Lyrebirds 15" in fired.content and "Lyrebirds 14" not in fired.content
    await acct.aclose()


async def test_a_new_follows_old_videos_are_not_new(tmp_path):
    """Following a channel brings its past uploads into the feed: not posted since the
    last look, so not news. One posted while Kaden was closed still is."""
    store, site = Store(), Bilibili()
    site.video(0)
    acct = await logged_in(tmp_path / "life", store, site, feed_check_s=0.02, batch_s=0.05)
    await acct.run("BiliFeed", {})
    site.video(1, author="A new follow", mid=88, at=int(time.time()) - 5 * 86_400)
    assert "Nothing new" in (await acct.run("BiliFeed", {})).content
    watching = asyncio.create_task(acct.run("BiliWatchFeed", {}))
    site.video(2, author="A new follow", mid=88, at=int(time.time()) - 6 * 86_400)
    await asyncio.sleep(0.1)
    assert not watching.done()
    site.video(3, author="A new follow", mid=88)  # posted now
    fired = await asyncio.wait_for(watching, 2)
    assert "Lyrebirds 3" in fired.content
    assert "Lyrebirds 1" not in fired.content and "Lyrebirds 2" not in fired.content
    await acct.aclose()


async def test_the_inbox_is_read_often_while_talking_and_seldom_when_quiet(tmp_path):
    store, site = Store(), Bilibili()
    site.follows[900] = "K5"
    acct = await logged_in(tmp_path / "life", store, site, message_check_s=10.0,
                           message_active_s=0.02)
    await acct.run("BiliMessages", {})
    quiet = asyncio.create_task(acct.run("BiliWatchMessages", {}))
    await asyncio.sleep(0.1)  # it has looked, and waits long
    site.say(900, "are you there?")
    await asyncio.sleep(0.2)
    assert not quiet.done()  # quiet: read seldom
    quiet.cancel()
    await acct.run("BiliMessages", {})
    await acct.run("BiliMessage", {"to": "900", "text": "yes!"})  # a conversation now
    talking = asyncio.create_task(acct.run("BiliWatchMessages", {}))
    await asyncio.sleep(0.1)
    site.say(900, "oh hi")
    fired = await asyncio.wait_for(talking, 1)  # read often
    assert "oh hi" in fired.content
    await acct.aclose()


async def test_a_login_that_stopped_working_is_forgotten_and_no_longer_watched(tmp_path):
    """Bilibili ended the login: the inbox watch is not started again each cycle, and the
    next BiliLogin gives a new code at once, saying why."""
    store, site = Store(), Bilibili()
    acct = await logged_in(tmp_path / "life", store, site)
    assert await acct.standing()
    acct._login = {"SESSDATA": "gone"}  # Bilibili answers -101 to it
    acct._retire()
    out = await asyncio.wait_for(acct.run("BiliWatchMessages", {}), 2)
    assert out.is_error and not out.retry
    assert await acct.standing() == [] and not store.saved
    assert names(await acct.specs()) == {"BiliLogin"}
    again = await acct.run("BiliLogin", {})
    assert "stopped working" in again.content and again.attachments
    await acct.aclose()
