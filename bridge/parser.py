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
import logging
import time
from dataclasses import replace
from datetime import datetime
from pathlib import Path

from .events import Event
from .locator import leaf_name

log = logging.getLogger(__name__)

SKIP_PREFIXES = ("<system-reminder>", "<local-command", "<command-name>")
LABEL_LIMIT = 90
INPUT_LIMIT = 600
OUTPUT_LIMIT = 900
# Seconds a call may wait at the end of its chat for a result before it is
# shown without one. Builds and subagents easily take minutes; two minutes,
# as it once was, showed them as having no result and then lost the real one.
PENDING_TIMEOUT = 1800.0
NO_RESULT = "(no result recorded)"
# How a compacted conversation's summary begins, in versions that do not flag it.
COMPACTED_PREFIX = "This session is being continued from a previous conversation"

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


def _prompt_text(prompt: object) -> str:
    """A prompt's text, whether recorded as a string or as content blocks."""
    if isinstance(prompt, str):
        return _clean(prompt)
    if not isinstance(prompt, list):
        return ""
    parts = []
    for block in prompt:
        if not isinstance(block, dict):
            continue
        if block.get("type") == "text":
            parts.append(str(block.get("text", "")))
        elif block.get("type") == "image":
            parts.append("[image]")
    return _clean("\n\n".join(part for part in parts if part.strip()))


def _is_compaction(record: dict, content: list) -> bool:
    if record.get("isCompactSummary"):
        return True
    first = next((b for b in content if isinstance(b, dict)
                  and b.get("type") == "text"), None)
    return bool(first) and str(first.get("text", "")).lstrip().startswith(
        COMPACTED_PREFIX)


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

    if kind == "attachment":
        attached = record.get("attachment")
        # Typed while Claude was busy, and taken into the turn already under
        # way. It never becomes an ordinary user record, so without this it
        # never reached Telegram at all.
        if isinstance(attached, dict) and attached.get("type") == "queued_command" \
                and attached.get("commandMode") == "prompt":
            text = _prompt_text(attached.get("prompt"))
            if text and not _is_noise(text):
                return [Event("user", session_id, text=text, uuid=uuid,
                              timestamp=timestamp, extra={"mid_turn": True})]
        return []

    if kind not in ("user", "assistant") or record.get("isMeta"):
        return []

    content = (record.get("message") or {}).get("content")
    if content is None:
        return []
    if isinstance(content, str):
        content = [{"type": "text", "text": content}]

    # Claude Code writes the summary of a compacted conversation as though
    # you had typed it. VS Code shows a marker; so do we.
    if kind == "user" and _is_compaction(record, content):
        return [Event("compacted", session_id, uuid=uuid, timestamp=timestamp)]

    events: list[Event] = []
    images = 0
    for block in content:
        if not isinstance(block, dict):
            continue
        block_type = block.get("type")

        if block_type == "image" and kind == "user":
            images += 1

        elif block_type == "text":
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
                       "input": tool_input(name, payload),
                       # Kept so a written document can be offered as a file.
                       "file_path": str(payload.get("file_path") or "")},
            ))

        elif block_type == "tool_result" and kind == "user":
            events.append(Event(
                "tool_result", session_id, uuid=uuid, timestamp=timestamp,
                extra={"id": block.get("tool_use_id", ""),
                       "output": _trim_block(_result_text(block), OUTPUT_LIMIT),
                       "is_error": bool(block.get("is_error"))},
            ))

        elif block_type == "thinking" and kind == "assistant":
            # Two different things arrive as "thinking" blocks. Claude's
            # reasoning is not recorded: its text is empty, leaving only a
            # signature. The short notes Claude writes between steps - "I'm
            # checking the log next" - are recorded in full, and VS Code shows
            # them in the chat, so they belong in Telegram too. Across this
            # machine's 39 chats, every block carrying text was such a note
            # and every reasoning block was empty.
            text = _clean(block.get("thinking", ""))
            if text and not _is_noise(text):
                events.append(Event("assistant", session_id, text=text,
                                    uuid=uuid, timestamp=timestamp))

    if images:
        # A pasted picture used to vanish without a trace. Say it was there.
        marker = "[image]" if images == 1 else f"[{images} images]"
        for index, event in enumerate(events):
            if event.kind == "user":
                events[index] = replace(event, text=f"{marker}\n\n{event.text}")
                break
        else:
            events.insert(0, Event("user", session_id, text=marker, uuid=uuid,
                                   timestamp=timestamp))
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


def started_at(path: Path) -> float | None:
    """When a chat began, as a Unix time, or None if that cannot be told.

    The first timestamp written inside it; failing that, the file's creation
    time where the system records one. Not the other way round: a backup
    copy was created when it was copied, not when its chat began.
    """
    try:
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            for _, line in zip(range(50), fh):
                if '"timestamp"' not in line:
                    continue
                try:
                    stamp = json.loads(line).get("timestamp")
                    return datetime.fromisoformat(stamp).timestamp()
                except (json.JSONDecodeError, AttributeError, TypeError,
                        ValueError):
                    continue
        born = getattr(path.stat(), "st_birthtime", None)
    except OSError:
        return None
    return float(born) if born else None


