"""Running Claude Code from Telegram.

We shell out to the same claude.exe the VS Code extension uses, with --resume
so the reply lands in the very same chat file the editor reads. We deliberately
do not parse what the CLI prints: the mirror is already tailing that file, so
Claude's answer, tool calls and all, shows up in Telegram on its own.

Permission questions are routed to Telegram by a hook we pass in with
--settings, which applies to this run only and never touches how Claude Code
behaves inside VS Code.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path

from .approval import ApprovalService
from .config import ROOT, Config

log = logging.getLogger(__name__)

RUN_TIMEOUT = 3600.0        # seconds; a very long turn is still a finite one
HOOK_TIMEOUT = 420          # seconds Claude Code waits for our permission hook
RUNTIME_DIR = ROOT / "runtime"
HOOK_SCRIPT = ROOT / "hooks" / "permission_hook.py"


@dataclass(frozen=True)
class RunResult:
    ok: bool
    session_id: str
    error: str = ""
    stopped: bool = False


class Runner:
    """Serialises prompts so one chat never has two turns running at once."""

    def __init__(self, config: Config, approval: ApprovalService | None = None):
        self.config = config
        self.approval = approval
        self._locks: dict[str, asyncio.Lock] = {}
        self._running: dict[str, asyncio.subprocess.Process] = {}
        self._stopped: set[str] = set()

    def _lock_for(self, session_id: str) -> asyncio.Lock:
        lock = self._locks.get(session_id)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[session_id] = lock
        return lock

    def is_busy(self, session_id: str) -> bool:
        lock = self._locks.get(session_id)
        return lock is not None and lock.locked()

    def stop(self, session_id: str) -> bool:
        """Cut a turn short. False if nothing was running."""
        proc = self._running.get(session_id)
        if proc is None or proc.returncode is not None:
            return False
        self._stopped.add(session_id)
        try:
            proc.terminate()
        except ProcessLookupError:
            return False
        return True

    # -- the permission hook ---------------------------------------------
    def _hook_settings_file(self) -> Path | None:
        """Settings passed to this one run, wiring permissions to Telegram."""
        if self.approval is None or not HOOK_SCRIPT.exists():
            return None
        RUNTIME_DIR.mkdir(exist_ok=True)
        path = RUNTIME_DIR / "permission-hook.json"
        settings = {
            "hooks": {
                "PermissionRequest": [
                    {
                        "hooks": [
                            {
                                "type": "command",
                                # exec form: no shell, so paths with spaces are fine
                                "command": sys.executable,
                                "args": [str(HOOK_SCRIPT)],
                                "timeout": HOOK_TIMEOUT,
                                "statusMessage": "Asking you on Telegram...",
                            }
                        ]
                    }
                ]
            }
        }
        path.write_text(json.dumps(settings, indent=2), encoding="utf-8")
        return path

    def _environment(self) -> dict[str, str]:
        env = dict(os.environ)
        if self.approval is not None and self.approval.port:
            env["BRIDGE_APPROVAL_URL"] = f"http://127.0.0.1:{self.approval.port}/ask"
            env["BRIDGE_APPROVAL_TOKEN"] = self.approval.token
        return env

    def _command(self, session_id: str | None, prompt: str) -> tuple[list[str], str]:
        cli = self.config.claude_cli
        if cli is None:
            raise RuntimeError(
                "Could not find claude.exe. Set CLAUDE_CLI in .env to its full path."
            )
        target = session_id or str(uuid.uuid4())
        args = [
            str(cli),
            "-p", prompt,
            "--output-format", "json",
            "--permission-mode", self.config.permission_mode,
        ]

        settings = self._hook_settings_file()
        if settings is not None:
            # "host" lets the permission question reach our hook.
            args += ["--settings", str(settings), "--permission-prompts", "host"]
        else:
            # Nobody can answer, so refuse rather than hang.
            args += ["--permission-prompts", "none"]

        args += ["--resume", target] if session_id else ["--session-id", target]
        return args, target

    # -- running ---------------------------------------------------------
    async def run(self, session_id: str | None, prompt: str,
                  project: Path) -> RunResult:
        try:
            args, target = self._command(session_id, prompt)
        except RuntimeError as exc:
            return RunResult(False, session_id or "", str(exc))
        if not project.is_dir():
            return RunResult(False, session_id or "",
                             f"the project folder is missing: {project}")

        async with self._lock_for(target):
            self._stopped.discard(target)
            log.info("running prompt in %s (%s)", target[:8],
                     "resume" if session_id else "new")
            try:
                proc = await asyncio.create_subprocess_exec(
                    *args,
                    cwd=str(project),
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    env=self._environment(),
                )
            except OSError as exc:
                return RunResult(False, target, f"could not start claude: {exc}")

            self._running[target] = proc
            try:
                raw_out, raw_err = await asyncio.wait_for(proc.communicate(),
                                                          timeout=RUN_TIMEOUT)
            except asyncio.TimeoutError:
                proc.kill()
                return RunResult(False, target,
                                 "the turn ran past an hour and was stopped")
            finally:
                self._running.pop(target, None)
                if self.approval is not None:
                    self.approval.forget_session(target)

            if target in self._stopped:
                self._stopped.discard(target)
                return RunResult(True, target, stopped=True)

            stdout = raw_out.decode("utf-8", errors="replace").strip()
            stderr = raw_err.decode("utf-8", errors="replace").strip()

            if proc.returncode != 0:
                detail = stderr or stdout or f"exit code {proc.returncode}"
                return RunResult(False, target, detail[:500])

            # The JSON tells us the real session id, which matters for a new chat.
            try:
                payload = json.loads(stdout)
            except json.JSONDecodeError:
                return RunResult(True, target)

            if payload.get("is_error"):
                return RunResult(False, payload.get("session_id", target),
                                 str(payload.get("result", "unknown error"))[:500])
            return RunResult(True, payload.get("session_id", target))
