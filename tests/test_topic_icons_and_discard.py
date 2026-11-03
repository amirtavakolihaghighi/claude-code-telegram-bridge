"""Topic icons, and dropping one chat's queued messages."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from telegram.error import BadRequest, NetworkError, RetryAfter

from bridge import sinks
from bridge.format import OutMessage
from tests.test_delivery import FakeBot, make, no_waiting  # noqa: F401


@pytest.fixture
def instant(monkeypatch):
    """Restyling pauses between topics; there is no limit to respect here."""
    async def no_wait(_seconds):
        return None
    monkeypatch.setattr(sinks.asyncio, "sleep", no_wait)


def with_icons(sink, style: str) -> None:
    sink.config = SimpleNamespace(topic_icons=style)


# -- dropping a chat's queue -------------------------------------------------
async def test_discard_drops_only_that_chats_queued_messages(tmp_path):
    sink, _ = make(tmp_path, FakeBot())
    for session, text in (("s1", "a"), ("s2", "b"), ("s1", "c")):
        await sink.send(session, OutMessage(text))

    assert await sink.discard("s1") == 2
    assert [session for session, _ in sink._pending] == ["s2"]


async def test_discard_waits_for_a_post_already_under_way(tmp_path):
    """Otherwise that one message would still land - in the old topic, or as
    the first message of the new one."""
    started, release = asyncio.Event(), asyncio.Event()
    bot = FakeBot()
    original = bot.send_message

    async def slow_send(**kwargs):
        started.set()
        await release.wait()
        await original(**kwargs)

    bot.send_message = slow_send
    sink, _ = make(tmp_path, bot)
    await sink.send("s1", OutMessage("in flight"))
    await sink.start()
    await asyncio.wait_for(started.wait(), timeout=5)

    dropping = asyncio.create_task(sink.discard("s1"))
    await asyncio.sleep(0.05)
    assert not dropping.done()                  # still waiting for that post

    release.set()
    await asyncio.wait_for(dropping, timeout=5)
    assert [text for _, text in bot.sent] == ["in flight"]
    await sink.stop()


# -- topic icons -------------------------------------------------------------
async def test_with_default_icons_a_new_topic_gets_none(tmp_path):
    bot = FakeBot()
    created = []

    async def create(chat_id, name, **kwargs):
        created.append(kwargs)
        return SimpleNamespace(message_thread_id=900)

    bot.create_forum_topic = create
    sink, state = make(tmp_path, bot)
    with_icons(sink, "default")
    state.forget_topic("s1")

    await sink._deliver("s1", "hello", silent=True)

    assert created[0]["icon_custom_emoji_id"] is None


async def test_restyling_to_default_clears_every_topics_icon(tmp_path, instant):
    bot = FakeBot()
    edits = []

    async def edit(chat_id, message_thread_id, icon_custom_emoji_id=None, **_):
        edits.append((message_thread_id, icon_custom_emoji_id))

    bot.edit_forum_topic = edit
    sink, state = make(tmp_path, bot)
    state.set_topic("s2", 8, "app: other", r"C:\Code\app")
    with_icons(sink, "default")

    assert await sink.restyle_topics() == (2, 0)
    assert sorted(edits) == [(7, ""), (8, "")]


async def test_restyling_waits_out_a_flood_limit_instead_of_skipping(
        tmp_path, instant):
    bot = FakeBot()
    tries = []

    async def edit(chat_id, message_thread_id, **_):
        tries.append(message_thread_id)
        if len(tries) == 1:
            raise RetryAfter(3)

    bot.edit_forum_topic = edit
    sink, _ = make(tmp_path, bot)
    with_icons(sink, "default")

    assert await sink.restyle_topics() == (1, 0)
    assert tries == [7, 7]


async def test_a_topic_that_already_looks_right_counts_as_done(tmp_path, instant):
    bot = FakeBot()

    async def edit(chat_id, message_thread_id, **_):
        raise BadRequest("Topic_not_modified")

    bot.edit_forum_topic = edit
    sink, _ = make(tmp_path, bot)
    with_icons(sink, "default")

    assert await sink.restyle_topics() == (0, 0)


async def test_a_topic_out_of_reach_is_counted_so_it_is_tried_again(
        tmp_path, instant):
    bot = FakeBot()

    async def edit(chat_id, message_thread_id, **_):
        raise NetworkError("offline")

    bot.edit_forum_topic = edit
    sink, _ = make(tmp_path, bot)
    with_icons(sink, "default")

    assert await sink.restyle_topics() == (0, 1)
