"""Asking permission on your phone.

When Claude wants to do something that needs your say-so, it runs a small hook
script. That script asks this service, which posts Allow / Deny buttons into
the right Telegram topic and waits for your tap.

The service listens on localhost only and checks a secret that changes every
time the bridge starts, so nothing else on the machine can answer for you.
"""
from __future__ import annotations

import asyncio
import json
import logging
import secrets
from dataclasses import dataclass, field
from pathlib import Path

from .locator import leaf_name
from .state import State

log = logging.getLogger(__name__)

DECISION_TIMEOUT = 300.0        # seconds to wait for a tap before refusing
MAX_BODY = 1_000_000


def rule_key(tool_name: str, tool_input: dict) -> tuple[str, str]:
    """A standing-permission key, and how to describe it to a human.

    Shell commands are keyed by their program - 'git', 'npm', 'rm' - so saying
    yes to one `git status` says yes to git, not to every command ever.
    """
    if tool_name in ("Bash", "PowerShell"):
        command = str(tool_input.get("command", "")).strip()
        words = command.split()
        if words:
            program = leaf_name(words[0]) or words[0]
            # npm, npm.cmd and npm.exe are the same thing to a human.
            if program.lower().endswith((".exe", ".cmd", ".bat", ".ps1", ".com")):
                program = program.rsplit(".", 1)[0]
            return f"{tool_name}:{program}", f"{tool_name} · {program}"
    return tool_name, tool_name


@dataclass
class Pending:
    request_id: str
    session_id: str
    tool_name: str
    tool_input: dict
    future: asyncio.Future = field(repr=False)

    @property
    def rule(self) -> tuple[str, str]:
        return rule_key(self.tool_name, self.tool_input)


class ApprovalService:
    def __init__(self, state: State | None = None,
                 timeout: float = DECISION_TIMEOUT):
        self.state = state
        self.token = secrets.token_urlsafe(24)
        self.timeout = timeout
        self.port = 0
        self.pending: dict[str, Pending] = {}
        self.always_allow: set[str] = set()
        self._server: asyncio.Server | None = None
        self._ask_hook = None       # set by whoever can post to Telegram

    def set_notifier(self, callback) -> None:
        """callback(pending) -> awaitable; posts the buttons."""
        self._ask_hook = callback

    # -- server ----------------------------------------------------------
    async def start(self) -> None:
        self._server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        self.port = self._server.sockets[0].getsockname()[1]
        log.info("approval service listening on 127.0.0.1:%d", self.port)

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
        for pending in list(self.pending.values()):
            if not pending.future.done():
                pending.future.set_result({"behavior": "deny",
                                           "message": "the bridge shut down"})
        self.pending.clear()

    async def _handle(self, reader: asyncio.StreamReader,
                      writer: asyncio.StreamWriter) -> None:
        try:
            payload = await self._read_request(reader)
            if payload is None:
                await self._respond(writer, 400, {"error": "bad request"})
                return
            decision = await self.ask(
                session_id=str(payload.get("session_id", "")),
                tool_name=str(payload.get("tool_name", "a tool")),
                tool_input=payload.get("tool_input") or {},
            )
            await self._respond(writer, 200, decision)
        except Exception:
            log.exception("approval request failed")
            try:
                await self._respond(writer, 500, {"behavior": "deny",
                                                  "message": "bridge error"})
            except OSError:
                pass
        finally:
            writer.close()

    async def _read_request(self, reader: asyncio.StreamReader) -> dict | None:
        header_blob = await reader.readuntil(b"\r\n\r\n")
        lines = header_blob.decode("latin-1").split("\r\n")
        headers = {}
        for line in lines[1:]:
            if ":" in line:
                key, _, value = line.partition(":")
                headers[key.strip().lower()] = value.strip()

        if headers.get("x-bridge-token") != self.token:
            log.warning("approval request with a bad token - ignored")
            return None

        length = int(headers.get("content-length", "0"))
        if length <= 0 or length > MAX_BODY:
            return None
        body = await reader.readexactly(length)
        try:
            return json.loads(body.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return None

    async def _respond(self, writer: asyncio.StreamWriter, status: int,
                       payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        head = (
            f"HTTP/1.1 {status} OK\r\n"
            f"Content-Type: application/json\r\n"
            f"Content-Length: {len(body)}\r\n"
            f"Connection: close\r\n\r\n"
        ).encode("latin-1")
        writer.write(head + body)
        await writer.drain()

    # -- the question ----------------------------------------------------
    async def ask(self, session_id: str, tool_name: str, tool_input: dict) -> dict:
        if session_id in self.always_allow:
            return {"behavior": "allow"}

        key, label = rule_key(tool_name, tool_input)
        if self.state is not None and self.state.allow_rule_exists(key):
            log.info("auto-allowed %s (standing rule)", label)
            return {"behavior": "allow"}

        if self._ask_hook is None:
            return {"behavior": "deny",
                    "message": "no way to reach you on Telegram right now"}

        request_id = secrets.token_hex(4)
        loop = asyncio.get_running_loop()
        pending = Pending(request_id, session_id, tool_name, tool_input,
                          loop.create_future())
        self.pending[request_id] = pending

        try:
            await self._ask_hook(pending)
        except Exception:
            log.exception("could not post the approval buttons")
            self.pending.pop(request_id, None)
            return {"behavior": "deny", "message": "could not ask you"}

        try:
            return await asyncio.wait_for(pending.future, timeout=self.timeout)
        except asyncio.TimeoutError:
            minutes = int(self.timeout // 60) or 1
            return {"behavior": "deny",
                    "message": f"no answer within {minutes} minutes"}
        finally:
            self.pending.pop(request_id, None)

    def resolve(self, request_id: str, allow: bool, always: bool = False,
                remember: bool = False) -> bool:
        """Called when a button is tapped. False means the request is gone."""
        pending = self.pending.get(request_id)
        if pending is None or pending.future.done():
            return False
        if always and allow:
            self.always_allow.add(pending.session_id)
        if remember and allow and self.state is not None:
            key, label = pending.rule
            self.state.add_allow_rule(key, label)
            log.info("remembered standing permission for %s", label)
        pending.future.set_result(
            {"behavior": "allow"} if allow
            else {"behavior": "deny", "message": "you said no"}
        )
        return True

    def forget_session(self, session_id: str) -> None:
        """A finished turn stops any blanket approval carrying into the next."""
        self.always_allow.discard(session_id)
