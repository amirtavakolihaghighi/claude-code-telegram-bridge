"""What your Claude Code sessions have cost, read from the chat files.

The numbers come from the same `~/.claude/projects/**/*.jsonl` files the mirror
tails, so this needs no extension installed and keeps working if one is removed.

Two things to be honest about:

* Token counts are exact. Every assistant record carries its own usage, with a
  timestamp, so they can be attributed to a day precisely.
* `cost-state` records hold a *running total* for the session, so a session's
  cost is its last such record, not the sum of them. That means cost can only be
  attributed to a whole session, and a session spanning midnight lands on the
  day it was last active.
"""
from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from .locator import find_session_files, leaf_name

# Parsing every file on each request would be slow, and they only grow.
_CACHE: dict[Path, tuple[int, float, "SessionUsage"]] = {}


@dataclass
class SessionUsage:
    session_id: str
    project: str
    title: str = ""
    cost: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read: int = 0
    cache_write: int = 0
    replies: int = 0
    first_day: date | None = None
    last_day: date | None = None
    tokens_by_day: dict[date, int] = field(default_factory=dict)

    @property
    def total_tokens(self) -> int:
        return (self.input_tokens + self.output_tokens
                + self.cache_read + self.cache_write)


def _day(stamp: str) -> date | None:
    try:
        return datetime.fromisoformat(
            stamp.replace("Z", "+00:00")).astimezone(timezone.utc).date()
    except (ValueError, AttributeError):
        return None


def read_session(path: Path, project: str) -> SessionUsage:
    """Totals for one chat file, cached against its size and modification time."""
    try:
        stat = path.stat()
    except OSError:
        return SessionUsage(path.stem, project)

    cached = _CACHE.get(path)
    if cached and cached[0] == stat.st_size and cached[1] == stat.st_mtime:
        return cached[2]

    usage = SessionUsage(path.stem, project)
    try:
        handle = path.open("r", encoding="utf-8", errors="replace")
    except OSError:
        return usage

    with handle:
        for line in handle:
            if '"usage"' not in line and '"totalCostUSD"' not in line \
                    and '"aiTitle"' not in line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue

            if record.get("type") == "ai-title" and record.get("aiTitle"):
                usage.title = str(record["aiTitle"])
                continue

            # A running total, so the last one seen is the session's cost.
            if record.get("totalCostUSD") is not None:
                usage.cost = float(record["totalCostUSD"])
                continue

            if record.get("type") != "assistant" or record.get("isSidechain"):
                continue
            counts = (record.get("message") or {}).get("usage") or {}
            if not counts:
                continue

            usage.replies += 1
            usage.input_tokens += int(counts.get("input_tokens") or 0)
            usage.output_tokens += int(counts.get("output_tokens") or 0)
            usage.cache_read += int(counts.get("cache_read_input_tokens") or 0)
            usage.cache_write += int(
                counts.get("cache_creation_input_tokens") or 0)

            when = _day(record.get("timestamp", ""))
            if when is not None:
                usage.first_day = min(usage.first_day or when, when)
                usage.last_day = max(usage.last_day or when, when)
                spent = (int(counts.get("input_tokens") or 0)
                         + int(counts.get("output_tokens") or 0)
                         + int(counts.get("cache_read_input_tokens") or 0)
                         + int(counts.get("cache_creation_input_tokens") or 0))
                usage.tokens_by_day[when] = \
                    usage.tokens_by_day.get(when, 0) + spent

    _CACHE[path] = (stat.st_size, stat.st_mtime, usage)
    return usage


@dataclass
class Report:
    sessions: list[SessionUsage]

    @property
    def cost(self) -> float:
        return sum(s.cost for s in self.sessions)

    @property
    def tokens(self) -> int:
        return sum(s.total_tokens for s in self.sessions)

    @property
    def cache_share(self) -> float:
        served = sum(s.cache_read for s in self.sessions)
        fresh = sum(s.input_tokens + s.cache_write for s in self.sessions)
        total = served + fresh
        return (served / total * 100) if total else 0.0

    def by_project(self) -> list[tuple[str, float, int]]:
        totals: dict[str, list[float]] = defaultdict(lambda: [0.0, 0])
        for session in self.sessions:
            entry = totals[leaf_name(session.project)]
            entry[0] += session.cost
            entry[1] += session.total_tokens
        rows = [(name, cost, int(tokens)) for name, (cost, tokens)
                in totals.items()]
        return sorted(rows, key=lambda row: -row[1])

    def tokens_by_day(self, days: int = 7) -> list[tuple[date, int]]:
        today = datetime.now(timezone.utc).date()
        window = {today - timedelta(days=offset) for offset in range(days)}
        totals: dict[date, int] = defaultdict(int)
        for session in self.sessions:
            for when, amount in session.tokens_by_day.items():
                if when in window:
                    totals[when] += amount
        return sorted(totals.items())

    def cost_since(self, when: date) -> float:
        """Approximate: a session counts on the day it was last active."""
        return sum(s.cost for s in self.sessions
                   if s.last_day is not None and s.last_day >= when)

    def top_sessions(self, limit: int = 5) -> list[SessionUsage]:
        return sorted(self.sessions, key=lambda s: -s.cost)[:limit]


def collect(claude_home: Path, projects) -> Report:
    return Report([read_session(session.path, str(session.project))
                   for session in find_session_files(claude_home, projects)])


def human_tokens(count: int) -> str:
    if count >= 1_000_000_000:
        return f"{count / 1e9:.1f}B"
    if count >= 1_000_000:
        return f"{count / 1e6:.1f}M"
    if count >= 1_000:
        return f"{count / 1e3:.0f}k"
    return str(count)
