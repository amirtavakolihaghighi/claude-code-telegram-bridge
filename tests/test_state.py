"""The small SQLite file that remembers what has been mirrored."""
from __future__ import annotations

import sqlite3
from pathlib import Path

from bridge.state import State


def test_a_chat_that_was_never_read_has_no_offset(tmp_path):
    state = State(tmp_path / "state.db")
    assert state.get_offset("unknown") is None


def test_an_offset_survives_reopening_the_file(tmp_path):
    db = tmp_path / "state.db"
    state = State(db)
    state.set_offset("s1", Path("chat.jsonl"), 512)
    state.close()

    assert State(db).get_offset("s1") == 512


def test_a_topic_remembers_its_chat_and_project(tmp_path):
    state = State(tmp_path / "state.db")
    state.set_topic("s1", 42, "app: fix things", r"C:\Code\app")

    assert state.get_topic("s1") == (42, "app: fix things")
    assert state.session_for_thread(42) == "s1"
    assert state.project_for_session("s1") == r"C:\Code\app"


def test_updating_a_topic_keeps_the_parts_not_given(tmp_path):
    state = State(tmp_path / "state.db")
    state.set_topic("s1", 42, "first name", r"C:\Code\app")
    state.set_topic("s1", None, "renamed", None)

    assert state.get_topic("s1") == (42, "renamed")
    assert state.project_for_session("s1") == r"C:\Code\app"


def test_standing_permissions_are_remembered_and_can_be_taken_back(tmp_path):
    state = State(tmp_path / "state.db")
    assert state.allow_rule_exists("Bash:git") is False

    state.add_allow_rule("Bash:git", "Bash · git")
    assert state.allow_rule_exists("Bash:git") is True
    assert state.list_allow_rules() == [("Bash:git", "Bash · git")]

    assert state.drop_allow_rule("Bash:git") is True
    assert state.allow_rule_exists("Bash:git") is False


def test_forgetting_a_rule_that_does_not_exist_says_so(tmp_path):
    state = State(tmp_path / "state.db")
    assert state.drop_allow_rule("Bash:nope") is False


def test_clearing_reports_how_many_rules_went(tmp_path):
    state = State(tmp_path / "state.db")
    state.add_allow_rule("Bash:git", "git")
    state.add_allow_rule("Bash:npm", "npm")
    assert state.clear_allow_rules() == 2
    assert state.list_allow_rules() == []


def test_an_older_database_without_the_project_column_is_upgraded(tmp_path):
    """An early version had no project column; opening it must not fail."""
    db = tmp_path / "state.db"
    old = sqlite3.connect(db)
    old.executescript(
        """
        CREATE TABLE files (session_id TEXT PRIMARY KEY, path TEXT NOT NULL,
                            offset INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE topics (session_id TEXT PRIMARY KEY, thread_id INTEGER,
                             title TEXT);
        INSERT INTO topics VALUES ('s1', 7, 'an older topic');
        """
    )
    old.commit()
    old.close()

    state = State(db)

    assert state.get_topic("s1") == (7, "an older topic")
    assert state.project_for_session("s1") is None
    state.set_topic("s1", None, None, r"C:\Code\app")
    assert state.project_for_session("s1") == r"C:\Code\app"
