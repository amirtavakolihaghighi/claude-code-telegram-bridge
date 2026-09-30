# Security

This project deliberately does something dangerous: it lets a Telegram message
run commands on your computer. That is its purpose. Read this before you run it.

## What it actually touches

- **Reads every Claude Code conversation** in the projects you point it at, from
  `~/.claude/projects/`, and posts them to a Telegram group. Those files contain
  whatever you discussed — source code, file contents, and anything you pasted.
- **Runs `claude.exe` on your machine** when you send a Telegram message, in the
  project folder that chat belongs to. Claude can then read, write and delete
  files and run shell commands, subject to `CLAUDE_PERMISSION_MODE`.
- **Writes a settings file** (`runtime/permission-hook.json`) that is passed to
  bridge-started Claude runs only. It does not change how Claude Code behaves
  inside VS Code.
- **Listens on `127.0.0.1`** on a random high port while running, so the
  permission hook can ask you for a decision. It is loopback-only and requires a
  secret that is regenerated every time the bridge starts.
- **Stores** read positions, topic mapping and standing permissions in
  `state.db`, unencrypted. It contains chat titles and project paths.

Nothing is sent anywhere except Telegram. There is no other server, no
telemetry, and no account to sign up for.

## The threat model

### Your bot token is equivalent to a password on your PC

Anyone holding it can message your bot. The owner check means they cannot make
it *act*, but the token is the key to the whole system. Treat it like an SSH
key:

- It lives in `.env`, which is git-ignored. Never commit it.
- If it leaks, revoke it immediately in [@BotFather](https://t.me/BotFather)
  (`/mybots` → your bot → *API Token* → *Revoke*).
- Anything already pushed to a public repository must be treated as
  compromised. Deleting the file does not unpublish it.

### Your Telegram account becomes a path to your computer

The bot accepts commands from exactly one Telegram user ID
(`TELEGRAM_OWNER_ID`). If that account is compromised, the attacker can run
commands on your PC, bounded only by `CLAUDE_PERMISSION_MODE`. Use two-factor
authentication on your Telegram account.

### Everyone in the group can read everything

The owner check stops other members *sending* anything. It does not stop them
**reading** every mirrored conversation. Keep the group private and do not add
anyone you would not show your source code to.

### Telegram can read the group

Group chats are not end-to-end encrypted. Everything mirrored is visible to
Telegram. Do not mirror projects whose contents you cannot put on a third-party
service.

### Prompt injection is a real risk here

Claude reads files in your project. A file containing text crafted to look like
instructions can influence what Claude does. Normally you are at the keyboard to
notice. Driven from a phone, with `CLAUDE_PERMISSION_MODE=bypassPermissions` and
standing "always allow" rules, there is nobody watching.

Mitigations, in order of effectiveness:

1. Keep `CLAUDE_PERMISSION_MODE=auto` (the default) or stricter.
2. Never set `bypassPermissions` on a machine with credentials you care about.
3. Grant "always allow" sparingly — it is per program (`git`, `npm`), and
   `/rules` shows what you have agreed to.
4. Be wary of mirroring projects containing code you did not write.

### What it cannot protect against

- Anyone with access to your unlocked computer — `.env` is a plain text file.
- A local process running as you: it can read the approval port and secret from
  the environment of the bridge process.
- Telegram or your network operator seeing that you use it and when.
- Claude making a mistake you approved.

## Reducing the blast radius

| If you want | Do this |
| --- | --- |
| Reading only, no remote execution | `REPLIES_ENABLED=false`, or run with `--no-replies` |
| Fewer surprises | Keep `CLAUDE_PERMISSION_MODE=auto`, never `bypassPermissions` |
| Limit exposure | Point `WATCH_PROJECTS` at specific folders instead of `ALL` |
| Revoke standing permissions | `/forget all` in Telegram |
| Stop everything now | `bridge.bat stop` |

## Supported versions

The latest release on `main` is the only supported version.

## Reporting a vulnerability

**Please do not open a public issue for a security problem.**

Use GitHub's private vulnerability reporting: go to the **Security** tab of this
repository and choose **Report a vulnerability**. That opens a private thread
visible only to the maintainer.

Please include what you did, what happened, and what you expected. A proof of
concept helps. This is a personal project maintained in spare time — expect a
reply within a couple of weeks, and no bug bounty.
