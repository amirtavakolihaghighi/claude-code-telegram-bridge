"""Turning a chat into a Markdown transcript."""
from __future__ import annotations

from bridge.export import chat_to_markdown, safe_filename
from tests.conftest import (assistant_text, cost_state, title, tool_result,
                            tool_use, user_text)


def test_the_conversation_appears_in_order(chat_file):
    path = chat_file(r"C:\Code\app", "s1", [
        user_text("do the thing"),
        assistant_text("done"),
    ])
    text = chat_to_markdown(path)
    assert text.index("do the thing") < text.index("done")
    assert "## You" in text
    assert "## Claude" in text


def test_the_header_says_what_this_is(chat_file):
    path = chat_file(r"C:\Code\app", "s1", [title("Fix the bug"),
                                            user_text("hello"),
                                            cost_state(1.5)])
    text = chat_to_markdown(path, project=r"C:\Code\app")
    assert text.startswith("# Fix the bug")
    assert r"C:\Code\app" in text
    assert "$1.50" in text
    assert "**Your messages:** 1" in text


def test_tool_output_is_kept_in_full(chat_file):
    """The mirror shortens tool output; an archive must not."""
    long_output = "\n".join(f"line {i}" for i in range(500))
    path = chat_file(r"C:\Code\app", "s1", [
        tool_use("Bash", {"command": "seq 500"}),
        tool_result(long_output),
    ])
    text = chat_to_markdown(path)
    assert "line 499" in text
    assert "more lines" not in text


def test_a_failed_tool_is_marked(chat_file):
    path = chat_file(r"C:\Code\app", "s1", [
        tool_use("Bash", {"command": "false"}),
        tool_result("boom", is_error=True),
    ])
    assert "failed" in chat_to_markdown(path)


def test_content_cannot_break_out_of_its_code_fence(chat_file):
    """Output containing backticks must not end the block early."""
    path = chat_file(r"C:\Code\app", "s1", [
        tool_use("Bash", {"command": "cat readme"}),
        tool_result("```\nnot the end\n```"),
    ])
    text = chat_to_markdown(path)
    assert "````" in text
    assert "not the end" in text


def test_system_reminders_stay_out(chat_file):
    path = chat_file(r"C:\Code\app", "s1",
                     [user_text("<system-reminder>hidden</system-reminder>"),
                      user_text("visible", uuid="u2")])
    text = chat_to_markdown(path)
    assert "hidden" not in text
    assert "visible" in text


def test_subagent_chatter_stays_out(chat_file):
    record = assistant_text("from a subagent")
    record["isSidechain"] = True
    path = chat_file(r"C:\Code\app", "s1", [record, assistant_text("main", "a2")])
    text = chat_to_markdown(path)
    assert "from a subagent" not in text
    assert "main" in text


def test_a_corrupt_line_does_not_stop_the_export(chat_file):
    path = chat_file(r"C:\Code\app", "s1", [user_text("before")])
    with path.open("a", encoding="utf-8") as handle:
        handle.write("{ not json }\n")
    path_text = chat_to_markdown(path)
    assert "before" in path_text


def test_a_missing_file_is_reported_clearly(tmp_path):
    import pytest
    with pytest.raises(RuntimeError):
        chat_to_markdown(tmp_path / "gone.jsonl")


def test_the_filename_is_safe_for_every_system():
    name = safe_filename('Fix: the "bug"/thing\\now', "abcdef1234")
    assert "/" not in name and "\\" not in name and ":" not in name
    assert name.endswith(".md")
    assert "abcdef12" in name


def test_a_chat_with_no_title_still_gets_a_filename():
    assert safe_filename("", "abcdef1234").endswith(".md")
