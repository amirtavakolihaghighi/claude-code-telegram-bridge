"""Long command answers must be split, not rejected."""
from __future__ import annotations

from bridge.inbox import MESSAGE_LIMIT, _split


def test_a_short_answer_is_left_alone():
    assert _split("hello") == ["hello"]


def test_a_long_answer_is_split_into_sendable_pieces():
    text = "\n".join(f"line {i} with some words on it" for i in range(500))
    parts = _split(text)
    assert len(parts) > 1
    assert all(len(part) <= MESSAGE_LIMIT + 200 for part in parts)


def test_nothing_is_lost_in_the_split():
    text = "\n".join(f"row {i}" for i in range(400))
    assert "row 399" in "".join(_split(text))


def test_a_split_inside_a_block_leaves_no_unbalanced_tag():
    text = "<pre>" + "\n".join(f"line {i}" for i in range(900)) + "</pre>"
    for part in _split(text):
        assert part.count("<pre>") == part.count("</pre>")
