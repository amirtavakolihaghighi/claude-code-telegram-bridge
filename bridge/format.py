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
# Telegram takes 4096 characters as shown - tags gone, entities decoded,
# counted in UTF-16 units. A little is left for the header on a first part.
VISIBLE_LIMIT = 4000
# Wider than this, a table in a monospace block runs off a phone screen and
# must be scrolled sideways, so it is shown as a list instead.
TABLE_WIDTH = 60
_TAG = re.compile(r"<[^>]+>")
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
COMPACTED_ICON = "\U0001f5dc️"
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


def visible_length(html_text: str) -> int:
    """The length Telegram holds a message to: what is shown, in UTF-16 units."""
    shown = html.unescape(_TAG.sub("", html_text))
    return len(shown.encode("utf-16-le")) // 2


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


def _table_as_list(header: list[str], body: list[list[str]], width: int) -> str:
    """Each row as a short block: its first cell in bold, then every other
    cell under its column's name."""
    labels = header + [""] * (width - len(header))
    blocks = []
    for row in body:
        cells = row + [""] * (width - len(row))
        lines = [f"{BULLET} <b>{_formatted(cells[0])}</b>" if cells[0] else BULLET]
        for label, value in zip(labels[1:], cells[1:]):
            if value:
                lines.append(f"<i>{_formatted(label)}:</i> {_formatted(value)}"
                             if label else _formatted(value))
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def _render_table(rows: list[list[str]]) -> str:
    """Telegram has no tables. Two columns read best as a labelled list. Wider
    ones keep their shape in a monospace block - unless that would be too wide
    for a phone, when each row becomes a short list of its own. Padding a long
    cell's whole column also once made a reply too long for Telegram to take,
    and it was never seen."""
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
    if sum(widths) + 2 * (width - 1) > TABLE_WIDTH:
        return _table_as_list(header, body, width)
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


def _fit(chunk: str, in_code: bool) -> tuple[list[str], bool]:
    """Convert a chunk, splitting it again if what comes out is still too long.

    The chunk was cut to size as text, but markup can make it grow, and a
    message over the limit is refused outright - lost, not shortened.
    """
    converted, after = _convert(chunk, in_code)
    if visible_length(converted) <= VISIBLE_LIMIT or len(chunk) < 200:
        return [converted], after
    parts, state = [], in_code
    for piece in _chunk(chunk, len(chunk) // 2):
        fitted, state = _fit(piece, state)
        parts += fitted
    return parts, state


def render_body(text: str) -> list[str]:
    """Message text -> one or more ready-to-send HTML strings."""
    messages: list[str] = []
    in_code = False
    for chunk in _chunk(text, CHUNK_LIMIT):
        fitted, in_code = _fit(chunk, in_code)
        messages += fitted
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
    if event.extra.get("late"):
        # Shown earlier as having no result; this is the result, come at last.
        header += " · <i>its result, which came later</i>"

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

    if event.kind == "compacted":
        # The summary itself runs to tens of thousands of characters that you
        # never wrote; VS Code folds it away behind a marker too.
        return [OutMessage(f"{COMPACTED_ICON} <i>Conversation compacted: Claude "
                           "carried on from a summary of everything before "
                           "this point.</i>", silent=True)]

    if event.kind == "notice":
        # Claude Code's own word about the conversation - most often that a
        # reply was stopped. Shown, so the gap it leaves is explained, but
        # silent: it is not someone talking to you.
        bodies = render_body(event.text)
        if not bodies:
            return []
        head = OutMessage(f"{ERROR_ICON} <b>Notice</b>\n\n{bodies[0]}",
                          silent=True)
        return [head] + [OutMessage(b, silent=True) for b in bodies[1:]]

    if event.kind == "turn_end":
        # Deliberately silent. This fired whenever a session's cost was written -
        # every time Claude exited or a VS Code window closed - which meant a
        # running total appearing in chats long after anything had happened in
        # them. Ask for it with /cost or /usage instead.
        return []

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
