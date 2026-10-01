"""Claude asking a question, and the MCP server that carries it."""
from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from pathlib import Path

import pytest

from bridge.approval import ApprovalService
from bridge.state import State

MCP = Path(__file__).resolve().parent.parent / "hooks" / "ask_mcp.py"


# -- the waiting and answering ------------------------------------------------
async def test_a_single_choice_comes_back(tmp_path):
    service = ApprovalService(State(tmp_path / "s.db"), timeout=5)

    async def tap_the_second(asked):
        service.answer(asked.request_id, [1])

    service.set_question_notifier(tap_the_second)
    answer = await service.ask_question("s1", "Which one?", ["left", "right"],
                                        False)
    assert answer == {"chosen": ["right"]}


async def test_several_choices_come_back_together(tmp_path):
    service = ApprovalService(State(tmp_path / "s.db"), timeout=5)

    async def tick_two(asked):
        service.toggle(asked.request_id, 0)
        service.toggle(asked.request_id, 2)
        service.answer(asked.request_id, sorted(asked.picked))

    service.set_question_notifier(tick_two)
    answer = await service.ask_question("s1", "Which?", ["a", "b", "c"], True)
    assert answer == {"chosen": ["a", "c"]}


async def test_ticking_the_same_option_twice_unticks_it(tmp_path):
    service = ApprovalService(State(tmp_path / "s.db"), timeout=5)
    picked = {}

    async def tick_and_untick(asked):
        service.toggle(asked.request_id, 0)
        service.toggle(asked.request_id, 0)
        picked["left"] = set(asked.picked)
        service.answer(asked.request_id, [1])

    service.set_question_notifier(tick_and_untick)
    await service.ask_question("s1", "Which?", ["a", "b"], True)
    assert picked["left"] == set()


async def test_no_answer_in_time_says_so_rather_than_hanging(tmp_path):
    service = ApprovalService(State(tmp_path / "s.db"), question_timeout=0.05)

    async def silence(asked):
        return

    service.set_question_notifier(silence)
    answer = await service.ask_question("s1", "Which?", ["a", "b"], False)
    assert answer["chosen"] == []
    assert "in time" in answer["reason"]


async def test_a_question_with_too_few_options_is_refused(tmp_path):
    service = ApprovalService(State(tmp_path / "s.db"), timeout=1)
    answer = await service.ask_question("s1", "Which?", ["only one"], False)
    assert answer["chosen"] == []


async def test_with_no_telegram_it_says_so(tmp_path):
    service = ApprovalService(State(tmp_path / "s.db"), timeout=1)
    answer = await service.ask_question("s1", "Which?", ["a", "b"], False)
    assert answer["chosen"] == []
    assert "Telegram" in answer["reason"]


async def test_shutting_down_releases_a_waiting_question(tmp_path):
    service = ApprovalService(State(tmp_path / "s.db"), timeout=30)
    started = asyncio.Event()

    async def notice(asked):
        started.set()

    service.set_question_notifier(notice)
    task = asyncio.create_task(
        service.ask_question("s1", "Which?", ["a", "b"], False))
    await started.wait()
    await service.stop()
    assert (await task)["chosen"] == []


async def test_answering_a_question_that_is_gone_is_harmless(tmp_path):
    service = ApprovalService(State(tmp_path / "s.db"))
    assert service.answer("nope", [0]) is None
    assert service.toggle("nope", 0) is None


# -- the MCP server itself ----------------------------------------------------
def talk_to_mcp(messages: list[dict], env: dict | None = None) -> list[dict]:
    """Send JSON-RPC lines to the server and collect its replies."""
    stdin = "\n".join(json.dumps(m) for m in messages) + "\n"
    finished = subprocess.run(
        [sys.executable, str(MCP)], input=stdin, capture_output=True, text=True,
        timeout=60, env=env)
    return [json.loads(line) for line in finished.stdout.splitlines() if line.strip()]


def test_it_introduces_itself_when_asked():
    replies = talk_to_mcp([{"jsonrpc": "2.0", "id": 1, "method": "initialize",
                            "params": {"protocolVersion": "2024-11-05"}}])
    result = replies[0]["result"]
    assert result["protocolVersion"] == "2024-11-05"
    assert "tools" in result["capabilities"]


def test_it_offers_the_ask_tool():
    replies = talk_to_mcp([{"jsonrpc": "2.0", "id": 2, "method": "tools/list"}])
    tools = replies[0]["result"]["tools"]
    assert [t["name"] for t in tools] == ["ask_user"]
    schema = tools[0]["inputSchema"]
    assert schema["required"] == ["question", "options"]
    assert "allow_multiple" in schema["properties"]


def test_a_notification_gets_no_reply():
    replies = talk_to_mcp([{"jsonrpc": "2.0",
                            "method": "notifications/initialized"}])
    assert replies == []


def test_an_unknown_method_is_an_error_not_a_crash():
    replies = talk_to_mcp([{"jsonrpc": "2.0", "id": 3, "method": "no/such"},
                           {"jsonrpc": "2.0", "id": 4, "method": "tools/list"}])
    assert replies[0]["error"]["code"] == -32601
    assert replies[1]["result"]["tools"]          # kept going afterwards


def test_rubbish_on_the_wire_is_ignored():
    stdin = "not json at all\n" + json.dumps(
        {"jsonrpc": "2.0", "id": 5, "method": "tools/list"}) + "\n"
    finished = subprocess.run([sys.executable, str(MCP)], input=stdin,
                              capture_output=True, text=True, timeout=60)
    assert '"tools"' in finished.stdout


def test_without_the_bridge_it_tells_claude_to_carry_on(monkeypatch):
    replies = talk_to_mcp([{
        "jsonrpc": "2.0", "id": 6, "method": "tools/call",
        "params": {"name": "ask_user",
                   "arguments": {"question": "Which?", "options": ["a", "b"]}}}],
        env={"PATH": ""})
    text = replies[0]["result"]["content"][0]["text"]
    assert "not reachable" in text


def test_calling_a_tool_that_does_not_exist_is_reported():
    replies = talk_to_mcp([{"jsonrpc": "2.0", "id": 7, "method": "tools/call",
                            "params": {"name": "something_else",
                                       "arguments": {}}}])
    assert replies[0]["result"]["isError"] is True


@pytest.mark.parametrize("arguments", [
    {"question": "", "options": ["a", "b"]},
    {"question": "Which?", "options": ["only one"]},
])
def test_an_incomplete_question_is_refused(arguments):
    env = {"BRIDGE_QUESTION_URL": "http://127.0.0.1:1/question",
           "BRIDGE_APPROVAL_TOKEN": "x", "PATH": ""}
    replies = talk_to_mcp([{"jsonrpc": "2.0", "id": 8, "method": "tools/call",
                            "params": {"name": "ask_user",
                                       "arguments": arguments}}], env=env)
    assert replies[0]["result"]["isError"] is True
