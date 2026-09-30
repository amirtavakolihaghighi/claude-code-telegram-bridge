"""Render events as Telegram HTML, split to fit the 4096-character limit.

Each rendered message also says whether it should arrive silently. Only
Claude's replies buzz the phone; tool activity and footers stay quiet.
"""
from __future__ import annotations

import html
import re
from dataclasses import dataclass

from .events import Event
from .parser import tool_icon

CHUNK_LIMIT = 3500
_LANG_TAG = re.compile(r"^[A-Za-z0-9_+.-]{0,15}\n")

# Telegram accepts a short list of tags and nothing else: no headings, no
# tables, no lists. Markdown that assumes otherwise has to be translated into
# what it can show, or it arrives as literal "## Heading" and rows of pipes.
_INLINE_CODE = re.compile(r"`([^`\n]+)`")
_LINK = re.compile(r"\[([^\]\n]+)\]\(([^)\s]+)\)")
_BOLD = re.compile(r"\*\*([^*\n]+)\*\*")
_ITALIC = re.compile(r"(?<!\*)\*([^*\n]+)\*(?!\*)")
_STRIKE = re.compile(r"~~([^~\n]+)~~")
_HEADING = re.compile(r"^\s{0,3}(#{1,6})\s+(.*?)\s*#*\s*$")
_BULLET = re.compile(r"^(\s*)[-*+]\s+(.*)$")
_QUOTE = re.compile(r"^\s*>\s?(.*)$")
_RULE = re.compile(r"^\s*(?:-{3,}|\*{3,}|_{3,})\s*$")
_TABLE_ROW = re.compile(r"^\s*\|.*\|\s*$")
_TABLE_DIVIDER = re.compile(r"^\s*\|[\s:|-]+\|\s*$")

RULE_LINE = "─" * 12
BULLET = "•"
STASH = "\x00"

USER_ICON = "\U0001f464"
CLAUDE_ICON = "\U0001f916"
ERROR_ICON = "⚠️"
DONE_ICON = "✅"
ARROW = "↳"


@dataclass(frozen=True)
class OutMessage:
    html: str
    silent: bool = True
    # A file this message could offer. The sink decides whether to show the
    # button, since only it knows which kinds are wanted.
    attach: str = ""


def _escape(text: str) -> str:
    return html.escape(text, quote=False)


def _inline(text: str) -> str:
    """Inline markdown -> Telegram HTML. `text` must already be escaped.

    Code spans are lifted out first so that bold and italic markers inside them
    are left alone - `a * b` is multiplication, not emphasis.
    """
    spans: list[str] = []

    def stash(match: re.Match) -> str:
        spans.append(match.group(1))
        return f"{STASH}{len(spans) - 1}{STASH}"

    text = _INLINE_CODE.sub(stash, text)
    text = _LINK.sub(
        lambda m: f'<a href="{html.escape(m.group(2), quote=True)}">'
                  f"{m.group(1)}</a>", text)
    text = _BOLD.sub(r"<b>\1</b>", text)
    text = _ITALIC.sub(r"<i>\1</i>", text)
    text = _STRIKE.sub(r"<s>\1</s>", text)
    return re.sub(rf"{STASH}(\d+){STASH}",
                  lambda m: f"<code>{spans[int(m.group(1))]}</code>", text)


def _formatted(text: str) -> str:
    return _inline(_escape(text))


def _plain(text: str) -> str:
    """Strip inline markers, for places that cannot carry formatting."""
    text = _INLINE_CODE.sub(r"\1", text)
    text = _LINK.sub(r"\1", text)
    text = _BOLD.sub(r"\1", text)
    text = _ITALIC.sub(r"\1", text)
    return _STRIKE.sub(r"\1", text)


def _cells(row: str) -> list[str]:
    return [cell.strip() for cell in row.strip().strip("|").split("|")]


def _render_table(rows: list[list[str]]) -> str:
    """Telegram has no tables. Two columns read best as a labelled list; wider
    ones keep their shape in a monospace block, which scrolls sideways."""
    width = max(len(row) for row in rows)
    header, body = rows[0], rows[1:]

    if width <= 2:
        lines = []
        if any(cell for cell in header):
            lines.append("<b>" + " · ".join(_formatted(c) for c in header
                                                 if c) + "</b>")
        for row in body:
            left = _formatted(row[0]) if row else ""
            right = _formatted(row[1]) if len(row) > 1 else ""
            if left and right:
                lines.append(f"{BULLET} <b>{left}</b> — {right}")
            elif left or right:
                lines.append(f"{BULLET} {left or right}")
        return "\n".join(lines)

    widths = [max(len(_plain(row[i])) if i < len(row) else 0 for row in rows)
              for i in range(width)]
    lines = []
    for row in rows:
        padded = [_plain(row[i] if i < len(row) else "").ljust(widths[i])
                  for i in range(width)]
        lines.append("  ".join(padded).rstrip())
    return f"<pre>{_escape(chr(10).join(lines))}</pre>"


