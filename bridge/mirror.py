"""The engine: watch chat files and push new messages to a sink.

A chat's read position in state.db only moves past messages once the sink has
posted them. Anything read but not yet sent when the bridge stops - a shutdown,
a crash, a long spell without a connection - is read and sent again next time
rather than lost.
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from pathlib import Path

from . import catchup
from .config import Config
from .echo import EchoGuard
from .format import render
from .locator import find_session_files, leaf_name
from .parser import SessionReader, read_title, started_at
from .state import State

log = logging.getLogger(__name__)

# A chat with more than this left to read is catching up rather than live. It
# is read a slice at a time, and only while the send queue has room, so a long
# backlog never sits in front of a live reply for more than a minute or two.
SLICE_BYTES = 512_000
# How often to record that the bridge is alive. A chat that began after the
# last record began while the bridge was off, and is sent from its first word.
ALIVE_EVERY = 30.0
# Neither that interval nor clocks are exact, so a chat begun this shortly
# before the last sign of life also counts as begun while the bridge was off.
AWAY_SLACK = 300.0


class Mirror:
    def __init__(self, config: Config, sink, state: State, backfill: bool = False,
                 echo: EchoGuard | None = None, import_all: bool = False,
                 catch_up: bool = False):
        self.config = config
        self.sink = sink
        self.state = state
        self.backfill = backfill
        self.echo = echo
        # Normally a topic appears when a chat first has something to say.
        # With import_all we create one for every chat up front, so old chats
        # can be picked up from Telegram without touching the PC first.
        self.import_all = import_all
        # Send everything never sent, oldest first: see catchup.py.
        self.catch_up = catch_up
        self.readers: dict[str, SessionReader] = {}
        self.projects: dict[str, Path] = {}
        # Chats already on disk when we start are history; chats that appear
        # afterwards are new, and we want them from their first word.
        self._first_pass = True
        # Where each chat's not-yet-posted messages begin, oldest first, one
        # entry per message handed to the sink.
        self._unsent: dict[str, deque[int]] = {}
        self._saved: dict[str, int] = {}
        # Read before this run records anything, so it still says when a
        # bridge was last running before this one.
        self._last_alive = state.last_alive()
        self._alive_noted = 0.0
        # A sink that reports deliveries lets read positions wait for them.
        # One that does not is taken to have posted a message once handed it.
        self._tracked = hasattr(sink, "on_delivered")
        if self._tracked:
            sink.on_delivered = self._delivered

    # -- naming ----------------------------------------------------------
    def _display_title(self, session_id: str, title: str) -> str:
        """Topic names read 'ProjectFolder: chat title'.

        A project usually has several chats, so the folder name has to be there
        to tell them apart. MACHINE_LABEL goes in front of it when one Telegram
        group is fed by more than one computer.
        """
        project = self.projects.get(session_id)
        name = leaf_name(project) if project is not None else "unknown"
        prefix = f"{self.config.machine_label} · " if self.config.machine_label else ""
        return f"{prefix}{name}: {title}"

    # -- readers ---------------------------------------------------------
    def _began_while_away(self, path: Path) -> bool:
        """Did this chat begin after a bridge was last seen running?"""
        if self._last_alive is None:
            return False
        began = started_at(path)
        return began is not None and began >= self._last_alive - AWAY_SLACK

    async def _reader_for(self, session_id: str, path: Path,
                          project: Path) -> SessionReader:
        self.projects[session_id] = project
        reader = self.readers.get(session_id)
        if reader is not None:
            # Same chat, new place: the project folder was moved or renamed.
            reader.path = path
            return reader

        offset = self.state.get_offset(session_id)
        if self.backfill:
            # --backfill means "post everything", including chats we have
            # already read past. Start from the top regardless.
            offset = None
        reader = SessionReader(path, session_id, offset or 0)
        name = leaf_name(project)
        mirrored_from: int | None = None
        if offset is not None:
            log.info("tracking %s [%s] from byte %d",
                     session_id[:8], name, reader.offset)
        elif not self._first_pass or self.backfill or self.catch_up:
            log.info("new chat %s [%s] - mirroring from the start",
                     session_id[:8], name)
            mirrored_from = 0
        elif self._began_while_away(path):
            log.info("chat %s [%s] began while the bridge was off - "
                     "mirroring from the start", session_id[:8], name)
            mirrored_from = 0
        else:
            # Chat existed before we started: begin from now, don't dump
            # history. Where it began is recorded, so catching up can send it.
            reader.seek_to_end()
            mirrored_from = reader.offset
            log.info("tracking %s [%s] from now (%d bytes of earlier history "
                     "not sent; catching up sends them)",
                     session_id[:8], name, reader.offset)

        self.state.set_offset(session_id, path, reader.offset, mirrored_from)
        self._saved[session_id] = reader.offset
        self.state.set_topic(session_id, None, None, str(project))
        self.readers[session_id] = reader

        # The title was written earlier in the file than where we resume, so
        # fetch it now. Even without one we set a name, so the topic is never
        # created as a bare chat id with no project on it.
        title = read_title(path) or f"chat {session_id[:8]}"
        await self.sink.set_title(session_id,
                                  self._display_title(session_id, title))

        if self.import_all:
            existing, _ = self.state.get_topic(session_id)
            if existing is None:
                await self.sink.ensure_topic(session_id)
                # Telegram's limit on creating topics is tighter than on
                # sending, and importing a whole PC's worth walks right into it.
                await asyncio.sleep(3.0)

        return reader

    # -- read positions --------------------------------------------------
    def _save_position(self, session_id: str) -> None:
        """Save how far this chat has been read *and posted*."""
        reader = self.readers.get(session_id)
        if reader is None:
            return
        safe = reader.offset
        waiting = reader.pending_start()
        if waiting is not None:
            safe = min(safe, waiting)
        unsent = self._unsent.get(session_id)
        if unsent:
            # Not simply the first: a tool's message begins at its call but is
            # only sent once the result lands, after text written in between.
            safe = min(safe, min(unsent))
        if self._saved.get(session_id) != safe:
            self.state.set_offset(session_id, reader.path, safe)
            self._saved[session_id] = safe

    def _delivered(self, session_id: str) -> None:
        """The sink has posted, or given up for good on, one of this chat's
        messages - always the oldest still outstanding."""
        unsent = self._unsent.get(session_id)
        if unsent:
            unsent.popleft()
        self._save_position(session_id)

    def _budget(self, reader: SessionReader) -> int | None:
        """How much of a chat to read this pass; None means all of it."""
        room = getattr(self.sink, "room", None)
        if room is None or reader.unread() <= SLICE_BYTES:
            return None
        return SLICE_BYTES if room() > 0 else 0

    def caught_up(self) -> bool:
        """Every chat not muted has been read to its end."""
        muted = self.state.muted_chats()
        return all(reader.unread() == 0 for session_id, reader
                   in self.readers.items() if session_id not in muted)

    async def resend(self, session_id: str) -> None:
        """Delete this chat's topic and send all of it again, in order.

        Whatever of it is still queued is dropped first - once any post of it
        already under way is finished - so nothing from before lands in the new
        topic. Read from the top again, its first post deletes the old topic.
        """
        discard = getattr(self.sink, "discard", None)
        if discard is not None:
            await discard(session_id)
        self.readers.pop(session_id, None)
        self._unsent.pop(session_id, None)
        self._saved.pop(session_id, None)
        self.state.set_muted(session_id, False)
        self.state.restart_chat(session_id, renew_topic=True)
        log.info("sending %s again from its first message", session_id[:8])

    def _all_posted(self, session_id: str) -> bool:
        """Everything this chat holds has been read and posted."""
        reader = self.readers.get(session_id)
        return (reader is not None and reader.unread() == 0
                and reader.pending_start() is None
                and not self._unsent.get(session_id))

    async def _rearchive(self, session_id: str) -> None:
        """A chat sent again in full had been archived; archive it again."""
        close = getattr(self.sink, "close_topic", None)
        if close is not None and await close(session_id):
            self.state.clear_rearchive(session_id)
            log.info("archived %s again, now that all of it is posted",
                     session_id[:8])

    def _note_alive(self) -> None:
        now = time.time()
        if now - self._alive_noted >= ALIVE_EVERY:
            self.state.set_last_alive(now)
            self._alive_noted = now

    def _schedule_catch_up(self) -> None:
        missing = catchup.find_missing(self.config, self.state)
        catchup.schedule(self.state, missing)
        log.info("catching up: %s", catchup.describe(missing))

    async def _dispatch(self, event) -> None:
        if event.kind == "title":
            await self.sink.set_title(
                event.session_id,
                self._display_title(event.session_id, event.text))
            return

        # A prompt typed in Telegram is already on screen there; don't repeat it.
        if (event.kind == "user" and self.echo is not None
                and self.echo.claim(event.session_id, event.text)):
            return

        for message in render(event):
            if self._tracked:
                self._unsent.setdefault(event.session_id, deque()).append(
                    event.start)
            await self.sink.send(event.session_id, message)

    # -- the loop --------------------------------------------------------
    async def tick(self) -> int:
        if self._first_pass and self.catch_up:
            self._schedule_catch_up()
        sent = 0
        muted = self.state.muted_chats()
        # Newest first: when there is a backlog, the chats in use now get room
        # in the queue before ones untouched for weeks.
        for session in reversed(find_session_files(self.config.claude_home,
                                                   self.config.watch_projects,
                                                   self.config.chat_backups)):
            if session.session_id in muted:
                continue                # not read, so it keeps its place
            try:
                reader = await self._reader_for(session.session_id,
                                                session.path, session.project)
                budget = self._budget(reader)
                if budget == 0:
                    continue            # backlog waits for room in the queue
                for event in reader.read_new(budget):
                    if self.readers.get(session.session_id) is not reader:
                        break           # sent over from the start meanwhile
                    try:
                        await self._dispatch(event)
                    except Exception:
                        # Lose this one event, not everything read after it.
                        log.exception("could not mirror a %s from chat %s",
                                      event.kind, session.session_id[:8])
                    sent += 1
                if self.readers.get(session.session_id) is not reader:
                    continue
                self._save_position(session.session_id)
                if self._all_posted(session.session_id) and \
                        self.state.rearchive_due(session.session_id):
                    await self._rearchive(session.session_id)
            except Exception:
                # One chat that cannot be read must not hold up the others.
                log.exception("could not mirror chat %s", session.session_id[:8])
        if self._first_pass and self.catch_up:
            # Only now: had this run died before scheduling, the request
            # would still be there for the next one.
            self.state.clear_catch_up_request()
        self._first_pass = False
        self._note_alive()
        return sent

    async def run(self, once: bool = False) -> int:
        """Follow the chats. With once, stop when every chat has been read to
        its end, and return how many events were handed to the sink."""
        if not once:
            if self.config.watches_everything:
                log.info("watching every project Claude Code knows about")
            else:
                for project in self.config.watch_projects:
                    log.info("watching %s", project)
        handed = 0
        while True:
            try:
                handed += await self.tick()
            except Exception:
                log.exception("mirror tick failed; continuing")
            if once and self.caught_up():
                return handed
            await asyncio.sleep(self.config.poll_interval)
