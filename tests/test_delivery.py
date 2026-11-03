"""Getting messages into Telegram: through outages, and into the right topic."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from telegram.error import BadRequest, NetworkError

from bridge import sinks
from bridge.format import OutMessage
from bridge.sinks import TelegramSink
from bridge.state import State


class FakeBot:
    """Stands in for Telegram. Offline for the first `offline` sends."""

    def __init__(self, offline: int = 0, refuse: bool = False,
                 explode: int = 0, may_delete: bool = True):
        self.offline = offline
        self.refuse = refuse
        self.explode = explode
        self.may_delete = may_delete
        self.attempts = 0
        self.sent: list[tuple[int, str]] = []
        self.created: list[str] = []
        self.deleted: list[int] = []
        self.renamed: list[tuple[int, str]] = []
        self.closed: list[int] = []

    async def delete_forum_topic(self, chat_id, message_thread_id):
        if not self.may_delete:
            raise BadRequest("Not enough rights to delete the topic")
        self.deleted.append(message_thread_id)

    async def send_message(self, chat_id, message_thread_id, text, **_):
        self.attempts += 1
        if self.explode:
            self.explode -= 1
            raise ValueError("something nobody expected")
        if self.offline:
            self.offline -= 1
            raise NetworkError("httpx.ConnectError: All connection attempts failed")
        if self.refuse:
            raise BadRequest("Can't parse entities")
        self.sent.append((message_thread_id, text))

    async def create_forum_topic(self, chat_id, name, **_):
        self.created.append(name)
        return SimpleNamespace(message_thread_id=500 + len(self.created))

    async def edit_forum_topic(self, chat_id, message_thread_id, name=None, **_):
        self.renamed.append((message_thread_id, name))

    async def close_forum_topic(self, chat_id, message_thread_id):
        self.closed.append(message_thread_id)

    async def reopen_forum_topic(self, chat_id, message_thread_id):
        return True

    async def get_forum_topic_icon_stickers(self):
        return []


@pytest.fixture(autouse=True)
def no_waiting(monkeypatch):
    """Outages are simulated, so there is nothing to wait out."""
    monkeypatch.setattr(sinks, "BACKOFF", (0,))
    monkeypatch.setattr(sinks, "SEND_SPACING", 0)


def make(tmp_path, bot) -> tuple[TelegramSink, State]:
    state = State(tmp_path / "state.db")
    state.set_topic("s1", 7, "app: chat", r"C:\Code\app")
    return TelegramSink(bot, -1001234567890, state), state


async def drain(sink: TelegramSink) -> None:
    await sink.start()
    await asyncio.wait_for(sink.flush(), timeout=5)
    await sink.stop()


# -- outages -----------------------------------------------------------------
async def test_a_message_waits_out_an_outage_instead_of_being_dropped(tmp_path):
    """The old limit was eight tries, about four minutes; then it was gone."""
    bot = FakeBot(offline=30)
    sink, _ = make(tmp_path, bot)

    await sink._deliver("s1", "hello", silent=True)

    assert bot.sent == [(7, "hello")]
    assert bot.attempts == 31


async def test_a_message_telegram_refuses_is_not_tried_again(tmp_path):
    bot = FakeBot(refuse=True)
    sink, _ = make(tmp_path, bot)

    await sink._deliver("s1", "<b>broken", silent=True)

    assert bot.sent == []
    assert bot.attempts == 1


# -- reporting back ----------------------------------------------------------
async def test_every_message_is_reported_once_it_has_gone_even_when_merged(
        tmp_path):
    bot = FakeBot()
    sink, _ = make(tmp_path, bot)
    reported: list[str] = []
    sink.on_delivered = reported.append
    for text in ("one", "two", "three"):
        await sink.send("s1", OutMessage(text))

    await drain(sink)

    assert len(bot.sent) == 1                   # merged into one post
    assert reported == ["s1", "s1", "s1"]


async def test_one_unexpected_error_does_not_stop_everything_after_it(tmp_path):
    """It used to end the sending loop for good, silently."""
    bot = FakeBot(explode=1)
    sink, _ = make(tmp_path, bot)
    reported: list[str] = []
    sink.on_delivered = reported.append
    await sink.send("s1", OutMessage("doomed"))
    await sink.send("s2", OutMessage("fine"))

    await drain(sink)

    assert [text for _, text in bot.sent] == ["fine"]
    assert reported == ["s1", "s2"]


async def test_each_post_is_counted_against_its_topic(tmp_path):
    bot = FakeBot()
    sink, state = make(tmp_path, bot)

    await sink._deliver("s1", "hello", silent=True)

    assert state.posted("s1") == 1


async def test_messages_full_of_emoji_are_not_merged_past_the_limit(tmp_path):
    """Telegram counts an emoji as two; counting it as one let a merged post
    run over."""
    sink, _ = make(tmp_path, FakeBot())
    await sink.send("s1", OutMessage("\U0001f916" * 1500))
    await sink.send("s1", OutMessage("\U0001f916" * 1500))

    assert sink._take_batch().count == 1


async def test_the_queue_says_when_it_is_full(tmp_path):
    sink, _ = make(tmp_path, FakeBot())
    assert sink.room() == sinks.HIGH_WATER

    for n in range(sinks.HIGH_WATER):
        await sink.send("s1", OutMessage(str(n)))

    assert sink.room() == 0


# -- starting a chat's topic afresh ------------------------------------------
async def test_a_chat_sent_again_has_its_old_topic_deleted_and_gets_a_new_one(
        tmp_path):
    bot = FakeBot()
    sink, state = make(tmp_path, bot)
    state.count_post("s1")
    state.restart_chat("s1", renew_topic=True)

    await sink._deliver("s1", "the very first message", silent=True)

    assert bot.deleted == [7]
    assert state.get_topic("s1")[0] == 501
    assert bot.sent == [(501, "the very first message")]
    assert state.renewing("s1") is False
    assert state.posted("s1") == 1


async def test_a_topic_that_may_not_be_deleted_is_closed_and_labelled_instead(
        tmp_path):
    bot = FakeBot(may_delete=False)
    sink, state = make(tmp_path, bot)
    state.restart_chat("s1", renew_topic=True)

    await sink._deliver("s1", "first", silent=True)

    assert bot.renamed == [(7, "app: chat" + sinks.PARTIAL)]
    assert bot.closed == [7]
    assert state.get_topic("s1")[0] == 501


async def test_an_archived_chat_sent_again_is_due_to_be_archived_again(tmp_path):
    bot = FakeBot()
    sink, state = make(tmp_path, bot)
    state.set_closed("s1", True)
    state.restart_chat("s1", renew_topic=True)

    await sink._deliver("s1", "first", silent=True)

    assert state.rearchive_due("s1") is True
    assert state.is_closed("s1") is False       # open while it fills up


async def test_only_the_first_post_after_catching_up_opens_a_fresh_topic(
        tmp_path):
    bot = FakeBot()
    sink, state = make(tmp_path, bot)
    state.restart_chat("s1", renew_topic=True)

    await sink._deliver("s1", "first", silent=True)
    await sink._deliver("s1", "second", silent=True)

    assert bot.created == ["app: chat"]
    assert [thread for thread, text in bot.sent if text == "second"] == [501]