def _prose(text: str) -> str:
    """A block of markdown that is not inside a code fence."""
    lines = text.split("\n")
    out: list[str] = []
    index = 0

    while index < len(lines):
        line = lines[index]

        if (_TABLE_ROW.match(line) and index + 1 < len(lines)
                and _TABLE_DIVIDER.match(lines[index + 1])):
            rows = [_cells(line)]
            index += 2                                  # skip the divider
            while index < len(lines) and _TABLE_ROW.match(lines[index]):
                rows.append(_cells(lines[index]))
                index += 1
            out.append(_render_table(rows))
            continue

        quote = _QUOTE.match(line)
        if quote:
            quoted = [quote.group(1)]
            index += 1
            while index < len(lines) and (m := _QUOTE.match(lines[index])):
                quoted.append(m.group(1))
                index += 1
            body = "\n".join(_formatted(q) for q in quoted)
            out.append(f"<blockquote>{body}</blockquote>")
            continue

        index += 1

        if _RULE.match(line):
            out.append(RULE_LINE)
            continue

        heading = _HEADING.match(line)
        if heading:
            out.append(f"<b>{_formatted(heading.group(2))}</b>")
            continue

        bullet = _BULLET.match(line)
        if bullet:
            out.append(f"{bullet.group(1)}{BULLET} {_formatted(bullet.group(2))}")
            continue

        out.append(_formatted(line))

    return "\n".join(out)


def _chunk(text: str, limit: int) -> list[str]:
    """Split on line boundaries, hard-splitting any single huge line."""
    chunks: list[str] = []
    current = ""
    for line in text.split("\n"):
        while len(line) > limit:
            if current:
                chunks.append(current)
                current = ""
            chunks.append(line[:limit])
            line = line[limit:]
        candidate = f"{current}\n{line}" if current else line
        if len(candidate) > limit:
            chunks.append(current)
            current = line
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks or [""]


def _convert(chunk: str, in_code: bool) -> tuple[str, bool]:
    """Markdown-ish text -> Telegram HTML, carrying fence state across chunks."""
    parts = chunk.split("```")
    rendered: list[str] = []
    for index, part in enumerate(parts):
        if in_code:
            body = _LANG_TAG.sub("", part).strip("\n")
            rendered.append(f"<pre>{_escape(body)}</pre>" if body else "")
        else:
            rendered.append(_prose(part))
        if index < len(parts) - 1:
            in_code = not in_code
    return "".join(rendered), in_code


def render_body(text: str) -> list[str]:
    """Message text -> one or more ready-to-send HTML strings."""
    messages: list[str] = []
    in_code = False
    for chunk in _chunk(text, CHUNK_LIMIT):
        converted, in_code = _convert(chunk, in_code)
        messages.append(converted)
    return [m for m in messages if m.strip()]


def _quote(text: str, expandable: bool = True) -> str:
    """A collapsible quote block - long tool output stays out of the way."""
    tag = "<blockquote expandable>" if expandable else "<blockquote>"
    return f"{tag}{_escape(text)}</blockquote>"


def _render_tool(event: Event) -> list[OutMessage]:
    name = event.tool_name or "tool"
    failed = bool(event.extra.get("is_error"))
    icon = ERROR_ICON if failed else tool_icon(name)

    header = f"{icon} <b>{_escape(name)}</b>"
    if event.text:
        header += f" · <i>{_escape(event.text)}</i>"
    if failed:
        header += " · <b>failed</b>"

    parts = [header]
    tool_in = (event.extra.get("input") or "").strip()
    if tool_in:
        parts.append(f"<pre>{_escape(tool_in)}</pre>")

    tool_out = (event.extra.get("output") or "").strip()
    if tool_out:
        parts.append(f"{ARROW} {_quote(tool_out)}")

    written = "" if failed else str(event.extra.get("file_path") or "")
    return [OutMessage("\n".join(parts), silent=True, attach=written)]


def render(event: Event) -> list[OutMessage]:
    """An event -> the messages to post. Empty list means 'post nothing'."""
    if event.kind == "tool":
        return _render_tool(event)

    if event.kind == "turn_end":
        cost = event.extra.get("cost")
        if cost is None:
            return []
        return [OutMessage(f"{DONE_ICON} <i>turn finished · ${cost:.2f}</i>",
                           silent=True)]

    if event.kind in ("user", "assistant"):
        bodies = render_body(event.text)
        if not bodies:
            return []
        is_user = event.kind == "user"
        icon = USER_ICON if is_user else CLAUDE_ICON
        who = "You" if is_user else "Claude"
        head = f"{icon} <b>{who}</b>"
        # Only Claude's replies are worth a notification.
        silent = is_user
        first = OutMessage(f"{head}\n\n{bodies[0]}", silent=silent)
        return [first] + [OutMessage(b, silent=True) for b in bodies[1:]]

    return []
