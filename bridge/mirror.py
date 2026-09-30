"""The engine: watch chat files and push new messages to a sink."""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from .config import Config
from .echo import EchoGuard
from .format import render
from .locator import find_session_files, leaf_name
from .parser import SessionReader, read_title
from .state import State

log = logging.getLogger(__name__)


class Mirror:
    def __init__(self, config: Config, sink, state: State, backfill: bool = False,
                 echo: EchoGuard | None = None, import_all: bool = False):
        self.config = config
        self.sink = sink
        self.state = state
        self.backfill = backfill
        self.echo = echo
        # Normally a topic appears when a chat first has something to say.
        # With import_all we create one for every chat up front, so old chats
        # can be picked up from Telegram without touching the PC first.
        self.import_all = import_all
        self.readers: dict[str, SessionReader] = {}
        self.projects: dict[str, Path] = {}
        # Chats already on disk when we start are history; chats that appear
        # afterwards are new, and we want them from their first word.
        self._first_pass = True

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
    async def _reader_for(self, session_id: str, path: Path,
                          project: Path) -> SessionReader:
        self.projects[session_id] = project
        reader = self.readers.get(session_id)
        if reader is not None:
            return reader

        offset = self.state.get_offset(session_id)
        if self.backfill:
            # --backfill means "post everything", including chats we have
            # already read past. Start from the top regardless.
            offset = None
        reader = SessionReader(path, session_id, offset or 0)
        unseen = offset is None
        if unseen and self._first_pass and not self.backfill:
            # Chat existed before we started: begin from now, don't dump history.
            reader.seek_to_end()
            log.info("tracking %s [%s] from now (%d bytes skipped)",
                     session_id[:8], leaf_name(project), reader.offset)
        elif unseen:
            log.info("new chat %s [%s] - mirroring from the start",
                     session_id[:8], leaf_name(project))
        else:
            log.info("tracking %s [%s] from byte %d",
                     session_id[:8], leaf_name(project), reader.offset)

        self.state.set_offset(session_id, path, reader.offset)
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
            await self.sink.send(event.session_id, message)

    # -- the loop --------------------------------------------------------
    async def tick(self) -> int:
        sent = 0
        for session in find_session_files(self.config.claude_home,
                                          self.config.watch_projects):
            reader = await self._reader_for(session.session_id, session.path,
                                            session.project)
            events = reader.read_new()
            if not events:
                continue
            for event in events:
                await self._dispatch(event)
                sent += 1
            self.state.set_offset(session.session_id, session.path, reader.offset)
        self._first_pass = False
        return sent

    async def run(self) -> None:
        if self.config.watches_everything:
            log.info("watching every project Claude Code knows about")
        else:
            for project in self.config.watch_projects:
                log.info("watching %s", project)
        while True:
            try:
                await self.tick()
            except Exception:
                log.exception("mirror tick failed; continuing")
            await asyncio.sleep(self.config.poll_interval)
