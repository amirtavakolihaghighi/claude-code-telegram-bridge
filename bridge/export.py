"""Turn a chat file into a readable transcript, as Markdown or HTML.

The file is parsed once into entries, then rendered either way:

  Markdown  plain, diff-friendly, and the right thing to hand back to an AI.
  HTML      keeps formatting, collapses long tool output, and marks text
            `dir="auto"` so right-to-left languages read correctly - something
            Markdown has no way to express.

Neither reuses the mirror's parser: that one shortens tool input and output to
keep Telegram messages readable, and an archive is the one place the whole
thing should be kept.
"""
from __future__ import annotations

import html
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path


@dataclass
class Entry:
    kind: str                       # "user" | "claude" | "tool"
    text: str = ""
    when: str = ""
    tool: str = ""
    payload: str = ""
    output: str = ""
    failed: bool = False


@dataclass
class Transcript:
    title: str
    session_id: str
    project: str | None = None
    first: str = ""
    last: str = ""
    turns: int = 0
    cost: float | None = None
    entries: list[Entry] = field(default_factory=list)


# ----------------------------------------------------------------- reading
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
            if not isinstance(piece, dict):
                continue
            if piece.get("type") == "text":
                parts.append(str(piece.get("text", "")))
            elif piece.get("type") == "image":
                parts.append("[image]")
        return "\n".join(parts)
    return ""


def _when(record: dict) -> str:
    stamp = record.get("timestamp")
    if not stamp:
        return ""
    try:
        moment = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
        return moment.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    except ValueError:
        return str(stamp)


def read_transcript(path: Path, title: str | None = None,
                    project: str | None = None) -> Transcript:
    try:
        raw_lines = path.read_text(encoding="utf-8",
                                   errors="replace").splitlines()
    except OSError as exc:
        raise RuntimeError(f"could not read the chat file: {exc}") from exc

    script = Transcript(title=title or path.stem, session_id=path.stem,
                        project=project)
    found_title = bool(title)
    pending: dict[str, tuple[str, dict]] = {}

    for raw in raw_lines:
        raw = raw.strip()
        if not raw:
            continue
        try:
            record = json.loads(raw)
        except json.JSONDecodeError:
            continue

        kind = record.get("type")
        if kind == "ai-title" and not found_title:
            if record.get("aiTitle"):
                script.title = str(record["aiTitle"])
        if record.get("isSidechain"):
            continue
        if kind == "cost-state" and record.get("totalCostUSD") is not None:
            script.cost = record["totalCostUSD"]

        stamp = _when(record)
        if stamp:
            script.first = script.first or stamp
            script.last = stamp

        if kind not in ("user", "assistant"):
            continue

        for block in _blocks(record):
            block_type = block.get("type")

            if block_type == "text":
                text = str(block.get("text", "")).strip()
                if not text or text.lstrip().startswith("<system-reminder>"):
                    continue
                if kind == "user":
                    script.turns += 1
                script.entries.append(
                    Entry("user" if kind == "user" else "claude",
                          text=text, when=stamp))

            elif block_type == "tool_use" and kind == "assistant":
                pending[str(block.get("id"))] = (str(block.get("name", "tool")),
                                                 block.get("input") or {})

            elif block_type == "tool_result" and kind == "user":
                name, payload = pending.pop(str(block.get("tool_use_id")),
                                            ("tool", {}))
                try:
                    shown = json.dumps(payload, ensure_ascii=False, indent=2)
                except (TypeError, ValueError):
                    shown = str(payload)
                script.entries.append(
                    Entry("tool", tool=name, payload=shown, when=stamp,
                          output=_result_text(block).rstrip(),
                          failed=bool(block.get("is_error"))))

    return script


# ---------------------------------------------------------------- markdown
def _fence(text: str, language: str = "") -> str:
    """Wrap in a fence long enough that the content cannot break out of it."""
    longest = run = 0
    for char in text:
        run = run + 1 if char == "`" else 0
        longest = max(longest, run)
    ticks = "`" * max(3, longest + 1)
    return f"{ticks}{language}\n{text.rstrip()}\n{ticks}"


def render_markdown(script: Transcript) -> str:
    lines = [f"# {script.title}", ""]
    if script.project:
        lines.append(f"- **Project:** `{script.project}`")
    lines.append(f"- **Chat id:** `{script.session_id}`")
    if script.first:
        lines.append(f"- **From:** {script.first}")
    if script.last and script.last != script.first:
        lines.append(f"- **To:** {script.last}")
    lines.append(f"- **Your messages:** {script.turns}")
    if script.cost is not None:
        lines.append(f"- **Cost:** ${script.cost:.2f}")
    lines += ["", "---", ""]

    for entry in script.entries:
        if entry.kind in ("user", "claude"):
            lines.append("## " + ("You" if entry.kind == "user" else "Claude"))
            if entry.when:
                lines.append(f"*{entry.when}*")
            lines += ["", entry.text, ""]
        else:
            lines.append(f"### {entry.tool}{' — failed' if entry.failed else ''}")
            lines += ["", _fence(entry.payload, "json"), ""]
            if entry.output:
                lines += [_fence(entry.output), ""]

    return "\n".join(lines).rstrip() + "\n"


