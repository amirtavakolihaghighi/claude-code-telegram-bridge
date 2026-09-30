# Contributing

Thanks for looking. This is a small personal project, so the bar is "does it
work and is it clear", not ceremony.

## Setting up

```bat
git clone https://github.com/amirtavakolihaghighi/claude-code-telegram-bridge.git
cd claude-code-telegram-bridge
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt -r requirements-dev.txt
.venv\Scripts\python -m pytest
```

Python 3.11 or newer. You do not need a Telegram bot, a token, or Claude Code
installed to run the tests.

## The rule that matters most

**Tests must never touch real data.**

Claude Code conversations live in `~/.claude/projects/`. They are the user's
actual work. A test that writes there — or worse, deletes there — destroys
something irreplaceable.

Every test builds its own fake `claude_home` inside pytest's `tmp_path` and
points the code at it. There is a `fake_claude_home` fixture in
`tests/conftest.py`; use it rather than inventing your own. If you add a test
that needs a chat file, use the `chat_file` helper, which writes well-formed
records into that temporary tree.

The same applies to `state.db`: always construct `State(tmp_path / "state.db")`,
never the real one.

## Running things

| Command | What it does |
| --- | --- |
| `python -m pytest` | The whole suite |
| `python -m pytest -q` | Quietly |
| `python -m pytest tests/test_parser.py -k tool` | One area |
| `python -m bridge --dry-run --once` | Exercise the real pipeline, print instead of posting |

`--dry-run` reads your real chat files but sends nothing anywhere. It is the
quickest way to see the effect of a formatting change.

## Conventions

- **Comments explain why, not what.** If a line needs saying twice, rewrite the
  line.
- **Name tests as sentences** describing the behaviour, so a failure reads as a
  statement about what broke: `test_resuming_a_chat_keeps_the_same_id`.
- **Test the refusal paths**, not only the happy ones — malformed records, a
  half-written line, a missing file, a chat that vanished mid-run.
- Line length 88. Four spaces. UTF-8. `.editorconfig` has the rest.
- Few dependencies, and justify any new one in the pull request.

## Things that are easy to get wrong

**The chat file is appended to while you read it.** Never parse past the last
newline; the final line may be half-written. `SessionReader` handles this — if
you touch it, keep that property and test it.

**Telegram rate limits are strict.** Roughly 20 messages a minute into one
group, and creating topics is tighter still. Anything that sends in a loop must
go through the queue in `sinks.py`, which merges adjacent messages and backs off
on `RetryAfter`.

**One bot token, one running copy.** Two instances polling with the same token
produce `Conflict` errors and neither works. Stop the background service before
running a second copy by hand.

**Windows paths have spaces and drive letters.** Compare paths with
`locator.normalise`, never with string equality.

## Pull requests

Say what problem you hit and how you know it is fixed. If it changes behaviour
people rely on, say so plainly — that decides whether the next release is a
patch or a minor version.

CI runs the suite on Windows and Linux across the supported Python versions. It
must be green.
