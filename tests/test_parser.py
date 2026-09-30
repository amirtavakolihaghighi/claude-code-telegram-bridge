"""Turning chat-file records into the events we mirror."""
from __future__ import annotations

import json

from bridge.parser import (SessionReader, describe_tool, parse_record,
                           read_title, tool_input)
from tests.conftest import (assistant_text, cost_state, title, tool_result,
                            tool_use, user_text)


def read_all(path):
    return SessionReader(path, path.stem).read_new()


# -- what we keep and what we drop -------------------------------------------
def test_a_typed_message_becomes_a_user_event(chat_file):
    path = chat_file(r"C:\Code\app", "s1", [user_text("fix the bug")])
    events = read_all(path)
    assert [(e.kind, e.text) for e in events] == [("user", "fix the bug")]


def test_system_reminders_are_not_mirrored(chat_file):
    path = chat_file(r"C:\Code\app", "s1",
                     [user_text("<system-reminder>ignore me</system-reminder>")])
    assert read_all(path) == []


def test_subagent_chatter_is_not_mirrored(chat_file):
    record = assistant_text("from a subagent")
    record["isSidechain"] = True
    path = chat_file(r"C:\Code\app", "s1", [record])
    assert read_all(path) == []


def test_thinking_blocks_are_not_mirrored(chat_file):
    record = {"type": "assistant", "uuid": "a9", "isSidechain": False,
              "message": {"role": "assistant",
                          "content": [{"type": "thinking", "thinking": "hmm",
                                       "signature": "x"}]}}
    path = chat_file(r"C:\Code\app", "s1", [record])
    assert read_all(path) == []


def test_the_end_of_turn_cost_is_kept(chat_file):
    path = chat_file(r"C:\Code\app", "s1", [cost_state(1.25)])
    events = read_all(path)
    assert events[0].kind == "turn_end"
    assert events[0].extra["cost"] == 1.25


# -- tool calls and their results --------------------------------------------
def test_a_tool_call_waits_for_its_result_and_arrives_as_one_event(chat_file):
    path = chat_file(r"C:\Code\app", "s1", [
        tool_use("Bash", {"command": "pytest -q", "description": "Run tests"}),
        tool_result("41 passed"),
    ])
    events = read_all(path)

    assert len(events) == 1
    event = events[0]
    assert event.kind == "tool"
    assert event.tool_name == "Bash"
    assert event.text == "Run tests"
    assert event.extra["input"] == "pytest -q"
    assert event.extra["output"] == "41 passed"
    assert event.extra["is_error"] is False


def test_a_failed_tool_is_marked_as_an_error(chat_file):
    path = chat_file(r"C:\Code\app", "s1", [
        tool_use("Bash", {"command": "false"}),
        tool_result("exit code 1", is_error=True),
    ])
    assert read_all(path)[0].extra["is_error"] is True


def test_a_call_and_its_result_pair_up_across_separate_reads(chat_file):
    path = chat_file(r"C:\Code\app", "s1",
                     [tool_use("Bash", {"command": "sleep 1"})])
    reader = SessionReader(path, "s1")

    assert reader.read_new() == []          # held back, the result has not landed

    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(tool_result("done")) + "\n")

    events = reader.read_new()
    assert [e.kind for e in events] == ["tool"]
    assert events[0].extra["output"] == "done"


def test_a_result_with_no_matching_call_is_ignored(chat_file):
    path = chat_file(r"C:\Code\app", "s1", [tool_result("orphan", tool_id="zzz")])
    assert read_all(path) == []


def test_an_abandoned_tool_call_eventually_shows_with_no_result(chat_file):
    path = chat_file(r"C:\Code\app", "s1",
                     [tool_use("Bash", {"command": "hang forever"})])
    reader = SessionReader(path, "s1")
    assert reader.read_new() == []

    # Pretend the wait has already elapsed rather than sleeping for two minutes.
    key, (event, _) = next(iter(reader._pending.items()))
    reader._pending[key] = (event, -1000.0)

    events = reader.read_new()
    assert events[0].extra["output"] == "(no result recorded)"


# -- reading a file that is still being written ------------------------------
def test_a_half_written_line_is_left_for_the_next_pass(chat_file):
    path = chat_file(r"C:\Code\app", "s1", [user_text("complete")])
    with path.open("a", encoding="utf-8") as handle:
        handle.write('{"type": "user", "uuid": "partial"')   # no newline yet

    reader = SessionReader(path, "s1")
    events = reader.read_new()

    assert [e.text for e in events] == ["complete"]
    assert reader.offset < path.stat().st_size


def test_reading_resumes_where_it_stopped(chat_file):
    path = chat_file(r"C:\Code\app", "s1", [user_text("first")])
    reader = SessionReader(path, "s1")
    reader.read_new()

    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(assistant_text("second")) + "\n")

    assert [e.text for e in reader.read_new()] == ["second"]


def test_a_rewritten_file_is_read_from_the_beginning_again(chat_file):
    path = chat_file(r"C:\Code\app", "s1", [user_text("a longer first message")])
    reader = SessionReader(path, "s1")
    reader.read_new()

    path.write_text(json.dumps(user_text("short")) + "\n", encoding="utf-8")

    assert [e.text for e in reader.read_new()] == ["short"]


def test_unreadable_lines_do_not_stop_the_rest(chat_file):
    path = chat_file(r"C:\Code\app", "s1", [user_text("before")])
    with path.open("a", encoding="utf-8") as handle:
        handle.write("{ this is not json }\n")
        handle.write(json.dumps(assistant_text("after")) + "\n")

    assert [e.text for e in read_all(path)] == ["before", "after"]


# -- titles ------------------------------------------------------------------
def test_the_latest_title_wins(chat_file):
    path = chat_file(r"C:\Code\app", "s1",
                     [title("First guess"), user_text("hi"), title("Better title")])
    assert read_title(path) == "Better title"


def test_a_chat_with_no_title_returns_nothing(chat_file):
    path = chat_file(r"C:\Code\app", "s1", [user_text("hi")])
    assert read_title(path) is None


# -- how tool calls are described --------------------------------------------
def test_each_tool_is_described_in_plain_words():
    assert describe_tool("Read", {"file_path": r"C:\Code\app\main.py"}) == "read main.py"
    assert describe_tool("Write", {"file_path": "/tmp/out.txt"}) == "wrote out.txt"
    assert describe_tool("Grep", {"pattern": "TODO"}) == "searched for TODO"
    assert describe_tool("Bash", {"command": "ls"}) == "ran a command"
    assert describe_tool("Bash", {"command": "ls", "description": "List"}) == "List"


def test_long_tool_output_says_how_much_was_left_out():
    payload = {"command": "\n".join(f"line {i}" for i in range(400))}
    shown = tool_input("Bash", payload)
    assert "more lines" in shown
    assert len(shown) < 800


def test_an_image_result_is_described_rather_than_dumped():
    record = {"type": "user", "uuid": "u1", "isSidechain": False,
              "message": {"role": "user", "content": [
                  {"type": "tool_result", "tool_use_id": "t1",
                   "content": [{"type": "image", "source": {}}]}]}}
    events = parse_record(record)
    assert events[0].extra["output"] == "[image]"
