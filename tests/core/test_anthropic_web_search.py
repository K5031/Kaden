"""Claude's own web search: offered as a tool Anthropic runs, recorded as ServerToolUse,
replayed to Anthropic, continued when the API pauses a turn, charged per search."""

from __future__ import annotations

import json

import pytest

from kaden.api import (
    InferenceRequest,
    Message,
    Role,
    ServerToolUse,
    Text,
    TextDelta,
    ToolResult,
    ToolSpec,
    ToolStatus,
)
from tests.conformance.core_suite import WEATHER_TOOL, collect, user
from tests.core.stub import Reply
from tests.core.test_anthropic import (
    AnthropicDriver,
    block,
    finish,
    message_start,
    text_block,
)

OPUS = "claude-opus-5-5"
RESULT = [{"type": "web_search_result", "title": "2026 World Championship",
           "url": "https://en.wikipedia.org/wiki/Worlds_2026", "encrypted_content": "E1",
           "page_age": None}]


def search_blocks(start: int, query: str = "worlds 2026 final") -> list:
    return [
        *block(start, {"type": "server_tool_use", "id": "srvtoolu_1", "name": "web_search",
                       "input": {}},
               [{"type": "input_json_delta", "partial_json": json_query(query)}]),
        *block(start + 1, {"type": "web_search_tool_result", "tool_use_id": "srvtoolu_1",
                           "content": RESULT}, []),
    ]


def json_query(query: str) -> str:
    return '{"query": "' + query + '"}'


def cited_text(index: int) -> list:
    citation = {"type": "web_search_result_location", "url": RESULT[0]["url"],
                "title": RESULT[0]["title"], "encrypted_index": "I1",
                "cited_text": "Gen.G and BLG"}
    return block(index, {"type": "text", "text": ""},
                 [{"type": "text_delta", "text": "Gen.G meets BLG."},
                  {"type": "citations_delta", "citation": citation}])


def searched(stop: str = "end_turn", searches: int = 1) -> list:
    events = finish(stop)
    events[0][1]["usage"]["server_tool_use"] = {"web_search_requests": searches}
    return events


def request(model: str = OPUS, messages=None) -> InferenceRequest:
    search = ToolSpec("WebSearch", "search", {"type": "object"})
    return InferenceRequest(model, "", messages or [user("Who plays the final?")],
                            [search, WEATHER_TOOL])


@pytest.fixture
def driver() -> AnthropicDriver:
    return AnthropicDriver(models={OPUS: {"input_usd_per_mtok": 4, "output_usd_per_mtok": 20}})


async def test_web_search_is_offered_as_anthropics_own(driver):
    assert [t.name for t in await driver.core.list_tools()] == ["WebSearch"]
    driver.stub.queue(Reply(events=[message_start(), *text_block(0, ["ok"]), *finish("end_turn")]))
    await collect(driver.core, request())
    tools = driver.stub.bodies[-1]["tools"]
    assert tools[0] == {"type": "web_search_20260209", "name": "web_search", "max_uses": 5}
    assert tools[1]["name"] == WEATHER_TOOL.name  # Kaden's own tools stay as they are
    driver.stub.queue(Reply(events=[message_start(), *text_block(0, ["ok"]), *finish("end_turn")]))
    await collect(driver.core, request("claude-haiku-5-5"))
    assert driver.stub.bodies[-1]["tools"][0]["type"] == "web_search_20250305"


async def test_a_search_becomes_a_step_with_its_sources(driver):
    driver.stub.queue(Reply(events=[message_start(), *search_blocks(0), *cited_text(2),
                                    *searched()]))
    events = await collect(driver.core, request())
    completed = events[-1]
    [use] = [b for b in completed.message.blocks if isinstance(b, ServerToolUse)]
    assert use.name == "web_search" and use.input == {"query": "worlds 2026 final"}
    assert use.summary == "Searched the web for “worlds 2026 final”" and not use.is_error
    assert "2026 World Championship" in use.content and RESULT[0]["url"] in use.content
    texts = [b.text for b in completed.message.blocks if isinstance(b, Text)]
    assert texts[0] == "Gen.G meets BLG."
    assert texts[-1] == f"\n\nSources: [2026 World Championship]({RESULT[0]['url']})"
    # What was streamed as text is the text of the message, the sources line included.
    assert "".join(e.text for e in events if isinstance(e, TextDelta)) == "".join(texts)
    assert completed.usage.extra_cost_usd == pytest.approx(0.01)


