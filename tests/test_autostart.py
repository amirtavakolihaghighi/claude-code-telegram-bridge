"""The helper behind bridge.bat. Task Scheduler, the lock and state.db are all
stood in for: these tests must not touch the real ones."""
from __future__ import annotations

import time

import pytest

from bridge import instance
from bridge.state import State
from tools import autostart


@pytest.fixture
def schtasks(monkeypatch):
    """Records which schtasks commands were run; installed or not, as asked."""
    calls: list[str] = []

    def setup(installed: bool) -> list[str]:
        def run(args):
            calls.append(args[1])
            if args[1] == "/Query":
                return (0 if installed else 1), ""
            return 0, ""
        monkeypatch.setattr(autostart, "_run", run)
        return calls
    return setup


def running(monkeypatch, up: bool, pid: int | None = None) -> None:
    monkeypatch.setattr(instance, "running", lambda: (up, pid))


# -- start and stop ----------------------------------------------------------
def test_start_installs_the_task_when_there_is_none(schtasks, monkeypatch):
    """It used to fail with 'cannot find the file specified' after an
    uninstall."""
    calls = schtasks(installed=False)
    running(monkeypatch, False)
    installs = []
    monkeypatch.setattr(autostart, "install", lambda: installs.append(1) or 0)

    assert autostart.start() == 0
    assert installs == [1]
    assert "/Run" not in calls


def test_start_runs_the_task_that_is_installed(schtasks, monkeypatch):
    calls = schtasks(installed=True)
    running(monkeypatch, False)

    assert autostart.start() == 0
    assert "/Run" in calls


def test_start_leaves_a_copy_that_is_already_running_alone(
        schtasks, monkeypatch, capsys):
    calls = schtasks(installed=True)
    running(monkeypatch, True, 4321)

    assert autostart.start() == 0
    assert "/Run" not in calls
    assert "4321" in capsys.readouterr().out


def test_stop_says_so_when_a_copy_in_another_window_keeps_running(
        schtasks, monkeypatch, capsys):
    schtasks(installed=True)
    running(monkeypatch, True, 4321)
    monkeypatch.setattr(autostart.time, "sleep", lambda _seconds: None)

    assert autostart.stop() == 1
    assert "Ctrl+C" in capsys.readouterr().out


# -- the line at the top of the menu -----------------------------------------
def brief_for(monkeypatch, tmp_path, capsys, alive_ago: float) -> str:
    state = State(tmp_path / "state.db")
    state.set_last_alive(time.time() - alive_ago)
    monkeypatch.setattr(autostart, "_state", lambda: (None, state))
    autostart.brief()
    return capsys.readouterr().out


def test_the_menu_says_when_all_is_well(schtasks, monkeypatch, tmp_path, capsys):
    schtasks(installed=True)
    running(monkeypatch, True, 1)

    assert "up to date" in brief_for(monkeypatch, tmp_path, capsys, 5)


def test_the_menu_notices_a_copy_that_runs_but_does_nothing(
        schtasks, monkeypatch, tmp_path, capsys):
    """Task Scheduler calls that 'Running'. It is not, in any useful sense."""
    schtasks(installed=True)
    running(monkeypatch, True, 1)

    line = brief_for(monkeypatch, tmp_path, capsys, 3 * 3600)

    assert "not working since 3 hours ago" in line


def test_the_menu_says_how_to_start_one_that_is_not_running(
        schtasks, monkeypatch, tmp_path, capsys):
    schtasks(installed=False)
    running(monkeypatch, False)

    assert "choose 5" in brief_for(monkeypatch, tmp_path, capsys, 5)


def test_amounts_are_given_in_units_that_read_well():
    assert autostart._amount(400) == "1 KB"
    assert autostart._amount(12_345) == "12 KB"
    assert autostart._amount(4_100_000) == "4.1 MB"


def test_times_are_said_the_way_a_person_would():
    assert autostart._ago(5) == "5 seconds ago"
    assert autostart._ago(600) == "10 minutes ago"
    assert autostart._ago(7200) == "2 hours ago"
    assert autostart._ago(3 * 86400) == "3 days ago"
