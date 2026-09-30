"""Giving each project a consistent look in the topic list.

A long list of identically grey topics is hard to scan. Telegram offers six
topic colours and a set of built-in icons, so each project gets one of each,
chosen from its path. The choice is a checksum rather than Python's `hash`,
which is salted per process and would give a project a different colour every
time the bridge restarted.
"""
from __future__ import annotations

import zlib

from telegram.constants import ForumIconColor

from .locator import normalise

COLOURS = [colour.value for colour in ForumIconColor]


def _seed(project: str) -> int:
    return zlib.crc32(normalise(project).encode("utf-8"))


def colour_for(project: str) -> int:
    """One of Telegram's six topic colours, always the same for one project."""
    return COLOURS[_seed(project) % len(COLOURS)]


def icon_for(project: str, icon_ids: list[str]) -> str | None:
    """One of Telegram's built-in topic icons, always the same for one project."""
    if not icon_ids:
        return None
    return icon_ids[_seed(project) % len(icon_ids)]
