"""Finding the chat files that belong to a project."""
from __future__ import annotations

from pathlib import Path

from bridge.locator import (find_session_files, known_projects, leaf_name,
                            normalise, slugify)
from tests.conftest import assistant_text, user_text


def test_slug_turns_every_non_alphanumeric_character_into_a_dash():
    assert slugify(r"c:\Code\My_App") == "c--Code-My-App"


def test_slug_keeps_repeated_dashes_so_the_drive_letter_survives():
    # "c:" becomes "c-" and the separator adds another, giving the "c--" prefix
    # Claude Code actually uses.
    assert slugify(r"c:\x").startswith("c--")


def test_paths_compare_equal_regardless_of_slash_or_case():
    assert normalise("D:/Code/App") == normalise(r"d:\code\app\\")


def test_finds_the_chat_files_of_one_project(chat_file, claude_home):
    chat_file(r"C:\Code\alpha", "aaaa", [user_text("hello")])
    chat_file(r"C:\Code\beta", "bbbb", [user_text("hello")])

    found = find_session_files(claude_home, [Path(r"C:\Code\alpha")])

    assert [session.session_id for session in found] == ["aaaa"]
    assert found[0].project == Path(r"C:\Code\alpha")


def test_no_projects_given_means_every_project(chat_file, claude_home):
    chat_file(r"C:\Code\alpha", "aaaa", [user_text("hello")])
    chat_file(r"C:\Code\beta", "bbbb", [user_text("hello")])

    found = find_session_files(claude_home, None)

    assert {session.session_id for session in found} == {"aaaa", "bbbb"}


def test_a_project_is_matched_by_its_recorded_path_not_its_folder_name(
        chat_file, claude_home):
    """Two paths can collide into one slug, so the recorded cwd decides."""
    path = chat_file(r"C:\Code\alpha", "aaaa", [user_text("hello")])
    renamed = path.parent.parent / "totally-unrelated-name"
    path.parent.rename(renamed)

    found = find_session_files(claude_home, [Path(r"C:\Code\alpha")])

    assert [session.session_id for session in found] == ["aaaa"]


def test_the_project_root_is_chosen_over_a_subfolder(chat_file, claude_home):
    """`cwd` is wherever Claude was working, which is often a subfolder. The
    folder's name is the slug of the root, so that is what identifies it."""
    root = r"C:\Code\app"
    deep = user_text("in a subfolder")
    deep["cwd"] = r"C:\Code\app\src\thing"
    start = user_text("at the root", uuid="u0")

    # The subfolder record comes first, so a naive "take the first" rule fails.
    chat_file(root, "aaaa", [deep, start])

    found = find_session_files(claude_home, None)
    assert found[0].project == Path(root)


def test_a_moved_project_falls_back_to_a_recorded_path(chat_file, claude_home):
    """After a move the folder is renamed but the records still hold the old
    path, so nothing matches the slug. It must still resolve to something."""
    path = chat_file(r"C:\Code\oldname", "aaaa", [user_text("hello")])
    path.parent.rename(path.parent.parent / "c--Code-newname")

    projects = known_projects(claude_home)

    assert [str(p) for p in projects.values()] == [r"C:\Code\oldname"]


def test_a_folder_with_no_readable_records_is_skipped(claude_home):
    empty = claude_home / "projects" / "c--Code-ghost"
    empty.mkdir(parents=True)
    (empty / "broken.jsonl").write_text("not json at all\n", encoding="utf-8")

    assert find_session_files(claude_home, None) == []


def test_known_projects_lists_each_project_once(chat_file, claude_home):
    chat_file(r"C:\Code\alpha", "aaaa", [user_text("one")])
    chat_file(r"C:\Code\alpha", "cccc", [assistant_text("two")])
    chat_file(r"C:\Code\beta", "bbbb", [user_text("three")])

    projects = known_projects(claude_home)

    assert sorted(leaf_name(p) for p in projects.values()) == ["alpha", "beta"]


def test_the_last_part_of_a_path_is_found_whatever_the_separator():
    """Recorded paths come from whichever machine Claude Code ran on, so a
    Windows path must still shorten correctly on a Linux host."""
    assert leaf_name(r"C:\Code\webshop") == "webshop"
    assert leaf_name("/home/me/code/webshop") == "webshop"
    assert leaf_name(r"C:\Code\webshop\\") == "webshop"
    assert leaf_name("webshop") == "webshop"


def test_a_missing_claude_home_is_not_an_error(tmp_path):
    assert find_session_files(tmp_path / "nothing-here", None) == []
    assert known_projects(tmp_path / "nothing-here") == {}
