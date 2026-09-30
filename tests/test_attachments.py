"""Which written files get offered, and which must never be."""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from bridge.config import looks_secret
from bridge.events import Event
from bridge.format import render
from bridge.sinks import TelegramSink
from bridge.state import State
from tests.test_mirror import config  # noqa: F401  (fixture)


def tool_event(name: str, path: str, failed: bool = False) -> Event:
    return Event("tool", "s1", text=f"wrote {Path(path).name}", tool_name=name,
                 extra={"input": path, "output": "ok", "is_error": failed,
                        "file_path": path})


# -- what the message carries -------------------------------------------------
def test_a_written_file_is_remembered_on_the_message():
    message = render(tool_event("Write", r"C:\Code\app\README.md"))[0]
    assert message.attach == r"C:\Code\app\README.md"


def test_a_failed_write_offers_nothing():
    message = render(tool_event("Write", r"C:\Code\app\README.md", failed=True))[0]
    assert message.attach == ""


def test_a_tool_with_no_file_offers_nothing():
    event = Event("tool", "s1", text="ran tests", tool_name="Bash",
                  extra={"input": "pytest", "output": "ok", "is_error": False})
    assert render(event)[0].attach == ""


# -- which files are worth offering -------------------------------------------
@pytest.fixture
def sink(tmp_path, config):  # noqa: F811
    return TelegramSink(bot=None, group_id=-1, state=State(tmp_path / "s.db"),
                        config=config)


def write(tmp_path: Path, name: str, size: int = 10) -> str:
    path = tmp_path / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)
    return str(path)


def test_documents_are_offered(sink, tmp_path):
    assert sink.offerable(write(tmp_path, "README.md")) is True
    assert sink.offerable(write(tmp_path, "notes.txt")) is True


def test_code_is_not_offered(sink, tmp_path):
    """The point is things worth reading on a phone, not source files."""
    assert sink.offerable(write(tmp_path, "parser.py")) is False
    assert sink.offerable(write(tmp_path, "app.ts")) is False


def test_a_file_that_does_not_exist_is_not_offered(sink, tmp_path):
    assert sink.offerable(str(tmp_path / "never-written.md")) is False


def test_nothing_is_offered_without_a_path(sink):
    assert sink.offerable("") is False


def test_a_file_over_the_limit_is_not_offered(sink, tmp_path, config):  # noqa: F811
    big = write(tmp_path, "huge.md", size=2_000_000)
    sink.config = replace(config, max_attach_mb=1.0)
    assert sink.offerable(big) is False


def test_the_allowed_kinds_can_be_changed(sink, tmp_path, config):  # noqa: F811
    source = write(tmp_path, "parser.py")
    assert sink.offerable(source) is False
    sink.config = replace(config, attach_suffixes=frozenset({".py"}))
    assert sink.offerable(source) is True


# -- secrets ------------------------------------------------------------------
def test_credential_files_are_recognised():
    for name in (".env", ".env.local", "id_rsa", "server.pem", "cert.key",
                 "credentials.json", ".npmrc"):
        assert looks_secret(name) is True, name


def test_ordinary_files_are_not_mistaken_for_secrets():
    for name in ("README.md", "notes.txt", "environment.md", "keyboard.md"):
        assert looks_secret(name) is False, name


def test_a_secret_is_never_offered_even_if_its_kind_is_allowed(
        sink, tmp_path, config):  # noqa: F811
    secret = write(tmp_path, ".env")
    sink.config = replace(config, attach_suffixes=frozenset({".env", ""}))
    assert sink.offerable(secret) is False


# -- batching ------------------------------------------------------------------
async def test_a_message_with_a_button_is_posted_on_its_own(sink, tmp_path):
    """Otherwise the button would appear under somebody else's text."""
    from bridge.format import OutMessage
    offered = write(tmp_path, "README.md")
    await sink.send("s1", OutMessage("first", attach=offered))
    await sink.send("s1", OutMessage("second"))

    session, text, _, attach = sink._take_batch()
    assert text == "first"
    assert attach == offered
    assert len(sink._pending) == 1


async def test_ordinary_messages_still_merge(sink):
    from bridge.format import OutMessage
    await sink.send("s1", OutMessage("one"))
    await sink.send("s1", OutMessage("two"))

    _, text, _, attach = sink._take_batch()
    assert "one" in text and "two" in text
    assert attach == ""
