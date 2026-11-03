# Changelog

All notable changes to this project are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.6.0] - 2026-11-03

The bridge stops losing messages when it has been off, and stops being off
without anyone knowing.

### Added

- **Catching up**: option 10 in `bridge.bat`, or `--catch-up`, sends every
  message that never reached Telegram, oldest first. That covers chats followed
  "from now", whose earlier history was never sent, and chats never followed at
  all — every chat, on a machine just set up. It runs in the background, and
  can be stopped and resumed.
- A chat whose topic already holds its later messages, but is missing its
  beginning, is sent again whole. Telegram cannot put messages above ones
  already posted, so its topic is deleted just before the first message goes
  out again, and everything goes into a new one, in order. A topic you had
  archived is archived again once it is complete. Without the *Delete
  messages* right, the old topic is closed and labelled `(partial copy)`
  instead.
- `CHAT_BACKUPS` names folders holding copies of `~/.claude/projects`, with
  wildcards for a folder renamed at each backup. A chat Claude Code has
  deleted is read from its most complete copy, so it can still be caught up
  on, sent again in full, or archived.
- `/resend`, in a topic, deletes it and sends the whole chat again, in order,
  after asking. For a topic that does not match its chat.
- `/mute` stops a chat being sent to Telegram without skipping anything;
  `/unmute` sends everything written meanwhile, in order. A message typed in a
  muted topic is not run.
- `TOPIC_ICONS=default` gives every topic Telegram's own icon instead of its
  project's. Changing the setting restyles every existing topic at the next
  start.
- If two copies of the bot run at once — one bot shared by two computers, both
  switched on — each says so, in the log and in the group's General topic.
- A message typed in a topic that belongs to another computer says so, instead
  of suggesting `/new`.
- `state.db` records where mirroring of each chat began, so history that is
  skipped can always be sent later.
- The status, and the line at the top of the `bridge.bat` menu, come from the
  bridge itself: whether it is running, when it last did anything, what is
  still to send and what was never sent. A copy that runs but does nothing is
  reported as such, where Task Scheduler says "Running".
- Only one copy can run at a time. A second one refuses to start and names the
  process already running. "Run here" offers to stop the background copy.

### Fixed

- After a restart, the bridge often did not start at sign-in, and stayed off
  until someone noticed. If Telegram was unreachable as it started — usual at
  sign-in, before the network is up — it exited before logging a word,
  and Task Scheduler does not restart a program that started and then failed.
  It now waits for Telegram. After any unexpected error it logs it and starts
  again by itself.
- A chat begun while the bridge was off was taken for old history when it came
  back, and only followed from that moment. Its topic then started part-way
  through the conversation. The bridge now records when it was last running,
  and sends such chats from their first message.
- Messages were dropped after eight failed attempts, about four minutes, when
  Telegram could not be reached. They are now held until it can be.
- Messages queued but not yet posted were lost when the bridge stopped,
  restarted or shut down with the PC, because read positions were saved when a
  message was queued rather than when it was posted. Positions now wait for the
  post, so the rest goes out next time.
- One unexpected error while posting ended the sending loop for good, and
  everything after it was silently never sent.
- Start failed with "cannot find the file specified" after an uninstall. It now
  installs the task.
- `--restyle-topics` skipped any topic that hit Telegram's flood limit, instead
  of waiting and trying it again.
- A reply containing a wide table could come out longer than Telegram allows,
  and was refused, so it never appeared. Lining up the columns had padded every
  line to the widest cell, hundreds of characters long. Every message is now
  measured the way Telegram counts it, in UTF-16 units of what is shown, and
  split again if it is over; merged posts are counted the same way.
- A message containing U+2028 or U+2029, which JSON may carry unescaped, split
  its record in two and was dropped.
- A chat file that briefly vanished, or moved with its project, had its read
  position reset to the start. Had it reappeared, the whole chat would have
  been posted again.

Topics now hold what VS Code shows. Several things went missing or came out
wrong, even when nothing else did:

- A message typed while Claude was working, and taken into the turn already
  under way, never reached Telegram. It is not an ordinary user record.
- A tool that took more than two minutes was shown as "(no result recorded)",
  and its real result was then thrown away. Results are now waited for
  much longer, and one that still comes late is posted, not dropped.
- A tool call that never got a result was shown at the end of the chat, after
  everything that followed it, or not at all. It now appears in its place,
  just before the next message you typed.
