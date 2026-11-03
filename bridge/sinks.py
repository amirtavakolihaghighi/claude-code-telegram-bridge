"""Where mirrored messages go: a real Telegram group, or the console.

Telegram allows a bot roughly 20 messages a minute into one group. A busy turn
easily produces more than that, so messages are queued and adjacent ones for
the same chat are merged into a single post. A burst of ten tool calls becomes
two or three messages instead of ten, and the mirror keeps up.

A message is only given up on when Telegram refuses it outright. Without a
connection the queue simply waits, however long that takes: the mirror's read
positions do not move past a message until it has been posted, so nothing is
lost even if the bridge is stopped in the meantime.
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Callable, NamedTuple

from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.error import BadRequest, RetryAfter, TelegramError

from .appearance import colour_for, icon_for
from .config import looks_secret
from .format import OutMessage
from .state import State

log = logging.getLogger(__name__)

TOPIC_NAME_LIMIT = 128
SEND_SPACING = 3.2      # seconds between posts, to stay under ~20/minute
MERGE_LIMIT = 3800      # characters; Telegram's hard limit is 4096
MERGE_MAX = 8           # never glue more than this many together
CLIP = chr(0x1F4CE)     # paperclip, on the offer button
# Queued messages beyond which the mirror stops reading backlog until there is
# room, so live messages are never stuck behind hours of history.
HIGH_WATER = 30
# Seconds between attempts while Telegram cannot be reached; the last repeats.
BACKOFF = (2, 5, 10, 20, 40, 60)
PARTIAL = " (partial copy)"


def _units(text: str) -> int:
    """Length as Telegram counts it, in UTF-16 units: an emoji is two. Taken
    over the markup too, which only makes it an overestimate."""
    return len(text.encode("utf-16-le")) // 2


def topic_name(session_id: str, title: str | None) -> str:
    name = (title or f"chat {session_id[:8]}").strip()
    return name[:TOPIC_NAME_LIMIT] or f"chat {session_id[:8]}"


class Batch(NamedTuple):
    """One post: a queued message, and any that could ride along with it."""
    session_id: str
    text: str
    silent: bool
    attach: str
    count: int          # how many queued messages it carries


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

    def __init__(self, bot: Bot, group_id: int, state: State, config=None):
        self.bot = bot
        self.group_id = group_id
        self.state = state
        self.config = config
        self._pending: list[tuple[str, OutMessage]] = []
        self._wake = asyncio.Event()
        self._idle = asyncio.Event()
        self._idle.set()
        self._worker: asyncio.Task | None = None
        self._icons: list[str] | None = None
        # Told about each message once it is posted or refused for good, so
        # read positions only move past what has actually gone out.
        self.on_delivered: Callable[[str], None] | None = None
        # Held while a batch is taken and posted, so a chat's queued messages
        # can be thrown away without one of them being half-way out.
        self._sending = asyncio.Lock()

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

    def room(self) -> int:
        """How many more messages the queue takes before backlog should wait."""
        return HIGH_WATER - len(self._pending)

    async def discard(self, session_id: str) -> int:
        """Drop this chat's queued messages, once any post of it already under
        way is finished. Returns how many were dropped."""
        async with self._sending:
            before = len(self._pending)
            self._pending = [(sid, m) for sid, m in self._pending
                             if sid != session_id]
            return before - len(self._pending)

    def offerable(self, path: str) -> bool:
        """Is this a file worth offering as an attachment?"""
        if not path or self.config is None:
            return False
        target = Path(path)
        if looks_secret(target) or target.suffix.lower() not in \
                self.config.attach_suffixes:
            return False
        try:
            return (target.is_file()
                    and target.stat().st_size <= self.config.max_attach_mb * 1e6)
        except OSError:
            return False

    def _take_batch(self) -> Batch:
        """Pop one post: the next message plus any that can ride along with it."""
        session_id, first = self._pending.pop(0)
        parts = [first.html]
        silent = first.silent
        total = _units(first.html)
        attach = first.attach if self.offerable(first.attach) else ""

        # A message carrying a button is posted on its own, so the button
        # cannot end up attached to somebody else's text.
        while self._pending and len(parts) < MERGE_MAX and not attach:
            next_session, candidate = self._pending[0]
            if next_session != session_id:
                break
            if candidate.attach and self.offerable(candidate.attach):
                break
            if total + _units(candidate.html) + 2 > MERGE_LIMIT:
                break
            self._pending.pop(0)
            parts.append(candidate.html)
            total += _units(candidate.html) + 2
            # If anything in the batch deserves a ping, the whole post pings.
            silent = silent and candidate.silent

        return Batch(session_id, "\n\n".join(parts), silent, attach, len(parts))

    async def _drain(self) -> None:
        posted = 0
        while True:
            if not self._pending:
                self._idle.set()
                self._wake.clear()
                await self._wake.wait()
                continue
            async with self._sending:
                if not self._pending:
                    continue        # discarded while waiting for the lock
                batch = self._take_batch()
                try:
                    await self._deliver(batch.session_id, batch.text,
                                        batch.silent, batch.attach)
                except asyncio.CancelledError:
                    raise       # stopping; the position still covers this batch
                except Exception:
                    # Before this, one surprise here ended the worker, and
                    # every message after it was silently never sent.
                    log.exception("could not post a message for %s",
                                  batch.session_id[:8])
                if self.on_delivered is not None:
                    for _ in range(batch.count):
                        self.on_delivered(batch.session_id)
            posted += 1
            # A backfill takes hours; say where it has got to now and then.
            if posted % 50 == 0:
                log.info("posted %d, %d message(s) still queued",
                         posted, len(self._pending))
            await asyncio.sleep(SEND_SPACING)

    # -- topics ----------------------------------------------------------
    async def ensure_topic(self, session_id: str) -> int | None:
        """Create the topic now, before there is anything to post in it."""
        try:
            return await self._ensure_thread(session_id)
        except TelegramError as exc:
            log.warning("could not create a topic for %s yet: %s",
                        session_id[:8], exc)
            return None

    async def _icon_ids(self) -> list[str]:
        """Telegram's built-in topic icons, fetched once."""
        if self._icons is None:
            try:
                stickers = await self.bot.get_forum_topic_icon_stickers()
                self._icons = [s.custom_emoji_id for s in stickers
                               if s.custom_emoji_id]
            except TelegramError as exc:
                # Not remembered: offline now is no reason to go without
                # icons for the rest of the run.
                log.warning("could not fetch topic icons: %s", exc)
                return []
        return self._icons

    def _project_icons(self) -> bool:
        return self.config is None or self.config.topic_icons == "project"

    async def _look_for(self, session_id: str) -> tuple[int | None, str | None]:
        project = self.state.project_for_session(session_id)
        if not project:
            return None, None
        icon = icon_for(project, await self._icon_ids()) \
            if self._project_icons() else None
        return colour_for(project), icon

    async def restyle_topics(self) -> tuple[int, int]:
        """Give every topic the icon TOPIC_ICONS asks for: its project's, or
        Telegram's default. Colour cannot be changed after creation, but the
        icon can.

        Returns how many changed, and how many could not be reached and are
        worth trying again later.
        """
        changed = unreachable = 0
        for session_id, thread_id, _, project in self.state.all_topics():
            if thread_id is None:
                continue
            if self._project_icons():
                icon = icon_for(project, await self._icon_ids()) if project \
                    else None
                if icon is None:
                    continue
            else:
                icon = ""               # an empty icon is Telegram's default
            for _ in range(5):
                try:
                    await self.bot.edit_forum_topic(chat_id=self.group_id,
                                                    message_thread_id=thread_id,
                                                    icon_custom_emoji_id=icon)
                    changed += 1
                    break
                except RetryAfter as exc:
                    await asyncio.sleep(float(exc.retry_after) + 1)
                except BadRequest as exc:
                    # Already looks like that, or deleted: nothing to retry.
                    if "not_modified" not in str(exc).lower():
                        log.warning("could not restyle thread %s: %s",
                                    thread_id, exc)
                    break
                except TelegramError as exc:
                    log.warning("could not restyle thread %s: %s", thread_id, exc)
                    await asyncio.sleep(5.0)
            else:
                unreachable += 1
            await asyncio.sleep(1.0)
        return changed, unreachable

    async def _ensure_thread(self, session_id: str) -> int | None:
        """The chat's topic, created if it has none yet.

        None means Telegram refused to create one. A lost connection raises
        instead, so the caller can wait and try again.
        """
        thread_id, title = self.state.get_topic(session_id)
        if thread_id is not None:
            return thread_id
        thread_id = await self._create_topic(session_id, title)
        if thread_id is not None:
            self.state.set_topic(session_id, thread_id, title)
        return thread_id

    async def _create_topic(self, session_id: str, title: str | None) -> int | None:
        name = topic_name(session_id, title)
        colour, icon = await self._look_for(session_id)
        # Creating topics has its own, stricter flood limit than sending.
        while True:
            try:
                topic = await self.bot.create_forum_topic(
                    chat_id=self.group_id, name=name,
                    icon_color=colour, icon_custom_emoji_id=icon)
            except RetryAfter as exc:
                wait = float(exc.retry_after) + 1
                log.info("topic flood limit, waiting %.0fs", wait)
                await asyncio.sleep(wait)
                continue
            except BadRequest as exc:
                log.error("could not create topic %r: %s", name, exc)
                return None
            log.info("created topic %r (thread %s)", name, topic.message_thread_id)
            return topic.message_thread_id

    async def _thread_for(self, session_id: str) -> int | None:
        if self.state.renewing(session_id):
            return await self._renew_topic(session_id)
        return await self._ensure_thread(session_id)

    async def _renew_topic(self, session_id: str) -> int | None:
        """Delete a chat's topic and start it afresh, to send it whole, in order.

        Telegram cannot put messages above ones already posted, so a topic
        missing part of its chat can only be put right by starting again. The
        steps run in this order so that stopping between any two of them
        leaves nothing to undo: deleting twice is harmless, and once the old
        topic is forgotten the chat simply gets a new one.
        """
        old_thread, title = self.state.get_topic(session_id)
        archived = self.state.is_closed(session_id)
        if old_thread is not None:
            await self._delete_topic(old_thread, topic_name(session_id, title))
        self.state.forget_topic(session_id, rearchive=archived)
        return await self._ensure_thread(session_id)

    async def _delete_topic(self, thread_id: int, name: str) -> None:
        try:
            await self.bot.delete_forum_topic(chat_id=self.group_id,
                                              message_thread_id=thread_id)
            log.info("deleted topic %r (thread %s) to send its chat again in "
                     "full", name, thread_id)
            return
        except BadRequest as exc:
            # Already gone, or the bot may not delete messages. Either way the
            # chat is sent again; an old copy that stays is at least labelled.
            log.warning("could not delete topic %s (%s); closing it instead",
                        thread_id, exc)
        for step in (
                lambda: self.bot.edit_forum_topic(
                    chat_id=self.group_id, message_thread_id=thread_id,
                    name=name[:TOPIC_NAME_LIMIT - len(PARTIAL)] + PARTIAL),
                lambda: self.bot.close_forum_topic(
                    chat_id=self.group_id, message_thread_id=thread_id)):
            try:
                await step()
            except BadRequest as exc:
                log.warning("could not close old topic %s: %s", thread_id, exc)

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
    async def close_topic(self, session_id: str) -> bool:
        thread_id, _ = self.state.get_topic(session_id)
        if thread_id is None:
            return False
        try:
            await self.bot.close_forum_topic(chat_id=self.group_id,
                                             message_thread_id=thread_id)
        except TelegramError as exc:
            log.warning("could not close thread %s: %s", thread_id, exc)
            return False
        self.state.set_closed(session_id, True)
        return True

    async def reopen_topic(self, session_id: str) -> bool:
        thread_id, _ = self.state.get_topic(session_id)
        if thread_id is None:
            return False
        try:
            await self.bot.reopen_forum_topic(chat_id=self.group_id,
                                              message_thread_id=thread_id)
        except TelegramError as exc:
            log.warning("could not reopen thread %s: %s", thread_id, exc)
            return False
        self.state.set_closed(session_id, False)
        return True

    def _attach_button(self, path: str) -> InlineKeyboardMarkup | None:
        if not path:
            return None
        try:
            size = Path(path).stat().st_size
        except OSError:
            return None                 # gone since it was offered
        label = f"{CLIP} Send {Path(path).name} ({size // 1024 or 1} KB)"
        return InlineKeyboardMarkup([[InlineKeyboardButton(
            label[:64], callback_data=f"f|{self.state.remember_attachment(path)}")]])

    async def _deliver(self, session_id: str, text: str, silent: bool,
                       attach: str = "") -> None:
        """Post one batch.

        Returns once it is posted, or refused in a way that trying again cannot
        fix. Without a connection it keeps trying for as long as that takes:
        giving up after a few minutes is how messages used to be lost whenever
        Telegram was out of reach.
        """
        failures = 0
        while True:
            try:
                await self._post(session_id, text, silent, attach)
                if failures:
                    log.info("sent after %d failed attempt(s)", failures)
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
                failures += 1
                pause = BACKOFF[min(failures, len(BACKOFF)) - 1]
                # A line per attempt through an hour offline would bury the log.
                if failures <= 3 or failures % 20 == 0:
                    log.warning("send failed (attempt %d), trying again in %ds: %s",
                                failures, pause, exc)
                await asyncio.sleep(pause)

    async def _post(self, session_id: str, text: str, silent: bool,
                    attach: str) -> None:
        thread_id = await self._thread_for(session_id)
        if thread_id is None:
            return
        # An archived chat that speaks again should not post into a closed
        # topic, where the reply would be easy to miss.
        if self.state.is_closed(session_id):
            log.info("reopening archived topic for %s", session_id[:8])
            await self.reopen_topic(session_id)
        await self.bot.send_message(
            chat_id=self.group_id,
            message_thread_id=thread_id,
            text=text,
            parse_mode=ParseMode.HTML,
            disable_notification=silent,
            disable_web_page_preview=True,
            reply_markup=self._attach_button(attach),
        )
        self.state.count_post(session_id)
