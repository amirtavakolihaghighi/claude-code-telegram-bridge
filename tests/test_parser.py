"""Turning chat-file records into the events we mirror."""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from types import SimpleNamespace

from bridge.parser import (SessionReader, describe_tool, parse_record,
                           read_title, started_at, tool_input)
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


def test_a_file_shorter_than_what_was_read_is_left_alone(chat_file):
    """Most likely an older copy from a backup. Starting over, as this once
    did, posted the whole chat a second time."""
    path = chat_file(r"C:\Code\app", "s1", [user_text("a longer first message")])
    reader = SessionReader(path, "s1")
    reader.read_new()
    read_to = reader.offset

    path.write_text(json.dumps(user_text("short")) + "\n", encoding="utf-8")

    assert reader.read_new() == []
    assert reader.offset == read_to


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


# -- knowing where things are in the file ------------------------------------
def test_each_event_knows_where_its_record_begins(chat_file):
    path = chat_file(r"C:\Code\app", "s1",
                     [user_text("first"), assistant_text("second")])
    first_line = path.read_bytes().index(b"\n") + 1

    events = read_all(path)

    assert [e.start for e in events] == [0, first_line]


def test_a_tool_event_begins_where_its_call_does(chat_file):
    path = chat_file(r"C:\Code\app", "s1", [
        assistant_text("checking"),
        tool_use("Bash", {"command": "pytest"}),
        tool_result("ok"),
    ])
    call_line = path.read_bytes().index(b"\n") + 1

    tool = [e for e in read_all(path) if e.kind == "tool"][0]

    assert tool.start == call_line


def test_a_call_waiting_for_its_result_holds_the_position_back(chat_file):
    path = chat_file(r"C:\Code\app", "s1", [
        assistant_text("checking"),
        tool_use("Bash", {"command": "pytest"}),
    ])
    reader = SessionReader(path, "s1")
    reader.read_new()

    assert reader.pending_start() == path.read_bytes().index(b"\n") + 1
    assert reader.offset == path.stat().st_size


# -- reading a long chat in slices -------------------------------------------
def test_reading_in_slices_gives_the_same_events_as_reading_at_once(chat_file):
    records = []
    for n in range(10):
        records += [tool_use("Bash", {"command": f"step {n}"}, tool_id=f"t{n}"),
                    tool_result(f"done {n}", tool_id=f"t{n}"),
                    assistant_text(f"after {n}")]
    path = chat_file(r"C:\Code\app", "s1", records)
    whole = read_all(path)

    reader = SessionReader(path, "s1")
    sliced = []
    while reader.unread():
        sliced += reader.read_new(max_bytes=150)

    assert [(e.kind, e.text, e.extra.get("output")) for e in sliced] == \
           [(e.kind, e.text, e.extra.get("output")) for e in whole]


def test_a_record_larger_than_the_slice_is_still_read_whole(chat_file):
    path = chat_file(r"C:\Code\app", "s1", [assistant_text("x" * 5000)])
    reader = SessionReader(path, "s1")

    events = reader.read_new(max_bytes=100)

    assert len(events) == 1 and len(events[0].text) == 5000


def test_a_call_whose_result_is_further_down_a_backlog_is_not_given_up_on(
        chat_file):
    path = chat_file(r"C:\Code\app", "s1", [
        tool_use("Bash", {"command": "slow"}),
        assistant_text("y" * 400),
        tool_result("finally"),
    ])
    reader = SessionReader(path, "s1")
    assert reader.read_new(max_bytes=50) == []
    # However long ago it was read: the rest of the file is still to come.
    key, (event, _) = next(iter(reader._pending.items()))
    reader._pending[key] = (event, -1000.0)

    events = []
    while reader.unread():
        events += reader.read_new(max_bytes=50)

    assert [e.extra.get("output") for e in events if e.kind == "tool"] == ["finally"]


# -- files that move, and odd characters -------------------------------------
def test_a_vanished_file_leaves_the_read_position_alone(chat_file):
    path = chat_file(r"C:\Code\app", "s1", [user_text("here")])
    reader = SessionReader(path, "s1")
    reader.read_new()
    read_to = reader.offset

    path.unlink()

    assert reader.read_new() == []
    assert reader.offset == read_to


def test_a_line_separator_inside_a_message_does_not_lose_it(chat_file):
    r"""JSON may carry U+2028 unescaped. Splitting on it, as str.splitlines
    does, broke the record in two and dropped both halves."""
    path = chat_file(r"C:\Code\app", "s1", [])
    with path.open("a", encoding="utf-8") as handle:
        record = user_text("one\u2028two")
        record.update(cwd=r"C:\Code\app", sessionId="s1")
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    assert [e.text for e in read_all(path)] == ["one\u2028two"]


def test_a_line_that_is_not_a_record_is_skipped(chat_file):
    path = chat_file(r"C:\Code\app", "s1", [])
    with path.open("a", encoding="utf-8") as handle:
        handle.write("[1, 2, 3]\n" + json.dumps(user_text("after")) + "\n")

    assert [e.text for e in read_all(path)] == ["after"]


# -- when a chat began -------------------------------------------------------
def test_a_chat_written_just_now_began_just_now(chat_file):
    record = user_text("hello")
    record["timestamp"] = datetime.now(timezone.utc).isoformat()
    path = chat_file(r"C:\Code\app", "s1", [record])

    # By its creation time where the system keeps one, else its first record.
    assert abs(started_at(path) - time.time()) < 300