- The summary Claude Code writes when it compacts a conversation — tens of
  thousands of characters — was posted as a message from you. It is now a
  one-line marker, as in VS Code.
- A pasted picture disappeared without a trace. It now shows as `[image]`.

### Changed

- A long backlog is read a slice at a time, only while the send queue has room,
  so a big catch-up never holds up a live reply for more than a minute or two.
  The chats used most recently get that room first.
- `--once` keeps going until every chat has been read to its end.
- A table too wide for a phone screen shows as a short list per row, instead
  of a monospace block that has to be scrolled sideways.
- A chat file that has become shorter than what was already read is left
  alone, rather than read again from the top: it is most likely an older copy
  from a backup, and starting over posted the whole chat twice.
- Trouble fetching messages is logged as one line every few minutes, instead
  of a full traceback on every retry, which buried everything else in the log.

## [0.5.0] - 2026-10-01

The bot stops being a one-thing-at-a-time bot, and stops talking about money
unprompted.

### Added

- `/cost` reports what this chat and today have cost, replacing the footer that
  used to post itself.

### Removed

- The "turn finished" cost footer. It appeared whenever a session's cost was
  written — every time Claude exited and every time a VS Code window closed — so
  a running total turned up in chats where nothing had happened. Ask with
  `/cost` or `/usage` instead.

### Fixed

- A console window no longer opens over whatever you are doing each time a
  message arrives. The spawned Claude is given no console on Windows.
- Buttons did nothing while Claude was working, which made a question Claude
  asked mid-turn impossible to answer. Telegram updates were handled strictly one
  at a time, and a message handler runs for as long as the whole turn - so a tap
  waited in the queue behind it, and Telegram abandoned the unanswered tap after
  a few seconds. That was the deadlock: the only way to answer arrived through
  the channel the question was blocking. Updates are now handled concurrently.
- A tap is logged on arrival, and a keyboard that could not be redrawn says why,
  so the next problem of this kind is visible in the log rather than guessed at.

## [0.4.1] - 2026-10-01

Making the new question buttons actually usable.

### Fixed

- Tapping an option showed no tick. The tap was recorded, but the keyboard was
  redrawn only after acknowledging the tap - and Telegram allows about fifteen
  seconds for that, which a slow connection misses, so the redraw never ran. The
  tick is now drawn first and the acknowledgement is best effort, with one retry
  past a network wobble.
- A tap with no message attached, which Telegram permits, raised instead of
  being handled.
- A question nobody answered was left looking live: taps did nothing visible and
  the selection seemed impossible to confirm. It is now struck through with its
  buttons removed when it expires, and tapping an expired one says so.
- The confirm button read "Pick at least one" until something was ticked, which
  hid that it was the way to send. It always reads as a Send button now, with a
  count.
- Each tap confirms what it added or removed, so something visibly happens even
  if editing the keyboard is slow.
- Claude waited fifteen minutes for an answer, leaving the topic showing
  "typing..." and saying nothing. Now five, configurable as QUESTION_TIMEOUT.
- The caller and the bridge both gave up at the same moment, so a missed answer
  surfaced as a bare connection timeout instead of an explanation. The caller
  now outlasts the bridge by a minute.

## [0.4.0] - 2026-10-01

Knowing what you are spending, and being asked instead of guessed at.

### Added

- Claude can now ask you a question and wait for the answer. It arrives as
  buttons to tap; questions where several answers apply tick instead, with a
  confirm button. Unanswered after fifteen minutes, Claude is told so and
  carries on rather than hanging. This works through a small MCP server in
  `hooks/ask_mcp.py`, since Claude Code's own question tool does not exist
  outside an interactive session.
- `/help` is grouped by what you are trying to do, and lists every command.
- `/usage` reports cost and tokens by project, by day and by chat, read straight
  from the chat files so it needs no extension installed. It states its own
  limits: token counts are exact, cost is per chat rather than per message, and
  chats predating cost recording show `$0.00` rather than pretending to be free.
