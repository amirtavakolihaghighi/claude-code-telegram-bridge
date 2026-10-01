"""Tapping an option must show a tick even when Telegram misbehaves.

A callback has to be acknowledged within about fifteen seconds. On a slow
connection that call fails, and it used to run before the keyboard was redrawn -
so the tick never appeared and the buttons looked inert.
"""
from __future__ import annotations

import asyncio

import pytest
from telegram.error import BadRequest, NetworkError

from bridge.approval import ApprovalService, Question
from bridge.config import Config
from bridge.echo import EchoGuard
from bridge.inbox import Inbox
from bridge.runner import Runner
from bridge.state import State


class FakeQuery:
    """Records what was called, in order, and can be told to fail."""

    def __init__(self, answer_fails: Exception | None = None,
                 edit_fails: Exception | None = None):
        self.calls: list[str] = []
        self._answer_fails = answer_fails
        self._edit_fails = edit_fails
        self.message = None
        self.data = ""

    async def answer(self, text="", **kwargs):
        self.calls.append("answer")
        if self._answer_fails:
            raise self._answer_fails

    async def edit_message_reply_markup(self, reply_markup=None):
        self.calls.append("redraw")
        if self._edit_fails:
            raise self._edit_fails


@pytest.fixture
async def setup(tmp_path):
    config = Config(
        bot_token="t", group_id=-100, owner_id=1, watch_projects=(),
        claude_home=tmp_path, poll_interval=1, state_db=tmp_path / "s.db",
        claude_cli=None, permission_mode="auto", replies_enabled=True,
        approval_timeout=5, machine_label="", archive_format="both",
    )
    state = State(tmp_path / "s.db")
    approval = ApprovalService(state, question_timeout=5)
    inbox = Inbox(config, state, Runner(config, approval), EchoGuard(), approval)

    loop = asyncio.get_running_loop()
    asked = Question("r1", "s1", "Which?", ["a", "b", "c"], True,
                     loop.create_future())
    approval.questions["r1"] = asked
    return inbox, approval, asked


async def test_the_tick_is_drawn_before_the_tap_is_acknowledged(setup):
    inbox, _, _ = setup
    query = FakeQuery()
    await inbox._on_question_button(query, "q", "r1|0")
    assert query.calls == ["redraw", "answer"]


async def test_a_failed_acknowledgement_does_not_lose_the_tick(setup):
    """The exact failure seen in the wild: Query is too old."""
    inbox, approval, asked = setup
    query = FakeQuery(answer_fails=BadRequest("Query is too old"))

    await inbox._on_question_button(query, "q", "r1|1")

    assert "redraw" in query.calls          # the tick was drawn anyway
    assert asked.picked == {1}              # and the choice was recorded


async def test_a_failed_redraw_still_records_the_choice(setup):
    """Even with nothing visible, Send must deliver what was tapped."""
    inbox, approval, asked = setup
    query = FakeQuery(edit_fails=NetworkError("no route"))

    await inbox._on_question_button(query, "q", "r1|2")

    assert asked.picked == {2}


async def test_the_redraw_is_retried_once_on_a_network_wobble(setup):
    inbox, _, _ = setup
    query = FakeQuery(edit_fails=NetworkError("flaky"))
    await inbox._on_question_button(query, "q", "r1|0")
    assert query.calls.count("redraw") == 2


async def test_a_pointless_redraw_is_not_retried(setup):
    """Telegram rejects an unchanged keyboard; trying again cannot help."""
    inbox, _, _ = setup
    query = FakeQuery(edit_fails=BadRequest("Message is not modified"))
    await inbox._on_question_button(query, "q", "r1|0")
    assert query.calls.count("redraw") == 1


async def test_tapping_twice_unticks_and_both_are_drawn(setup):
    inbox, _, asked = setup
    await inbox._on_question_button(FakeQuery(), "q", "r1|0")
    assert asked.picked == {0}
    await inbox._on_question_button(FakeQuery(), "q", "r1|0")
    assert asked.picked == set()


async def test_send_delivers_everything_ticked(setup):
    inbox, approval, asked = setup
    await inbox._on_question_button(FakeQuery(), "q", "r1|0")
    await inbox._on_question_button(FakeQuery(), "q", "r1|2")

    class SendQuery(FakeQuery):
        def __init__(self):
            super().__init__()
            self.message = type("M", (), {"text_html": "Which?"})()

        async def edit_message_text(self, **kwargs):
            self.calls.append("rewrite")

    query = SendQuery()
    await inbox._on_question_button(query, "Q", "r1|0")
    assert asked.future.done()
    assert asked.future.result() == {"chosen": ["a", "c"]}
    assert query.calls == ["rewrite", "answer"]


async def test_a_tap_with_no_message_attached_does_not_crash(setup):
    """Telegram does not always attach the message to a callback."""
    inbox, _, asked = setup
    await inbox._on_question_button(FakeQuery(), "q", "r1|1")
    query = FakeQuery()                     # message stays None
    await inbox._on_question_button(query, "Q", "r1|0")
    assert asked.future.result() == {"chosen": ["b"]}


async def test_send_with_nothing_ticked_asks_you_to_pick(setup):
    inbox, _, asked = setup
    query = FakeQuery()
    await inbox._on_question_button(query, "Q", "r1|0")
    assert not asked.future.done()
    assert query.calls == ["answer"]


async def test_an_expired_question_has_its_buttons_taken_away(setup):
    inbox, approval, _ = setup
    approval.questions.clear()
    query = FakeQuery()
    await inbox._on_question_button(query, "q", "r1|0")
    assert query.calls == ["redraw", "answer"]