def test_without_a_creation_time_the_first_timestamp_is_used(chat_file,
                                                              monkeypatch):
    record = user_text("hello")
    record["timestamp"] = "2026-01-01T00:00:00Z"
    path = chat_file(r"C:\Code\app", "s1",
                     [{"type": "summary", "summary": "no time here"}, record])
    monkeypatch.setattr(type(path), "stat",
                        lambda self, **kw: SimpleNamespace(st_size=1))

    assert started_at(path) == 1767225600.0


def test_a_chat_with_no_times_at_all_cannot_say_when_it_began(chat_file,
                                                              monkeypatch):
    path = chat_file(r"C:\Code\app", "s1", [user_text("timeless")])
    monkeypatch.setattr(type(path), "stat",
                        lambda self, **kw: SimpleNamespace(st_size=1))

    assert started_at(path) is None


# -- what VS Code shows, and the mirror used to miss -------------------------
def queued(text: str, mode: str = "prompt") -> dict:
    """A message typed while Claude was busy, taken into the running turn."""
    return {"type": "attachment", "uuid": "q1",
            "attachment": {"type": "queued_command", "commandMode": mode,
                           "prompt": [{"type": "text", "text": text}]}}


def test_a_message_typed_while_claude_was_busy_is_mirrored(chat_file):
    path = chat_file(r"C:\Code\app", "s1", [queued("also check the logs")])

    [event] = read_all(path)

    assert (event.kind, event.text) == ("user", "also check the logs")


def test_a_background_task_notice_is_not_taken_for_a_message(chat_file):
    path = chat_file(r"C:\Code\app", "s1",
                     [queued("<task-notification>done</task-notification>",
                             mode="task-notification")])

    assert read_all(path) == []


def test_a_pasted_image_is_marked_rather_than_silently_dropped(chat_file):
    record = user_text("what is wrong here?")
    record["message"]["content"].insert(0, {"type": "image", "source": {}})
    path = chat_file(r"C:\Code\app", "s1", [record])

    [event] = read_all(path)

    assert event.text == "[image]\n\nwhat is wrong here?"


def test_a_compaction_summary_is_not_presented_as_something_you_typed(chat_file):
    summary = user_text("This session is being continued from a previous "
                        "conversation that ran out of context. " + "x" * 20000)
    summary["isCompactSummary"] = True
    path = chat_file(r"C:\Code\app", "s1", [summary])

    assert [e.kind for e in read_all(path)] == ["compacted"]


def test_a_call_that_never_got_its_result_shows_where_it_was(chat_file):
    """Not at the end of the chat, after everything that followed it."""
    path = chat_file(r"C:\Code\app", "s1", [
        tool_use("Bash", {"command": "crashed mid-way"}, tool_id="t1"),
        user_text("let's try again", uuid="u9"),
        assistant_text("ok", uuid="a9"),
    ])

    events = read_all(path)

    assert [e.kind for e in events] == ["tool", "user", "assistant"]
    assert events[0].extra["output"] == "(no result recorded)"


def test_a_message_typed_mid_turn_does_not_cut_a_running_call_short(chat_file):
    path = chat_file(r"C:\Code\app", "s1", [
        tool_use("Bash", {"command": "slow build"}, tool_id="t1"),
        queued("how long will this take?"),
        tool_result("built", tool_id="t1"),
    ])

    events = read_all(path)

    assert [(e.kind, e.extra.get("output")) for e in events] == \
           [("user", None), ("tool", "built")]


def test_a_result_that_comes_after_being_given_up_on_is_still_shown(chat_file):
    path = chat_file(r"C:\Code\app", "s1",
                     [tool_use("Agent", {"description": "long research"})])
    reader = SessionReader(path, "s1")
    reader.read_new()
    key, (event, _) = next(iter(reader._pending.items()))
    reader._pending[key] = (event, -1e9)            # waited past the limit
    [shown] = reader.read_new()
    assert shown.extra["output"] == "(no result recorded)"

    chat_file(r"C:\Code\app", "s1", [tool_result("the findings")])
    [late] = reader.read_new()

    assert late.extra["output"] == "the findings"
    assert late.extra["late"] is True


def test_a_slow_call_is_waited_for_well_past_two_minutes(chat_file):
    """Two minutes was the old limit; builds and subagents run longer."""
    path = chat_file(r"C:\Code\app", "s1", [tool_use("Bash", {"command": "build"})])
    reader = SessionReader(path, "s1")
    reader.read_new()
    key, (event, seen) = next(iter(reader._pending.items()))
    reader._pending[key] = (event, seen - 600)       # ten minutes ago

    assert reader.read_new() == []


def test_the_first_record_beats_the_file_creation_time(chat_file, monkeypatch):
    """A backup copy is created when it is copied, not when its chat began."""
    record = user_text("from august")
    record["timestamp"] = "2026-08-19T22:03:00Z"
    path = chat_file(r"C:\Code\app", "s1", [record])
    monkeypatch.setattr(type(path), "stat",
                        lambda self, **kw: SimpleNamespace(st_size=1,
                                                           st_birthtime=2e9))

    assert started_at(path) == datetime(2026, 8, 19, 22, 3,
                                        tzinfo=timezone.utc).timestamp()
