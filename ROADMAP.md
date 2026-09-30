# Claude Code → Telegram Bridge

Mirror VS Code Claude Code chats into Telegram, and chat back from there.

## How it works (the short version)

Claude Code chats are plain files on the PC:

```text
%USERPROFILE%\.claude\projects\<project-folder>\<chat-id>.jsonl
```

One folder per project directory. One `.jsonl` file per chat. VS Code reads and
writes these files — so anything else can too.

Telegram side: a **supergroup with Topics turned on**. One topic per chat.
(Channels don't support topics. It has to be a supergroup.)

**The one rule:** only one place can type into a chat at a time — VS Code *or*
Telegram, never both at once. Only matters from Phase 3 on.

## Verified as working

- [x] Chats are stored as `.jsonl`, one file per chat, shared by VS Code and CLI
- [x] The VS Code extension ships its own `claude.exe` (no separate install):
      `~/.vscode/extensions/anthropic.claude-code-<ver>-win32-x64/resources/native-binary/claude.exe`
- [x] `claude --resume <chat-id> -p "text"` keeps the same chat ID, appends to the
      same file, and remembers the whole conversation — tested, confirmed
- [x] Each chat has an auto-generated title (`ai-title`) → use it as the topic name
- [x] `vscode://Anthropic.claude-code/open?session=<id>&prompt=<text>` opens a chat
      in VS Code with text pre-filled (does not auto-send)

## Tech

Python + `python-telegram-bot` + `watchdog`. Config in `.env`.

---

## Phase 0 — Setup (one evening)

- [x] Create the bot with @BotFather, save the token
- [ ] Create a Telegram supergroup, turn on Topics in group settings
- [ ] Add the bot, make it **admin** (required to create topics)
- [ ] Get my own Telegram user ID and the group ID
      (tool is built: `python -m tools.discover_ids`, then send `/id` in the group)
- [x] `.env` with: bot token, group ID, my user ID, which project folder to watch
- [x] **Ignore messages from anyone but me** — this bot will eventually run commands
      on this PC. Do this now, not later.

## Phase 1 — MVP: read-only mirror, ONE project

The goal: open Telegram, see my VS Code chats appearing live. No typing back yet.

- [x] Point at one project folder (config value, easy to change later)
- [x] Read a `.jsonl` file and turn it into readable text
- [x] Watch the folder for changes — new lines added, new chat files appearing
- [x] New chat detected → create a Telegram topic, name it from the chat title
- [x] New message detected → post it into the matching topic
- [x] Remember what's already been sent, so restarting doesn't re-post everything
- [x] Split messages longer than 4096 characters (Telegram's limit)
- [x] Use HTML formatting, not Markdown (Telegram's Markdown escaping is painful)

**Done when:** I type in VS Code and it shows up on my phone.

## Phase 2 — Make it pleasant to read

Phase 1 will be noisy and ugly. This fixes that.

- [x] Tool calls show what went in AND what came out, not just a label
- [x] Long tool output collapses into an expandable block instead of a wall of text
- [x] A different icon per tool type, and a warning icon when a tool fails
- [x] Tool messages arrive silently — only Claude's replies notify my phone
- [x] Slow down sending: Telegram cuts you off around 20 messages/minute per group
- [x] Show cost at the end of each turn
- [x] Rename the topic if the chat title changes
- [x] On startup, post anything missed while the bot was off
- [ ] One message per reply, edited as it progresses — not 30 separate messages
- [ ] Show status while Claude is still working, not just when it finishes
- [ ] Token counts alongside the cost
- [ ] Make notification rules configurable in `.env` rather than hard-coded

## Phase 3 — Type back from Telegram

- [x] Message in a topic → run `claude --resume <chat-id> -p "<message>"` in that
      project folder
- [x] The reply comes back on its own — the CLI writes to the same chat file the
      mirror is already watching, so nothing extra had to be built
- [x] Never run two turns in the same chat at once
- [x] Warn me when the project is open in VS Code (checks live window locks,
      ignores stale ones)
- [x] Don't echo back prompts I typed in Telegram — I can already see them
- [x] A brand new chat gets its own topic automatically
- [x] `/new` — start a fresh chat in a project
- [x] `/c` — send a message as a command (works even with privacy mode on)
- [x] `/vscode` — link that opens this chat in the editor
- [x] `/status` and `/help`
- [ ] **Turn OFF privacy mode in @BotFather** so plain typing works, not just `/c`
      (BotFather -> /mybots -> Bot Settings -> Group Privacy -> Turn off)
- [ ] Detect which *chat* is open in VS Code, not just which project

**Done when:** I reply from the bus and it's waiting for me in VS Code at home.

## Phase 4 — Remote control

- [x] Permission requests → Allow / Deny buttons in Telegram
- [x] An "allow everything in this turn" button, which resets when the turn ends
- [x] If I don't answer within 5 minutes, it refuses by itself rather than hanging
- [x] Only my taps count — anyone else's are rejected
- [x] `/stop` — interrupt a reply that's running
- [x] Remember "always allow this command" between turns, per program
      (`git` yes doesn't mean `rm` yes), with `/rules` and `/forget`
- [x] Answer Claude's multiple-choice questions — nothing to build: that tool
      isn't available in this mode, so Claude just asks in plain text and I
      reply by typing

## Phase 5 — Everything else

- [x] Watch all projects, auto-discover new ones (`WATCH_PROJECTS=ALL`)
- [x] `/projects` to see what exists and what is mirrored
- [x] Topics named `Project · chat title` when more than one project is watched
- [x] `--import-all` gives every existing chat a topic, so old conversations
      can be picked up from the phone without touching the PC
- [x] Topics named `Project: chat title`
- [x] Starts with the PC, no window, restarts itself
      (`python -m tools.autostart install`)
- [x] `MACHINE_LABEL` so a laptop and a desktop can share one group
- [x] `bridge.bat` - a menu for start / stop / install / logs, no typing needed
- [x] `--backfill` fills every topic with its whole past conversation
- [x] Progress logging and patient retries, so an hours-long backfill survives
      a wobbly connection
- [ ] Search across all chats
- [ ] A digest message when several chats are busy at once
- [ ] Clean up topics whose chat has been deleted

---

## Notes to self

- Existing notifier hooks in `~/.claude/settings.json` already fire on prompt,
  stop, and permission events — could feed the bot faster than watching files.
  Use hooks as the *trigger*, the `.jsonl` as the *source of truth*.
- The `.jsonl` is a tree (`parentUuid` / `leafUuid`), not a flat list. Two writers
  create a branch, not a corrupt file — but a branch is still confusing, hence the
  locking in Phase 3.
- Built-in alternatives worth 10 minutes before building more: Remote Control
  (`remoteControlAtStartup` in settings) and `claude --bg` background sessions.
