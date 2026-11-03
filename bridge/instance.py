"""Only one copy of the bridge may run at a time.

Two copies share one bot token, so both ask Telegram for messages and both
fail with Conflict - and both read the same chat files, so everything is posted
twice. Whoever holds a lock on runtime/bridge.lock runs. The operating system
releases the lock the moment that process ends, however it ends, so a crash
never leaves a stale lock behind.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

RUNTIME = Path(__file__).resolve().parent.parent / "runtime"
LOCK_FILE = RUNTIME / "bridge.lock"


class InstanceLock:
    def __init__(self, path: Path = LOCK_FILE):
        self.path = path
        self.pid_path = path.with_suffix(".pid")
        self._handle = None

    def acquire(self) -> bool:
        """Take the lock if nobody holds it. False if another copy does."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = open(self.path, "a+b")
        try:
            if sys.platform == "win32":
                import msvcrt
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            handle.close()
            return False
        self._handle = handle
        self.pid_path.write_text(str(os.getpid()), encoding="ascii")
        return True

    def release(self) -> None:
        if self._handle is None:
            return
        try:
            if sys.platform == "win32":
                import msvcrt
                self._handle.seek(0)
                msvcrt.locking(self._handle.fileno(), msvcrt.LK_UNLCK, 1)
        except OSError:
            pass                # closing releases it anyway
        self._handle.close()
        self._handle = None
        self.pid_path.unlink(missing_ok=True)

    def holder(self) -> int | None:
        """The process id of the copy holding the lock, if one is known."""
        try:
            return int(self.pid_path.read_text(encoding="ascii").strip())
        except (OSError, ValueError):
            return None


def running() -> tuple[bool, int | None]:
    """Is a copy of the bridge running right now, and which process is it?"""
    probe = InstanceLock()
    if probe.acquire():
        probe.release()
        return False, None
    return True, probe.holder()
