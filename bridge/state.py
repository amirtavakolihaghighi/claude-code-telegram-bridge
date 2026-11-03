"""Small SQLite file remembering what has already been mirrored.

Three things live here: how far we have read into each chat file, which
Telegram topic each chat owns, and the standing "always allow" answers.

A chat's read position only moves past what has actually been posted, and
`mirrored_from` records where mirroring of that chat began - non-zero when its
earlier history was skipped, which is what catching up later sends.
"""
from __future__ import annotations

import sqlite3
import time
from pathlib import Path


def _last_written(path: Path) -> float | None:
    times = []
    for candidate in (path, path.with_name(path.name + "-wal")):
        try:
            times.append(candidate.stat().st_mtime)
        except OSError:
            continue
    return max(times) if times else None


class State:
    def __init__(self, path: Path):
        # Read before opening, which itself writes to the file.
        written = _last_written(Path(path))
        self.conn = sqlite3.connect(path)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS files (
                session_id TEXT PRIMARY KEY,
                path       TEXT NOT NULL,
                offset     INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS topics (
                session_id TEXT PRIMARY KEY,
                thread_id  INTEGER,
                title      TEXT
            );
            CREATE TABLE IF NOT EXISTS allow_rules (
                key     TEXT PRIMARY KEY,
                label   TEXT NOT NULL,
                created REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS attachments (
                id      INTEGER PRIMARY KEY AUTOINCREMENT,
                path    TEXT NOT NULL,
                created REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS meta (
                key   TEXT PRIMARY KEY,
                value TEXT
            );
            """
        )
        self._add_column("topics", "project", "TEXT")
        self._add_column("topics", "closed", "INTEGER NOT NULL DEFAULT 0")
        # NULL in a row from an older version means "not recorded", and is
        # read as 0: those versions only skipped history on the first run.
        self._add_column("files", "mirrored_from", "INTEGER")
        if self._add_column("topics", "posted", "INTEGER NOT NULL DEFAULT 0"):
            # Older versions did not count posts. A topic that exists has
            # almost certainly been posted in, so assume it has.
            self.conn.execute(
                "UPDATE topics SET posted = 1 WHERE thread_id IS NOT NULL")
        self._add_column("topics", "renew", "INTEGER NOT NULL DEFAULT 0")
        self._add_column("topics", "rearchive", "INTEGER NOT NULL DEFAULT 0")
        self._add_column("files", "muted", "INTEGER NOT NULL DEFAULT 0")
        if self.last_alive() is None and written is not None and \
                self.conn.execute("SELECT 1 FROM files LIMIT 1").fetchone():
            # Versions before the heartbeat wrote here whenever they mirrored
            # something, so this is the closest record of when one last ran.
            self._set_meta("last_alive", str(written))
        self.conn.commit()

    def _add_column(self, table: str, column: str, kind: str) -> bool:
        """Bring an older state.db up to date without losing it."""
        existing = {row[1] for row in
                    self.conn.execute(f"PRAGMA table_info({table})")}
        if column in existing:
            return False
        self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {kind}")
        return True

    def _get_meta(self, key: str) -> str | None:
        row = self.conn.execute(
            "SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row[0] if row else None

    def _set_meta(self, key: str, value: str | None) -> None:
        if value is None:
            self.conn.execute("DELETE FROM meta WHERE key = ?", (key,))
        else:
            self.conn.execute(
                "INSERT INTO meta (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value))

    # -- signs of life ---------------------------------------------------
    def last_alive(self) -> float | None:
        """When a running bridge last recorded that it was following chats."""
        value = self._get_meta("last_alive")
        return float(value) if value else None

    def set_last_alive(self, when: float) -> None:
        self._set_meta("last_alive", repr(when))
        self.conn.commit()

    # -- catching up -----------------------------------------------------
    def request_catch_up(self) -> None:
        """Ask the next bridge to start to send everything never sent."""
        self._set_meta("catch_up", "requested")
        self.conn.commit()

    def catch_up_requested(self) -> bool:
        return self._get_meta("catch_up") is not None

    def clear_catch_up_request(self) -> None:
        self._set_meta("catch_up", None)
        self.conn.commit()

    # -- how topics look -------------------------------------------------
    def topic_style(self) -> str | None:
        """The icon style last given to every topic, if one was recorded."""
        return self._get_meta("topic_icons")

    def set_topic_style(self, style: str) -> None:
        self._set_meta("topic_icons", style)
        self.conn.commit()

    # -- muted chats -----------------------------------------------------
    def set_muted(self, session_id: str, muted: bool) -> None:
        """A muted chat is not read, so it keeps its place: unmuted, it sends
        everything written in the meantime."""
        self.conn.execute("UPDATE files SET muted = ? WHERE session_id = ?",
                          (1 if muted else 0, session_id))
        self.conn.commit()

    def is_muted(self, session_id: str) -> bool:
        row = self.conn.execute(
            "SELECT muted FROM files WHERE session_id = ?", (session_id,)
        ).fetchone()
        return bool(row and row[0])

    def muted_chats(self) -> set[str]:
        return {row[0] for row in
                self.conn.execute("SELECT session_id FROM files WHERE muted = 1")}

    # -- byte offsets ----------------------------------------------------
    def get_offset(self, session_id: str) -> int | None:
        row = self.conn.execute(
            "SELECT offset FROM files WHERE session_id = ?", (session_id,)
        ).fetchone()
        return row[0] if row else None

    def set_offset(self, session_id: str, path: Path, offset: int,
                   mirrored_from: int | None = None) -> None:
        """Save a read position; mirrored_from too, when given."""
        self.conn.execute(
            "INSERT INTO files (session_id, path, offset, mirrored_from) "
            "VALUES (?, ?, ?, ?) "
            "ON CONFLICT(session_id) DO UPDATE SET offset = excluded.offset, "
            "path = excluded.path, "
            "mirrored_from = COALESCE(excluded.mirrored_from, files.mirrored_from)",
            (session_id, str(path), offset, mirrored_from),
        )
        self.conn.commit()

    def get_mirrored_from(self, session_id: str) -> int:
        """Where mirroring of this chat began: 0 if it was sent from the start."""
        row = self.conn.execute(
            "SELECT mirrored_from FROM files WHERE session_id = ?", (session_id,)
        ).fetchone()
        return (row[0] or 0) if row else 0

    def all_files(self) -> list[tuple[str, str, int, int]]:
        """Every chat followed: id, path, read position, where mirroring began."""
        return self.conn.execute(
            "SELECT session_id, path, offset, COALESCE(mirrored_from, 0) "
            "FROM files").fetchall()

    def restart_chat(self, session_id: str, renew_topic: bool) -> None:
        """Send this chat again from its first message.

        With renew_topic, its topic is deleted just before the first message
        goes out, and the whole chat goes into a new one.
        """
        self.conn.execute(
            "UPDATE files SET offset = 0, mirrored_from = 0 WHERE session_id = ?",
            (session_id,))
        if renew_topic:
            self.conn.execute(
                "UPDATE topics SET renew = 1 WHERE session_id = ?", (session_id,))
        self.conn.commit()

    # -- topic mapping ---------------------------------------------------
    def get_topic(self, session_id: str) -> tuple[int | None, str | None]:
        row = self.conn.execute(
            "SELECT thread_id, title FROM topics WHERE session_id = ?", (session_id,)
        ).fetchone()
        return (row[0], row[1]) if row else (None, None)

    def set_topic(self, session_id: str, thread_id: int | None,
                  title: str | None, project: str | None = None) -> None:
        self.conn.execute(
            "INSERT INTO topics (session_id, thread_id, title, project) "
            "VALUES (?, ?, ?, ?) "
            "ON CONFLICT(session_id) DO UPDATE SET "
            "thread_id = COALESCE(excluded.thread_id, topics.thread_id), "
            "title = COALESCE(excluded.title, topics.title), "
            "project = COALESCE(excluded.project, topics.project)",
            (session_id, thread_id, title, project),
        )
        self.conn.commit()

    def session_for_thread(self, thread_id: int) -> str | None:
        """Which chat a Telegram topic belongs to."""
        row = self.conn.execute(
            "SELECT session_id FROM topics WHERE thread_id = ?", (thread_id,)
        ).fetchone()
        return row[0] if row else None

    def project_for_session(self, session_id: str) -> str | None:
        row = self.conn.execute(
            "SELECT project FROM topics WHERE session_id = ?", (session_id,)
        ).fetchone()
        return row[0] if row else None

    def is_closed(self, session_id: str) -> bool:
        row = self.conn.execute(
            "SELECT closed FROM topics WHERE session_id = ?", (session_id,)
        ).fetchone()
        return bool(row and row[0])

    def set_closed(self, session_id: str, closed: bool) -> None:
        self.conn.execute("UPDATE topics SET closed = ? WHERE session_id = ?",
                          (1 if closed else 0, session_id))
        self.conn.commit()

    def all_topics(self) -> list[tuple[str, int | None, str | None, str | None]]:
        return self.conn.execute(
            "SELECT session_id, thread_id, title, project FROM topics"
        ).fetchall()

    def count_post(self, session_id: str) -> None:
        self.conn.execute(
            "UPDATE topics SET posted = posted + 1 WHERE session_id = ?",
            (session_id,))
        self.conn.commit()

    def posted(self, session_id: str) -> int:
        """How many posts have gone into this chat's current topic."""
        row = self.conn.execute(
            "SELECT posted FROM topics WHERE session_id = ?", (session_id,)
        ).fetchone()
        return row[0] if row else 0

    def renewing(self, session_id: str) -> bool:
        """Is this chat waiting to be given a fresh, complete topic?"""
        row = self.conn.execute(
            "SELECT renew FROM topics WHERE session_id = ?", (session_id,)
        ).fetchone()
        return bool(row and row[0])

    def forget_topic(self, session_id: str, rearchive: bool = False) -> None:
        """The chat's topic is gone; its next post opens a new one.

        With rearchive, that new topic is closed again once the whole chat is
        in it, as the one it replaces had been archived.
        """
        self.conn.execute(
            "UPDATE topics SET thread_id = NULL, renew = 0, posted = 0, "
            "closed = 0, rearchive = ? WHERE session_id = ?",
            (1 if rearchive else 0, session_id))
        self.conn.commit()

    def rearchive_due(self, session_id: str) -> bool:
        row = self.conn.execute(
            "SELECT rearchive FROM topics WHERE session_id = ?", (session_id,)
        ).fetchone()
        return bool(row and row[0])

    def clear_rearchive(self, session_id: str) -> None:
        self.conn.execute("UPDATE topics SET rearchive = 0 WHERE session_id = ?",
                          (session_id,))
        self.conn.commit()

    # -- standing permissions --------------------------------------------
    def allow_rule_exists(self, key: str) -> bool:
        return self.conn.execute(
            "SELECT 1 FROM allow_rules WHERE key = ?", (key,)
        ).fetchone() is not None

    def add_allow_rule(self, key: str, label: str) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO allow_rules (key, label, created) "
            "VALUES (?, ?, ?)", (key, label, time.time()),
        )
        self.conn.commit()

    def list_allow_rules(self) -> list[tuple[str, str]]:
        return self.conn.execute(
            "SELECT key, label FROM allow_rules ORDER BY created"
        ).fetchall()

    def drop_allow_rule(self, key: str) -> bool:
        cursor = self.conn.execute("DELETE FROM allow_rules WHERE key = ?", (key,))
        self.conn.commit()
        return cursor.rowcount > 0

    def clear_allow_rules(self) -> int:
        cursor = self.conn.execute("DELETE FROM allow_rules")
        self.conn.commit()
        return cursor.rowcount

    # -- offered files ---------------------------------------------------
    def remember_attachment(self, path: str) -> int:
        """A button can only carry 64 bytes, so the path is looked up by id."""
        row = self.conn.execute(
            "SELECT id FROM attachments WHERE path = ?", (path,)).fetchone()
        if row:
            return row[0]
        cursor = self.conn.execute(
            "INSERT INTO attachments (path, created) VALUES (?, ?)",
            (path, time.time()))
        self.conn.commit()
        return cursor.lastrowid

    def attachment_path(self, attachment_id: int) -> str | None:
        row = self.conn.execute(
            "SELECT path FROM attachments WHERE id = ?", (attachment_id,)
        ).fetchone()
        return row[0] if row else None

    def close(self) -> None:
        self.conn.close()
