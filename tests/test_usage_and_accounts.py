"""Cost and token totals, and the cswap account wrapper."""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone

from bridge.accounts import Account, Accounts, Window, mask_email
from bridge.usage import collect, human_tokens, read_session
from tests.conftest import assistant_text, cost_state, title, user_text


def spend(input_tokens=0, output=0, cache_read=0, cache_write=0, when=None,
          uuid="a1") -> dict:
    record = assistant_text("thinking about it", uuid=uuid)
    record["message"]["usage"] = {
        "input_tokens": input_tokens, "output_tokens": output,
        "cache_read_input_tokens": cache_read,
        "cache_creation_input_tokens": cache_write,
    }
    if when:
        record["timestamp"] = when
    return record


# -- reading one chat ---------------------------------------------------------
def test_tokens_are_added_up(chat_file):
    path = chat_file(r"C:\Code\app", "s1", [
        spend(input_tokens=10, output=5, cache_read=100, cache_write=20),
        spend(input_tokens=1, output=2, uuid="a2"),
    ])
    usage = read_session(path, r"C:\Code\app")
    assert usage.input_tokens == 11
    assert usage.output_tokens == 7
    assert usage.cache_read == 100
    assert usage.cache_write == 20
    assert usage.total_tokens == 138
    assert usage.replies == 2


def test_cost_is_the_running_total_not_the_sum(chat_file):
    """cost-state records accumulate, so summing them would double-count."""
    path = chat_file(r"C:\Code\app", "s1",
                     [cost_state(0.50), cost_state(1.25), cost_state(2.00)])
    assert read_session(path, r"C:\Code\app").cost == 2.00


def test_subagent_usage_is_not_counted_twice(chat_file):
    record = spend(input_tokens=999)
    record["isSidechain"] = True
    path = chat_file(r"C:\Code\app", "s1", [record])
    assert read_session(path, r"C:\Code\app").input_tokens == 0


def test_the_title_is_picked_up(chat_file):
    path = chat_file(r"C:\Code\app", "s1", [title("Fix the bug"), spend(output=1)])
    assert read_session(path, r"C:\Code\app").title == "Fix the bug"


def test_tokens_are_attributed_to_the_day_they_happened(chat_file):
    path = chat_file(r"C:\Code\app", "s1", [
        spend(output=10, when="2026-09-20T10:00:00.000Z"),
        spend(output=5, when="2026-09-21T10:00:00.000Z", uuid="a2"),
    ])
    usage = read_session(path, r"C:\Code\app")
    assert usage.tokens_by_day[date(2026, 9, 20)] == 10
    assert usage.tokens_by_day[date(2026, 9, 21)] == 5
    assert usage.first_day == date(2026, 9, 20)
    assert usage.last_day == date(2026, 9, 21)


def test_a_chat_with_no_usage_records_is_harmless(chat_file):
    path = chat_file(r"C:\Code\app", "s1", [user_text("hello")])
    usage = read_session(path, r"C:\Code\app")
    assert usage.total_tokens == 0 and usage.cost == 0


def test_a_missing_file_does_not_raise(tmp_path):
    assert read_session(tmp_path / "gone.jsonl", "p").total_tokens == 0


def test_re_reading_an_unchanged_file_uses_the_cache(chat_file):
    path = chat_file(r"C:\Code\app", "s1", [spend(output=3)])
    first = read_session(path, r"C:\Code\app")
    assert read_session(path, r"C:\Code\app") is first


def test_a_changed_file_is_read_again(chat_file):
    path = chat_file(r"C:\Code\app", "s1", [spend(output=3)])
    read_session(path, r"C:\Code\app")
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(spend(output=7, uuid="a9")) + "\n")
    assert read_session(path, r"C:\Code\app").output_tokens == 10


# -- the report ---------------------------------------------------------------
def test_projects_are_totalled_separately(chat_file, claude_home):
    chat_file(r"C:\Code\alpha", "s1", [spend(output=10), cost_state(1.0)])
    chat_file(r"C:\Code\beta", "s2", [spend(output=30), cost_state(3.0)])
    report = collect(claude_home, None)

    rows = dict((name, cost) for name, cost, _ in report.by_project())
    assert rows == {"alpha": 1.0, "beta": 3.0}
    assert report.cost == 4.0


def test_the_cache_share_is_reported(chat_file, claude_home):
    chat_file(r"C:\Code\app", "s1", [spend(input_tokens=10, cache_read=90)])
    assert round(collect(claude_home, None).cache_share) == 90


def test_recent_days_are_reported_and_older_ones_left_out(chat_file, claude_home):
    today = datetime.now(timezone.utc).date()
    chat_file(r"C:\Code\app", "s1", [
        spend(output=5, when=f"{today}T10:00:00.000Z"),
        spend(output=7, when=f"{today - timedelta(days=30)}T10:00:00.000Z",
              uuid="a2"),
    ])
    daily = dict(collect(claude_home, None).tokens_by_day(7))
    assert daily == {today: 5}


def test_the_dearest_chats_come_first(chat_file, claude_home):
    chat_file(r"C:\Code\app", "s1", [title("cheap"), cost_state(1.0)])
    chat_file(r"C:\Code\app", "s2", [title("dear"), cost_state(9.0)])
    assert collect(claude_home, None).top_sessions(1)[0].title == "dear"


def test_token_counts_are_written_for_humans():
    assert human_tokens(999) == "999"
    assert human_tokens(12_400) == "12k"
    assert human_tokens(12_600) == "13k"
    assert human_tokens(3_400_000) == "3.4M"
    assert human_tokens(3_900_000_000) == "3.9B"


# -- accounts -----------------------------------------------------------------
def test_an_address_is_hidden_by_default():
    assert mask_email("amirtavakoli@yahoo.com") == "a***@yahoo.com"


def test_an_address_can_be_shown_or_withheld_entirely():
    assert mask_email("a@b.com", "full") == "a@b.com"
    assert mask_email("a@b.com", "none") == ""


def test_something_that_is_not_an_address_still_hides():
    assert mask_email("not-an-address") == "***"
    assert mask_email("") == ""


def test_a_window_is_read_from_cswaps_shape():
    window = Window.read({"pct": 51.0, "countdown": "4h 25m", "clock": "05:20",
                          "aheadOfPace": False})
    assert window.pct == 51.0 and window.countdown == "4h 25m"
    assert window.ahead_of_pace is False


def test_a_window_with_no_figure_is_absent():
    assert Window.read(None) is None
    assert Window.read({}) is None


def test_an_account_needing_a_login_says_so():
    account = Account(1, "a@b.com", False, "relogin_required", None, None)
    assert account.needs_login is True
    assert Account(2, "a@b.com", True, "ok", None, None).needs_login is False


async def test_with_no_cswap_the_wrapper_stays_quiet():
    accounts = Accounts(None)
    assert accounts.available is False
    assert await accounts.quota() is None
    assert await accounts.all() == []
    ok, message = await accounts.switch("2")
    assert ok is False and "not installed" in message
