"""The engine: which chats get followed, from where, and what gets posted."""
from __future__ import annotations

import json
import time
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import pytest

from bridge import mirror as mirror_module
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
        self.on_delivered = None

    async def set_title(self, session_id, text):
        self.titles[session_id] = text

    async def send(self, session_id, message):
        self.messages.append((session_id, message.html))
        self.on_delivered(session_id)

    async def ensure_topic(self, session_id):
        self.created.append(session_id)

    def texts(self):
        return [html for _, html in self.messages]


class QueueSink(FakeSink):
    """Holds messages until the test says they went out, like a real queue
    waiting on Telegram."""

    def __init__(self, room: int = 1000):
        super().__init__()
        self.queue: list[tuple[str, str]] = []
        self.space = room

    async def send(self, session_id, message):
        self.queue.append((session_id, message.html))

    def room(self):
        return self.space - len(self.queue)

    def deliver(self, count: int | None = None):
        for _ in range(len(self.queue) if count is None else count):
            session_id, html = self.queue.pop(0)
            self.messages.append((session_id, html))
            self.on_delivered(session_id)


def stamped(record: dict, when: float) -> dict:
    """A record carrying a timestamp, the way Claude Code writes them."""
    record["timestamp"] = datetime.fromtimestamp(when, timezone.utc).isoformat()
    return record


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


# -- after the bridge was off ------------------------------------------------
async def test_a_chat_begun_while_the_bridge_was_off_is_sent_from_its_first_word(
        config, tmp_path, chat_file):
    state = State(tmp_path / "state.db")
    state.set_last_alive(time.time() - 3600)        # it last ran an hour ago
    chat_file(r"C:\Code\app", "s1",
              [stamped(user_text("written while it was off"), time.time())])
    sink = FakeSink()

    await Mirror(config, sink, state).tick()

    assert any("written while it was off" in html for html in sink.texts())
    assert state.get_mirrored_from("s1") == 0


async def test_a_chat_older_than_the_last_run_is_still_followed_from_now(
        config, tmp_path, chat_file, monkeypatch):
    """There while the bridge ran, but not followed - say, a project only now
    added to WATCH_PROJECTS. Its history is not dumped unasked."""
    monkeypatch.setattr(mirror_module, "started_at",
                        lambda path: time.time() - 7200)
    state = State(tmp_path / "state.db")
    state.set_last_alive(time.time() - 3600)
    chat_file(r"C:\Code\app", "s1", [user_text("ancient")])
    sink = FakeSink()

    await Mirror(config, sink, state).tick()

    assert sink.texts() == []


async def test_skipped_history_is_recorded_so_it_can_be_sent_later(
        config, tmp_path, chat_file):
    path = chat_file(r"C:\Code\app", "s1", [user_text("old news")])
    mirror, sink, state = build(config, tmp_path)

    await mirror.tick()

    assert sink.texts() == []
    assert state.get_mirrored_from("s1") == path.stat().st_size


async def test_a_running_bridge_records_that_it_is_alive(config, tmp_path):
    mirror, _, state = build(config, tmp_path)
    before = time.time()

    await mirror.tick()

    assert state.last_alive() >= before


# -- catching up -------------------------------------------------------------
async def test_catching_up_sends_history_that_was_skipped(
        config, tmp_path, chat_file):
    chat_file(r"C:\Code\app", "s1", [user_text("old news")])
    first, _, state = build(config, tmp_path)
    await first.tick()                          # followed from now: nothing posted

    sink = FakeSink()
    await Mirror(config, sink, state, catch_up=True).tick()

    assert any("old news" in html for html in sink.texts())
    assert state.get_mirrored_from("s1") == 0


async def test_catching_up_sends_a_chat_never_followed_from_its_first_word(
        config, tmp_path, chat_file):
    """A machine being set up: nothing has been sent from it yet."""
    chat_file(r"C:\Code\app", "s1", [user_text("from before the bridge")])
    sink = FakeSink()

    await Mirror(config, sink, State(tmp_path / "state.db"),
                 catch_up=True).tick()

    assert any("from before the bridge" in html for html in sink.texts())


async def test_catching_up_again_sends_nothing_twice(config, tmp_path, chat_file):
    chat_file(r"C:\Code\app", "s1", [user_text("old news")])
    first, _, state = build(config, tmp_path)
    await first.tick()
    await Mirror(config, FakeSink(), state, catch_up=True).tick()

    again = FakeSink()
    await Mirror(config, again, state, catch_up=True).tick()

    assert again.texts() == []


async def test_a_catch_up_request_is_cleared_once_acted_on(config, tmp_path):
    state = State(tmp_path / "state.db")
    state.request_catch_up()

    await Mirror(config, FakeSink(), state, catch_up=True).tick()

    assert state.catch_up_requested() is False


