"""Settings, loaded from .env at the project root."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent

PERMISSION_MODES = ("acceptEdits", "auto", "bypassPermissions", "manual",
                    "dontAsk", "plan")

# Which files get an offer button when Claude writes one. Documents, not code:
# a README or a handover note is worth reading on a phone, a .py file is not.
DEFAULT_ATTACH = ("md,markdown,txt,rst,adoc,org,html,htm,pdf,csv,"
                  "png,jpg,jpeg,gif,webp,svg")

# Names that should never be sent to Telegram without you saying so explicitly.
SECRET_NAMES = {".env", ".npmrc", ".netrc", ".pypirc", "credentials",
                "credentials.json", "id_rsa", "id_ed25519", "secrets.json"}
SECRET_SUFFIXES = {".pem", ".key", ".p12", ".pfx", ".keystore", ".jks", ".ppk"}


def looks_secret(path: Path | str) -> bool:
    """A conservative guess at files that carry credentials."""
    name = Path(path).name.lower()
    if name in SECRET_NAMES or name.startswith(".env"):
        return True
    return Path(name).suffix in SECRET_SUFFIXES


@dataclass(frozen=True)
class Config:
    bot_token: str
    group_id: int | None
    owner_id: int | None
    watch_projects: tuple[Path, ...]    # empty means "every project"
    claude_home: Path
    poll_interval: float
    state_db: Path
    claude_cli: Path | None
    permission_mode: str
    replies_enabled: bool
    approval_timeout: float
    machine_label: str
    archive_format: str
    attach_suffixes: frozenset[str]
    max_attach_mb: float

    @property
    def watches_everything(self) -> bool:
        return not self.watch_projects

    @property
    def default_project(self) -> Path | None:
        """Where a new chat goes when nothing else says otherwise."""
        return self.watch_projects[0] if self.watch_projects else None


def _optional_int(name: str) -> int | None:
    raw = os.getenv(name, "").strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError as exc:
        raise SystemExit(f"{name} must be a number, got {raw!r}") from exc


def _flag(name: str, default: bool) -> bool:
    raw = os.getenv(name, "").strip().lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "on")


def _parse_projects() -> tuple[Path, ...]:
    """WATCH_PROJECTS: 'ALL', or folders separated by semicolons.

    WATCH_PROJECT_DIR is the older single-folder name and still works.
    """
    raw = os.getenv("WATCH_PROJECTS", "").strip()
    if not raw:
        raw = os.getenv("WATCH_PROJECT_DIR", "").strip()
    if not raw:
        return (Path(ROOT),)
    if raw.upper() == "ALL":
        return ()
    return tuple(Path(part.strip()) for part in raw.split(";") if part.strip())


def find_claude_cli() -> Path | None:
    """The claude.exe bundled with the VS Code extension, newest version first.

    Using the extension's own binary means no separate install and the same
    logged-in account.
    """
    roots = [
        Path.home() / ".vscode" / "extensions",
        Path.home() / ".vscode-insiders" / "extensions",
    ]
    found: list[Path] = []
    for root in roots:
        if not root.is_dir():
            continue
        for ext in root.glob("anthropic.claude-code-*"):
            exe = ext / "resources" / "native-binary" / "claude.exe"
            if not exe.exists():
                exe = ext / "resources" / "native-binary" / "claude"
            if exe.exists():
                found.append(exe)
    if not found:
        return None
    # Folder names end with the version, so the last one sorted is the newest.
    return sorted(found, key=lambda p: p.parent.parent.parent.name)[-1]


def load_config(require_token: bool = True) -> Config:
    load_dotenv(ROOT / ".env")

    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if require_token and not token:
        raise SystemExit(
            "TELEGRAM_BOT_TOKEN is missing. Copy .env.example to .env and fill it in."
        )

    watch_projects = _parse_projects()
    claude_home_raw = os.getenv("CLAUDE_HOME", "").strip()
    claude_home = Path(claude_home_raw) if claude_home_raw else Path.home() / ".claude"

    cli_raw = os.getenv("CLAUDE_CLI", "").strip()
    claude_cli = Path(cli_raw) if cli_raw else find_claude_cli()

    mode = os.getenv("CLAUDE_PERMISSION_MODE", "").strip() or "auto"
    if mode not in PERMISSION_MODES:
        raise SystemExit(
            f"CLAUDE_PERMISSION_MODE must be one of {', '.join(PERMISSION_MODES)}"
        )

    try:
        poll = float(os.getenv("POLL_INTERVAL", "2.0"))
    except ValueError:
        poll = 2.0

    try:
        approval_timeout = float(os.getenv("APPROVAL_TIMEOUT", "300"))
    except ValueError:
        approval_timeout = 300.0

    archive_format = (os.getenv("ARCHIVE_FORMAT", "").strip().lower() or "both")
    if archive_format not in ("md", "html", "both"):
        raise SystemExit("ARCHIVE_FORMAT must be md, html or both")

    raw_suffixes = os.getenv("ATTACH_SUFFIXES", "").strip() or DEFAULT_ATTACH
    attach_suffixes = frozenset(
        "." + part.strip().lstrip(".").lower()
        for part in raw_suffixes.split(",") if part.strip())

    try:
        max_attach_mb = float(os.getenv("MAX_ATTACH_MB", "20"))
    except ValueError:
        max_attach_mb = 20.0

    return Config(
        bot_token=token,
        group_id=_optional_int("TELEGRAM_GROUP_ID"),
        owner_id=_optional_int("TELEGRAM_OWNER_ID"),
        watch_projects=watch_projects,
        claude_home=claude_home,
        poll_interval=poll,
        state_db=ROOT / "state.db",
        claude_cli=claude_cli,
        permission_mode=mode,
        replies_enabled=_flag("REPLIES_ENABLED", True),
        approval_timeout=approval_timeout,
        machine_label=os.getenv("MACHINE_LABEL", "").strip(),
        archive_format=archive_format,
        attach_suffixes=attach_suffixes,
        max_attach_mb=max_attach_mb,
    )