async def test_a_paused_turn_is_continued(driver):
    driver.stub.queue(Reply(events=[message_start(), *search_blocks(0), *searched("pause_turn")]))
    driver.stub.queue(Reply(events=[message_start(), *cited_text(0), *searched("end_turn", 0)]))
    events = await collect(driver.core, request())
    assert len(driver.stub.bodies) == 2
    resumed = driver.stub.bodies[1]["messages"][-1]
    assert resumed["role"] == "assistant"
    assert [c["type"] for c in resumed["content"]] == ["server_tool_use",
                                                       "web_search_tool_result"]
    blocks = events[-1].message.blocks
    assert any(isinstance(b, ServerToolUse) for b in blocks)
    assert any(isinstance(b, Text) and b.text == "Gen.G meets BLG." for b in blocks)


async def test_a_search_is_replayed_to_anthropic_and_as_text_to_another_model(driver):
    driver.stub.queue(Reply(events=[message_start(), *search_blocks(0), *cited_text(2),
                                    *searched()]))
    answer = (await collect(driver.core, request()))[-1].message
    later = [user("Who plays the final?"), answer, user("And who is favoured?")]
    # The search went with the turn that made it; a later question gets it as text.
    for model, expected in ((OPUS, ["text", "text", "text"]),
                            ("claude-sonnet-5-5", ["text", "text", "text"])):
        driver.stub.queue(Reply(events=[message_start(), *text_block(0, ["ok"]),
                                        *finish("end_turn")]))
        await collect(driver.core, request(model, later))
        replayed = driver.stub.bodies[-1]["messages"][1]
        assert [c["type"] for c in replayed["content"]] == expected, model
    as_text = driver.stub.bodies[-1]["messages"][1]["content"][0]["text"]
    assert "Searched the web for" in as_text and RESULT[0]["url"] in as_text


async def test_a_failed_search_is_an_error_step(driver):
    error = {"type": "web_search_tool_result_error", "error_code": "too_many_requests"}
    events = [message_start(),
              *block(0, {"type": "server_tool_use", "id": "srvtoolu_1", "name": "web_search",
                         "input": {"query": "x"}}, []),
              *block(1, {"type": "web_search_tool_result", "tool_use_id": "srvtoolu_1",
                         "content": error}, []),
              *text_block(2, ["Search failed."]), *searched()]
    driver.stub.queue(Reply(events=events))
    [use] = [b for b in (await collect(driver.core, request()))[-1].message.blocks
             if isinstance(b, ServerToolUse)]
    assert use.is_error and "too_many_requests" in use.content


async def test_switched_off_search_falls_back_to_kadens(driver):
    driver.stub.queue(Reply(status=400, json_body={"type": "error", "error": {
        "type": "invalid_request_error",
        "message": "Web search is not enabled for your organization."}}))
    driver.stub.queue(Reply(events=[message_start(), *text_block(0, ["ok"]), *finish("end_turn")]))
    await collect(driver.core, request())
    assert all(t.get("type") != "web_search_20260209" for t in driver.stub.bodies[-1]["tools"])
    assert await driver.core.list_tools() == []


async def test_a_continuation_that_is_refused_is_sent_again_as_a_continuation(driver):
    """The fallback redoes the request it stands for: a paused turn sent back to be
    continued goes back with what it had so far, not from the start."""
    driver.stub.queue(Reply(events=[message_start(), *search_blocks(0), *searched("pause_turn")]))
    driver.stub.queue(Reply(status=400, json_body={"type": "error", "error": {
        "type": "invalid_request_error",
        "message": "Web search is not enabled for your organization."}}))
    driver.stub.queue(Reply(events=[message_start(), *text_block(0, ["ok"]), *finish("end_turn")]))
    events = await collect(driver.core, request())
    assert len(driver.stub.bodies) == 3
    again = driver.stub.bodies[2]["messages"]
    assert again[-1]["role"] == "assistant"  # what the turn had when it was paused
    assert again[:-1] == driver.stub.bodies[0]["messages"]
    assert any(isinstance(b, Text) and b.text == "ok" for b in events[-1].message.blocks)


def tool_results() -> Message:
    """The results of the client tools the answer also called: the same turn goes on."""
    return Message("r", Role.TOOL, (ToolResult("toolu_1", ToolStatus.OK, "done"),), 0.0)