async def test_a_chat_whose_topic_began_part_way_is_given_a_fresh_one(
        config, tmp_path, chat_file):
    chat_file(r"C:\Code\app", "s1", [user_text("old news")])
    first, _, state = build(config, tmp_path)
    await first.tick()
    state.set_topic("s1", 77, "app: news")
    state.count_post("s1")                      # its topic holds later messages

    await Mirror(config, FakeSink(), state, catch_up=True).tick()

    assert state.renewing("s1") is True


# -- nothing lost ------------------------------------------------------------
async def test_the_read_position_waits_until_messages_are_posted(
        config, tmp_path, chat_file):
    path = chat_file(r"C:\Code\app", "s1",
                     [user_text("one"), assistant_text("two")])
    state = State(tmp_path / "state.db")
    sink = QueueSink()
    mirror = Mirror(config, sink, state, backfill=True)

    await mirror.tick()
    assert state.get_offset("s1") == 0          # read, but nothing posted yet

    sink.deliver()
    assert state.get_offset("s1") == path.stat().st_size


async def test_messages_still_queued_when_it_stops_are_sent_after_a_restart(
        config, tmp_path, chat_file):
    chat_file(r"C:\Code\app", "s1",
              [user_text("one", uuid="u1"), assistant_text("two", uuid="a1")])
    state = State(tmp_path / "state.db")
    sink = QueueSink()
    await Mirror(config, sink, state, backfill=True).tick()
    sink.deliver(1)                             # stopped with one still queued

    after = FakeSink()
    await Mirror(config, after, state).tick()

    assert len(after.texts()) == 1
    assert "two" in after.texts()[0]


async def test_a_tool_call_still_waiting_for_its_result_survives_a_restart(
        config, tmp_path, chat_file):
    chat_file(r"C:\Code\app", "s1", [
        assistant_text("let me check"),
        tool_use("Bash", {"command": "pytest", "description": "Run tests"}),
    ])
    state = State(tmp_path / "state.db")
    await Mirror(config, FakeSink(), state, backfill=True).tick()
    chat_file(r"C:\Code\app", "s1", [tool_result("all good")])

    after = FakeSink()
    await Mirror(config, after, state).tick()

    assert len(after.texts()) == 1
    assert "pytest" in after.texts()[0] and "all good" in after.texts()[0]


async def test_a_tool_queued_behind_text_written_after_its_call_survives_a_restart(
        config, tmp_path, chat_file):
    """The tool's message begins at its call but is sent after the text that
    followed it, so the first queued message is not the earliest."""
    chat_file(r"C:\Code\app", "s1", [
        tool_use("Bash", {"command": "pytest", "description": "Run tests"}),
        assistant_text("while that runs"),
        tool_result("all good"),
    ])
    state = State(tmp_path / "state.db")
    await Mirror(config, QueueSink(), state, backfill=True).tick()
    assert state.get_offset("s1") == 0          # nothing posted yet

    after = FakeSink()
    await Mirror(config, after, state).tick()

    assert any("all good" in html for html in after.texts())


async def test_a_message_that_cannot_be_formatted_does_not_take_the_rest_with_it(
        config, tmp_path, chat_file, monkeypatch):
    chat_file(r"C:\Code\app", "s1", [assistant_text("poison", uuid="a1"),
                                     assistant_text("fine", uuid="a2")])
    real = mirror_module.render

    def render(event):
        if event.text == "poison":
            raise ValueError("cannot format this")
        return real(event)

    monkeypatch.setattr(mirror_module, "render", render)
    mirror, sink, _ = build(config, tmp_path, backfill=True)

    await mirror.tick()

    assert any("fine" in html for html in sink.texts())


# -- a long backlog ----------------------------------------------------------
def _numbered(count: int) -> list[dict]:
    return [assistant_text(f"message {n}", uuid=f"a{n}") for n in range(count)]


def _numbers(texts: list[str]) -> list[int]:
    import re
    return [int(re.search(r"message (\d+)", text).group(1)) for text in texts]


async def test_a_long_backlog_waits_while_the_send_queue_is_full(
        config, tmp_path, chat_file, monkeypatch):
    monkeypatch.setattr(mirror_module, "SLICE_BYTES", 100)
    chat_file(r"C:\Code\app", "s1", _numbered(20))
    sink = QueueSink(room=0)

    await Mirror(config, sink, State(tmp_path / "state.db"),
                 backfill=True).tick()

    assert sink.queue == []