def _merge(call: Event, output: str = "", is_error: bool = False,
           late: bool = False) -> Event:
    extra = dict(call.extra)
    extra["output"] = output
    extra["is_error"] = is_error
    extra["late"] = late
    return Event("tool", call.session_id, text=call.text, tool_name=call.tool_name,
                 uuid=call.uuid, timestamp=call.timestamp, extra=extra,
                 start=call.start)


class SessionReader:
    """Reads new lines from one chat file, remembering where it stopped."""

    def __init__(self, path: Path, session_id: str, offset: int = 0):
        self.path = path
        self.session_id = session_id
        self.offset = offset
        self._pending: dict[str, tuple[Event, float]] = {}
        # Calls already shown as having no result, in case it comes after all.
        self._given_up: dict[str, Event] = {}
        self._shrunk = False

    def size(self) -> int:
        try:
            return self.path.stat().st_size
        except OSError:
            return 0

    def unread(self) -> int:
        """Bytes written to the chat that have not been read yet."""
        return max(self.size() - self.offset, 0)

    def seek_to_end(self) -> None:
        self.offset = self.size()

    def pending_start(self) -> int | None:
        """Where the oldest tool call still waiting for its result begins.

        Reading again from any later point would find the result without its
        call and drop it, so a saved read position must not pass this.
        """
        starts = [event.start for event, _ in self._pending.values()]
        return min(starts) if starts else None

    def _give_up(self, keys: list[str]) -> list[Event]:
        """Show these calls as having no result, remembering them in case it
        comes after all."""
        shown = []
        for key in keys:
            call, _ = self._pending.pop(key)
            self._given_up[key] = call
            shown.append(_merge(call, NO_RESULT))
        while len(self._given_up) > 200:
            self._given_up.pop(next(iter(self._given_up)))
        return shown

    def _flush_stale(self, now: float) -> list[Event]:
        """Show tool calls whose result never arrived (interrupted, crashed)."""
        return self._give_up([key for key, (_, seen) in self._pending.items()
                              if now - seen > PENDING_TIMEOUT])

    def _read_raw(self, max_bytes: int | None = None) -> list[Event]:
        try:
            size = self.path.stat().st_size
        except OSError:
            # Gone, or being moved. Starting over here would send the whole
            # chat again if it reappears, so leave the position alone.
            return []
        if size < self.offset:
            # Shorter than what was already read: an older copy of the chat
            # from a backup, most likely. Starting over used to send the
            # whole chat a second time; there is nothing new here to send.
            if not self._shrunk:
                log.info("chat %s is shorter than what was already sent, so "
                         "there is nothing new in it", self.session_id[:8])
                self._shrunk = True
            return []
        if size == self.offset:
            return []

        try:
            with self.path.open("rb") as fh:
                fh.seek(self.offset)
                data = fh.read(max_bytes) if max_bytes else fh.read()
                # A single record can be larger than the slice, an image
                # especially; it still has to be read whole.
                while max_bytes and b"\n" not in data:
                    more = fh.read(max_bytes)
                    if not more:
                        break
                    data += more
        except OSError:
            return []

        last_newline = data.rfind(b"\n")
        if last_newline == -1:      # a half-written line; wait for the rest
            return []

        complete = data[: last_newline + 1]
        position = self.offset
        self.offset += len(complete)

        events: list[Event] = []
        # Split on newlines only. str.splitlines() also breaks at characters
        # such as U+2028, which JSON may carry unescaped inside a string, and
        # would lose that record.
        for line in complete.split(b"\n")[:-1]:
            start = position
            position += len(line) + 1
            raw = line.decode("utf-8", errors="replace").strip()
            if not raw:
                continue
            try:
                record = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if not isinstance(record, dict):
                continue
            for event in parse_record(record):
                events.append(replace(event, start=start,
                                      session_id=event.session_id
                                      or self.session_id))
        return events

    def read_new(self, max_bytes: int | None = None) -> list[Event]:
        """Events from what has been written since the last read.

        With max_bytes, reads roughly that much at most, so a long backlog can
        be taken a slice at a time.
        """
        now = time.monotonic()
        out: list[Event] = []

        for event in self._read_raw(max_bytes):
            if event.kind == "tool_call":
                key = event.extra.get("id") or event.uuid
                self._pending[key] = (event, now)
            elif event.kind == "tool_result":
                key = event.extra.get("id", "")
                output = event.extra.get("output", "")
                failed = event.extra.get("is_error", False)
                held = self._pending.pop(key, None)
                if held is not None:
                    out.append(_merge(held[0], output, failed))
                elif key in self._given_up:
                    # Shown as having no result, and then it came: show it.
                    out.append(_merge(self._given_up.pop(key), output, failed,
                                      late=True))
                # Otherwise a result for a call read before where we started.
            elif event.kind == "user" and not event.extra.get("mid_turn"):
                # A new prompt means the turn before it is over, so a call
                # still waiting never got its result. Show it where it was,
                # not at the end of the chat.
                out.extend(self._give_up(list(self._pending)))
                out.append(event)
            else:
                out.append(event)

        # While a backlog is still being read, a missing result is usually
        # just further down the file, however long ago the call was read.
        if self.unread() == 0:
            out.extend(self._flush_stale(now))
        return out
