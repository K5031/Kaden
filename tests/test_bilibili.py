"""Watching Bilibili as a visitor: a video's details and subtitles, its danmaku, its
comments and frames of it, through Bilibili's own web API (here a stand-in that answers as
it does)."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import parse_qs

import httpx2
import pytest

from kaden.tools.shell_files.bilibili import NAMES, Bilibili, BiliError

BV = "BV1xx411c7mD"
VIEW = {"code": 0, "data": {
    "bvid": BV, "aid": 2, "cid": 62131, "title": "字幕君交流场所", "duration": 2055,
    "pubdate": 1246376402, "desc": "The oldest video on the site.",
    "owner": {"name": "碧诗", "mid": 2},
    "stat": {"view": 5633609, "like": 279154, "danmaku": 129082, "reply": 89787},
    "pages": [{"page": 1, "cid": 62131, "part": "Part one", "duration": 2055},
              {"page": 2, "cid": 70000, "part": "Part two", "duration": 60}]}}
DANMAKU = ('<?xml version="1.0" encoding="UTF-8"?><i>'
           '<d p="65.2,1,25,16777215,1,0,a,1,10">later one</d>'
           '<d p="0.0,5,25,15138834,1,0,b,2,10">注意：最古老的视频</d>'
           '<d p="3.5,1,25,16777215,1,0,c,3,10">hello &amp; welcome</d></i>')
REPLIES = {"code": 0, "data": {"replies": [
    {"member": {"uname": "碧诗"}, "content": {"message": "wwwww"}, "like": 56032,
     "replies": [{"member": {"uname": "a fan"}, "content": {"message": "so old!"},
                  "like": 12}]},
    {"member": {"uname": "古关忧"}, "content": {"message": "最古的av号"}, "like": 55116,
     "replies": None}]}}


def bilibili(subtitles: list[dict[str, Any]] | None = None, *, view: dict[str, Any] = VIEW,
             asked: list[httpx2.Request] | None = None, **kwargs: Any) -> Bilibili:
    """Bilibili's API as a visitor sees it, answering from the data above."""
    subtitle_body = {"body": [{"from": 3.0, "to": 5.0, "content": "Hello, everyone"},
                              {"from": 61.5, "to": 63.0, "content": "That is all"}]}

    def answer(request: httpx2.Request) -> httpx2.Response:
        if asked is not None:
            asked.append(request)
        path = request.url.path
        if request.url.host == "www.bilibili.com" and path == "/":
            return httpx2.Response(200, text="<html></html>",
                                   headers={"set-cookie": "buvid3=visitor; Domain=.bilibili.com"})
        if request.url.host == "b23.tv":
            return httpx2.Response(302, headers={
                "location": f"https://www.bilibili.com/video/{BV}?share_source=copy"})
        if path == "/x/web-interface/nav":
            return httpx2.Response(200, json={"code": -101, "data": {"wbi_img": {
                "img_url": "https://i0.hdslb.com/bfs/wbi/7cd084941338484aae1ad9425b84077c.png",
                "sub_url": "https://i0.hdslb.com/bfs/wbi/4932caff0ff746eab6f01bf08b70ac45.png"}}})
        if path == "/x/web-interface/view":
            return httpx2.Response(200, json=view)
        if path == "/x/player/wbi/v2":
            return httpx2.Response(200, json={"code": 0, "data": {"subtitle": {
                "subtitles": subtitles or []}}})
        if path == "/subtitle.json":
            return httpx2.Response(200, json=subtitle_body)
        if path == "/x/v1/dm/list.so":
            return httpx2.Response(200, text=DANMAKU)
        if path == "/x/v2/reply/wbi/main":
            return httpx2.Response(200, json=REPLIES)
        if path == "/x/player/wbi/playurl":
            return httpx2.Response(200, json={"code": 0, "data": {"durl": [
                {"url": "https://upos.example/video.mp4", "length": 2055000}]}})
        return httpx2.Response(404)

    return Bilibili(transport=httpx2.MockTransport(answer), **kwargs)


async def test_a_video_is_read_with_its_details_and_subtitles():
    asked: list[httpx2.Request] = []
    tools = bilibili([{"lan": "zh-CN", "lan_doc": "中文",
                       "subtitle_url": "//aisubtitle.example/subtitle.json"}], asked=asked)
    text = await tools.run("BiliVideo", {"video": f"https://www.bilibili.com/video/{BV}/"})
    for part in ("字幕君交流场所", "By 碧诗 (uploader id 2)", "34:15", "5,633,609 views", "The oldest video",
                 "1. Part one", "2. Part two", "Subtitles (中文)", "[00:03] Hello, everyone",
                 "[01:01] That is all"):
        assert part in text, part
    signed = [r for r in asked if r.url.path == "/x/player/wbi/v2"]
    assert signed and "w_rid" in parse_qs(signed[0].url.query.decode())  # Bilibili's signing