async def test_a_long_backlog_is_read_a_slice_at_a_time_and_in_order(
        config, tmp_path, chat_file, monkeypatch):
    monkeypatch.setattr(mirror_module, "SLICE_BYTES", 400)
    chat_file(r"C:\Code\app", "s1", _numbered(20))
    sink = QueueSink()
    mirror = Mirror(config, sink, State(tmp_path / "state.db"), backfill=True)

    await mirror.tick()
    assert 0 < len(sink.queue) < 20

    while not mirror.caught_up():
        await mirror.tick()
    assert _numbers([html for _, html in sink.queue]) == list(range(20))


async def test_once_keeps_reading_until_every_chat_is_read_to_its_end(
        config, tmp_path, chat_file, monkeypatch):
    monkeypatch.setattr(mirror_module, "SLICE_BYTES", 400)
    chat_file(r"C:\Code\app", "s1", _numbered(20))
    sink = QueueSink()
    mirror = Mirror(config, sink, State(tmp_path / "state.db"), backfill=True)

    assert await mirror.run(once=True) == 20
    assert len(sink.queue) == 20


# -- copies from a backup folder ---------------------------------------------
async def test_a_deleted_chat_found_in_an_older_copy_is_not_sent_again(
        config, tmp_path, chat_file, claude_home):
    live = chat_file(r"C:\Code\app", "s1",
                     [user_text("one", uuid="u1"), assistant_text("two", uuid="a1")])
    mirror, sink, state = build(config, tmp_path, backfill=True)
    await mirror.tick()
    sent = len(sink.texts())
    # Claude Code deletes it; the weekly copy was taken before "two" was said.
    folder = claude_home.parent / "backup" / live.parent.name
    folder.mkdir(parents=True)
    (folder / live.name).write_bytes(live.read_bytes()[: live.read_bytes().index(b"\n") + 1])
    live.unlink()

    again = FakeSink()
    later = Mirror(replace(config, chat_backups=(str(claude_home.parent / "backup"),)),
                   again, state)
    await later.tick()

    assert sent == 2 and again.texts() == []


async def test_a_chat_known_only_from_a_backup_can_be_sent_again_in_full(
        config, tmp_path, chat_file, claude_home):
    live = chat_file(r"C:\Code\app", "s1", [user_text("kept safe")])
    state = State(tmp_path / "state.db")
    state.set_offset("s1", live, live.stat().st_size, mirrored_from=0)
    folder = claude_home.parent / "backup" / live.parent.name
    folder.mkdir(parents=True)
    (folder / live.name).write_bytes(live.read_bytes())
    live.unlink()
    state.restart_chat("s1", renew_topic=False)

    sink = FakeSink()
    await Mirror(replace(config, chat_backups=(str(claude_home.parent / "backup"),)),
                 sink, state).tick()

    assert any("kept safe" in html for html in sink.texts())


# -- archived chats sent again -----------------------------------------------
class ClosingSink(FakeSink):
    def __init__(self, state):
        super().__init__()
        self.state = state
        self.closed: list[str] = []

    async def close_topic(self, session_id):
        self.closed.append(session_id)
        self.state.set_closed(session_id, True)
        return True


async def test_an_archived_chat_sent_again_is_archived_once_all_of_it_is_posted(
        config, tmp_path, chat_file):
    chat_file(r"C:\Code\app", "s1", [user_text("one"), assistant_text("two")])
    state = State(tmp_path / "state.db")
    state.set_topic("s1", None, "app: chat")
    state.forget_topic("s1", rearchive=True)
    sink = ClosingSink(state)

    await Mirror(config, sink, state, backfill=True).tick()

    assert sink.closed == ["s1"]
    assert state.rearchive_due("s1") is False


async def test_an_archived_chat_is_not_closed_while_still_being_sent(
        config, tmp_path, chat_file):
    chat_file(r"C:\Code\app", "s1", [user_text("one"), assistant_text("two")])
    state = State(tmp_path / "state.db")
    state.set_topic("s1", None, "app: chat")
    state.forget_topic("s1", rearchive=True)
    sink = QueueSink()
    closed: list[str] = []

    async def close_topic(session_id):
        closed.append(session_id)
        return True

    sink.close_topic = close_topic
    mirror = Mirror(config, sink, state, backfill=True)

    await mirror.tick()
    assert closed == []                     # still queued, not yet posted

    sink.deliver()
    await mirror.tick()
    assert closed == ["s1"]


async def test_with_a_backlog_the_chat_in_use_now_goes_first(
        config, tmp_path, chat_file, monkeypatch):
    import os
    monkeypatch.setattr(mirror_module, "SLICE_BYTES", 100)
    old = chat_file(r"C:\Code\app", "old", _numbered(10))
    chat_file(r"C:\Code\app", "new", _numbered(10))
    os.utime(old, (1_000_000_000, 1_000_000_000))       # untouched for years
    sink = QueueSink(room=1)

    await Mirror(config, sink, State(tmp_path / "state.db"), backfill=True).tick()

    assert {session for session, _ in sink.queue} == {"new"}
