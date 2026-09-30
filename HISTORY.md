# How this project got here

A record of what was built, in what order, and why. Written so that anyone
picking this up later — including me in six months — can see the reasoning, not
just the result.

## The problem

I use Claude Code through the official VS Code extension and want to keep using
it that way. But the chat panel is cramped, and the moment I leave my desk I
lose access to everything I was working on.

The idea: mirror every chat into Telegram, one topic per chat, and be able to
reply from there — so the conversation carries on whether I'm at the keyboard
or on the bus.

## What made it possible

Four things were checked on the actual machine before any code was written.
Each one could have killed the idea:

1. **Chats are ordinary files.** `~/.claude/projects/<project>/<chat-id>.jsonl`,
   one folder per project, one file per chat. The VS Code extension and the
   command-line tool read and write the same files. So anything else can too.

2. **The extension ships its own `claude.exe`**, inside
   `~/.vscode/extensions/anthropic.claude-code-*/resources/native-binary/`.
   No separate install, and it is already signed in as me.

3. **`--resume` genuinely continues a chat.** Tested directly: same chat id,
   appended to the same file, full memory of what came before. This is the
   foundation of the whole thing — without it there is no two-way bridge.

4. **A permission hook can answer for me.** `PermissionRequest` hooks may return
   `{"behavior": "allow"}` or `"deny"`, which is what lets a phone tap decide
   what happens on the PC.

The one real constraint, found at the same time: **a live VS Code panel holds
its chat in memory.** Appending to a chat that is open in the editor doesn't
show up there, and typing in the editor afterwards can fork the conversation.
So: one writer at a time. Everything since has been built around that rule
rather than pretending it isn't there.

## Built in order

**Phase 1 — a read-only mirror.** Watch the chat files, turn new lines into
readable messages, post them into a topic per chat. Byte offsets are remembered
in `state.db` so a restart never re-posts anything.

**Phase 2 — made it pleasant.** The first version was a wall of noise. Tool
calls now show what went in *and* what came out, with long output collapsed
into a block you tap to expand. Every tool type has its own icon; failures get
a warning sign. Tool messages arrive silently — only Claude's actual replies
are worth a buzz.

**Phase 3 — typing back.** A message in a topic runs `claude --resume` in that
project. The neat part: nothing had to be built to get the answer back, because
the command-line tool writes into the same file the mirror is already watching.
The reply mirrors itself. Prompts typed in Telegram are not echoed back, since
they are already on screen there.

**Phase 4 — permission buttons.** Risky actions now stop and ask, with
Allow / Deny / Allow-everything-this-turn / Always-allow. Verified properly:
tapping Deny left a test file alone, tapping Allow deleted it. Standing
permissions are per program — yes to `git` is not yes to `rm`.

**Phase 5 — every project.** `WATCH_PROJECTS=ALL` mirrors everything Claude
Code knows about. Each chat remembers its own project folder, so a reply always
runs in the right place. Topics are named `Project: chat title`, because one
project usually holds several chats and they were impossible to tell apart
otherwise.

**Always running.** A Windows Scheduled Task starts the bridge at sign-in with
no visible window, restarts it if it crashes, and keeps going on battery.

## Things that went wrong, and what fixed them

**Telegram rate limits.** One message per event blew straight past the ~20 per
minute a bot gets in a group, and the mirror started falling behind. Fixed by
queueing messages and merging adjacent ones from the same chat into a single
post — ten tool calls become two or three messages.

**Creating topics has a stricter limit still.** Importing a whole PC's worth of
chats at once tripped flood control. Fixed with a proper wait-and-retry and a
slower pace when creating many at once.

**New chats were being skipped.** Chats present at startup are treated as
history and picked up from that moment. But a chat *created* while the bridge
is running is new, and should be mirrored from its first word. The two cases
are now told apart.

**Topics named after chat ids.** The chat's title sits earlier in the file than
the point the mirror resumes reading from, so it was never seen. Now the file
is scanned for the title when a chat is first picked up.

**Two copies fighting.** Leftover processes from testing kept polling Telegram
with the same token, which produces `Conflict` errors. Only one instance of the
bot may run per token — worth remembering before adding a second machine.

**A network timeout posted a message twice.** The default 5-second timeout was
too tight; a send that had actually succeeded looked like a failure and got
retried. Timeouts raised to 30 seconds.

## Decisions worth remembering

- **The mirror reads files; it does not parse what the CLI prints.** One source
  of truth, and replies typed in Telegram and in VS Code behave identically.
- **Permissions go through a hook passed in with `--settings`**, which applies
  to bridge-driven runs only and never changes how Claude Code behaves inside
  VS Code.
- **The bot ignores everyone except one Telegram user id.** It can run commands
  on a personal PC; that check came before any of the convenience features.
- **Standing permissions are keyed by program**, not by whole command, because
  a per-command rule would never match twice and a per-tool rule would be far
  too broad.
- **Topics are created but history is not dumped.** Importing eighteen chats
  gives eighteen topics ready to use, without thousands of old messages.

## What is deliberately not done

- The bot knows a *project* is open in VS Code, but not which *chat*. It warns;
  it cannot prevent.
- Subagent chatter and Claude's thinking are not mirrored. Too noisy.
- Multiple-choice questions need nothing: that tool isn't available in this
  mode, so Claude asks in plain words and I answer by typing.
- Searching across chats, and a digest when several chats are busy at once.
