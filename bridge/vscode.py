r"""Knowing what VS Code is doing, so Telegram and the editor don't collide.

Claude Code's VS Code extension drops a lock file per window in
~/.claude/ide/<pid>.lock listing the folders that window has open. Those files
are not cleaned up reliably, so we check whether the process is still alive
before believing one.

This tells us a project is open in VS Code. It cannot tell us which *chat* is
open in it - that lives in the extension's memory. So it is a warning, not a
guarantee.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from urllib.parse import quote

EXTENSION_ID = "Anthropic.claude-code"


def pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if sys.platform == "win32":
        import ctypes

        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        STILL_ACTIVE = 259
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return False
        try:
            code = ctypes.c_ulong()
            if kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return code.value == STILL_ACTIVE
            return True
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except (ProcessLookupError, PermissionError) as exc:
        return isinstance(exc, PermissionError)
    return True


def _normalise(path: str) -> str:
    return str(path).replace("/", "\\").rstrip("\\").casefold()


def open_workspaces(claude_home: Path) -> list[str]:
    """Folders currently open in a live VS Code window."""
    ide_dir = claude_home / "ide"
    if not ide_dir.is_dir():
        return []

    folders: list[str] = []
    for lock in ide_dir.glob("*.lock"):
        try:
            data = json.loads(lock.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        pid = data.get("pid")
        if not isinstance(pid, int) or not pid_alive(pid):
            continue
        for folder in data.get("workspaceFolders") or []:
            folders.append(str(folder))
    return folders


def is_project_open(claude_home: Path, project_dir: Path) -> bool:
    target = _normalise(project_dir)
    return any(_normalise(f) == target for f in open_workspaces(claude_home))


def handoff_url(session_id: str, prompt: str = "") -> str:
    """A link that opens this chat in VS Code, optionally with text typed in.

    The extension registers this scheme. It fills the composer but does not
    send - you still press Enter yourself.
    """
    url = f"vscode://{EXTENSION_ID}/open?session={quote(session_id)}"
    if prompt:
        url += f"&prompt={quote(prompt)}"
    return url
