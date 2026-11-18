"""Finding claude.exe, and finding it again after VS Code updates the extension.

The extension keeps claude.exe in a folder named after its version and deletes
that folder once it has updated. A bridge started before an update used to keep
the old path, so every reply from Telegram failed with "could not start claude:
[WinError 2]" until it was restarted.
"""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from bridge import config as config_module
from bridge import runner as runner_module
from bridge.config import Config
from bridge.runner import Runner


def _extension(root: Path, version: str) -> Path:
    """A fake extension folder holding claude.exe, as VS Code lays it out."""
    exe = (root / f"anthropic.claude-code-{version}-win32-x64"
           / "resources" / "native-binary" / "claude.exe")
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"")
    return exe


def _remove(exe: Path) -> None:
    """What VS Code does to the old version after an update."""
    shutil.rmtree(exe.parent.parent.parent)


def _config(tmp_path: Path, claude_cli: Path | None,
            pinned: bool = False) -> Config:
    return Config(
        bot_token="t", group_id=-100, owner_id=1, watch_projects=(),
        claude_home=tmp_path, poll_interval=1, state_db=tmp_path / "s.db",
        claude_cli=claude_cli, permission_mode="auto", replies_enabled=True,
        approval_timeout=5, machine_label="", archive_format="both",
        claude_cli_pinned=pinned,
    )


@pytest.fixture
def extensions(tmp_path, monkeypatch):
    """An extensions folder of our own, so the real one is never looked at."""
    root = tmp_path / "extensions"
    root.mkdir()
    monkeypatch.setattr(runner_module, "find_claude_cli",
                        lambda: config_module.find_claude_cli([root]))
    return root


# -- which version counts as newest ------------------------------------------
def test_versions_are_compared_as_numbers_not_as_text(tmp_path):
    _extension(tmp_path, "2.1.99")
    newest = _extension(tmp_path, "2.1.100")
    assert config_module.find_claude_cli([tmp_path]) == newest


def test_a_newer_minor_version_beats_a_bigger_patch_number(tmp_path):
    _extension(tmp_path, "2.9.500")
    newest = _extension(tmp_path, "2.10.0")
    assert config_module.find_claude_cli([tmp_path]) == newest


def test_no_extension_means_nothing_is_found(tmp_path):
    assert config_module.find_claude_cli([tmp_path]) is None


# -- running across an extension update ----------------------------------------
def test_the_path_found_at_startup_is_used_while_it_exists(tmp_path, extensions):
    current = _extension(extensions, "2.1.325")
    runner = Runner(_config(tmp_path, current))
    args, _ = runner._command("abc", "hello")
    assert args[0] == str(current)


def test_claude_exe_removed_by_an_update_is_looked_up_again(tmp_path, extensions):
    old = _extension(extensions, "2.1.325")
    runner = Runner(_config(tmp_path, old))

    new = _extension(extensions, "2.1.326")
    _remove(old)

    args, _ = runner._command("abc", "hello")
    assert args[0] == str(new)


def test_the_new_path_is_kept_for_the_messages_after(tmp_path, extensions,
                                                     monkeypatch):
    old = _extension(extensions, "2.1.325")
    runner = Runner(_config(tmp_path, old))
    new = _extension(extensions, "2.1.326")
    _remove(old)
    runner._command("abc", "first")

    searches = []
    monkeypatch.setattr(runner_module, "find_claude_cli",
                        lambda: searches.append(1))
    args, _ = runner._command("abc", "second")
    assert args[0] == str(new)
    assert searches == []


def test_one_installed_after_startup_is_found(tmp_path, extensions):
    runner = Runner(_config(tmp_path, None))
    installed = _extension(extensions, "2.1.326")
    args, _ = runner._command("abc", "hello")
    assert args[0] == str(installed)


def test_with_none_anywhere_the_reply_says_how_to_fix_it(tmp_path, extensions):
    old = _extension(extensions, "2.1.325")
    runner = Runner(_config(tmp_path, old))
    _remove(old)
    with pytest.raises(RuntimeError, match="CLAUDE_CLI"):
        runner._command("abc", "hello")


def test_a_path_set_in_env_is_never_swapped_for_another(tmp_path, extensions):
    yours = tmp_path / "somewhere" / "claude.exe"       # does not exist
    _extension(extensions, "2.1.326")
    runner = Runner(_config(tmp_path, yours, pinned=True))
    args, _ = runner._command("abc", "hello")
    assert args[0] == str(yours)


async def test_the_reply_after_an_update_launches_the_new_one(tmp_path, extensions,
                                                             monkeypatch):
    """Through run() itself: the stale path never reaches the launch."""
    old = _extension(extensions, "2.1.325")
    runner = Runner(_config(tmp_path, old))
    new = _extension(extensions, "2.1.326")
    _remove(old)

    launched = []

    async def fake_exec(*args, **kwargs):
        launched.append(args[0])
        raise OSError("stop here")

    monkeypatch.setattr(runner_module.asyncio, "create_subprocess_exec", fake_exec)
    result = await runner.run("abc", "hello", tmp_path)

    assert launched == [str(new)]
    assert "stop here" in result.error