async def test_a_video_without_subtitles_open_to_visitors_says_so():
    text = await bilibili().run("BiliVideo", {"video": BV})
    assert "No subtitles open to visitors" in text


async def test_a_short_link_and_a_later_part_are_followed():
    asked: list[httpx2.Request] = []
    tools = bilibili(asked=asked)
    await tools.run("BiliDanmaku", {"video": "https://b23.tv/abc123", "part": 2})
    danmaku = [r for r in asked if r.url.path == "/x/v1/dm/list.so"]
    assert danmaku[0].url.params["oid"] == "70000"  # part two's


async def test_a_missing_video_is_an_error():
    tools = bilibili(view={"code": -404, "message": "啥都木有"})
    with pytest.raises(BiliError, match="not found"):
        await tools.run("BiliVideo", {"video": BV})
    with pytest.raises(BiliError, match="BV id or a link"):
        await tools.run("BiliVideo", {"video": "a cat video"})


async def test_danmaku_come_at_their_moments_and_a_stretch_of_them():
    tools = bilibili()
    text = await tools.run("BiliDanmaku", {"video": BV})
    lines = text.splitlines()
    assert lines[0].startswith("3 danmaku")
    assert lines[1:] == ["[00:00] 注意：最古老的视频", "[00:03] hello & welcome",
                         "[01:05] later one"]
    stretch = await tools.run("BiliDanmaku", {"video": BV, "from_s": 60, "to_s": 120})
    assert stretch.splitlines()[1:] == ["[01:05] later one"]


async def test_the_top_comments_come_with_likes_and_replies():
    text = await bilibili().run("BiliComments", {"video": BV})
    assert "碧诗 (56,032 likes): wwwww" in text
    assert "  ↳ a fan (12 likes): so old!" in text
    assert "古关忧 (55,116 likes): 最古的av号" in text


async def test_frames_are_pictures_at_the_moments_asked(tmp_path):
    cut: list[tuple[str, float]] = []

    async def fake_cut(url: str, second: float, out: Path) -> None:
        cut.append((url, second))
        out.write_bytes(b"\xff\xd8 jpeg " + str(second).encode())

    tools = bilibili(ffmpeg="/usr/bin/ffmpeg", cut=fake_cut)
    output = await tools.frames({"video": BV, "seconds": [30, 90.5]})
    assert [second for _url, second in cut] == [30, 90.5]
    assert all(url == "https://upos.example/video.mp4" for url, _second in cut)
    assert [a.media_type for a in output.attachments] == ["image/jpeg", "image/jpeg"]
    assert "00:30" in output.content and "01:30" in output.content
    with pytest.raises(BiliError, match="34:15 long"):
        await tools.frames({"video": BV, "seconds": [9999]})


def test_frames_are_offered_only_with_ffmpeg():
    assert "BiliFrames" in {s.name for s in Bilibili(ffmpeg="/usr/bin/ffmpeg").specs}
    without = {s.name for s in Bilibili(ffmpeg=None).specs}
    assert without == NAMES - {"BiliFrames"}


async def test_the_built_in_tools_offer_them(tmp_path):
    from types import SimpleNamespace

    from kaden.api import ToolCall
    from kaden.tools.shell_files import ShellFilesTools

    def context(call_id: str) -> Any:
        return SimpleNamespace(call_id=call_id, conversation_id="c", confined=True,
                               progress=lambda text: None)

    tools = ShellFilesTools(tmp_path, bilibili_tools=bilibili(ffmpeg=None))
    names = {spec.name for spec in await tools.list_tools()}
    assert {"BiliVideo", "BiliDanmaku", "BiliComments"} <= names
    call = ToolCall("c1", "BiliComments", {"video": BV})
    assert await tools.confined(call)  # read-only, like the web tools
    output = await tools.execute(call, context("c1"))
    assert "wwwww" in output.content and not output.is_error
    missing = await tools.execute(ToolCall("c2", "BiliVideo", {"video": "nothing"}),
                                  context("c2"))
    assert missing.is_error and "BV id" in missing.content
