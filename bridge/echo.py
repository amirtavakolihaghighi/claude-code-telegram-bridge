"""Stops the mirror echoing back prompts that were typed in Telegram.

When you send a prompt from Telegram, Claude writes it into the chat file like
any other message. The mirror would then post it back at you, so you would see
your own message twice. We remember what we sent and skip the first match.
"""
from __future__ import annotations

import time


def _key(text: str) -> str:
    return " ".join(text.split()).casefold()


class EchoGuard:
    def __init__(self, ttl: float = 900.0):
        self.ttl = ttl
        self._seen: dict[tuple[str, str], float] = {}

    def remember(self, session_id: str, text: str) -> None:
        self._seen[(session_id, _key(text))] = time.monotonic()

    def claim(self, session_id: str, text: str) -> bool:
        """True if we sent this ourselves - and forget it, so repeats still show."""
        self._expire()
        return self._seen.pop((session_id, _key(text)), None) is not None

    def rename(self, old_session: str, new_session: str) -> None:
        """A brand new chat gets its real id only after the CLI answers."""
        for (session, text), seen in list(self._seen.items()):
            if session == old_session:
                del self._seen[(session, text)]
                self._seen[(new_session, text)] = seen

    def _expire(self) -> None:
        cutoff = time.monotonic() - self.ttl
        for key, seen in list(self._seen.items()):
            if seen < cutoff:
                del self._seen[key]