def filtered_search() -> list:
    """As Opus 5.5 sends it: code (dynamic filtering) calls the search, and the code's
    result comes after the search's."""
    return [
        *block(0, {"type": "server_tool_use", "id": "srvtoolu_code", "name": "code_execution",
                   "input": {"code": "search()"}}, []),
        *block(1, {"type": "server_tool_use", "id": "srvtoolu_1", "name": "web_search",
                   "input": {"query": "worlds 2026 final"},
                   "caller": {"type": "code_execution_20260120", "tool_id": "srvtoolu_code"}},
               []),
        *block(2, {"type": "web_search_tool_result", "tool_use_id": "srvtoolu_1",
                   "content": RESULT}, []),
        *block(3, {"type": "code_execution_tool_result", "tool_use_id": "srvtoolu_code",
                   "content": {"type": "code_execution_result", "stdout": "filtered",
                               "stderr": "", "return_code": 0, "content": []}}, []),
        *cited_text(4),
    ]


async def test_filtered_searches_are_replayed_in_the_order_they_came(driver):
    driver.stub.queue(Reply(events=[message_start(), *filtered_search(), *searched()]))
    answer = (await collect(driver.core, request()))[-1].message
    shown = [b for b in answer.blocks if isinstance(b, ServerToolUse)]
    assert [b.name for b in shown] == ["web_search"]  # the filtering code is not a step
    driver.stub.queue(Reply(events=[message_start(), *text_block(0, ["ok"]), *finish("end_turn")]))
    await collect(driver.core, request(OPUS, [user("q"), answer, tool_results()]))
    replayed = driver.stub.bodies[-1]["messages"][1]["content"]
    assert [c.get("id") or c.get("tool_use_id") or c["type"] for c in replayed][:4] == [
        "srvtoolu_code", "srvtoolu_1", "srvtoolu_1", "srvtoolu_code"]


async def test_searches_the_api_will_not_take_back_go_back_as_text(driver):
    driver.stub.queue(Reply(events=[message_start(), *filtered_search(), *searched()]))
    answer = (await collect(driver.core, request()))[-1].message
    driver.stub.queue(Reply(status=400, json_body={"type": "error", "error": {
        "type": "invalid_request_error", "message": "source tool `srvtoolu_x` not found"}}))
    driver.stub.queue(Reply(events=[message_start(), *text_block(0, ["ok"]), *finish("end_turn")]))
    events = await collect(driver.core, request(OPUS, [user("q"), answer, tool_results()]))
    assert events[-1].message.blocks[0].text == "ok"
    retried = driver.stub.bodies[-1]["messages"][1]["content"]
    assert retried[0]["type"] == "text" and "Searched the web for" in retried[0]["text"]
    # The same turn goes on: what was refused is not sent as it came a second time.
    sent = len(driver.stub.bodies)
    driver.stub.queue(Reply(events=[message_start(), *text_block(0, ["ok"]), *finish("end_turn")]))
    await collect(driver.core, request(OPUS, [user("q"), answer, tool_results()]))
    assert len(driver.stub.bodies) == sent + 1
    again = driver.stub.bodies[-1]["messages"][1]["content"]
    assert again[0]["type"] == "text" and "Searched the web for" in again[0]["text"]


async def test_another_rejection_while_replaying_searches_is_reported_as_it_is(driver):
    """Only a 400 about the replayed blocks is answered by sending them as text: any
    other one is the request's own error, reported from the request that got it."""
    from kaden.api import CoreError

    driver.stub.queue(Reply(events=[message_start(), *filtered_search(), *searched()]))
    answer = (await collect(driver.core, request()))[-1].message
    driver.stub.queue(Reply(status=400, json_body={"type": "error", "error": {
        "type": "invalid_request_error", "message": "max_tokens: must be at least 1"}}))
    sent = len(driver.stub.bodies)
    with pytest.raises(CoreError, match="max_tokens"):
        await collect(driver.core, request(OPUS, [user("q"), answer, tool_results()]))
    assert len(driver.stub.bodies) == sent + 1


def thought(index: int, text: str, signature: str) -> list:
    return block(index, {"type": "thinking", "thinking": "", "signature": ""},
                 [{"type": "thinking_delta", "thinking": text},
                  {"type": "signature_delta", "signature": signature}])


