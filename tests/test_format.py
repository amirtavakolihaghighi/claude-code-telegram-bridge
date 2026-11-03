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


def test_the_end_of_turn_says_nothing():
    """It used to post a running total whenever a session's cost was written -
    every Claude exit and every window close - in chats where nothing had
    happened. /cost and /usage replace it."""
    assert render(Event("turn_end", "s1", extra={"cost": 0.3712})) == []


def test_an_empty_message_renders_nothing():
    assert render(Event("assistant", "s1", text="   ")) == []


def test_a_title_event_produces_no_message():
    assert render(Event("title", "s1", text="Some chat")) == []


# -- markdown Telegram cannot show natively ----------------------------------
def test_a_heading_becomes_bold_not_literal_hashes():
    assert render_body("## Final state")[0] == "<b>Final state</b>"
    assert render_body("# One")[0] == "<b>One</b>"
    assert render_body("###### Six")[0] == "<b>Six</b>"


def test_a_hash_that_is_not_a_heading_is_left_alone():
    assert "#" in render_body("issue #42 is fixed")[0]


def test_a_two_column_table_becomes_a_labelled_list():
    rendered = render_body(
        "| | |\n|---|---|\n| Working tree | clean |\n| CI | green |")[0]
    assert "|" not in rendered
    assert "• <b>Working tree</b> — clean" in rendered
    assert "• <b>CI</b> — green" in rendered


def test_a_two_column_table_keeps_a_real_header():
    rendered = render_body(
        "| Check | Result |\n|---|---|\n| CI | green |")[0]
    assert "<b>Check · Result</b>" in rendered


def test_a_wider_table_keeps_its_shape_in_a_monospace_block():
    rendered = render_body(
        "| Format | Good for | Size |\n|---|---|---|\n"
        "| Markdown | feeding an AI | 158 KB |\n| HTML | reading | 165 KB |")[0]
    assert rendered.startswith("<pre>")
    assert "|" not in rendered
    assert "Markdown  feeding an AI  158 KB" in rendered


def test_a_line_of_pipes_that_is_not_a_table_is_left_alone():
    """Without a divider row it is not a table, so it must not be mangled."""
    assert "|" in render_body("a | b | c")[0]


def test_bullets_become_real_bullets_and_keep_their_indentation():
    rendered = render_body("- top\n  - nested\n* star")[0]
    assert "• top" in rendered
    assert "  • nested" in rendered
    assert "• star" in rendered


def test_numbered_lists_are_left_as_they_are():
    assert "1. first" in render_body("1. first\n2. second")[0]


def test_a_quote_becomes_a_quote_block():
    rendered = render_body("> one\n> two")[0]
    assert rendered == "<blockquote>one\ntwo</blockquote>"


def test_a_horizontal_rule_becomes_a_line():
    assert "─" in render_body("---")[0]


def test_a_link_is_clickable():
    rendered = render_body("see the [changelog](https://example.com/a.md)")[0]
    assert '<a href="https://example.com/a.md">changelog</a>' in rendered


def test_italic_and_strikethrough():
    assert "<i>and</i>" in render_body("the commit *and* the tag")[0]
    assert "<s>ignore</s>" in render_body("~~ignore~~ this")[0]


def test_stars_inside_code_are_not_treated_as_formatting():
    rendered = render_body("use `a * b ** c` here")[0]
    assert "<code>a * b ** c</code>" in rendered
    assert "<i>" not in rendered and "<b>" not in rendered


def test_stars_inside_a_fenced_block_are_left_alone():
    rendered = render_body("```\nprint('a * b ** c')\n```")[0]
    assert "a * b ** c" in rendered
    assert "<i>" not in rendered


def test_a_url_with_a_quote_cannot_break_out_of_the_tag():
    rendered = render_body('[x](https://e.com/a"onmouseover=1)')[0]
    assert '"onmouseover' not in rendered.split("</a>")[0].replace("&quot;", "")


def test_markdown_inside_a_table_cell_still_formats():
    rendered = render_body("| a | b |\n|---|---|\n| **bold** | `code` |")[0]
    assert "<b>" in rendered and "<code>code</code>" in rendered


# -- markers -----------------------------------------------------------------
def test_a_compacted_conversation_is_one_short_marker():
    from bridge.events import Event
    [message] = render(Event("compacted", "s1"))

    assert "Conversation compacted" in message.html
    assert message.silent is True


def test_a_notice_is_shown_but_does_not_notify():
    [message] = render(Event("notice", "s1",
                             text="Claude's reply was stopped."))

    assert "Notice" in message.html
    assert "Claude's reply was stopped." in message.html
    assert message.silent is True


def test_an_empty_notice_posts_nothing():
    assert render(Event("notice", "s1", text="   ")) == []


def test_a_result_that_came_later_says_so():
    from bridge.events import Event
    late = Event("tool", "s1", text="long research", tool_name="Agent",
                 extra={"input": "", "output": "the findings", "late": True})

    [message] = render(late)

    assert "its result, which came later" in message.html
    assert "the findings" in message.html


# -- staying inside Telegram's limit -----------------------------------------
WIDE_TABLE = (
    "| Option | What it does | Special cases |\n|---|---|---|\n"
    "| **Install** | Sets the bridge to start by itself every time you sign in, "
    "hidden, as you | If it is already installed, it rewrites the task |\n"
    "| **Stop** | Ends the background copy | Only until your next sign-in |\n")


def test_a_table_too_wide_for_a_phone_becomes_a_list():
    rendered = render_body(WIDE_TABLE)[0]

    assert "<pre>" not in rendered and "|" not in rendered
    assert rendered.startswith("• <b><b>Install</b></b>")   # bold kept, as ever
    assert "<i>What it does:</i> Ends the background copy" in rendered
    assert "<i>Special cases:</i> Only until your next sign-in" in rendered


def test_a_narrow_table_keeps_its_columns_lined_up():
    rendered = render_body("| chat | MB | posts |\n|---|---|---|\n"
                           "| 3c9e1f70 | 120.5 | 812 |\n| a47d02b8 | 2.1 | 40 |")[0]

    assert rendered.startswith("<pre>")


def test_no_part_of_a_long_reply_is_longer_than_telegram_takes():
    """A reply with a wide table was refused as too long, and never seen."""
    from bridge.format import VISIBLE_LIMIT, visible_length
    rows = "".join(f"| row {n} | {'a long cell ' * 12} | {'and more ' * 9} |\n"
                   for n in range(40))
    reply = "Intro.\n\n| A | B | C |\n|---|---|---|\n" + rows + "\nThe end."

    parts = render_body(reply)

    assert all(visible_length(part) <= VISIBLE_LIMIT for part in parts)
    assert "The end." in parts[-1]


def test_a_part_that_grows_past_the_limit_is_split_again(monkeypatch):
    """Even a narrow table can be padded past the limit over enough rows."""
    from bridge import format as fmt
    rows = "".join(f"| {n} | x | y |\n" for n in range(260))
    text = "| a | b | c |\n|---|---|---|\n| " + "w" * 40 + " | x | y |\n" + rows

    parts = render_body(text)

    assert len(parts) > 1
    assert all(fmt.visible_length(part) <= fmt.VISIBLE_LIMIT for part in parts)


def test_length_is_counted_the_way_telegram_counts_it():
    from bridge.format import visible_length
    assert visible_length("<b>a&amp;b</b>") == 3
    assert visible_length("\U0001f916") == 2        # an emoji is two units
