"""The two sides of a question must not give up at the same moment.

If the caller's wait equals the bridge's, they race: the caller sees a bare
connection timeout instead of the bridge's explanation that nobody answered.
"""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path

from bridge.approval import QUESTION_TIMEOUT, ApprovalService

ASK_MCP = Path(__file__).resolve().parent.parent / "hooks" / "ask_mcp.py"


def load_ask_mcp():
    spec = importlib.util.spec_from_file_location("ask_mcp", ASK_MCP)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_caller_waits_longer_than_the_bridge(monkeypatch):
    monkeypatch.setenv("BRIDGE_QUESTION_WAIT", str(QUESTION_TIMEOUT))
    module = load_ask_mcp()
    assert module.reply_timeout() > QUESTION_TIMEOUT


def test_the_caller_follows_whatever_the_bridge_was_told(monkeypatch):
    monkeypatch.setenv("BRIDGE_QUESTION_WAIT", "120")
    module = load_ask_mcp()
    assert module.reply_timeout() == 180.0


def test_a_nonsense_setting_falls_back_to_something_sane(monkeypatch):
    monkeypatch.setenv("BRIDGE_QUESTION_WAIT", "soon")
    module = load_ask_mcp()
    assert module.reply_timeout() == 360.0


def test_with_no_setting_the_caller_still_outlasts_the_default(monkeypatch):
    monkeypatch.delenv("BRIDGE_QUESTION_WAIT", raising=False)
    module = load_ask_mcp()
    assert module.reply_timeout() > QUESTION_TIMEOUT


def test_the_wait_is_short_enough_not_to_strand_a_turn():
    """Fifteen minutes left the topic showing 'typing...' and said nothing."""
    assert QUESTION_TIMEOUT <= 300


def test_the_service_takes_the_wait_it_is_given(tmp_path):
    from bridge.state import State
    service = ApprovalService(State(tmp_path / "s.db"), question_timeout=42)
    assert service.question_timeout == 42


def test_the_service_defaults_to_the_shared_figure(tmp_path):
    from bridge.state import State
    service = ApprovalService(State(tmp_path / "s.db"), timeout=1234)
    assert service.question_timeout == QUESTION_TIMEOUT