# -------------------------------------------------------------------- html
STYLE = """
:root { color-scheme: light dark;
  --bg:#fff; --fg:#1a1a1a; --muted:#6b7280; --line:#e5e7eb;
  --you:#eff6ff; --code:#f6f8fa; --fail:#b91c1c; }
@media (prefers-color-scheme: dark) { :root {
  --bg:#15171a; --fg:#e6e6e6; --muted:#9aa1ab; --line:#2b2f36;
  --you:#1b2836; --code:#1b1e23; --fail:#f87171; } }
* { box-sizing: border-box; }
body { margin:0; padding:24px 16px 64px; background:var(--bg); color:var(--fg);
  font:16px/1.65 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,
  "Helvetica Neue",Arial,"Noto Sans","Vazirmatn",sans-serif; }
main { max-width: 820px; margin: 0 auto; }
header { border-bottom:1px solid var(--line); padding-bottom:16px;
  margin-bottom:24px; }
h1 { font-size:1.6rem; margin:0 0 12px; }
dl { display:grid; grid-template-columns:auto 1fr; gap:4px 12px; margin:0;
  font-size:.875rem; color:var(--muted); }
dt { font-weight:600; }
dd { margin:0; word-break:break-word; }
.msg { padding:12px 16px; margin:0 0 14px; border-radius:10px;
  border:1px solid var(--line); }
.msg.user { background:var(--you); }
.who { font-weight:700; font-size:.8rem; letter-spacing:.04em;
  text-transform:uppercase; color:var(--muted); margin-bottom:6px;
  display:flex; justify-content:space-between; gap:12px; }
.when { font-weight:400; text-transform:none; letter-spacing:0; }
.body { white-space:pre-wrap; overflow-wrap:anywhere; }
details { margin:0 0 14px; border:1px solid var(--line); border-radius:10px;
  overflow:hidden; }
summary { cursor:pointer; padding:10px 16px; font-size:.9rem;
  background:var(--code); }
summary::marker { color:var(--muted); }
details[open] summary { border-bottom:1px solid var(--line); }
.tool-name { font-weight:700; }
.failed .tool-name { color:var(--fail); }
pre { margin:0; padding:12px 16px; background:var(--code); overflow-x:auto;
  font:13px/1.5 ui-monospace,SFMono-Regular,Consolas,"Liberation Mono",monospace; }
pre + pre { border-top:1px solid var(--line); }
footer { margin-top:32px; padding-top:16px; border-top:1px solid var(--line);
  font-size:.8rem; color:var(--muted); text-align:center; }
"""


def _esc(text: str) -> str:
    return html.escape(text, quote=False)


def render_html(script: Transcript) -> str:
    rows = []
    if script.project:
        rows.append(("Project", script.project))
    rows.append(("Chat id", script.session_id))
    if script.first:
        rows.append(("From", script.first))
    if script.last and script.last != script.first:
        rows.append(("To", script.last))
    rows.append(("Your messages", str(script.turns)))
    if script.cost is not None:
        rows.append(("Cost", f"${script.cost:.2f}"))

    parts = [
        "<!doctype html>",
        '<html lang="en"><head><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width,initial-scale=1">',
        f"<title>{_esc(script.title)}</title>",
        f"<style>{STYLE}</style></head><body><main>",
        f'<header><h1 dir="auto">{_esc(script.title)}</h1><dl>',
    ]
    for label, value in rows:
        parts.append(f"<dt>{_esc(label)}</dt>"
                     f'<dd dir="auto">{_esc(str(value))}</dd>')
    parts.append("</dl></header>")

    for entry in script.entries:
        if entry.kind in ("user", "claude"):
            who = "You" if entry.kind == "user" else "Claude"
            klass = "msg user" if entry.kind == "user" else "msg"
            parts.append(
                f'<section class="{klass}"><div class="who"><span>{who}</span>'
                f'<span class="when">{_esc(entry.when)}</span></div>'
                # dir="auto" lets each message pick its own direction, so
                # right-to-left text reads correctly next to English.
                f'<div class="body" dir="auto">{_esc(entry.text)}</div>'
                f"</section>")
        else:
            state = " failed" if entry.failed else ""
            label = f"{_esc(entry.tool)}{' — failed' if entry.failed else ''}"
            body = f"<pre>{_esc(entry.payload)}</pre>"
            if entry.output:
                body += f"<pre>{_esc(entry.output)}</pre>"
            parts.append(
                f'<details class="tool{state}"><summary>'
                f'<span class="tool-name">{label}</span></summary>{body}</details>')

    parts.append("<footer>Exported from Claude Code by the Telegram bridge."
                 "</footer></main></body></html>")
    return "\n".join(parts)


# ------------------------------------------------------------------ public
def chat_to_markdown(path: Path, title: str | None = None,
                     project: str | None = None) -> str:
    return render_markdown(read_transcript(path, title, project))


def chat_to_html(path: Path, title: str | None = None,
                 project: str | None = None) -> str:
    return render_html(read_transcript(path, title, project))


def safe_filename(title: str, session_id: str, suffix: str = ".md") -> str:
    keep = [c if c.isalnum() or c in " -_" else "-" for c in (title or "chat")]
    cleaned = "".join(keep).strip().replace(" ", "-")[:60].strip("-")
    return f"{cleaned or 'chat'}-{session_id[:8]}{suffix}"
