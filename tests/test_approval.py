"""Permission questions: how they are keyed, answered and refused."""
from __future__ import annotations

import asyncio
import json
import urllib.error
import urllib.request

import pytest

from bridge.approval import ApprovalService, rule_key
from bridge.state import State


# -- how a standing permission is keyed --------------------------------------
def test_a_shell_command_is_keyed_by_its_program_not_the_whole_line():
    assert rule_key("Bash", {"command": "git push origin main"})[0] == "Bash:git"
    assert rule_key("Bash", {"command": "git status"})[0] == "Bash:git"


def test_saying_yes_to_one_program_does_not_cover_another():
    assert rule_key("Bash", {"command": "git status"})[0] != \
           rule_key("Bash", {"command": "rm -rf build"})[0]


def test_a_program_is_recognised_by_path_or_extension():
    assert rule_key("Bash", {"command": r"C:\tools\npm.cmd install"})[0] == "Bash:npm"
    assert rule_key("Bash", {"command": "npm run build"})[0] == "Bash:npm"


def test_other_tools_are_keyed_by_tool_name():
    assert rule_key("Write", {"file_path": "a.txt"})[0] == "Write"


def test_an_empty_command_falls_back_to_the_tool_name():
    assert rule_key("Bash", {"command": "   "})[0] == "Bash"


# -- answering ---------------------------------------------------------------
async def test_tapping_allow_lets_the_action_through(tmp_path):
    service = ApprovalService(State(tmp_path / "s.db"), timeout=5)

    async def responder(pending):
        service.resolve(pending.request_id, allow=True)

    service.set_notifier(responder)
    assert await service.ask("s1", "Bash", {"command": "ls"}) == {"behavior": "allow"}


async def test_tapping_deny_refuses_it(tmp_path):
    service = ApprovalService(State(tmp_path / "s.db"), timeout=5)

    async def responder(pending):
        service.resolve(pending.request_id, allow=False)

    service.set_notifier(responder)
    answer = await service.ask("s1", "Bash", {"command": "rm -rf /"})
    assert answer["behavior"] == "deny"


async def test_no_answer_within_the_timeout_refuses_rather_than_hanging(tmp_path):
    service = ApprovalService(State(tmp_path / "s.db"), timeout=0.2)

    async def silence(pending):
        return

    service.set_notifier(silence)
    answer = await service.ask("s1", "Bash", {"command": "ls"})
    assert answer["behavior"] == "deny"
    assert "no answer" in answer["message"]


async def test_with_nobody_to_ask_it_refuses(tmp_path):
    service = ApprovalService(State(tmp_path / "s.db"), timeout=1)
    answer = await service.ask("s1", "Bash", {"command": "ls"})
    assert answer["behavior"] == "deny"


# -- remembering -------------------------------------------------------------
async def test_always_allow_stops_it_asking_about_that_program_again(tmp_path):
    state = State(tmp_path / "s.db")
    service = ApprovalService(state, timeout=5)
    asked = []

    async def responder(pending):
        asked.append(pending.tool_name)
        service.resolve(pending.request_id, allow=True, remember=True)

    service.set_notifier(responder)

    await service.ask("s1", "Bash", {"command": "git status"})
    await service.ask("s1", "Bash", {"command": "git log"})

    assert len(asked) == 1
    assert state.allow_rule_exists("Bash:git")


async def test_a_different_program_still_asks(tmp_path):
    state = State(tmp_path / "s.db")
    service = ApprovalService(state, timeout=5)
    asked = []

    async def responder(pending):
        asked.append(pending.rule[0])
        service.resolve(pending.request_id, allow=True, remember=True)

    service.set_notifier(responder)
    await service.ask("s1", "Bash", {"command": "git status"})
    await service.ask("s1", "Bash", {"command": "npm install"})

    assert asked == ["Bash:git", "Bash:npm"]


async def test_allow_everything_this_turn_lasts_only_until_the_turn_ends(tmp_path):
    service = ApprovalService(State(tmp_path / "s.db"), timeout=5)
    asked = []

    async def responder(pending):
        asked.append(1)
        service.resolve(pending.request_id, allow=True, always=True)

    service.set_notifier(responder)
    await service.ask("s1", "Bash", {"command": "git status"})
    await service.ask("s1", "Bash", {"command": "rm -rf build"})
    assert len(asked) == 1

    service.forget_session("s1")
    await service.ask("s1", "Bash", {"command": "rm -rf build"})
    assert len(asked) == 2


def test_answering_a_question_that_already_expired_is_harmless(tmp_path):
    service = ApprovalService(State(tmp_path / "s.db"))
    assert service.resolve("no-such-request", allow=True) is False


# -- the loopback service ----------------------------------------------------
async def test_the_hook_can_ask_and_is_answered(tmp_path):
    service = ApprovalService(State(tmp_path / "s.db"), timeout=5)

    async def responder(pending):
        service.resolve(pending.request_id, allow=True)

    service.set_notifier(responder)
    await service.start()
    try:
        body = json.dumps({"session_id": "s1", "tool_name": "Bash",
                           "tool_input": {"command": "ls"}}).encode()

        def post(token):
            request = urllib.request.Request(
                f"http://127.0.0.1:{service.port}/ask", data=body, method="POST",
                headers={"Content-Type": "application/json",
                         "X-Bridge-Token": token})
            with urllib.request.urlopen(request, timeout=10) as response:
                return json.loads(response.read())

        assert (await asyncio.to_thread(post, service.token))["behavior"] == "allow"

        # Refused one way or another: a 400, or the socket closed on us.
        with pytest.raises((urllib.error.URLError, OSError)):
            await asyncio.to_thread(post, "the-wrong-secret")
    finally:
        await service.stop()


async def test_shutting_down_releases_anyone_still_waiting(tmp_path):
    service = ApprovalService(State(tmp_path / "s.db"), timeout=30)
    started = asyncio.Event()

    async def responder(pending):
        started.set()

    service.set_notifier(responder)
    task = asyncio.create_task(service.ask("s1", "Bash", {"command": "ls"}))
    await started.wait()
    await service.stop()

    assert (await task)["behavior"] == "deny"
