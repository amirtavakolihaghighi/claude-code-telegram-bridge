"""Finding what never reached Telegram, and scheduling it to go."""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from bridge.catchup import describe, find_missing, schedule
from bridge.config import Config
from bridge.state import State
from tests.conftest import user_text


@pytest.fixture
def config(claude_home, tmp_path):
    return Config(
        bot_token="test-token", group_id=-100123, owner_id=42,
        watch_projects=(), claude_home=claude_home, poll_interval=0.01,
        state_db=tmp_path / "state.db", claude_cli=None,
        permission_mode="auto", replies_enabled=True, approval_timeout=5,
        machine_label="", archive_format="both",
    )


@pytest.fixture
def state(tmp_path):
    return State(tmp_path / "state.db")


def test_a_chat_never_followed_is_missing_in_full(config, state, chat_file):
    path = chat_file(r"C:\Code\app", "s1", [user_text("hello")])

    [missing] = find_missing(config, state)

    assert missing.never_followed is True
    assert missing.unsent == path.stat().st_size
    assert missing.renew_topic is False


def test_a_chat_sent_from_its_start_is_not_missing(config, state, chat_file):
    path = chat_file(r"C:\Code\app", "s1", [user_text("hello")])
    state.set_offset("s1", path, path.stat().st_size, mirrored_from=0)

    assert find_missing(config, state) == []


def test_a_chat_followed_part_way_is_missing_its_beginning(
        config, state, chat_file):
    path = chat_file(r"C:\Code\app", "s1", [user_text("hello")])
    state.set_offset("s1", path, path.stat().st_size, mirrored_from=40)

    [missing] = find_missing(config, state)

    assert missing.never_followed is False
    assert missing.unsent == 40


def test_a_part_way_chat_whose_topic_has_posts_gets_a_fresh_topic(
        config, state, chat_file):
    path = chat_file(r"C:\Code\app", "s1", [user_text("hello")])
    state.set_offset("s1", path, path.stat().st_size, mirrored_from=40)
    state.set_topic("s1", 7, "app: hello")
    state.count_post("s1")

    [missing] = find_missing(config, state)

    assert missing.renew_topic is True


def test_a_part_way_chat_with_an_empty_topic_keeps_it(config, state, chat_file):
    """A topic made by --import-all with nothing in it can simply be used."""
    path = chat_file(r"C:\Code\app", "s1", [user_text("hello")])
    state.set_offset("s1", path, path.stat().st_size, mirrored_from=40)
    state.set_topic("s1", 7, "app: hello")

    [missing] = find_missing(config, state)

    assert missing.renew_topic is False


def test_only_watched_projects_are_considered(config, state, chat_file):
    chat_file(r"C:\Code\wanted", "s1", [user_text("keep")])
    chat_file(r"C:\Code\ignored", "s2", [user_text("leave")])
    narrowed = replace(config, watch_projects=(Path(r"C:\Code\wanted"),))

    assert [m.session_id for m in find_missing(narrowed, state)] == ["s1"]


def test_scheduling_restarts_part_way_chats_and_leaves_the_rest(
        config, state, chat_file):
    seen = chat_file(r"C:\Code\app", "s1", [user_text("hello")])
    chat_file(r"C:\Code\app", "s2", [user_text("never seen")])
    state.set_offset("s1", seen, seen.stat().st_size, mirrored_from=40)
    state.set_topic("s1", 7, "app: hello")
    state.count_post("s1")

    schedule(state, find_missing(config, state))

    assert state.get_offset("s1") == 0
    assert state.renewing("s1") is True
    assert state.get_offset("s2") is None   # read from the start when caught up


def test_the_summary_says_when_there_is_nothing_to_do():
    assert "nothing to catch up on" in describe([])


def test_the_summary_counts_both_kinds(config, state, chat_file):
    seen = chat_file(r"C:\Code\app", "s1", [user_text("hello")])
    chat_file(r"C:\Code\app", "s2", [user_text("never seen")])
    state.set_offset("s1", seen, seen.stat().st_size, mirrored_from=40)

    line = describe(find_missing(config, state))

    assert "2 chat(s)" in line
    assert "1 never sent" in line
    assert "1 missing their beginning" in line
