"""Rendering events as Telegram HTML."""
from __future__ import annotations

from bridge.events import Event
from bridge.format import CHUNK_LIMIT, render, render_body


def test_html_special_characters_are_escaped():
    assert render_body("a < b & c > d") == ["a &lt; b &amp; c &gt; d"]


def test_fenced_code_becomes_a_preformatted_block():
    rendered = render_body("before\n```python\nprint('<hi>')\n```\nafter")[0]
    assert "<pre>print('&lt;hi&gt;')</pre>" in rendered
    assert "python" not in rendered          # the language tag is stripped


def test_inline_code_and_bold_survive():
    rendered = render_body("use `pip` and **do not** guess")[0]
    assert "<code>pip</code>" in rendered
    assert "<b>do not</b>" in rendered


def test_a_long_message_is_split_into_several():
    text = "\n".join(f"line {i}" for i in range(2000))
    parts = render_body(text)
    assert len(parts) > 1
    assert all(len(part) <= CHUNK_LIMIT + 200 for part in parts)


def test_a_single_enormous_line_is_split_too():
    parts = render_body("x" * (CHUNK_LIMIT * 3))
    assert len(parts) == 3


def test_a_code_block_split_across_messages_is_closed_and_reopened():
    body = "```\n" + "\n".join(f"line {i}" for i in range(2000)) + "\n```"
    parts = render_body(body)
    assert len(parts) > 1
    for part in parts:
        assert part.count("<pre>") == part.count("</pre>")


def test_your_own_messages_arrive_silently_and_claudes_do_not():
    mine = render(Event("user", "s1", text="hello"))[0]
    theirs = render(Event("assistant", "s1", text="hi"))[0]
    assert mine.silent is True
    assert theirs.silent is False


def test_a_tool_message_shows_what_went_in_and_what_came_out():
    event = Event("tool", "s1", text="Run tests", tool_name="Bash",
                  extra={"input": "pytest -q", "output": "41 passed",
                         "is_error": False})
    html = render(event)[0].html
    assert "<b>Bash</b>" in html
    assert "<pre>pytest -q</pre>" in html
    assert "41 passed" in html
    assert "blockquote" in html              # output collapses


def test_a_failed_tool_is_marked_as_failed():
    event = Event("tool", "s1", text="Run tests", tool_name="Bash",
                  extra={"input": "false", "output": "exit 1", "is_error": True})
    html = render(event)[0].html
    assert "<b>failed</b>" in html


def test_tool_messages_never_notify():
    event = Event("tool", "s1", text="x", tool_name="Read",
                  extra={"input": "a.py", "output": "1 line", "is_error": False})
    assert render(event)[0].silent is True


def test_the_end_of_turn_shows_the_cost():
    html = render(Event("turn_end", "s1", extra={"cost": 0.3712}))[0].html
    assert "$0.37" in html


def test_an_empty_message_renders_nothing():
    assert render(Event("assistant", "s1", text="   ")) == []


def test_a_title_event_produces_no_message():
    assert render(Event("title", "s1", text="Some chat")) == []