async def test_thinking_after_a_search_goes_when_the_search_goes_back_as_text(driver):
    """A reply can think, search and think again. The second thought is bound to the
    search as it came: once a later question gets the search as text, that thought would
    be rejected ("bound to a different conversation"), and is left out. The first, with
    nothing changed before it, stays."""
    driver.stub.queue(Reply(events=[
        message_start(), *thought(0, "Search first.", "sig-before"), *search_blocks(1),
        *thought(3, "They agree.", "sig-after"), *cited_text(4), *searched()]))
    answer = (await collect(driver.core, request()))[-1].message

    def sent() -> list[str]:
        replayed = driver.stub.bodies[-1]["messages"][1]["content"]
        return [c.get("signature") or c["type"] for c in replayed]

    driver.stub.queue(Reply(events=[message_start(), *text_block(0, ["ok"]), *finish("end_turn")]))
    await collect(driver.core, request(OPUS, [user("Who plays the final?"), answer,
                                              tool_results()]))  # the same turn: as it came
    assert sent()[:4] == ["sig-before", "server_tool_use", "web_search_tool_result",
                          "sig-after"] and set(sent()[4:]) == {"text"}
    driver.stub.queue(Reply(events=[message_start(), *text_block(0, ["ok"]), *finish("end_turn")]))
    await collect(driver.core, request(OPUS, [user("Who plays the final?"), answer,
                                              user("And who is favoured?")]))
    assert sent()[0] == "sig-before" and set(sent()[1:]) == {"text"}


def filtered_in_two_steps() -> list:
    """Thinking between two steps of filtering code, as Opus 5.5 can send it: the code
    calls the search, the model thinks, more code reads the results."""
    return [
        *thought(0, "Search first.", "sig-before"),
        *block(1, {"type": "server_tool_use", "id": "srvtoolu_code", "name": "code_execution",
                   "input": {"code": "r = search()"}}, []),
        *block(2, {"type": "server_tool_use", "id": "srvtoolu_1", "name": "web_search",
                   "input": {"query": "worlds 2026 final"},
                   "caller": {"type": "code_execution_20260120", "tool_id": "srvtoolu_code"}},
               []),
        *block(3, {"type": "web_search_tool_result", "tool_use_id": "srvtoolu_1",
                   "content": RESULT}, []),
        *block(4, {"type": "code_execution_tool_result", "tool_use_id": "srvtoolu_code",
                   "content": {"type": "code_execution_result", "stdout": "", "stderr": "",
                               "return_code": 0, "content": []}}, []),
        *thought(5, "Read the first two.", "sig-between"),
        *block(6, {"type": "server_tool_use", "id": "srvtoolu_code2", "name": "code_execution",
                   "input": {"code": "print(r[:2])"}}, []),
        *block(7, {"type": "code_execution_tool_result", "tool_use_id": "srvtoolu_code2",
                   "content": {"type": "code_execution_result", "stdout": "two",
                               "stderr": "", "return_code": 0, "content": []}}, []),
        *thought(8, "They agree.", "sig-after"),
        *cited_text(9),
    ]


async def test_thinking_between_steps_of_a_search_is_replayed_where_it_came(driver):
    """A thought is bound to everything before it: one written between two steps of the
    filtering code goes back between them, once, or the API rejects it ("content before
    this block that was not present when it was created"). It is still shown as thinking.
    Once a later question gets the search as text, it goes with the search."""
    from kaden.api import Reasoning

    driver.stub.queue(Reply(events=[message_start(), *filtered_in_two_steps(), *searched()]))
    answer = (await collect(driver.core, request()))[-1].message
    assert [b.text for b in answer.blocks if isinstance(b, Reasoning)] == [
        "Search first.", "Read the first two.", "They agree."]

    def sent() -> list[str]:
        replayed = driver.stub.bodies[-1]["messages"][1]["content"]
        return [c.get("signature") or c.get("id") or c.get("tool_use_id") or c["type"]
                for c in replayed]

    driver.stub.queue(Reply(events=[message_start(), *text_block(0, ["ok"]), *finish("end_turn")]))
    await collect(driver.core, request(OPUS, [user("Who plays the final?"), answer,
                                              tool_results()]))  # the same turn: as it came
    assert sent()[:10] == ["sig-before", "srvtoolu_code", "srvtoolu_1", "srvtoolu_1",
                           "srvtoolu_code", "sig-between", "srvtoolu_code2", "srvtoolu_code2",
                           "sig-after", "text"]
    driver.stub.queue(Reply(events=[message_start(), *text_block(0, ["ok"]), *finish("end_turn")]))
    await collect(driver.core, request(OPUS, [user("Who plays the final?"), answer,
                                              user("And who is favoured?")]))
    assert sent()[0] == "sig-before" and set(sent()[1:]) == {"text"}


