"""/resend starts a chat's topic over; /mute holds a chat back until /unmute."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from bridge.approval import ApprovalService
from bridge.echo import EchoGuard
from bridge.inbox import KEEP, RESEND, Inbox
from bridge.mirror import Mirror
from bridge.runner import Runner
from bridge.state import State
from tests.conftest import assistant_text, title, user_text
from tests.test_mirror import FakeSink, QueueSink, build, config  # noqa: F401


# -- the engine --------------------------------------------------------------
async def test_a_muted_chat_is_held_and_sent_in_order_once_unmuted(
        config, tmp_path, chat_file):
    chat_file(r"C:\Code\app", "s1", [user_text("before", uuid="u1")])
    mirror, sink, state = build(config, tmp_path, backfill=True)
    await mirror.tick()
    state.set_muted("s1", True)
    chat_file(r"C:\Code\app", "s1", [assistant_text("while muted", uuid="a1"),
                                     assistant_text("still muted", uuid="a2")])

    await mirror.tick()
    assert len(sink.texts()) == 1               # nothing new while muted

    state.set_muted("s1", False)
    await mirror.tick()
    assert "while muted" in sink.texts()[1]
    assert "still muted" in sink.texts()[2]


async def test_once_does_not_wait_for_a_muted_chat(config, tmp_path, chat_file):
    path = chat_file(r"C:\Code\app", "s1", [user_text("hello")])
    state = State(tmp_path / "state.db")
    state.set_offset("s1", path, 0, mirrored_from=0)
    state.set_muted("s1", True)

    assert await Mirror(config, FakeSink(), state).run(once=True) == 0


async def test_resend_sends_the_whole_chat_again_into_a_new_topic(
        config, tmp_path, chat_file):
    chat_file(r"C:\Code\app", "s1", [user_text("one"), assistant_text("two")])
    mirror, sink, state = build(config, tmp_path, backfill=True)
    await mirror.tick()
    state.set_topic("s1", 7, "app: chat")

    await mirror.resend("s1")
    await mirror.tick()

    assert state.renewing("s1") is True         # the old topic goes first
    assert len(sink.texts()) == 4               # both messages, twice


async def test_resend_drops_what_was_still_queued_for_that_chat(
        config, tmp_path, chat_file):
    chat_file(r"C:\Code\app", "s1", [user_text("one"), assistant_text("two")])
    chat_file(r"C:\Code\app", "s2", [user_text("other chat")])
    state = State(tmp_path / "state.db")
    sink = QueueSink()

    async def discard(session_id):
        before = len(sink.queue)
        sink.queue = [(s, m) for s, m in sink.queue if s != session_id]
        return before - len(sink.queue)

    sink.discard = discard
    mirror = Mirror(config, sink, state, backfill=True)
    await mirror.tick()

    await mirror.resend("s1")

    assert [session for session, _ in sink.queue] == ["s2"]
    assert state.get_offset("s1") == 0


async def test_resend_unmutes_the_chat(config, tmp_path, chat_file):
    chat_file(r"C:\Code\app", "s1", [user_text("one")])
    mirror, _, state = build(config, tmp_path, backfill=True)
    await mirror.tick()
    state.set_muted("s1", True)

    await mirror.resend("s1")

    assert state.is_muted("s1") is False


async def test_a_resend_part_way_through_a_pass_stops_its_old_messages(
        config, tmp_path, chat_file):
    """Old messages still being handed over must not open the new topic."""
    chat_file(r"C:\Code\app", "s1", [user_text("one", uuid="u1"),
                                     title("named"),
                                     assistant_text("two", uuid="a1")])
    state = State(tmp_path / "state.db")
    sink = FakeSink()
    mirror = Mirror(config, sink, state, backfill=True)
    titles = []

    async def set_title(session_id, text):
        titles.append(text)
        if len(titles) == 2:                    # the title met inside the chat
            await mirror.resend("s1")

    sink.set_title = set_title
    await mirror.tick()

    assert len(sink.texts()) == 1 and "one" in sink.texts()[0]


# -- the commands ------------------------------------------------------------
class FakeMessage:
    def __init__(self, thread_id: int = 7, text: str = ""):
        self.message_thread_id = thread_id
        self.chat_id = -100
        self.text = text
        self.replies: list[str] = []

    async def reply_text(self, text, **_):
        self.replies.append(text)


class FakeBot:
    def __init__(self):
        self.sent: list[dict] = []

    async def send_message(self, **kwargs):
        self.sent.append(kwargs)


class FakeMirror:
    def __init__(self):
        self.resent: list[str] = []

    async def resend(self, session_id):
        self.resent.append(session_id)


class FakeQuery:
    def __init__(self, data: str):
        self.data = data
        self.answers: list[str] = []
        self.edits: list[str] = []
        self.message = None

    async def answer(self, text="", **_):
        self.answers.append(text)

    async def edit_message_text(self, text, **_):
        self.edits.append(text)


def update_with(message=None, query=None):
    return SimpleNamespace(effective_user=SimpleNamespace(id=42),
                           effective_message=message, callback_query=query)


@pytest.fixture
def inbox(config, tmp_path, chat_file):
    chat_file(r"C:\Code\app", "s1", [user_text("hello")])
    state = State(tmp_path / "state.db")
    state.set_offset("s1", Path("x"), 0, mirrored_from=0)
    state.set_topic("s1", 7, "app: chat", r"C:\Code\app")
    approval = ApprovalService(state)
    box = Inbox(config, state, Runner(config, approval), EchoGuard(), approval)
    box.bot = FakeBot()
    box.mirror = FakeMirror()
    return box


async def test_mute_holds_the_chat_and_unmute_lets_it_go(inbox):
    message = FakeMessage()

    await inbox.on_mute(update_with(message), None)
    assert inbox.state.is_muted("s1") is True

    await inbox.on_unmute(update_with(message), None)
    assert inbox.state.is_muted("s1") is False
    assert "Muted" in message.replies[0] and "Unmuted" in message.replies[1]


async def test_a_message_typed_in_a_muted_topic_is_not_run(inbox):
    inbox.state.set_muted("s1", True)
    ran = []
    inbox.runner.run = lambda *args: ran.append(args)
    message = FakeMessage(text="do something")

    await inbox.on_text(update_with(message), None)

    assert ran == []
    assert "muted" in message.replies[0]


async def test_resend_asks_first_and_does_nothing_until_confirmed(inbox):
    await inbox.on_resend(update_with(FakeMessage()), None)

    buttons = [button.callback_data for row in
               inbox.bot.sent[0]["reply_markup"].inline_keyboard for button in row]
    assert buttons == [f"{RESEND}|s1", f"{KEEP}|s1"]
    assert inbox.mirror.resent == []


async def test_confirming_resend_starts_the_chat_over(inbox):
    query = FakeQuery(f"{RESEND}|s1")

    await inbox.on_button(update_with(query=query), None)

    assert inbox.mirror.resent == ["s1"]


async def test_keeping_the_topic_changes_nothing(inbox):
    query = FakeQuery(f"{KEEP}|s1")

    await inbox.on_button(update_with(query=query), None)

    assert inbox.mirror.resent == []
    assert query.edits == ["Left as it is."]


async def test_resend_refuses_when_nothing_is_left_to_send_it_from(
        inbox, claude_home):
    for chat in (claude_home / "projects").glob("*/*.jsonl"):
        chat.unlink()
    message = FakeMessage()

    await inbox.on_resend(update_with(message), None)

    assert inbox.bot.sent == []
    assert "only copy left" in message.replies[0]
