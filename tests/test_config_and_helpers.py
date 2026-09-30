"""Settings parsing, the echo guard, and the VS Code lookups."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

from bridge import config as config_module
from bridge.config import load_config
from bridge.echo import EchoGuard
from bridge.vscode import handoff_url, is_project_open, open_workspaces, pid_alive

TELEGRAM_KEYS = ["TELEGRAM_BOT_TOKEN", "TELEGRAM_GROUP_ID", "TELEGRAM_OWNER_ID",
                 "WATCH_PROJECTS", "WATCH_PROJECT_DIR", "REPLIES_ENABLED",
                 "CLAUDE_PERMISSION_MODE", "APPROVAL_TIMEOUT", "MACHINE_LABEL",
                 "CLAUDE_CLI", "CLAUDE_HOME", "POLL_INTERVAL"]


@pytest.fixture
def clean_env(monkeypatch, tmp_path):
    """No .env file and no leftover variables, so tests see only what they set."""
    for key in TELEGRAM_KEYS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(config_module, "ROOT", tmp_path)
    return monkeypatch


# -- settings ----------------------------------------------------------------
def test_a_missing_token_stops_it_rather_than_failing_later(clean_env):
    with pytest.raises(SystemExit):
        load_config()


def test_the_token_is_optional_for_a_dry_run(clean_env):
    assert load_config(require_token=False).bot_token == ""


def test_watching_all_projects_is_an_empty_list(clean_env):
    clean_env.setenv("TELEGRAM_BOT_TOKEN", "x")
    clean_env.setenv("WATCH_PROJECTS", "ALL")
    settings = load_config()
    assert settings.watch_projects == ()
    assert settings.watches_everything is True


def test_several_projects_are_separated_by_semicolons(clean_env):
    clean_env.setenv("TELEGRAM_BOT_TOKEN", "x")
    clean_env.setenv("WATCH_PROJECTS", r"C:\Code\a ; C:\Code\b")
    settings = load_config()
    assert [p.name for p in settings.watch_projects] == ["a", "b"]
    assert settings.default_project == Path(r"C:\Code\a")


def test_the_older_single_folder_setting_still_works(clean_env):
    clean_env.setenv("TELEGRAM_BOT_TOKEN", "x")
    clean_env.setenv("WATCH_PROJECT_DIR", r"C:\Code\legacy")
    assert load_config().watch_projects == (Path(r"C:\Code\legacy"),)


def test_an_unknown_permission_mode_is_refused(clean_env):
    clean_env.setenv("TELEGRAM_BOT_TOKEN", "x")
    clean_env.setenv("CLAUDE_PERMISSION_MODE", "whatever-i-like")
    with pytest.raises(SystemExit):
        load_config()


def test_a_non_numeric_id_is_refused(clean_env):
    clean_env.setenv("TELEGRAM_BOT_TOKEN", "x")
    clean_env.setenv("TELEGRAM_OWNER_ID", "not-a-number")
    with pytest.raises(SystemExit):
        load_config()


def test_replies_can_be_turned_off(clean_env):
    clean_env.setenv("TELEGRAM_BOT_TOKEN", "x")
    clean_env.setenv("REPLIES_ENABLED", "false")
    assert load_config().replies_enabled is False


def test_a_broken_poll_interval_falls_back_to_the_default(clean_env):
    clean_env.setenv("TELEGRAM_BOT_TOKEN", "x")
    clean_env.setenv("POLL_INTERVAL", "soon")
    assert load_config().poll_interval == 2.0


# -- not repeating your own messages -----------------------------------------
def test_a_remembered_prompt_is_claimed_once_only():
    guard = EchoGuard()
    guard.remember("s1", "hello there")
    assert guard.claim("s1", "hello there") is True
    assert guard.claim("s1", "hello there") is False


def test_matching_ignores_spacing_and_case():
    guard = EchoGuard()
    guard.remember("s1", "Hello   There")
    assert guard.claim("s1", "hello there") is True


def test_another_chat_is_not_affected():
    guard = EchoGuard()
    guard.remember("s1", "hello")
    assert guard.claim("s2", "hello") is False


def test_a_new_chat_keeps_its_prompt_once_its_real_id_is_known():
    guard = EchoGuard()
    guard.remember("pending", "start something")
    guard.rename("pending", "s9")
    assert guard.claim("s9", "start something") is True


def test_old_entries_expire():
    guard = EchoGuard(ttl=-1)
    guard.remember("s1", "hello")
    assert guard.claim("s1", "hello") is False


# -- VS Code -----------------------------------------------------------------
def test_the_handoff_link_points_at_this_chat():
    url = handoff_url("abc-123")
    assert url.startswith("vscode://Anthropic.claude-code/open?session=abc-123")


def test_the_handoff_link_can_carry_text_and_escapes_it():
    assert "a%20b%20%26%20c" in handoff_url("abc", "a b & c")


def test_this_process_counts_as_alive():
    assert pid_alive(os.getpid()) is True


def test_an_impossible_process_id_is_not_alive():
    assert pid_alive(0) is False
    assert pid_alive(-5) is False


def test_a_project_open_in_a_live_window_is_detected(claude_home, monkeypatch):
    lock_dir = claude_home / "ide"
    lock_dir.mkdir()
    (lock_dir / "1.lock").write_text(json.dumps({
        "pid": os.getpid(), "workspaceFolders": [r"C:\Code\app"]}), encoding="utf-8")

    assert is_project_open(claude_home, Path(r"c:\code\app")) is True


def test_a_lock_left_behind_by_a_dead_window_is_ignored(claude_home):
    lock_dir = claude_home / "ide"
    lock_dir.mkdir()
    (lock_dir / "stale.lock").write_text(json.dumps({
        "pid": 2 ** 30, "workspaceFolders": [r"C:\Code\app"]}), encoding="utf-8")

    assert open_workspaces(claude_home) == []
    assert is_project_open(claude_home, Path(r"C:\Code\app")) is False


def test_a_corrupt_lock_file_does_not_raise(claude_home):
    lock_dir = claude_home / "ide"
    lock_dir.mkdir()
    (lock_dir / "bad.lock").write_text("not json", encoding="utf-8")

    assert open_workspaces(claude_home) == []


def test_no_ide_folder_means_nothing_is_open(claude_home):
    assert open_workspaces(claude_home) == []


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-only lookup")
def test_the_bundled_claude_executable_is_found_if_present():
    found = config_module.find_claude_cli()
    assert found is None or found.exists()
