"""Reading and switching Claude accounts through the cswap CLI.

cswap (https://github.com/realiti4/claude-swap) manages several Claude accounts
and reports how much of each rate-limit window has been used. It offers JSON
output, so this shells out to it the same way the bridge already shells out to
claude.exe rather than reimplementing any of it.

Everything here is optional: with no cswap installed, the account commands say
so and the rest of the bridge is unaffected.
"""
from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)
TIMEOUT = 30.0


def find_cswap(override: str = "") -> Path | None:
    """Locate the cswap executable, preferring an explicit setting."""
    if override:
        candidate = Path(override)
        return candidate if candidate.exists() else None

    names = ("cswap.exe", "cswap")
    roots = [
        Path.home() / ".local" / "bin",
        Path.home() / "AppData" / "Roaming" / "uv" / "tools",
        Path.home() / "AppData" / "Local" / "uv" / "tools",
        Path.home() / ".local" / "pipx" / "venvs",
        # A source checkout's own virtual environment, which is where it lives
        # when cswap has been cloned rather than installed as a tool.
        Path("D:/") / "Files" / "Projects" / "claude-swap" / ".venv" / "Scripts",
    ]
    for root in roots:
        if not root.exists():
            continue
        for name in names:
            direct = root / name
            if direct.exists():
                return direct
            try:
                found = next(root.rglob(name), None)
            except OSError:
                found = None
            if found:
                return found

    import shutil
    located = shutil.which("cswap")
    return Path(located) if located else None


def mask_email(email: str, mode: str = "mask") -> str:
    """Telegram keeps whatever it is shown, so addresses are hidden by default."""
    if not email:
        return ""
    if mode == "full":
        return email
    if mode == "none":
        return ""
    name, _, domain = email.partition("@")
    if not domain:
        return "***"
    return f"{name[:1]}***@{domain}"


@dataclass(frozen=True)
class Window:
    pct: float
    countdown: str = ""
    clock: str = ""
    ahead_of_pace: bool | None = None

    @classmethod
    def read(cls, blob: dict | None) -> "Window | None":
        if not isinstance(blob, dict) or blob.get("pct") is None:
            return None
        return cls(float(blob["pct"]), str(blob.get("countdown") or ""),
                   str(blob.get("clock") or ""), blob.get("aheadOfPace"))


@dataclass(frozen=True)
class Account:
    number: int
    email: str
    active: bool
    status: str
    five_hour: Window | None
    seven_day: Window | None
    stale: bool = False

    @property
    def needs_login(self) -> bool:
        return self.status == "relogin_required"


class Accounts:
    def __init__(self, cswap: Path | None):
        self.cswap = cswap

    @property
    def available(self) -> bool:
        return self.cswap is not None

    async def _json(self, *args: str) -> dict | None:
        """cswap prints JSON on stdout; its exit code is not reliable."""
        if self.cswap is None:
            return None
        try:
            proc = await asyncio.create_subprocess_exec(
                str(self.cswap), *args,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            out, err = await asyncio.wait_for(proc.communicate(), timeout=TIMEOUT)
        except (OSError, asyncio.TimeoutError) as exc:
            log.warning("cswap %s failed: %s", " ".join(args), exc)
            return None

        text = out.decode("utf-8", errors="replace").strip()
        start = text.find("{")
        if start == -1:
            log.warning("cswap %s gave no JSON: %s", " ".join(args),
                        (err.decode("utf-8", "replace") or text)[:200])
            return None
        try:
            return json.loads(text[start:])
        except json.JSONDecodeError:
            return None

    async def quota(self) -> Account | None:
        """The active account and how much of each window it has used."""
        blob = await self._json("status", "--json")
        active = (blob or {}).get("active")
        if not isinstance(active, dict):
            return None
        usage = active.get("usage") or {}
        age = active.get("usageAgeSeconds")
        return Account(
            number=int(active.get("number", 0)),
            email=str(active.get("email") or ""),
            active=True,
            status=str(active.get("usageStatus") or "unknown"),
            five_hour=Window.read(usage.get("fiveHour")),
            seven_day=Window.read(usage.get("sevenDay")),
            stale=bool(age is not None and age > 900),
        )

    async def all(self) -> list[Account]:
        blob = await self._json("list", "--json")
        rows = (blob or {}).get("accounts")
        if not isinstance(rows, list):
            return []

        accounts = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            # A stale account reports its last known figures instead.
            usage = row.get("usage") or row.get("lastGoodUsage") or {}
            accounts.append(Account(
                number=int(row.get("number", 0)),
                email=str(row.get("email") or ""),
                active=bool(row.get("active")),
                status=str(row.get("usageStatus") or "unknown"),
                five_hour=Window.read(usage.get("fiveHour")),
                seven_day=Window.read(usage.get("sevenDay")),
                stale=row.get("usage") is None,
            ))
        return sorted(accounts, key=lambda a: a.number)

    async def switch(self, target: str) -> tuple[bool, str]:
        """Switch to an account by slot number or email."""
        if self.cswap is None:
            return False, "cswap is not installed"
        try:
            proc = await asyncio.create_subprocess_exec(
                str(self.cswap), "switch", target,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            out, err = await asyncio.wait_for(proc.communicate(), timeout=TIMEOUT)
        except (OSError, asyncio.TimeoutError) as exc:
            return False, str(exc)

        message = (out.decode("utf-8", "replace").strip()
                   or err.decode("utf-8", "replace").strip())
        # Confirm against what cswap now reports rather than trusting a code.
        now = await self.quota()
        if now is not None and (str(now.number) == target
                                or target.lower() in now.email.lower()):
            return True, message
        return proc.returncode == 0, message or "no output"
