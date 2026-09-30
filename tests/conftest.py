"""Shared fixtures.

Every test runs against a throwaway Claude home built inside pytest's tmp_path.
Nothing here ever reads or writes the real ~/.claude directory: those files are
the user's actual conversations and cannot be regenerated.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from bridge import locator


@pytest.fixture(autouse=True)
def _clear_locator_cache():
    """The project-folder lookup is cached for speed; tests must not share it."""
    locator._CWD_CACHE.clear()
    yield
    locator._CWD_CACHE.clear()


@pytest.fixture
def claude_home(tmp_path: Path) -> Path:
    """A fake ~/.claude, safely inside the temp directory."""
    home = tmp_path / "claude"
    (home / "projects").mkdir(parents=True)
    return home


@pytest.fixture
def chat_file(claude_home: Path):
    """Write records into a fake chat file, the way Claude Code lays them out."""
    def make(project: str, session_id: str, records: list[dict]) -> Path:
        folder = claude_home / "projects" / locator.slugify(project)
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{session_id}.jsonl"
        with path.open("a", encoding="utf-8") as handle:
            for record in records:
                record.setdefault("cwd", project)
                record.setdefault("sessionId", session_id)
                handle.write(json.dumps(record) + "\n")
        return path
    return make


# -- record builders, matching the real on-disk shapes -----------------------
def user_text(text: str, uuid: str = "u1") -> dict:
    return {"type": "user", "uuid": uuid, "isSidechain": False,
            "message": {"role": "user", "content": [{"type": "text", "text": text}]}}


def assistant_text(text: str, uuid: str = "a1") -> dict:
    return {"type": "assistant", "uuid": uuid, "isSidechain": False,
            "message": {"role": "assistant",
                        "content": [{"type": "text", "text": text}]}}


def tool_use(name: str, payload: dict, tool_id: str = "t1", uuid: str = "a2") -> dict:
    return {"type": "assistant", "uuid": uuid, "isSidechain": False,
            "message": {"role": "assistant",
                        "content": [{"type": "tool_use", "id": tool_id,
                                     "name": name, "input": payload}]}}


def tool_result(output: str, tool_id: str = "t1", is_error: bool = False,
                uuid: str = "u2") -> dict:
    block = {"type": "tool_result", "tool_use_id": tool_id, "content": output}
    if is_error:
        block["is_error"] = True
    return {"type": "user", "uuid": uuid, "isSidechain": False,
            "message": {"role": "user", "content": [block]}}


def title(text: str) -> dict:
    return {"type": "ai-title", "aiTitle": text}


def cost_state(cost: float) -> dict:
    return {"type": "cost-state", "uuid": "c1", "totalCostUSD": cost}
