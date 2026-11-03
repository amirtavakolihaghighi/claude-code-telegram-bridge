"""The small SQLite file that remembers what has been mirrored."""
from __future__ import annotations

import os
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


# -- what has been sent ------------------------------------------------------
def _old_database(db: Path, script: str) -> None:
    old = sqlite3.connect(db)
    old.executescript(
        """
        CREATE TABLE files (session_id TEXT PRIMARY KEY, path TEXT NOT NULL,
                            offset INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE topics (session_id TEXT PRIMARY KEY, thread_id INTEGER,
                             title TEXT);
        """ + script)
    old.commit()
    old.close()


def test_where_mirroring_began_is_kept_until_changed(tmp_path):
    state = State(tmp_path / "state.db")
    state.set_offset("s1", Path("chat.jsonl"), 900, mirrored_from=900)
    state.set_offset("s1", Path("chat.jsonl"), 1200)

    assert state.get_mirrored_from("s1") == 900
    assert state.get_offset("s1") == 1200


def test_restarting_a_chat_reads_it_from_the_top_again(tmp_path):
    state = State(tmp_path / "state.db")
    state.set_offset("s1", Path("chat.jsonl"), 900, mirrored_from=900)
    state.set_topic("s1", 7, "app: chat")

    state.restart_chat("s1", renew_topic=True)

    assert state.get_offset("s1") == 0
    assert state.get_mirrored_from("s1") == 0
    assert state.renewing("s1") is True


def test_a_forgotten_topic_leaves_the_chat_ready_for_a_new_one(tmp_path):
    state = State(tmp_path / "state.db")
    state.set_topic("s1", 7, "app: chat")
    state.count_post("s1")
    state.restart_chat("s1", renew_topic=True)

    state.forget_topic("s1")

    assert state.get_topic("s1") == (None, "app: chat")
    assert state.posted("s1") == 0
    assert state.renewing("s1") is False
    assert state.rearchive_due("s1") is False


def test_a_replaced_archived_topic_is_due_to_be_archived_again(tmp_path):
    state = State(tmp_path / "state.db")
    state.set_topic("s1", 7, "app: chat")
    state.set_closed("s1", True)

    state.forget_topic("s1", rearchive=True)

    assert state.is_closed("s1") is False       # the new topic starts open
    assert state.rearchive_due("s1") is True
    state.clear_rearchive("s1")
    assert state.rearchive_due("s1") is False


def test_a_catch_up_request_waits_for_the_next_start(tmp_path):
    db = tmp_path / "state.db"
    State(db).request_catch_up()

    state = State(db)
    assert state.catch_up_requested() is True
    state.clear_catch_up_request()
    assert state.catch_up_requested() is False


def test_the_last_sign_of_life_survives_reopening(tmp_path):
    db = tmp_path / "state.db"
    State(db).set_last_alive(1234.5)

    assert State(db).last_alive() == 1234.5


def test_a_brand_new_database_has_no_record_of_a_previous_run(tmp_path):
    assert State(tmp_path / "state.db").last_alive() is None


def test_a_database_from_before_the_heartbeat_dates_the_last_run_by_its_writes(
        tmp_path):
    """Older versions wrote here whenever they posted, so the file's own age
    is the best record there is of when one last ran."""
    db = tmp_path / "state.db"
    _old_database(db, "INSERT INTO files VALUES ('s1', 'chat.jsonl', 10);")
    os.utime(db, (1_700_000_000, 1_700_000_000))

    assert State(db).last_alive() == 1_700_000_000


def test_an_older_database_assumes_its_topics_were_posted_in(tmp_path):
    """Older versions did not count posts; a topic that exists has been used."""
    db = tmp_path / "state.db"
    _old_database(db, """
        INSERT INTO files VALUES ('s1', 'chat.jsonl', 10);
        INSERT INTO topics VALUES ('s1', 7, 'used'), ('s2', NULL, 'never');
    """)

    state = State(db)

    assert state.posted("s1") == 1
    assert state.posted("s2") == 0
    assert state.get_mirrored_from("s1") == 0       # not recorded back then


def test_a_muted_chat_stays_muted_until_unmuted(tmp_path):
    db = tmp_path / "state.db"
    state = State(db)
    state.set_offset("s1", Path("chat.jsonl"), 10)
    state.set_muted("s1", True)

    reopened = State(db)
    assert reopened.is_muted("s1") is True
    assert reopened.muted_chats() == {"s1"}
    reopened.set_muted("s1", False)
    assert reopened.muted_chats() == set()


def test_the_topic_icon_style_last_applied_is_remembered(tmp_path):
    state = State(tmp_path / "state.db")
    assert state.topic_style() is None
    state.set_topic_style("default")
    assert state.topic_style() == "default"
