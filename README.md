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

Telegram has no headings, tables or lists, so Markdown that assumes them is
translated into what it can show — headings become bold, two-column tables
become labelled lists, wider ones keep their shape in a monospace block, and
any too wide for a phone become a short list per row. You never see a literal
`## Heading` or a row of pipes.

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
   4  Restart       8  Watch log       9  Run here

  10  Send missed history
```

Choose **5** once and it starts with the PC, with no window. It runs as you —
no administrator rights — because Claude Code uses your own login. Logs go to
`runtime\bridge.log`.

What the options mean:

| Option | What it does |
| --- | --- |
| **Start** | Runs it in the background, with no window. If it is not installed yet, it installs it first, so it also starts at every sign-in. |
| **Stop** | Stops the background copy. It starts again at the next sign-in, unless you uninstall it. |
| **Install** | Makes it start by itself at every sign-in, and starts it now. |
| **Uninstall** | Stops it, and stops it starting at sign-in. |
| **Run here** | Runs it in the menu's own window, so you can watch it work. Ctrl+C stops it. If the background copy is running, it offers to stop that one first. |
| **Send missed history** | Sends everything that never reached Telegram. See [Catching up](#catching-up). |

**Only one copy can run at a time.** Two copies would share one bot token and
post everything twice, so a second one refuses to start and says which process
is already running.

The line at the top of the menu comes from the bridge itself, not from Task
Scheduler. It says when a copy is running but has done nothing for a while,
which Task Scheduler still reports as "Running".

### When it cannot reach Telegram

If Telegram is unreachable when the bridge starts — at sign-in the network, or
a VPN if you use one, is often not up yet — the bridge waits and
keeps trying, and says so in the log. Messages written in the meantime are sent
once it gets through.

If it hits an unexpected error, it logs it and starts again by itself 30
seconds later. Task Scheduler's own "restart on failure" setting does not help
here: it only covers a task that fails to launch, not a program that started
and then failed.

It also takes arguments: `bridge.bat status`, `bridge.bat stop`,
`bridge.bat catch-up`.

---

## Using it from Telegram

Type in a topic to continue that chat.

| Command | What it does |
| --- | --- |
| *(just type)* | Continue the chat this topic belongs to |
| `/new <text>` | Start a new chat in this topic's project |
| `/c <text>` | Send a message the long way round |
| `/stop` | Cut short whatever is running here |
| `/archive [md\|html\|both]` | Save the chat as files, then close the topic |
| `/reopen` | Reopen a closed topic so it can continue |
| `/resend` | Delete this topic and send the whole chat again, in order (asks first) |
| `/mute` | Stop sending this chat here |
| `/unmute` | Send it here again, starting with everything written while muted |
| `/cost` | What this chat and today have cost |
| `/usage [all]` | The full breakdown, by project and day |
| `/quota` | How much of your 5-hour and 7-day limits is used |
| `/accounts` | Claude accounts and their remaining quota |
| `/switch <n>` | Change which account Claude uses |
| `/file <path>` | Send me a file from this project |
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

### When Claude needs to ask you something

Claude Code's own question tool only exists in an interactive session, so a turn
driven from Telegram has no way to ask you anything — it can only guess, or ask
in prose you might not read for hours.

The bridge gives it one. Claude can now ask directly, and the question arrives
as buttons:

```text
❓ Claude is asking

The migration can keep the old column or drop it. Which?

[ Keep it, safer                 ]
[ Drop it, cleaner               ]
[ Stop and let me look           ]
```

Tap one and Claude carries on with your answer. For questions where several
answers apply, the buttons tick instead, and a confirm button sends them
together:

```text
Tap the ones that apply, then Send.

