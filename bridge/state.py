"""Small SQLite file remembering what has already been mirrored.

Three things live here: how far we have read into each chat file, which
Telegram topic each chat owns, and the standing "always allow" answers.
"""
from __future__ import annotations

import sqlite3
import time
from pathlib import Path


class State:
    def __init__(self, path: Path):
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
            """
        )
        self._add_column("topics", "project", "TEXT")
        self._add_column("topics", "closed", "INTEGER NOT NULL DEFAULT 0")
        self.conn.commit()

    def _add_column(self, table: str, column: str, kind: str) -> None:
        """Bring an older state.db up to date without losing it."""
        existing = {row[1] for row in
                    self.conn.execute(f"PRAGMA table_info({table})")}
        if column not in existing:
            self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {kind}")

    # -- byte offsets ----------------------------------------------------
    def get_offset(self, session_id: str) -> int | None:
        row = self.conn.execute(
            "SELECT offset FROM files WHERE session_id = ?", (session_id,)
        ).fetchone()
        return row[0] if row else None

    def set_offset(self, session_id: str, path: Path, offset: int) -> None:
        self.conn.execute(
            "INSERT INTO files (session_id, path, offset) VALUES (?, ?, ?) "
            "ON CONFLICT(session_id) DO UPDATE SET offset = excluded.offset, "
            "path = excluded.path",
            (session_id, str(path), offset),
        )
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
