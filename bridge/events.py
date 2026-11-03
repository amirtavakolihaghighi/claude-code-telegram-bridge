"""The handful of things we care about inside a chat file."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Event:
    kind: str           # title | user | assistant | tool | turn_end
                        # | compacted | notice
    session_id: str
    text: str = ""
    tool_name: str = ""
    uuid: str = ""
    timestamp: str = ""
    extra: dict = field(default_factory=dict)
    # Byte offset in the chat file where this event's first record begins, so
    # a read position can be kept short of anything not yet posted. -1 when
    # the event did not come from a file.
    start: int = -1
