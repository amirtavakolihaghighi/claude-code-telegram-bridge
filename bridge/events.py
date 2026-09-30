"""The handful of things we care about inside a chat file."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Event:
    kind: str           # title | user | assistant | tool | turn_end
    session_id: str
    text: str = ""
    tool_name: str = ""
    uuid: str = ""
    timestamp: str = ""
    extra: dict = field(default_factory=dict)
