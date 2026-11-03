"""Sending what never reached Telegram.

Two kinds of chat have history that was never posted:

* chats the bridge has never followed - on a machine just set up, or in a
  project only now added to WATCH_PROJECTS - and
* chats it began following part-way through: those already on disk when it
  first ran are followed from that moment rather than dumped, and before this
  module existed, so were chats begun while the bridge was off.

Catching up sends both, oldest message first. Telegram cannot put messages
above ones already posted, so a chat whose topic already holds its later part
is given a fresh topic with the whole conversation in order. The old topic is
deleted, or closed and labelled if the bot may not delete it.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .config import Config
from .locator import find_session_files
from .state import State


@dataclass(frozen=True)
class Missing:
    session_id: str
    path: Path
    project: Path
    unsent: int             # bytes of the chat file never posted
    never_followed: bool    # no record of the chat at all
    renew_topic: bool       # its topic is replaced by a complete one


def _size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def find_missing(config: Config, state: State) -> list[Missing]:
    """Every watched chat with history that was never posted. Changes nothing."""
    found: list[Missing] = []
    for chat in find_session_files(config.claude_home, config.watch_projects,
                                   config.chat_backups):
        if state.get_offset(chat.session_id) is None:
            found.append(Missing(chat.session_id, chat.path, chat.project,
                                 _size(chat.path), True, False))
            continue
        began = state.get_mirrored_from(chat.session_id)
        if began > 0:
            thread_id, _ = state.get_topic(chat.session_id)
            renew = thread_id is not None and state.posted(chat.session_id) > 0
            found.append(Missing(chat.session_id, chat.path, chat.project,
                                 began, False, renew))
    return found


def schedule(state: State, missing: list[Missing]) -> None:
    """Have each chat read again from its first message.

    Chats never followed need nothing here: a bridge catching up reads a chat
    it has no record of from the start.
    """
    for item in missing:
        if not item.never_followed:
            state.restart_chat(item.session_id, renew_topic=item.renew_topic)


def describe(missing: list[Missing]) -> str:
    """A sentence or two saying what catching up will send."""
    if not missing:
        return "nothing to catch up on - every chat has been sent in full"
    # Each chat is read again from its first byte, so this is what gets posted.
    total = sum(_size(item.path) for item in missing)
    never = sum(item.never_followed for item in missing)
    partial = len(missing) - never
    renewed = sum(item.renew_topic for item in missing)
    parts = []
    if never:
        parts.append(f"{never} never sent")
    if partial:
        parts.append(f"{partial} missing their beginning")
    line = (f"{len(missing)} chat(s) to post, {total / 1e6:.1f} MB of chat "
            "files: " + ", ".join(parts) + ".")
    # This is the only warning before topics are deleted, so it must say so.
    if renewed == 1:
        line += (" 1 has a topic that began part-way: that topic is deleted, "
                 "with everything in it, and the whole chat is sent again "
                 "into a new one, in order.")
    elif renewed:
        line += (f" {renewed} have topics that began part-way: each is "
                 "deleted, with everything in it, and its whole chat is sent "
                 "again into a new one, in order.")
    if renewed:
        line += (" If the bot may not delete topics, an old one is closed "
                 "and labelled (partial copy) instead.")
    return line