def two_searches_with_words_between() -> list:
    """A reply that searches, says something, thinks and searches again."""
    return [
        *thought(0, "Search first.", "sig-a"),
        *block(1, {"type": "server_tool_use", "id": "srvtoolu_1", "name": "web_search",
                   "input": {"query": "worlds 2026 final"}}, []),
        *block(2, {"type": "web_search_tool_result", "tool_use_id": "srvtoolu_1",
                   "content": RESULT}, []),
        *text_block(3, ["Looking further."]),
        *thought(4, "Try the bracket.", "sig-b"),
        *block(5, {"type": "server_tool_use", "id": "srvtoolu_2", "name": "web_search",
                   "input": {"query": "worlds 2026 bracket"}}, []),
        *block(6, {"type": "web_search_tool_result", "tool_use_id": "srvtoolu_2",
                   "content": RESULT}, []),
        *thought(7, "They agree.", "sig-c"),
        *cited_text(8),
    ]


async def test_words_between_searches_are_replayed_where_they_came(driver):
    """Text between two searches goes back between them, once, so the thoughts after it
    stay valid; it is still shown in its place among the steps."""
    driver.stub.queue(Reply(events=[message_start(), *two_searches_with_words_between(),
                                    *searched(searches=2)]))
    answer = (await collect(driver.core, request()))[-1].message
    assert "Looking further." in [b.text for b in answer.blocks if isinstance(b, Text)]
    driver.stub.queue(Reply(events=[message_start(), *text_block(0, ["ok"]), *finish("end_turn")]))
    await collect(driver.core, request(OPUS, [user("Who plays the final?"), answer,
                                              tool_results()]))
    replayed = driver.stub.bodies[-1]["messages"][1]["content"]
    kinds = [c.get("signature") or c.get("id") or c.get("tool_use_id") or c.get("text")
             or c["type"] for c in replayed]
    assert kinds[:9] == ["sig-a", "srvtoolu_1", "srvtoolu_1", "Looking further.", "sig-b",
                         "srvtoolu_2", "srvtoolu_2", "sig-c", "Gen.G meets BLG."]
    assert kinds.count("Looking further.") == 1


async def test_a_shortened_answer_sends_no_thought_among_its_searches(driver):
    """Memory shortens an answer that does not fit by cutting its text and dropping its
    thinking. A thought kept among the search blocks would then follow text it never saw,
    so it goes too. The searches go back as they came, with the words between them in
    full from the stored blocks; only text outside them goes cut."""
    from dataclasses import replace

    from kaden.api import Reasoning

    driver.stub.queue(Reply(events=[message_start(), *two_searches_with_words_between(),
                                    *searched(searches=2)]))
    answer = (await collect(driver.core, request()))[-1].message
    shortened = replace(answer, blocks=tuple(
        Text(b.text[:4]) if isinstance(b, Text) else b
        for b in answer.blocks if not isinstance(b, Reasoning)))
    driver.stub.queue(Reply(events=[message_start(), *text_block(0, ["ok"]), *finish("end_turn")]))
    await collect(driver.core, request(OPUS, [user("Who plays the final?"), shortened,
                                              tool_results()]))
    replayed = driver.stub.bodies[-1]["messages"][1]["content"]
    assert not any(c["type"] == "thinking" for c in replayed)
    assert [c.get("id") or c.get("tool_use_id") for c in replayed
            if c["type"] != "text"][:4] == ["srvtoolu_1", "srvtoolu_1", "srvtoolu_2",
                                             "srvtoolu_2"]
    texts = [c["text"] for c in replayed if c["type"] == "text"]
    assert texts.count("Looking further.") == 1
    assert not any(t.startswith("Look") and t != "Looking further." for t in texts)


def replayed_kinds(driver: AnthropicDriver) -> list:
    return [c.get("signature") or c.get("id") or c.get("tool_use_id") or c.get("text")
            or c["type"] for c in driver.stub.bodies[-1]["messages"][1]["content"]]


async def replay_in_turn(driver: AnthropicDriver, answer: Message) -> None:
    driver.stub.queue(Reply(events=[message_start(), *text_block(0, ["ok"]), *finish("end_turn")]))
    await collect(driver.core, request(OPUS, [user("Who plays the final?"), answer,
                                              tool_results()]))