[ ✅ Tests          ]
[ Docs              ]
[ ✅ CI             ]
[ 📤 Send 2 answers ]
```

Each tap confirms what it did, and the Send button always reads as one, so the
selection is never left looking unconfirmable.

If you do not answer within five minutes, the question is struck through, its
buttons are removed, and Claude is told so and carries on rather than hanging.
An expired question says so if you tap it, instead of silently doing nothing.

This works through a small MCP server in `hooks/ask_mcp.py` — a standard way for
a tool to be offered to Claude. Claude Code starts it, sees a tool called
`ask_user`, and calling it reaches the bridge over the same loopback connection
the permission buttons use.

### Cost, quota and accounts

`/usage` reads the chat files directly, so it needs nothing installed and keeps
working whatever else you remove. It reports total and recent cost, the share
served from cache, a breakdown per project, tokens per day and your dearest
chats.

Two honest caveats it states itself: token counts are exact, but cost is
recorded per chat rather than per message, so a chat spanning midnight lands on
the day it was last active — and chats old enough to predate cost recording show
`$0.00`, which does not mean they were free.

`/quota`, `/accounts` and `/switch <n>` work through
[cswap](https://github.com/realiti4/claude-swap), if you have it. They show how
much of each rate-limit window is spent, when it resets, and which accounts need
logging in again. Without cswap they say so and nothing else is affected.

Account addresses are **masked** by default (`a***@example.com`), because
Telegram keeps whatever it is shown. `SHOW_EMAILS` can set that to `full` or
`none`.

Switching is refused while a chat is working, since credentials would change
underneath a running turn.

### Finishing with a chat

When a project is done, `/archive` posts the whole conversation as files and
closes the topic.

**Closing deletes nothing.** Every message stays, stays searchable and stays
readable — the topic moves to a Closed section and stops accepting new
messages. `/reopen` brings it back, and anything new arriving in that chat
reopens it by itself.

Two formats, because they are good at different things:

| Format | Good for |
| --- | --- |
| Markdown | Handing the conversation back to an AI, diffing, grepping |
| HTML | Reading. Keeps formatting, collapses long tool output, and marks text `dir="auto"` so right-to-left languages read correctly — something Markdown cannot express. |

`ARCHIVE_FORMAT` in `.env` sets the default; `/archive html` overrides it once.

The HTML is a single self-contained file with no external assets, so it opens
from your downloads folder with no internet. **Telegram will not render it
in-app** — tapping it hands the file to your browser.

### Starting a topic over, and muting a chat

**`/resend`** puts right a topic that does not match its chat: one missing
messages, say, or holding something twice. It asks first, because it deletes
the topic with everything in it, including files sent with `/file` and
`/archive`. Then the whole chat goes into a new topic, from its first message,
in order, alongside whatever else is being sent. It needs the chat's file, or a
copy of it in [`CHAT_BACKUPS`](#chats-claude-code-has-deleted). Without either,
the topic is the only copy left and is kept.

**`/mute`** stops a chat being sent here. Nothing is skipped: the chat keeps its
place, and **`/unmute`** sends everything written in the meantime, in order. A
message typed in a muted topic is not run, since its answer would not show up.

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
an old conversation from your phone. To fill those topics with the past
conversation, use [Catching up](#catching-up).

### Which chats are followed, and from where

The log calls following a chat *tracking* it. **Every chat file in a watched
project is tracked**. There is no per-chat switch: `WATCH_PROJECTS` is the
control. Every two seconds the bridge reads whatever each tracked file has
gained since last time.

Not tracked:

- chats in projects outside `WATCH_PROJECTS`
- a project folder in which no chat records the project's path yet
- subagent chatter and Claude's reasoning, which are skipped inside every chat
- chats Claude Code has deleted. By default it deletes chat files 30 days
  after their last use; `cleanupPeriodDays` in `~/.claude/settings.json`
  changes that. The Telegram topic outlives the file, and with
  [`CHAT_BACKUPS`](#chats-claude-code-has-deleted) the chat can still be read
  from a copy.

What differs between chats is **where tracking starts**. The log says which
case it is:

| The log says | When | What is sent |
| --- | --- | --- |
| `tracking … from byte N` | a chat it has followed before | everything after where it stopped, so a restart misses nothing |
| `new chat … mirroring from the start` | a chat that appeared while it was running | the whole chat |
| `… began while the bridge was off` | a chat started while it was not running | the whole chat |
| `tracking … from now` | a chat that was already there before the bridge ever ran here | nothing old. That history is recorded as never sent, so [catching up](#catching-up) can send it later |

A read position only moves past a message once it has been posted. If the
bridge stops part-way — a shutdown, a crash, an hour without a connection —
whatever was not posted yet is sent next time, in order, rather than lost.

## Catching up

**Option 10 in `bridge.bat`** sends every message that never reached Telegram,
oldest first:

- chats followed "from now", whose earlier history was never sent
- chats the bridge has never followed. On a machine just set up, that is all
  of them.

The menu says what it found and restarts the bridge, which does the sending in
the background. New messages keep arriving as usual meanwhile: a backlog is
read a slice at a time, so it never holds up a live reply for more than a
minute or two. Option 1 shows how far it has got. You can stop it part-way; it
carries on from there.

Be ready to wait. Telegram allows a bot roughly 20 messages a minute into one
group, so a lot of history takes **hours**.

**A chat whose topic already has messages, but is missing its beginning, is
sent again whole.** Telegram cannot put messages above ones already posted, so
the only way to show such a chat complete and in order is to start again. Its
topic is deleted, with everything in it, just before the first message goes
out again, and the whole chat goes into a new topic. The bot needs the
*Delete messages* admin right for this; without it, the old topic is closed
and renamed `(partial copy)` instead. A topic you had archived is archived
again once all of it is back.

Catching up never sends anything twice. A chat that has already been sent in
full is left alone, so running it again is harmless.

From the command line, `python -m bridge --catch-up` does the same in the
foreground.

### Chats Claude Code has deleted

Claude Code deletes a chat 30 days after its last use. If you keep copies of
`~/.claude/projects`, point `CHAT_BACKUPS` at them, and such a chat is read
from its copy instead:

```bat
CHAT_BACKUPS=C:\Users\you\.claude\projects - Backup*
```

The wildcard matters if the folder is renamed with each backup. A chat that is
still in Claude Code's own folder is always read from there. A copy is only
used once the original is gone, and then the most complete copy. A copy that
is shorter than what was already posted adds nothing and changes nothing.

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
| `CHAT_BACKUPS` | Folders holding copies of `~/.claude/projects`, for chats Claude Code has deleted. Wildcards allowed; several separated by `;` |
| `TOPIC_ICONS` | `project` (default): each project's topics share an icon. `default`: Telegram's own. Changing it restyles every topic at the next start. |
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
| `python -m bridge --catch-up` | Also send everything never sent, oldest first, then follow |
| `python -m bridge --backfill` | Re-read every chat from the top and post all of it again, duplicates included |
| `python -m bridge --import-all` | Give every existing chat a topic straight away |
| `python -m bridge --dry-run` | Print to the console instead of Telegram |
| `python -m bridge --once` | One pass, then exit |
| `python -m bridge --no-replies` | Mirror only; ignore what you type |
| `python -m bridge --log-file PATH` | Also write the log to a file |
| `python -m bridge -v` | Verbose logging |

---

## Adding a second computer

Each machine has its own chats, and both can feed the same Telegram group.

**One bot token can only be used by one *polling* copy at a time.** Two copies
both checking for your messages fight, and both fail with `Conflict`. So pick
one:

| | Second bot token | Can you reply to that machine's chats? |
| --- | --- | --- |
| **One machine at a time** | No — the same bot on both | Yes, to the chats of whichever machine is on |
| **Both at once** | Yes — make another bot in @BotFather | Yes, to both, any time |
| **Mirror only** | No — reuse the same token with `--no-replies` | No, read-only |

**One machine at a time** suits a desktop and a laptop that are not used
together. Shut one down before using the other: sleep is not enough, since a
sleeping machine's bridge carries on where it was the moment it wakes. Each
machine has its own chats and its own topics, so nothing collides. The ways it
can trip you up:

- If both do run at once, each bridge says so: a warning in the log and in the
  group's General topic. Replies then reach only one of them.
- A reply typed in a topic belonging to the machine that is off reaches the one
  that is on. It answers that the topic is not one of its chats, rather than
  running anything.
- Replies typed while neither machine is running are discarded when one starts.
  That way a message typed hours ago never runs by surprise.

Adding a bot to a group it is already in is fine; Telegram allows several.

### Setting up the second machine

1. **Install it** the same way as the first: clone or download, create the
   virtual environment, install requirements.

2. **For both at once only**, make a second bot in
   [@BotFather](https://t.me/BotFather) (`/newbot`), turn its **Group Privacy
   off** as before, add it to the same group and make it an admin with
   **Manage Topics** and **Delete messages**.

3. **Write `.env` on the new machine.** `TELEGRAM_GROUP_ID` and
   `TELEGRAM_OWNER_ID` are the *same values as the first machine* — copy them
   across rather than looking them up again. The token is the same too, unless
   you made a second bot:

   ```bat
   TELEGRAM_BOT_TOKEN=<the same token, or the second bot's>
   TELEGRAM_GROUP_ID=<same as the first machine>
   TELEGRAM_OWNER_ID=<same as the first machine>
   WATCH_PROJECTS=ALL
   MACHINE_LABEL=Laptop
   ```

   `MACHINE_LABEL` is what keeps the two apart: topics read
   `Laptop · project: chat title`.

4. **Send its history, and keep it running.** With the first machine shut
   down if they share a bot, open `bridge.bat` and choose **10**, then **5**:

   - **10** finds every chat on this machine — none has been sent yet — and
     marks them all to go, oldest message first.
   - **5** installs it to start with the PC and starts it. It creates each
     topic as it gets there and posts the lot in the background, while new
     messages carry on as usual.

   It takes hours for a lot of history. Option **1** shows how far it has got,
   and you can restart the machine part-way: it carries on where it stopped.

With a second bot, the first machine can keep running throughout. With one
bot, that first send takes hours, so leave the laptop on until option 1 says
it is up to date; it carries on next time otherwise.

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

`state.db` remembers how far into each file has been *posted*, so restarting
neither re-posts nor loses anything. It also records where mirroring of each
chat began, which is how catching up knows what was never sent.

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
- Subagent chatter and Claude Code's own notices (API retries, hook summaries)
  are not mirrored. Too noisy.
- Claude's reasoning is not mirrored, because it is not written to the chat
  file: only a signature is kept. The short notes Claude writes between steps
  are written down, and are mirrored, as VS Code shows them.
- A compacted conversation shows as a one-line marker, as VS Code shows it,
  not as the summary Claude Code records in your name.
- A picture you paste into a chat shows as `[image]`; the picture itself is not
  uploaded.
- Tool input is capped at 600 characters, output at 900, to keep messages short.
- Telegram's rate limit makes catching up on a lot of history slow.
- A bridge that is killed in the instant between posting a message and saving
  that it did will post that message again: once twice rather than never.

## Licence

MIT — see [LICENSE](LICENSE).
