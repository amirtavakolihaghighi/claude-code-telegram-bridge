"""Where mirrored messages go: a real Telegram group, or the console.

Telegram allows a bot roughly 20 messages a minute into one group. A busy turn
easily produces more than that, so messages are queued and adjacent ones for
the same chat are merged into a single post. A burst of ten tool calls becomes
two or three messages instead of ten, and the mirror keeps up.
"""
from __future__ import annotations

import asyncio
import logging

from telegram import Bot
from telegram.constants import ParseMode
from telegram.error import BadRequest, RetryAfter, TelegramError

from .format import OutMessage
from .state import State

log = logging.getLogger(__name__)

TOPIC_NAME_LIMIT = 128
SEND_SPACING = 3.2      # seconds between posts, to stay under ~20/minute
MERGE_LIMIT = 3800      # characters; Telegram's hard limit is 4096
MERGE_MAX = 8           # never glue more than this many together


def topic_name(session_id: str, title: str | None) -> str:
    name = (title or f"chat {session_id[:8]}").strip()
    return name[:TOPIC_NAME_LIMIT] or f"chat {session_id[:8]}"


class ConsoleSink:
    """Dry run: print what would be posted. No bot token needed."""

    def __init__(self) -> None:
        self.titles: dict[str, str] = {}

    async def start(self) -> None:
        return

    async def flush(self) -> None:
        return

    async def stop(self) -> None:
        return

    async def set_title(self, session_id: str, title: str) -> None:
        if self.titles.get(session_id) != title:
            self.titles[session_id] = title
            print(f"\n=== topic renamed -> {topic_name(session_id, title)} ===")

    async def ensure_topic(self, session_id: str) -> None:
        print(f"=== would create topic: "
              f"{topic_name(session_id, self.titles.get(session_id))} ===")

    async def send(self, session_id: str, message: OutMessage) -> None:
        label = self.titles.get(session_id) or session_id[:8]
        bell = "quiet" if message.silent else "NOTIFY"
        print(f"\n--- [{label}] ({bell}) ---")
        print(message.html)


class TelegramSink:
    """Posts into one topic per chat inside a Topics-enabled supergroup."""

    def __init__(self, bot: Bot, group_id: int, state: State):
        self.bot = bot
        self.group_id = group_id
        self.state = state
        self._pending: list[tuple[str, OutMessage]] = []
        self._wake = asyncio.Event()
        self._idle = asyncio.Event()
        self._idle.set()
        self._worker: asyncio.Task | None = None

    # -- lifecycle -------------------------------------------------------
    async def start(self) -> None:
        if self._worker is None:
            self._worker = asyncio.create_task(self._drain())

    async def flush(self) -> None:
        """Wait until everything queued has been posted."""
        await self._idle.wait()

    async def stop(self) -> None:
        if self._worker is not None:
            self._worker.cancel()
            try:
                await self._worker
            except asyncio.CancelledError:
                pass
            self._worker = None

    # -- queue -----------------------------------------------------------
    async def send(self, session_id: str, message: OutMessage) -> None:
        self._pending.append((session_id, message))
        self._idle.clear()
        self._wake.set()

    def _take_batch(self) -> tuple[str, str, bool]:
        """Pop one post: the next message plus any that can ride along with it."""
        session_id, first = self._pending.pop(0)
        parts = [first.html]
        silent = first.silent
        total = len(first.html)

        while self._pending and len(parts) < MERGE_MAX:
            next_session, candidate = self._pending[0]
            if next_session != session_id:
                break
            if total + len(candidate.html) + 2 > MERGE_LIMIT:
                break
            self._pending.pop(0)
            parts.append(candidate.html)
            total += len(candidate.html) + 2
            # If anything in the batch deserves a ping, the whole post pings.
            silent = silent and candidate.silent

        return session_id, "\n\n".join(parts), silent

    async def _drain(self) -> None:
        posted = 0
        while True:
            if not self._pending:
                self._idle.set()
                self._wake.clear()
                await self._wake.wait()
                continue
            session_id, text, silent = self._take_batch()
            await self._deliver(session_id, text, silent)
            posted += 1
            # A backfill takes hours; say where it has got to now and then.
            if posted % 50 == 0:
                log.info("posted %d, %d message(s) still queued",
                         posted, len(self._pending))
            await asyncio.sleep(SEND_SPACING)

    # -- topics ----------------------------------------------------------
    async def ensure_topic(self, session_id: str) -> int | None:
        """Create the topic now, before there is anything to post in it."""
        return await self._ensure_thread(session_id)

    async def _ensure_thread(self, session_id: str) -> int | None:
        thread_id, title = self.state.get_topic(session_id)
        if thread_id is not None:
            return thread_id
        name = topic_name(session_id, title)

        # Creating topics has its own, stricter flood limit than sending.
        for attempt in range(4):
            try:
                topic = await self.bot.create_forum_topic(chat_id=self.group_id,
                                                          name=name)
            except RetryAfter as exc:
                wait = float(exc.retry_after) + 1
                log.info("topic flood limit, waiting %.0fs", wait)
                await asyncio.sleep(wait)
                continue
            except TelegramError as exc:
                log.error("could not create topic %r: %s", name, exc)
                return None
            self.state.set_topic(session_id, topic.message_thread_id, title)
            log.info("created topic %r (thread %s)", name, topic.message_thread_id)
            return topic.message_thread_id

        log.error("gave up creating topic %r", name)
        return None

    async def set_title(self, session_id: str, title: str) -> None:
        thread_id, current = self.state.get_topic(session_id)
        if current == title:
            return
        self.state.set_topic(session_id, None, title)
        if thread_id is None:
            return                      # topic not created yet; name is used at creation
        try:
            await self.bot.edit_forum_topic(
                chat_id=self.group_id,
                message_thread_id=thread_id,
                name=topic_name(session_id, title),
            )
            log.info("renamed topic -> %r", topic_name(session_id, title))
        except TelegramError as exc:
            log.warning("could not rename topic: %s", exc)

    # -- delivery --------------------------------------------------------
    async def _deliver(self, session_id: str, text: str, silent: bool) -> None:
        thread_id = await self._ensure_thread(session_id)
        if thread_id is None:
            return
        # A long backfill can outlive a wobbly connection, so back off and keep
        # trying rather than dropping a message on the floor.
        backoff = [2, 5, 10, 20, 40, 60, 60, 60]
        for attempt, pause in enumerate(backoff, start=1):
            try:
                await self.bot.send_message(
                    chat_id=self.group_id,
                    message_thread_id=thread_id,
                    text=text,
                    parse_mode=ParseMode.HTML,
                    disable_notification=silent,
                    disable_web_page_preview=True,
                )
                return
            except RetryAfter as exc:
                wait = float(exc.retry_after) + 1
                log.info("rate limited, waiting %.0fs", wait)
                await asyncio.sleep(wait)
            except BadRequest as exc:
                # Malformed HTML or a deleted topic: retrying cannot help.
                log.error("message rejected for %s: %s", session_id[:8], exc)
                return
            except TelegramError as exc:
                log.warning("send failed (attempt %d/%d): %s",
                            attempt, len(backoff), exc)
                await asyncio.sleep(pause)
        log.error("giving up on a message for session %s", session_id)