async def test_an_empty_text_between_searches_is_not_sent(driver):
    events = two_searches_with_words_between()
    events = [(kind, {**data, "delta": {**data["delta"], "text": ""}})
              if data.get("delta", {}).get("text") == "Looking further." else (kind, data)
              for kind, data in events]
    driver.stub.queue(Reply(events=[message_start(), *events, *searched(searches=2)]))
    answer = (await collect(driver.core, request()))[-1].message
    await replay_in_turn(driver, answer)
    assert "" not in replayed_kinds(driver) and "text" not in replayed_kinds(driver)[:7]


async def test_a_thought_after_cited_words_follows_them_as_they_came(driver):
    """The words between a search and a later thought go back as they came, with their
    citations, as the thought saw them."""
    driver.stub.queue(Reply(events=[
        message_start(), *search_blocks(0), *cited_text(2), *thought(3, "Enough.", "sig-x"),
        *text_block(4, ["Done."]), *searched()]))
    answer = (await collect(driver.core, request()))[-1].message
    await replay_in_turn(driver, answer)
    replayed = driver.stub.bodies[-1]["messages"][1]["content"]
    assert replayed_kinds(driver)[:5] == ["srvtoolu_1", "srvtoolu_1", "Gen.G meets BLG.",
                                          "sig-x", "Done."]
    assert replayed[2].get("citations")


async def test_a_search_step_is_shown_where_the_search_began(driver):
    """Whatever came between a search and its result is shown after the step, and goes
    back between them as it came."""
    from kaden.api import Reasoning

    driver.stub.queue(Reply(events=[
        message_start(),
        *block(0, {"type": "server_tool_use", "id": "srvtoolu_1", "name": "web_search",
                   "input": {"query": "worlds 2026 final"}}, []),
        *thought(1, "Waiting on it.", "sig-wait"),
        *block(2, {"type": "web_search_tool_result", "tool_use_id": "srvtoolu_1",
                   "content": RESULT}, []),
        *cited_text(3), *searched()]))
    answer = (await collect(driver.core, request()))[-1].message
    assert [type(b) for b in answer.blocks[:2]] == [ServerToolUse, Reasoning]
    await replay_in_turn(driver, answer)
    assert replayed_kinds(driver)[:3] == ["srvtoolu_1", "sig-wait", "srvtoolu_1"]


async def test_every_search_goes_as_text_when_the_api_will_not_take_them_back(driver):
    driver.stub.queue(Reply(events=[message_start(), *two_searches_with_words_between(),
                                    *searched(searches=2)]))
    answer = (await collect(driver.core, request()))[-1].message
    driver.stub.queue(Reply(status=400, json_body={"type": "error", "error": {
        "type": "invalid_request_error", "message": "source tool `srvtoolu_1` not found"}}))
    driver.stub.queue(Reply(events=[message_start(), *text_block(0, ["ok"]), *finish("end_turn")]))
    await collect(driver.core, request(OPUS, [user("q"), answer, tool_results()]))
    sent = json.dumps(driver.stub.bodies[-1]["messages"][1]["content"])
    assert "worlds 2026 final" in sent and "worlds 2026 bracket" in sent
    assert "server_tool_use" not in sent


async def test_thinking_among_search_steps_is_left_out_once_its_binding_changed(driver):
    """Within the same turn the search still goes back as it came, but a thought among
    its steps is bound to the system prompt and tools too: once they changed, it is left
    out, as any other thought would be."""
    driver.stub.queue(Reply(events=[message_start(), *filtered_in_two_steps(), *searched()]))
    answer = (await collect(driver.core, request()))[-1].message
    driver.stub.queue(Reply(events=[message_start(), *text_block(0, ["ok"]), *finish("end_turn")]))
    changed = InferenceRequest(OPUS, "A new system prompt.",
                               [user("Who plays the final?"), answer, tool_results()],
                               request().tools)
    await collect(driver.core, changed)
    replayed = driver.stub.bodies[-1]["messages"][1]["content"]
    assert not any(c["type"] == "thinking" for c in replayed)
    assert [c.get("id") or c.get("tool_use_id") for c in replayed[:6]] == [
        "srvtoolu_code", "srvtoolu_1", "srvtoolu_1", "srvtoolu_code", "srvtoolu_code2",
        "srvtoolu_code2"]


