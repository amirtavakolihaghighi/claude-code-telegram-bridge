# Claude Code → Telegram

[![CI](https://github.com/amirtavakolihaghighi/claude-code-telegram-bridge/actions/workflows/ci.yml/badge.svg)](https://github.com/amirtavakolihaghighi/claude-code-telegram-bridge/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/downloads/)

**Your Claude Code conversations are stuck at your desk.** Close the laptop and
you lose sight of what you were doing; the chat panel only exists inside
VS Code, on that one machine.

This puts every conversation into a Telegram group — one topic per chat, live —
and lets you carry on from your phone. A reply you send from the bus runs Claude
on your PC, edits real files, and is waiting for you in VS Code when you get
home.

It works because Claude Code stores each conversation as a plain file. VS Code
writes to those files; so can something else.

> **Windows only** at the moment. The core would port easily; the
> start-with-the-PC helper is Windows-specific.

---

## Is this for you?

**Yes, if** you use Claude Code in VS Code, leave your PC on, and want to read
or continue your work away from the desk.

**No, if** you want a hosted bot. This *cannot* run on a rented server — it
reads your chat files and runs Claude on your machine. Your PC has to be awake.

---

## What it looks like

Each chat becomes a topic named `ProjectFolder: chat title`:

```text
webshop: Fix the checkout rounding bug
webshop: Add dark mode toggle
scraper-cli: Retry logic for flaky pages
```

Inside a topic, tool calls arrive as one message each — what went in, and what
came out in a block you tap to expand:

```text
💻 Bash · Run the test suite
┌────────────────────────────┐
│ pytest -q                  │
└────────────────────────────┘
↳ ▸ 41 passed in 1.2s
```

Only Claude's replies notify you. Tool activity is silent.

---

## Install

You need **Python 3.11 or newer** and **Claude Code for VS Code**, signed in.

### If you are not a developer

1. Download this repository (green **Code** button → **Download ZIP**) and
   unzip it somewhere permanent, like `C:\Tools\claude-telegram`.
2. Open that folder, click the address bar, type `cmd` and press Enter.
3. Paste these two lines:

   ```bat
   python -m venv .venv
   .venv\Scripts\python -m pip install -r requirements.txt
   ```

4. Copy `.env.example` to `.env` and open it in Notepad. Leave it for a moment —
   the next section fills it in.

### If you are a developer

```bash
git clone https://github.com/amirtavakolihaghighi/claude-code-telegram-bridge.git
cd claude-code-telegram-bridge
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt -r requirements-dev.txt
.venv\Scripts\python -m pytest
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for how the tests are isolated from your
real chat data.

---

## Setup (about five minutes, in Telegram)

1. **Make your own bot.** Message [@BotFather](https://t.me/BotFather), send
   `/newbot`, follow the prompts. It gives you a token that looks like
   `1234567890:AA...`. Put it in `.env` as `TELEGRAM_BOT_TOKEN`.

2. **Turn off its privacy mode.** Still in BotFather: `/mybots` → your bot →
   *Bot Settings* → *Group Privacy* → **Turn off**. Without this the bot cannot
   see ordinary messages in a group, only commands.

3. **Create the group.** New Group → name it → add anyone (you can remove them
   after). Then group settings → *Edit* → turn on **Topics**.

4. **Add your bot and make it an admin** with **Manage Topics** enabled.
   Without that permission it cannot create topics.

5. **Find the two IDs:**

   ```bat
   .venv\Scripts\python -m tools.discover_ids
   ```

   Send `/id` in the group. Paste the two lines it prints into `.env`, then
   press Ctrl+C.

6. **Start it:**

   ```bat
   .venv\Scripts\python -m bridge
   ```

Open Claude Code and type something — it appears in Telegram within a couple of
seconds.

### Try it without Telegram first

Nothing is sent anywhere; this just prints what *would* be posted:

```bat
.venv\Scripts\python -m bridge --dry-run --backfill --once
```

---

## Keeping it running

**Double-click `bridge.bat`** for a menu:

```text
   1  Status        5  Install (start at sign-in)
   2  Start         6  Uninstall
   3  Stop          7  Show log
   4  Restart       8  Watch log       9  Run in this window
```

Choose **5** once and it starts with the PC, with no window, restarting itself
if it crashes. It runs as you — no administrator rights — because Claude Code
uses your own login. Logs go to `runtime\bridge.log`.

It also takes arguments: `bridge.bat status`, `bridge.bat stop`.

---

## Using it from Telegram

Type in a topic to continue that chat.

| Command | What it does |
| --- | --- |
| *(just type)* | Continue the chat this topic belongs to |
| `/new <text>` | Start a new chat in this topic's project |
| `/c <text>` | Send a message the long way round |
| `/stop` | Cut short whatever is running here |
| `/projects` | Which projects exist, and which are mirrored |
| `/rules` | Things Claude is always allowed to do |
| `/forget <key\|all>` | Undo one of those |
| `/vscode` | Link that opens this chat in the editor |
| `/status` | Which chat, which project, busy or not |
| `/help` | The list above |

**Only you can use the bot.** Every message is checked against
`TELEGRAM_OWNER_ID` and dropped otherwise.

### When Claude needs permission

Risky actions stop and ask:

```text
❓ Can Claude do this?
💻 Bash · Delete the old build folder
┌──────────────────┐
│ rm -rf build/    │
└──────────────────┘
[ ✅ Allow ]  [ 🚫 Deny ]
[ ⚡ Allow everything in this turn ]
[ 🔁 Always allow Bash · rm ]
```

"Always allow" is **per program**: yes to `git status` covers every `git`
command, but not `npm` or `rm`. `/rules` lists what you have agreed to;
`/forget Bash:git` takes it back.

If you don't answer within five minutes it refuses on your behalf, so nothing
hangs waiting.

### The one rule

**A chat can only be written to from one place at a time.** If the same chat is
open in a VS Code tab and you reply from Telegram, the two can write over each
other and the conversation forks. The bot warns you when the project is open in
VS Code — close the chat's tab there first.

The other direction is always safe: reply from Telegram, open it in VS Code
later, everything is waiting.

---

## Which projects get mirrored

`WATCH_PROJECTS` in `.env` takes any of:

```bat
WATCH_PROJECTS=ALL                        every project on this PC
WATCH_PROJECTS=C:\Code\webshop            just that one
WATCH_PROJECTS=C:\Code\a;C:\Code\b        a chosen few
```

Existing chats only get a topic once they have something new to say. Run once
with `--import-all` to give every chat a topic immediately, so you can pick up
an old conversation from your phone.

To also fill those topics with the past conversation:

```bat
bridge.bat stop
.venv\Scripts\python -m bridge --backfill --once --no-replies --log-file runtime\backfill.log
bridge.bat start
```

Stop the service first — only one copy may run at a time. Be ready to wait:
Telegram allows a bot roughly 20 messages a minute into one group, so a PC with
a lot of history takes **hours**. Everything is queued up front, so it finishes
even if the connection wobbles.

---

## Settings (`.env`)

| Key | Meaning |
| --- | --- |
| `TELEGRAM_BOT_TOKEN` | From @BotFather. **Never commit this.** |
| `TELEGRAM_GROUP_ID` | The group to post into |
| `TELEGRAM_OWNER_ID` | Your user ID. Nobody else may use the bot. |
| `WATCH_PROJECTS` | `ALL`, one folder, or several separated by `;` |
| `REPLIES_ENABLED` | `false` for a read-only mirror |
| `CLAUDE_PERMISSION_MODE` | How freely Claude may act — see below |
| `APPROVAL_TIMEOUT` | Seconds to wait for your tap. Default 300. |
| `MACHINE_LABEL` | Name in front of topics when two PCs share one group |
| `POLL_INTERVAL` | Seconds between checks. Default 2. |
| `CLAUDE_CLI` | Path to `claude.exe`. Found automatically; override if needed. |

`CLAUDE_PERMISSION_MODE` decides how often you are asked:

| Mode | Meaning |
| --- | --- |
| `manual` | Ask about nearly everything |
| `acceptEdits` | File edits go through; commands still ask |
| `auto` | **Default.** Same judgement as VS Code |
| `bypassPermissions` | Never ask. Full trust. |

---

## Command-line options

| Command | What it does |
| --- | --- |
| `python -m bridge` | Follow from now on |
| `python -m bridge --backfill` | Re-read every chat from the top and post all of it |
| `python -m bridge --import-all` | Give every existing chat a topic straight away |
| `python -m bridge --dry-run` | Print to the console instead of Telegram |
| `python -m bridge --once` | One pass, then exit |
| `python -m bridge --no-replies` | Mirror only; ignore what you type |
| `python -m bridge --log-file PATH` | Also write the log to a file |
| `python -m bridge -v` | Verbose logging |

---

## Adding a second computer

Each machine has its own chats, and both can feed the same Telegram group.

**One bot token can only be used by one *polling* copy.** Two copies both
checking for your messages fight, and both fail with `Conflict`. So pick one:

| | Second bot token | Can you reply to that machine's chats? |
| --- | --- | --- |
| **Full setup** (recommended) | Yes — make another bot in @BotFather | Yes |
| **Mirror only** | No — reuse the same token with `--no-replies` | No, read-only |

Adding a bot to a group it is already in is fine; Telegram allows several.

### Setting up the second machine

1. **Install it** the same way as the first: clone or download, create the
   virtual environment, install requirements.

2. **Make a second bot** in [@BotFather](https://t.me/BotFather) (`/newbot`),
   and turn its **Group Privacy off** as before. Skip this if you only want
   mirroring.

3. **Add that bot to the same group** and make it an admin with
   **Manage Topics**.

4. **Write `.env` on the new machine.** `TELEGRAM_GROUP_ID` and
   `TELEGRAM_OWNER_ID` are the *same values as the first machine* — copy them
   across rather than looking them up again. Only the token differs:

   ```bat
   TELEGRAM_BOT_TOKEN=<the second bot's token>
   TELEGRAM_GROUP_ID=<same as the first machine>
   TELEGRAM_OWNER_ID=<same as the first machine>
   WATCH_PROJECTS=ALL
   MACHINE_LABEL=Laptop
   ```

   `MACHINE_LABEL` is what keeps the two apart: topics read
   `Laptop · project: chat title`.

5. **Create the topics and fill them with history.** This is the same procedure
   as the first machine, and it takes hours for a lot of history:

   ```bat
   .venv\Scripts\python -m bridge --backfill --once --no-replies --log-file runtime\backfill.log
   ```

   `--backfill` reads every chat from the beginning, creates each topic as it
   gets there, and posts the lot. Watch progress with
   `bridge.bat` → **8**.

6. **Then start it normally**, and install it to start with the PC:

   ```bat
   bridge.bat
   ```

   Choose **5**.

The first machine keeps running throughout — different tokens, no conflict.

## If you move a project

Claude Code files chats under a folder named after the project's path, so moving
a project hides its chats until you rename that folder to match the new path.
Once you have done that, VS Code finds them again.

The bridge needs one more step, because the path recorded *inside* each chat
still points at the old location:

1. Rename the folder in `%USERPROFILE%\.claude\projects\` as usual.
2. **Send one message in that chat from VS Code.** This writes a record carrying
   the new path.
3. Restart the bridge — `bridge.bat` → **4**.

After that the bridge recognises the project by its new path, the topic is
renamed to match, and replying from Telegram works again.

**Until you do step 2 and 3, mirroring keeps working but replying does not** —
the bridge would try to run Claude in a folder that no longer exists, and tells
you so rather than guessing.

Nothing is lost either way: the topic, its history and its link to the chat all
survive a move. Only the "where do I run this" answer goes stale.

### Do not sync the chat files themselves

Do **not** put `~/.claude/projects` in OneDrive, Dropbox or similar. Those files
are appended to constantly and contain machine-specific paths; two machines
writing one file will corrupt conversations. Let each machine mirror its own.

---

## How it works

```text
Claude Code (VS Code)
   writes ->  ~/.claude/projects/<project>/<chat-id>.jsonl
                     |
            bridge/locator.py   finds the files for WATCH_PROJECTS
            bridge/parser.py    reads only the new bytes each pass
            bridge/format.py    -> Telegram HTML, split at 4096 chars
            bridge/sinks.py     -> one topic per chat, batched
                     |
                  Telegram
```

A reply from Telegram runs `claude --resume <chat-id>` in that project's
folder. Because the CLI writes to the same file the mirror is watching,
**Claude's answer mirrors itself** — nothing extra was needed to get it back.

`state.db` remembers how far into each file we have read, so restarting never
re-posts anything.

---

## Privacy and safety

Read [SECURITY.md](SECURITY.md) before running this. In short:

- **It reads every Claude Code conversation on your PC** and sends them to a
  Telegram group. Telegram can see everything posted there.
- **It can run commands on your PC** from a Telegram message. That is the point,
  but it means the bot token is as sensitive as a password.
- Nothing leaves your machine except to Telegram. There is no other server.

---

## Documentation

- [ROADMAP.md](ROADMAP.md) — what is done and what is next
- [HISTORY.md](HISTORY.md) — how it was built and why it works this way
- [CONTRIBUTING.md](CONTRIBUTING.md) — running the tests, conventions
- [SECURITY.md](SECURITY.md) — threat model and reporting
- [CHANGELOG.md](CHANGELOG.md) — what changed, by version

---

## Known limits

- The bot knows a *project* is open in VS Code, but not which *chat*.
- Subagent chatter and Claude's thinking are not mirrored. Too noisy.
- Tool input is capped at 600 characters, output at 900, to keep messages short.
- Telegram's rate limit makes large backfills slow.

## Licence

MIT — see [LICENSE](LICENSE).
