# Changelog

All notable changes to this project are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Each project gets a consistent topic colour and icon, so a long topic list can
  be scanned at a glance. --restyle-topics applies icons to topics that
  already exist.
- The bot registers its commands with Telegram, so typing / offers them with
  descriptions.

### Changed

- While Claude is working the topic shows typing..., replacing the
  working... message that used to be posted and left behind.

## [0.1.0] - 2026-09-30

First public release. Everything below is new, so this lists what the release
does rather than what changed.

### Added

**Mirroring**

- Watches Claude Code chat files and posts new messages to a Telegram supergroup,
  one topic per chat, named `ProjectFolder: chat title`.
- Tool calls arrive as a single message showing the input and the output, with
  long output in a collapsible block, and a distinct icon per tool.
- Messages are queued and adjacent ones merged, to stay inside Telegram's limit
  of roughly 20 messages a minute per group.
- Only Claude's replies notify; your own messages and tool activity are silent.
- Read positions are stored in `state.db`, so restarting never re-posts.
- `--import-all` gives every existing chat a topic immediately.
- `--backfill` re-reads every chat from the beginning and posts its history.

**Replying from Telegram**

- Typing in a topic continues that chat by running `claude --resume` in the right
  project folder. The reply mirrors itself, because the CLI writes to the same
  file the mirror is watching.
- Commands: `/new`, `/c`, `/stop`, `/projects`, `/rules`, `/forget`, `/vscode`,
  `/status`, `/help`.
- Every message is checked against `TELEGRAM_OWNER_ID`; everyone else is ignored.
- Warns when the project is open in VS Code, since a chat can only be written to
  from one place at a time.
- Prompts typed in Telegram are not echoed back.

**Permissions**

- Permission requests become Allow / Deny buttons in Telegram, answered by a
  `PermissionRequest` hook passed to bridge-started runs only.
- "Allow everything in this turn" lasts until that reply finishes.
- "Always allow" is remembered per program, so yes to `git` is not yes to `rm`.
- Unanswered requests are refused after `APPROVAL_TIMEOUT` (default 5 minutes)
  rather than hanging.

**Running it**

- Recorded paths are read the same way whatever machine the bridge runs on, so
  a Windows path recorded by Claude Code still shortens correctly elsewhere.
- `bridge.bat` menu for start, stop, install, logs.
- `tools.autostart` registers a Windows Scheduled Task that starts at sign-in
  with no window, restarts on failure, and keeps running on battery.
- `MACHINE_LABEL` so two computers can feed one Telegram group.
- Rotating log file via `--log-file`.

### Known limitations

- Windows only. The core is portable; the autostart helper is not.
- The bridge knows a *project* is open in VS Code, but not which *chat*.
- Subagent output and Claude's thinking are not mirrored.
- Tool input is truncated at 600 characters, output at 900.

[Unreleased]: https://github.com/amirtavakolihaghighi/claude-code-telegram-bridge/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/amirtavakolihaghighi/claude-code-telegram-bridge/releases/tag/v0.1.0