async def test_an_answer_after_a_fallback_is_bound_to_the_request_that_was_sent(driver):
    """Its thinking goes back with the next request of the turn: it is bound to the
    body the API took, not to the one it refused."""
    driver.stub.queue(Reply(status=400, json_body={"type": "error", "error": {
        "type": "invalid_request_error",
        "message": "Web search is not enabled for your organization."}}))
    driver.stub.queue(Reply(events=[
        message_start(), *thought(0, "Let me check.", "sig-1"),
        *block(1, {"type": "tool_use", "id": "toolu_1", "name": "get_weather", "input": {}},
               [{"type": "input_json_delta", "partial_json": "{}"}]),
        *finish("tool_use")]))
    answer = (await collect(driver.core, request()))[-1].message
    driver.stub.queue(Reply(events=[message_start(), *text_block(0, ["ok"]), *finish("end_turn")]))
    # Search is off now: the runtime no longer offers the Core's WebSearch.
    await collect(driver.core, InferenceRequest(
        OPUS, "", [user("Who plays the final?"), answer, tool_results()], [WEATHER_TOOL]))
    replayed = driver.stub.bodies[-1]["messages"][1]["content"]
    assert replayed[0].get("signature") == "sig-1"


SEARCH_OFF = {"type": "error", "error": {
    "type": "invalid_request_error",
    "message": "Web search is not enabled for your organization."}}


async def test_the_request_after_search_is_switched_off_has_no_search_tool(driver):
    """The Core's WebSearch goes as Anthropic's own or not at all: no module runs it as a
    tool of Kaden's. Once search is off, a WebSearch in a request is Kaden's own."""
    driver.stub.queue(Reply(status=400, json_body=SEARCH_OFF))
    driver.stub.queue(Reply(events=[message_start(), *text_block(0, ["ok"]), *finish("end_turn")]))
    await collect(driver.core, request())
    assert [t["name"] for t in driver.stub.bodies[-1]["tools"]] == [WEATHER_TOOL.name]
    driver.stub.queue(Reply(events=[message_start(), *text_block(0, ["ok"]), *finish("end_turn")]))
    await collect(driver.core, request())  # the runtime now offers Kaden's own WebSearch
    assert driver.stub.bodies[-1]["tools"][0] == {
        "name": "WebSearch", "description": "search", "input_schema": {"type": "object"}}


async def test_a_retry_without_search_sends_no_tools_when_none_are_left(driver):
    driver.stub.queue(Reply(status=400, json_body=SEARCH_OFF))
    driver.stub.queue(Reply(events=[message_start(), *text_block(0, ["ok"]), *finish("end_turn")]))
    only_search = InferenceRequest(OPUS, "", [user("q")], [ToolSpec("WebSearch", "s", {})])
    await collect(driver.core, only_search)
    assert "tools" not in driver.stub.bodies[-1]


async def test_a_turn_paused_too_often_is_cut_short(driver):
    """After MAX_CONTINUATIONS the turn ends unfinished: its stop says so, and a search
    still waiting for its result is a failed step, which goes back as text."""
    from kaden.api import StopReason
    from kaden.core.anthropic import MAX_CONTINUATIONS

    for _ in range(MAX_CONTINUATIONS):
        driver.stub.queue(Reply(events=[message_start(), *search_blocks(0),
                                        *searched("pause_turn")]))
    pending = block(0, {"type": "server_tool_use", "id": "srvtoolu_9", "name": "web_search",
                        "input": {}},
                    [{"type": "input_json_delta", "partial_json": json_query("more")}])
    driver.stub.queue(Reply(events=[message_start(), *pending, *searched("pause_turn")]))
    done = (await collect(driver.core, request()))[-1]
    assert len(driver.stub.bodies) == MAX_CONTINUATIONS + 1
    assert done.stop is StopReason.MAX_TOKENS
    [unfinished] = [b for b in done.message.blocks
                    if isinstance(b, ServerToolUse) and b.id == "srvtoolu_9"]
    assert unfinished.is_error and unfinished.input == {"query": "more"}
    driver.stub.queue(Reply(events=[message_start(), *text_block(0, ["ok"]), *finish("end_turn")]))
    await collect(driver.core, request(OPUS, [user("q"), done.message, tool_results()]))
    sent = driver.stub.bodies[-1]["messages"][1]["content"]
    assert "srvtoolu_9" not in json.dumps([c for c in sent if c["type"] != "text"])
