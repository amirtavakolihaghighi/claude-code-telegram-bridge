r"""Turn chat-file records into the events we want to mirror.

A .jsonl chat file holds many record types. We keep:
  ai-title   -> the chat's name (used for the Telegram topic)
  user       -> what I typed, and the results of tool calls
  assistant  -> Claude's text, and the tool calls themselves
  cost-state -> end-of-turn cost, shown as a footer

A tool call and its result live in two separate records, often written seconds
apart. We hold the call back until its result lands, then emit them together as
one event, so each tool shows up in Telegram as a single message carrying both
what went in and what came out.

The file is appended to while Claude is working, so we only ever parse up to
the last complete newline and remember our byte offset.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from .events import Event
from .locator import leaf_name

SKIP_PREFIXES = ("<system-reminder>", "<local-command", "<command-name>")
LABEL_LIMIT = 90
INPUT_LIMIT = 600
OUTPUT_LIMIT = 900
PENDING_TIMEOUT = 120.0     # seconds to wait for a result before giving up

DEFAULT_ICON = "⚙️"
TOOL_ICONS = {
    "Bash": "\U0001f4bb", "PowerShell": "\U0001f4bb",
    "Read": "\U0001f4d6", "Write": "\U0001f4dd", "Edit": "✏️",
    "NotebookEdit": "✏️",
    "Grep": "\U0001f50d", "Glob": "\U0001f4c2",
    "WebFetch": "\U0001f310", "WebSearch": "\U0001f310",
    "Agent": "\U0001f9e9", "Task": "\U0001f9e9",
    "TodoWrite": "\U0001f4cb", "Skill": "\U0001f9e0",
    "Artifact": "\U0001f5bc️", "AskUserQuestion": "❓",
}


def _clean(text: str) -> str:
    return text.replace("\r\n", "\n").strip()


def _is_noise(text: str) -> bool:
    stripped = text.lstrip()
    return any(stripped.startswith(p) for p in SKIP_PREFIXES)


def _squash(value: object, limit: int) -> str:
    text = " ".join(str(value).split())
    return text[:limit] + ("..." if len(text) > limit else "")


def _trim_block(text: str, limit: int) -> str:
    """Shorten a multi-line block, saying how much was left out."""
    text = text.replace("\r\n", "\n").strip("\n")
    if len(text) <= limit:
        return text
    kept = text[:limit].rsplit("\n", 1)[0] or text[:limit]
    dropped = text[len(kept):].count("\n") + 1
    return kept + "\n... (+" + str(dropped) + " more lines)"


def _filename(value: object) -> str:
    try:
        return leaf_name(value) or str(value)
    except (TypeError, ValueError):
        return str(value)


def tool_icon(name: str) -> str:
    return TOOL_ICONS.get(name, DEFAULT_ICON)


def describe_tool(name: str, payload: dict) -> str:
    """One short human line saying what the tool call is for."""
    if name in ("Bash", "PowerShell"):
        description = payload.get("description")
        return _squash(description, LABEL_LIMIT) if description else "ran a command"
    if name == "Read":
        return "read " + _filename(payload.get("file_path", ""))
    if name == "Write":
        return "wrote " + _filename(payload.get("file_path", ""))
    if name in ("Edit", "NotebookEdit"):
        return "edited " + _filename(payload.get("file_path", ""))
    if name == "Grep":
        return "searched for " + _squash(payload.get("pattern", ""), LABEL_LIMIT)
    if name == "Glob":
        return "files matching " + _squash(payload.get("pattern", ""), LABEL_LIMIT)
    if name in ("WebFetch", "WebSearch"):
        return _squash(payload.get("url") or payload.get("query", ""), LABEL_LIMIT)
    if name in ("Agent", "Task"):
        return "delegated: " + _squash(payload.get("description", ""), LABEL_LIMIT)
    if name == "TodoWrite":
        return "updated the task list"
    detail = payload.get("description") or payload.get("query") or ""
    return _squash(detail, LABEL_LIMIT) if detail else name


def tool_input(name: str, payload: dict) -> str:
    """The part of a tool call worth showing verbatim."""
    if name in ("Bash", "PowerShell"):
        return _trim_block(str(payload.get("command", "")), INPUT_LIMIT)
    if name in ("Read", "Write", "Edit", "NotebookEdit"):
        path = str(payload.get("file_path", ""))
        body = payload.get("new_string") or payload.get("content") or ""
        if name in ("Write", "Edit") and body:
            return path + "\n" + _trim_block(str(body), INPUT_LIMIT // 2)
        return path
    if name in ("Grep", "Glob"):
        bits = ["pattern: " + str(payload.get("pattern", ""))]
        for key in ("path", "glob", "type", "output_mode"):
            if payload.get(key):
                bits.append(key + ": " + str(payload[key]))
        return "\n".join(bits)
    if name in ("WebFetch", "WebSearch"):
        return str(payload.get("url") or payload.get("query", ""))
    if name in ("Agent", "Task"):
        return _trim_block(str(payload.get("prompt", "")), INPUT_LIMIT)
    if name == "TodoWrite":
        return ""
    try:
        return _trim_block(json.dumps(payload, ensure_ascii=False, indent=1), INPUT_LIMIT)
    except (TypeError, ValueError):
        return _squash(payload, INPUT_LIMIT)


def _result_text(block: dict) -> str:
    content = block.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for piece in content:
            if not isinstance(piece, dict):
                continue
            if piece.get("type") == "text":
                parts.append(str(piece.get("text", "")))
            elif piece.get("type") == "image":
                parts.append("[image]")
        return "\n".join(parts)
    return ""


def parse_record(record: dict) -> list[Event]:
    kind = record.get("type")
    session_id = record.get("session_id") or record.get("sessionId") or ""

    if kind == "ai-title":
        title = _clean(record.get("aiTitle", ""))
        return [Event("title", session_id, text=title)] if title else []

    # Subagent chatter stays out of the mirror for now.
    if record.get("isSidechain"):
        return []

    uuid = record.get("uuid", "")
    timestamp = record.get("timestamp", "")

    if kind == "cost-state":
        cost = record.get("totalCostUSD")
        if cost is None:
            return []
        return [Event("turn_end", session_id, uuid=uuid, timestamp=timestamp,
                      extra={"cost": cost})]

    if kind not in ("user", "assistant") or record.get("isMeta"):
        return []

    content = (record.get("message") or {}).get("content")
    if content is None:
        return []
    if isinstance(content, str):
        content = [{"type": "text", "text": content}]

    events: list[Event] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        block_type = block.get("type")

        if block_type == "text":
            text = _clean(block.get("text", ""))
            if text and not _is_noise(text):
                events.append(Event("user" if kind == "user" else "assistant",
                                    session_id, text=text, uuid=uuid,
                                    timestamp=timestamp))

        elif block_type == "tool_use" and kind == "assistant":
            name = block.get("name", "tool")
            payload = block.get("input") or {}
            events.append(Event(
                "tool_call", session_id,
                text=describe_tool(name, payload), tool_name=name,
                uuid=uuid, timestamp=timestamp,
                extra={"id": block.get("id", ""),
                       "input": tool_input(name, payload)},
            ))

        elif block_type == "tool_result" and kind == "user":
            events.append(Event(
                "tool_result", session_id, uuid=uuid, timestamp=timestamp,
                extra={"id": block.get("tool_use_id", ""),
                       "output": _trim_block(_result_text(block), OUTPUT_LIMIT),
                       "is_error": bool(block.get("is_error"))},
            ))

        # thinking blocks are intentionally ignored

    return events


def read_title(path: Path) -> str | None:
    """Scan a whole chat file for its most recent title.

    Needed when we start following a chat mid-way: the title was recorded
    earlier in the file, before the point we resume reading from.
    """
    title = None
    try:
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line or '"ai-title"' not in line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if record.get("type") == "ai-title":
                    text = _clean(record.get("aiTitle", ""))
                    if text:
                        title = text
    except OSError:
        return None
    return title


def _merge(call: Event, output: str = "", is_error: bool = False) -> Event:
    extra = dict(call.extra)
    extra["output"] = output
    extra["is_error"] = is_error
    return Event("tool", call.session_id, text=call.text, tool_name=call.tool_name,
                 uuid=call.uuid, timestamp=call.timestamp, extra=extra)


class SessionReader:
    """Reads new lines from one chat file, remembering where it stopped."""

    def __init__(self, path: Path, session_id: str, offset: int = 0):
        self.path = path
        self.session_id = session_id
        self.offset = offset
        self._pending: dict[str, tuple[Event, float]] = {}

    def size(self) -> int:
        try:
            return self.path.stat().st_size
        except OSError:
            return 0

    def seek_to_end(self) -> None:
        self.offset = self.size()

    def _flush_stale(self, now: float) -> list[Event]:
        """Show tool calls whose result never arrived (interrupted, crashed)."""
        stale = [key for key, (_, seen) in self._pending.items()
                 if now - seen > PENDING_TIMEOUT]
        return [_merge(self._pending.pop(key)[0], "(no result recorded)")
                for key in stale]

    def _read_raw(self) -> list[Event]:
        size = self.size()
        if size < self.offset:      # file was rewritten - start over
            self.offset = 0
        if size == self.offset:
            return []

        try:
            with self.path.open("rb") as fh:
                fh.seek(self.offset)
                data = fh.read()
        except OSError:
            return []

        last_newline = data.rfind(b"\n")
        if last_newline == -1:      # a half-written line; wait for the rest
            return []

        complete = data[: last_newline + 1]
        self.offset += len(complete)

        events: list[Event] = []
        for raw in complete.decode("utf-8", errors="replace").splitlines():
            raw = raw.strip()
            if not raw:
                continue
            try:
                record = json.loads(raw)
            except json.JSONDecodeError:
                continue
            for event in parse_record(record):
                if event.session_id:
                    events.append(event)
                else:
                    events.append(Event(event.kind, self.session_id, event.text,
                                        event.tool_name, event.uuid,
                                        event.timestamp, event.extra))
        return events

    def read_new(self) -> list[Event]:
        now = time.monotonic()
        out: list[Event] = []

        for event in self._read_raw():
            if event.kind == "tool_call":
                key = event.extra.get("id") or event.uuid
                self._pending[key] = (event, now)
            elif event.kind == "tool_result":
                held = self._pending.pop(event.extra.get("id", ""), None)
                if held is None:
                    continue        # a result for a call we never saw
                out.append(_merge(held[0], event.extra.get("output", ""),
                                  event.extra.get("is_error", False)))
            else:
                out.append(event)

        out.extend(self._flush_stale(now))
        return out