- `/quota`, `/accounts` and `/switch` work through
  [cswap](https://github.com/realiti4/claude-swap) when it is available,
  showing rate-limit windows, reset times and which accounts need re-login.
  Without cswap they say so and nothing else changes.
- Account addresses are masked by default, since Telegram keeps whatever it is
  shown. `SHOW_EMAILS` allows `full` or `none`.
- Switching accounts is refused while a chat is working, because credentials
  would change underneath a running turn.

### Fixed

- Long command answers are split instead of being rejected. `/usage` with many
  projects could exceed Telegram's 4096-character limit, and a split inside a
  preformatted block no longer leaves an unbalanced tag.

## [0.3.0] - 2026-10-01

### Added

- When Claude writes a document, the message carries a button that sends you the
  file. Markdown, text, HTML, PDF, CSV and images are offered; images arrive as
  pictures rather than attachments. Code is not offered — attaching every file
  Claude touches would flood a topic and upload far more than you asked for.
- `/file <path>` sends any file. Relative paths resolve against the project the
  topic belongs to.
- Files that look like credentials (`.env`, `id_rsa`, `*.pem`, `*.key`,
  `credentials.json`) are never offered and are refused by `/file`, since
  Telegram keeps a copy of everything sent to it. `/file <path> force` overrides
  it deliberately.
- `ATTACH_SUFFIXES` and `MAX_ATTACH_MB` control which files are offered and how
  large they may be.

### Changed

- A message carrying a button is posted on its own rather than merged with its
  neighbours, so the button cannot end up under unrelated text.

### Note

Local file paths cannot be made clickable. Telegram accepts any `href` but keeps
only `http` and `https`, silently discarding relative paths, `file://` and
`vscode://` targets. Sending the file is the workable alternative.

## [0.2.1] - 2026-09-30

### Fixed

- Markdown that Telegram cannot show natively arrived as literal syntax — a
  heading appeared as `## Final state`, and a table as rows of pipes. It is now
  translated into what Telegram can render: headings become bold, two-column
  tables become labelled lists, wider tables keep their shape in a monospace
  block, bullets become real bullets, and rules become a line. Links, italics
  and strikethrough render properly.
- Emphasis markers inside code spans and fenced blocks are left alone, so
  `a * b ** c` stays arithmetic instead of turning into italics.

## [0.2.0] - 2026-09-30

Finishing with a chat, and living with a lot of them.

### Added

- `/archive` saves a finished conversation as a file and closes its topic;
  `/reopen` brings it back, and anything new in that chat reopens it by itself.
  Closing hides a topic from the active list without deleting anything.
- Archives come as Markdown, HTML or both (`ARCHIVE_FORMAT`, or an argument to
  `/archive`). The HTML is self-contained, collapses long tool output, and marks
  text `dir="auto"` so right-to-left languages read correctly.
- Archives keep tool output in full, unlike the mirror, which shortens it.
- Each project gets a consistent topic colour and icon, so a long topic list can
  be scanned at a glance. `--restyle-topics` applies icons to topics that
  already exist.
- The bot registers its commands with Telegram, so typing `/` offers them with
  descriptions.

### Changed

- While Claude is working the topic shows "typing…", replacing the
  "working…" message that used to be posted and then left behind.

### Fixed

- A project is now identified by its folder name rather than the first `cwd`
  found in its records. `cwd` is wherever Claude happened to be working, not the
  project root — one project showed twenty different values — so a chat that
  began in a subfolder could be filed under it, giving the wrong topic name and
  sending replies to the wrong directory.
- Recorded paths are read the same way whatever machine the bridge runs on.
  Windows paths came back whole on Linux, which would have put a full path into
  every topic name and keyed standing permissions so they never matched twice.

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

[Unreleased]: https://github.com/amirtavakolihaghighi/claude-code-telegram-bridge/compare/v0.6.0...HEAD
[0.6.0]: https://github.com/amirtavakolihaghighi/claude-code-telegram-bridge/compare/v0.5.0...v0.6.0
[0.5.0]: https://github.com/amirtavakolihaghighi/claude-code-telegram-bridge/compare/v0.4.1...v0.5.0
[0.4.1]: https://github.com/amirtavakolihaghighi/claude-code-telegram-bridge/compare/v0.4.0...v0.4.1
[0.4.0]: https://github.com/amirtavakolihaghighi/claude-code-telegram-bridge/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/amirtavakolihaghighi/claude-code-telegram-bridge/compare/v0.2.1...v0.3.0
[0.2.1]: https://github.com/amirtavakolihaghighi/claude-code-telegram-bridge/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/amirtavakolihaghighi/claude-code-telegram-bridge/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/amirtavakolihaghighi/claude-code-telegram-bridge/releases/tag/v0.1.0
