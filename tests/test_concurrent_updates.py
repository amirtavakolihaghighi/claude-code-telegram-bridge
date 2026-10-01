"""Updates must be handled concurrently, or questions cannot be answered.

A message handler runs for as long as Claude's whole turn. With updates handled
one at a time, a button tapped during that turn waits in the queue until it
finishes - and a question Claude asks mid-turn can never be answered, because
the answer arrives as a tap. Telegram abandons an unanswered tap after a few
seconds, so the button simply appeared to do nothing.
"""
from __future__ import annotations

from pathlib import Path

from telegram.request import HTTPXRequest

from bridge.approval import ApprovalService
from bridge.config import Config
from bridge.echo import EchoGuard
from bridge.inbox import build_application
from bridge.runner import Runner
from bridge.state import State


def build(tmp_path: Path):
    config = Config(
        bot_token="123456:fake-token-for-construction-only",
        group_id=-100, owner_id=1, watch_projects=(), claude_home=tmp_path,
        poll_interval=1, state_db=tmp_path / "s.db", claude_cli=None,
        permission_mode="auto", replies_enabled=True, approval_timeout=5,
        machine_label="", archive_format="both",
    )
    state = State(tmp_path / "s.db")
    approval = ApprovalService(state)
    return build_application(config, state, Runner(config, approval),
                            EchoGuard(), HTTPXRequest(), approval)


def test_updates_are_handled_concurrently(tmp_path):
    app = build(tmp_path)
    assert app.concurrent_updates, (
        "a tap during a running turn would queue behind it, so a question "
        "Claude asks mid-turn could never be answered"
    )


def test_a_question_tap_has_somewhere_to_go(tmp_path):
    """The callback handler and the question notifier must both be wired."""
    app = build(tmp_path)
    inbox = app.bot_data["inbox"]
    assert inbox.approval is not None
    assert inbox.approval._question_hook is not None
    assert inbox.approval._question_closer is not None


def test_an_error_handler_is_registered(tmp_path):
    """Otherwise a failed update prints a bare traceback and a warning."""
    app = build(tmp_path)
    assert app.error_handlers
