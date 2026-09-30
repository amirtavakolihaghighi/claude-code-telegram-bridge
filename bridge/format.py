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
_INLINE_CODE = re.compile(r"`([^`\n]+)`")
_BOLD = re.compile(r"\*\*([^*\n]+)\*\*")
_LANG_TAG = re.compile(r"^[A-Za-z0-9_+.-]{0,15}\n")

USER_ICON = "\U0001f464"
CLAUDE_ICON = "\U0001f916"
ERROR_ICON = "⚠️"
DONE_ICON = "✅"
ARROW = "↳"


@dataclass(frozen=True)
class OutMessage:
    html: str
    silent: bool = True


def _escape(text: str) -> str:
    return html.escape(text, quote=False)


def _inline(text: str) -> str:
    text = _INLINE_CODE.sub(r"<code>\1</code>", text)
    return _BOLD.sub(r"<b>\1</b>", text)


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
            rendered.append(_inline(_escape(part)))
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

    return [OutMessage("\n".join(parts), silent=True)]


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
