r"""Find the .jsonl chat files, and which project each one belongs to.

Claude Code stores chats at:
    <claude_home>/projects/<slug>/<chat-id>.jsonl

The slug is the project's absolute path with every non-alphanumeric character
replaced by '-'  (so c:\Code\My_App -> c--Code-My-App: the colon, the separators
and the underscore all become dashes).

That mapping is lossy: two different paths can collide, and Claude appends a
numeric suffix when they do. So we never rely on the folder name - we read the
`cwd` field recorded inside the files themselves. A folder's project never
changes, so the answer is cached.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

_CWD_CACHE: dict[Path, str | None] = {}

# How far into each chat file to look for the project root. The first record of
# a session carries it, so this only needs to be generous, not exhaustive.
SCAN_LINES = 60


def slugify(path: Path | str) -> str:
    """Path -> the folder name Claude Code uses for it."""
    return "".join(c if c.isalnum() else "-" for c in str(path))


def normalise(path: Path | str) -> str:
    return str(path).replace("/", "\\").rstrip("\\").casefold()


def leaf_name(path: Path | str) -> str:
    r"""The last part of a path, whichever separator it uses.

    Paths reach us as strings recorded by whichever machine Claude Code ran on,
    so they may be Windows-style even when this is not a Windows machine.
    `Path(r"C:\Code\app").name` is the whole string on Linux, which would put a
    full path into every topic name. Splitting on both separators is correct
    everywhere.
    """
    text = str(path).replace("\\", "/").rstrip("/")
    return text.rsplit("/", 1)[-1] or text


def _cwd_of(session_dir: Path) -> str | None:
    r"""Work out which project folder a session directory belongs to.

    Records carry a `cwd`, but it is wherever Claude happened to be working at
    that moment, not the project root - a single project can show twenty of
    them as Claude moves through subfolders.

    The folder's own name is the reliable answer, because Claude Code derives it
    from the project root with `slugify`. So we look for the recorded path whose
    slug matches the folder name, which identifies the root exactly. Only if
    none matches - after a project has been moved and its folder renamed, say -
    do we fall back to the most recently recorded path.
    """
    if session_dir in _CWD_CACHE:
        cached = _CWD_CACHE[session_dir]
        if cached is not None:
            return cached

    wanted = session_dir.name.casefold()
    fallback: str | None = None

    for jsonl in sorted(session_dir.glob("*.jsonl")):
        try:
            with jsonl.open("r", encoding="utf-8", errors="replace") as fh:
                for _ in range(SCAN_LINES):
                    line = fh.readline()
                    if not line:
                        break
                    if '"cwd"' not in line:
                        continue
                    try:
                        cwd = json.loads(line).get("cwd")
                    except json.JSONDecodeError:
                        continue
                    if not cwd:
                        continue
                    if slugify(cwd).casefold() == wanted:
                        _CWD_CACHE[session_dir] = str(cwd)
                        return str(cwd)
                    if fallback is None:
                        fallback = str(cwd)
        except OSError:
            continue

    _CWD_CACHE[session_dir] = fallback
    return fallback


def known_projects(claude_home: Path) -> dict[str, Path]:
    """Every project Claude Code has chats for: normalised path -> real path."""
    root = claude_home / "projects"
    if not root.is_dir():
        return {}
    projects: dict[str, Path] = {}
    for folder in sorted(p for p in root.iterdir() if p.is_dir()):
        cwd = _cwd_of(folder)
        if cwd:
            projects.setdefault(normalise(cwd), Path(cwd))
    return projects


def find_session_dirs(claude_home: Path, project_dir: Path) -> list[Path]:
    """Every session folder that belongs to `project_dir`."""
    root = claude_home / "projects"
    if not root.is_dir():
        return []

    target = normalise(project_dir)
    matches: list[Path] = []

    for candidate in sorted(p for p in root.iterdir() if p.is_dir()):
        cwd = _cwd_of(candidate)
        if cwd is not None:
            if normalise(cwd) == target:
                matches.append(candidate)
            continue
        # Empty folder with no records to read - fall back to the name.
        if candidate.name.casefold().startswith(slugify(project_dir).casefold()):
            matches.append(candidate)

    return matches


@dataclass(frozen=True)
class SessionFile:
    path: Path
    session_id: str
    project: Path

    @property
    def mtime(self) -> float:
        try:
            return self.path.stat().st_mtime
        except OSError:
            return 0.0


def find_session_files(claude_home: Path,
                       projects: tuple[Path, ...] | list[Path] | None
                       ) -> list[SessionFile]:
    """Chat files for the given projects, oldest-touched first.

    An empty or None `projects` means every project Claude Code knows about.
    """
    root = claude_home / "projects"
    if not root.is_dir():
        return []

    wanted = {normalise(p) for p in projects} if projects else None
    found: list[SessionFile] = []

    for folder in sorted(p for p in root.iterdir() if p.is_dir()):
        cwd = _cwd_of(folder)
        if cwd is None:
            continue
        key = normalise(cwd)
        if wanted is not None and key not in wanted:
            continue
        project = Path(cwd)
        for jsonl in folder.glob("*.jsonl"):
            found.append(SessionFile(path=jsonl, session_id=jsonl.stem,
                                     project=project))

    found.sort(key=lambda s: s.mtime)
    return found
