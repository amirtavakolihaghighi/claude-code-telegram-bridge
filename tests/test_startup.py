"""Starting up, and staying up, without anyone watching."""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from telegram.error import InvalidToken, NetworkError, TimedOut

from bridge import __main__ as entry


@pytest.fixture(autouse=True)
def no_waiting(monkeypatch):
    async def instantly(_seconds):
        return None
    monkeypatch.setattr(entry.asyncio, "sleep", instantly)


class FlakyApp:
    """An application whose first few attempts to reach Telegram fail."""

    def __init__(self, failures: list[Exception]):
        self.failures = list(failures)
        self.attempts = 0

    async def initialize(self):
        self.attempts += 1
        if self.failures:
            raise self.failures.pop(0)


# -- reaching Telegram -------------------------------------------------------
async def test_it_waits_for_telegram_instead_of_giving_up(caplog):
    """At sign-in the network is often not up yet. This used to end the
    bridge before it had logged anything."""
    app = FlakyApp([NetworkError("no route"), TimedOut(), NetworkError("dns")])

    await entry._connect(app)

    assert app.attempts == 4
    assert "cannot reach Telegram yet" in caplog.text


async def test_a_rejected_token_is_not_tried_again():
    app = FlakyApp([InvalidToken()])

    with pytest.raises(InvalidToken):
        await entry._connect(app)
    assert app.attempts == 1


# -- staying up --------------------------------------------------------------
def args(**overrides):
    return SimpleNamespace(**{"once": False, **overrides})


async def test_an_unexpected_error_is_logged_and_it_starts_again(
        monkeypatch, caplog):
    runs = []

    async def run_live(_args):
        runs.append(1)
        if len(runs) == 1:
            raise RuntimeError("out of the blue")
        return 0

    monkeypatch.setattr(entry, "run_live", run_live)

    assert await entry._supervise(args()) == 0
    assert len(runs) == 2
    assert "out of the blue" in caplog.text


async def test_a_one_off_run_reports_the_error_instead_of_starting_again(
        monkeypatch):
    async def run_live(_args):
        raise RuntimeError("out of the blue")

    monkeypatch.setattr(entry, "run_live", run_live)

    assert await entry._supervise(args(once=True)) == 1


async def test_a_bad_setting_is_logged_and_ends_the_run(monkeypatch, caplog):
    """Starting again cannot fix a missing setting."""
    async def run_live(_args):
        raise SystemExit("TELEGRAM_GROUP_ID is not set yet.")

    monkeypatch.setattr(entry, "run_live", run_live)

    with pytest.raises(SystemExit):
        await entry._supervise(args())
    assert "TELEGRAM_GROUP_ID is not set yet." in caplog.text


async def test_a_rejected_token_ends_the_run_with_a_reason(monkeypatch, caplog):
    async def run_live(_args):
        raise InvalidToken()

    monkeypatch.setattr(entry, "run_live", run_live)

    assert await entry._supervise(args()) == 1
    assert "TELEGRAM_BOT_TOKEN" in caplog.text


# -- two computers sharing one bot -------------------------------------------
class FakeApp:
    def __init__(self):
        self.tasks = []
        self.bot = SimpleNamespace(send_message=self._send)
        self.sent = []

    async def _send(self, **kwargs):
        self.sent.append(kwargs)

    def create_task(self, coroutine):
        self.tasks.append(coroutine)


async def test_a_second_copy_fetching_messages_is_reported_once_not_endlessly(
        caplog):
    from telegram.error import Conflict
    app = FakeApp()
    report = entry.polling_trouble(app, -1001)

    for _ in range(5):
        report(Conflict("terminated by other getUpdates request"))
    for task in app.tasks:
        await task

    assert caplog.text.count("another copy of this bot") == 1
    assert len(app.sent) == 1
    assert "Two copies of this bot" in app.sent[0]["text"]


async def test_a_lost_connection_is_logged_in_one_line_now_and_then(caplog):
    report = entry.polling_trouble(FakeApp(), -1001)

    for _ in range(50):
        report(NetworkError("httpx.ConnectError: All connection attempts failed"))

    assert caplog.text.count("cannot fetch messages from Telegram") == 1
