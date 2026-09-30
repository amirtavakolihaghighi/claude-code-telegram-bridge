"""The engine: which chats get followed, from where, and what gets posted."""
from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from bridge.config import Config
from bridge.echo import EchoGuard
from bridge.mirror import Mirror
from bridge.state import State
from tests.conftest import assistant_text, title, tool_result, tool_use, user_text


class FakeSink:
    """Records what would have been posted, and never touches Telegram."""

    def __init__(self):
        self.messages: list[tuple[str, str]] = []
        self.titles: dict[str, str] = {}
        self.created: list[str] = []

    async def set_title(self, session_id, text):
        self.titles[session_id] = text

    async def send(self, session_id, message):
        self.messages.append((session_id, message.html))

    async def ensure_topic(self, session_id):
        self.created.append(session_id)

    def texts(self):
        return [html for _, html in self.messages]


@pytest.fixture
def config(claude_home, tmp_path):
    return Config(
        bot_token="test-token", group_id=-100123, owner_id=42,
        watch_projects=(), claude_home=claude_home, poll_interval=0.01,
        state_db=tmp_path / "state.db", claude_cli=None,
        permission_mode="auto", replies_enabled=True, approval_timeout=5,
        machine_label="", archive_format="both",
        attach_suffixes=frozenset({".md", ".txt", ".png"}), max_attach_mb=20.0,
    )


def build(config, tmp_path, **kwargs):
    state = State(tmp_path / "state.db")
    sink = FakeSink()
    return Mirror(config, sink, state, **kwargs), sink, state


# -- where it starts reading -------------------------------------------------
async def test_chats_that_already_existed_are_followed_from_now(
        config, tmp_path, chat_file):
    chat_file(r"C:\Code\app", "s1", [user_text("old news")])
    mirror, sink, _ = build(config, tmp_path)

    await mirror.tick()

    assert sink.texts() == []


async def test_backfill_posts_everything_from_the_beginning(
        config, tmp_path, chat_file):
    chat_file(r"C:\Code\app", "s1", [user_text("old news")])
    mirror, sink, _ = build(config, tmp_path, backfill=True)

    await mirror.tick()

    assert any("old news" in html for html in sink.texts())


async def test_a_chat_created_while_running_is_posted_from_its_first_word(
        config, tmp_path, chat_file):
    chat_file(r"C:\Code\app", "s1", [user_text("existing")])
    mirror, sink, _ = build(config, tmp_path)
    await mirror.tick()

    chat_file(r"C:\Code\app", "s2", [user_text("brand new chat")])
    await mirror.tick()

    assert any("brand new chat" in html for html in sink.texts())


async def test_nothing_is_posted_twice(config, tmp_path, chat_file):
    path = chat_file(r"C:\Code\app", "s1", [user_text("one")])
    mirror, sink, _ = build(config, tmp_path, backfill=True)
    await mirror.tick()
    before = len(sink.texts())

    await mirror.tick()
    assert len(sink.texts()) == before

    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(assistant_text("two")) + "\n")
    await mirror.tick()
    assert len(sink.texts()) == before + 1


# -- naming ------------------------------------------------------------------
async def test_a_topic_is_named_project_then_chat_title(
        config, tmp_path, chat_file):
    chat_file(r"C:\Code\webshop", "s1", [title("Fix the rounding bug")])
    mirror, sink, _ = build(config, tmp_path)

    await mirror.tick()

    assert sink.titles["s1"] == "webshop: Fix the rounding bug"


async def test_a_chat_with_no_title_still_says_which_project_it_is_in(
        config, tmp_path, chat_file):
    chat_file(r"C:\Code\webshop", "s1", [user_text("hello")])
    mirror, sink, _ = build(config, tmp_path)

    await mirror.tick()

    assert sink.titles["s1"].startswith("webshop: chat ")


async def test_a_machine_label_goes_in_front(config, tmp_path, chat_file):
    chat_file(r"C:\Code\webshop", "s1", [title("Fix things")])
    mirror, sink, _ = build(replace(config, machine_label="Laptop"), tmp_path)

    await mirror.tick()

    assert sink.titles["s1"] == "Laptop · webshop: Fix things"


# -- which projects ----------------------------------------------------------
async def test_only_the_watched_projects_are_mirrored(
        config, tmp_path, chat_file):
    chat_file(r"C:\Code\wanted", "s1", [user_text("keep me")])
    chat_file(r"C:\Code\ignored", "s2", [user_text("not me")])
    narrowed = replace(config, watch_projects=(Path(r"C:\Code\wanted"),))
    mirror, sink, _ = build(narrowed, tmp_path, backfill=True)

    await mirror.tick()

    assert any("keep me" in html for html in sink.texts())
    assert not any("not me" in html for html in sink.texts())


# -- not repeating yourself --------------------------------------------------
async def test_a_prompt_sent_from_telegram_is_not_echoed_back(
        config, tmp_path, chat_file):
    chat_file(r"C:\Code\app", "s1", [user_text("sent from my phone")])
    echo = EchoGuard()
    echo.remember("s1", "sent from my phone")
    mirror, sink, _ = build(config, tmp_path, backfill=True, echo=echo)

    await mirror.tick()

    assert not any("sent from my phone" in html for html in sink.texts())


async def test_the_same_words_typed_again_later_do_show(
        config, tmp_path, chat_file):
    chat_file(r"C:\Code\app", "s1",
              [user_text("again", uuid="u1"), user_text("again", uuid="u2")])
    echo = EchoGuard()
    echo.remember("s1", "again")
    mirror, sink, _ = build(config, tmp_path, backfill=True, echo=echo)

    await mirror.tick()

    assert sum("again" in html for html in sink.texts()) == 1


# -- importing ---------------------------------------------------------------
async def test_import_all_gives_every_chat_a_topic_without_posting(
        config, tmp_path, chat_file):
    chat_file(r"C:\Code\app", "s1", [user_text("old")])
    chat_file(r"C:\Code\app", "s2", [user_text("older")])
    mirror, sink, _ = build(config, tmp_path, import_all=True)

    await mirror.tick()

    assert sorted(sink.created) == ["s1", "s2"]
    assert sink.texts() == []


# -- robustness --------------------------------------------------------------
async def test_a_tool_call_and_its_result_arrive_as_one_message(
        config, tmp_path, chat_file):
    chat_file(r"C:\Code\app", "s1", [
        tool_use("Bash", {"command": "pytest", "description": "Run tests"}),
        tool_result("all good"),
    ])
    mirror, sink, _ = build(config, tmp_path, backfill=True)

    await mirror.tick()

    assert len(sink.texts()) == 1
    assert "pytest" in sink.texts()[0]
    assert "all good" in sink.texts()[0]


async def test_a_chat_deleted_mid_run_does_not_stop_the_others(
        config, tmp_path, chat_file):
    doomed = chat_file(r"C:\Code\app", "s1", [user_text("here")])
    chat_file(r"C:\Code\app", "s2", [user_text("also here")])
    mirror, sink, _ = build(config, tmp_path, backfill=True)
    await mirror.tick()

    doomed.unlink()
    await mirror.tick()          # must not raise
