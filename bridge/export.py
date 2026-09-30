"""Turn a chat file into a Markdown transcript.

This deliberately does not reuse the mirror's parser. That one shortens tool
input and output to keep Telegram messages readable; an archive is the one
place where the whole thing should be kept.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path


def _blocks(record: dict) -> list[dict]:
    content = (record.get("message") or {}).get("content")
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    return [b for b in (content or []) if isinstance(b, dict)]


def _result_text(block: dict) -> str:
    content = block.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for piece in content:
            if isinstance(piece, dict) and piece.get("type") == "text":
                parts.append(str(piece.get("text", "")))
            elif isinstance(piece, dict) and piece.get("type") == "image":
                parts.append("[image]")
        return "\n".join(parts)
    return ""


def _fence(text: str, language: str = "") -> str:
    """Wrap in a fence long enough that the content cannot break out of it."""
    longest = 0
    run = 0
    for char in text:
        run = run + 1 if char == "`" else 0
        longest = max(longest, run)
    ticks = "`" * max(3, longest + 1)
    return f"{ticks}{language}\n{text.rstrip()}\n{ticks}"


def _when(record: dict) -> str:
    stamp = record.get("timestamp")
    if not stamp:
        return ""
    try:
        moment = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
        return moment.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    except ValueError:
        return str(stamp)


def chat_to_markdown(path: Path, title: str | None = None,
                     project: str | None = None) -> str:
    """The whole conversation, in the order it happened."""
    lines: list[str] = []
    pending: dict[str, tuple[str, dict]] = {}
    first_time = last_time = ""
    turns = 0
    cost = None

    try:
        raw_lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as exc:
        raise RuntimeError(f"could not read the chat file: {exc}") from exc

    for raw in raw_lines:
        raw = raw.strip()
        if not raw:
            continue
        try:
            record = json.loads(raw)
        except json.JSONDecodeError:
            continue

        kind = record.get("type")
        if kind == "ai-title" and not title:
            title = record.get("aiTitle")
        if record.get("isSidechain"):
            continue
        if kind == "cost-state" and record.get("totalCostUSD") is not None:
            cost = record["totalCostUSD"]

        stamp = _when(record)
        if stamp:
            first_time = first_time or stamp
            last_time = stamp

        if kind not in ("user", "assistant"):
            continue

        for block in _blocks(record):
            block_type = block.get("type")

            if block_type == "text":
                text = str(block.get("text", "")).strip()
                if not text or text.lstrip().startswith("<system-reminder>"):
                    continue
                who = "You" if kind == "user" else "Claude"
                if kind == "user":
                    turns += 1
                lines.append(f"## {who}")
                if stamp:
                    lines.append(f"*{stamp}*")
                lines.append("")
                lines.append(text)
                lines.append("")

            elif block_type == "tool_use" and kind == "assistant":
                pending[str(block.get("id"))] = (str(block.get("name", "tool")),
                                                 block.get("input") or {})

            elif block_type == "tool_result" and kind == "user":
                name, payload = pending.pop(str(block.get("tool_use_id")),
                                            ("tool", {}))
                output = _result_text(block).rstrip()
                failed = " — failed" if block.get("is_error") else ""
                lines.append(f"### {name}{failed}")
                lines.append("")
                try:
                    shown = json.dumps(payload, ensure_ascii=False, indent=2)
                except (TypeError, ValueError):
                    shown = str(payload)
                lines.append(_fence(shown, "json"))
                lines.append("")
                if output:
                    lines.append(_fence(output))
                    lines.append("")

    header = [f"# {title or path.stem}", ""]
    if project:
        header.append(f"- **Project:** `{project}`")
    header.append(f"- **Chat id:** `{path.stem}`")
    if first_time:
        header.append(f"- **From:** {first_time}")
    if last_time and last_time != first_time:
        header.append(f"- **To:** {last_time}")
    header.append(f"- **Your messages:** {turns}")
    if cost is not None:
        header.append(f"- **Cost:** ${cost:.2f}")
    header += ["", "---", ""]

    return "\n".join(header + lines).rstrip() + "\n"


def safe_filename(title: str, session_id: str) -> str:
    keep = [c if c.isalnum() or c in " -_" else "-" for c in (title or "chat")]
    cleaned = "".join(keep).strip().replace(" ", "-")[:60].strip("-")
    return f"{cleaned or 'chat'}-{session_id[:8]}.md"
