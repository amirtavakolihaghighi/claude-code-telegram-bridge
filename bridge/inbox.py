"""Handling what you type and tap in Telegram.

Every message is checked against TELEGRAM_OWNER_ID first. This bot can run
commands on your PC, so nobody else gets to talk to it - not even other members
of the group.

Which chat a message belongs to comes from the topic it was sent in, and which
project that chat lives in is remembered alongside it, so a reply always runs
in the right folder.
"""
from __future__ import annotations

import asyncio
import html
import logging
from pathlib import Path

from io import BytesIO

from telegram import (BotCommand, InlineKeyboardButton, InlineKeyboardMarkup,
                      InputFile, Update)
from telegram.constants import ChatAction, ParseMode
from telegram.error import BadRequest, TelegramError
from telegram.ext import (Application, CallbackQueryHandler, CommandHandler,
                          ContextTypes, MessageHandler, filters)

from datetime import datetime, timedelta, timezone

from .accounts import Accounts, mask_email
from .approval import ApprovalService, Pending
from .config import Config, looks_secret
from .usage import collect, human_tokens
from .echo import EchoGuard
from .export import (read_transcript, render_html, render_markdown,
                     safe_filename)
from .locator import (find_session_files, known_projects, leaf_name,
                      normalise)
from .parser import describe_tool, tool_icon, tool_input
from .runner import Runner
from .state import State
from .vscode import handoff_url, is_project_open

log = logging.getLogger(__name__)

WORKING = "⏳"
WARN = "⚠️"
CROSS = "❌"
SPARK = "✨"
ASK = "❓"
ALLOWED = "✅"
DENIED = "\U0001f6ab"
STOPPED = "\U0001f6d1"
BOX = "\U0001f4e6"
BOLT = "⚡"
REPEAT = "\U0001f501"

ALLOW = "a"
DENY = "d"
ALLOW_ALL = "A"
REMEMBER = "R"
SEND_FILE = "f"
QUESTION = "q"
QUESTION_DONE = "Q"
TICK = "✅"
SEND = "\U0001f4e4"

PHOTO_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
CHART = "\U0001f4ca"
GAUGE = "\U0001f6e0️"
BULLET = "•"
BAR_FULL = "█"
BAR_EMPTY = "░"

# Registered with Telegram so typing "/" offers them, with descriptions.
COMMANDS = [
    BotCommand("new", "Start a new chat in this project"),
    BotCommand("c", "Send a message the long way round"),
    BotCommand("stop", "Cut short whatever is running here"),
    BotCommand("projects", "Which projects exist, and which are mirrored"),
    BotCommand("rules", "Things Claude is always allowed to do"),
    BotCommand("forget", "Undo a standing permission, or all of them"),
    BotCommand("usage", "What your sessions have cost"),
    BotCommand("quota", "How much of your rate limits is used"),
    BotCommand("accounts", "Claude accounts and their remaining quota"),
    BotCommand("switch", "Change which Claude account is in use"),
    BotCommand("file", "Send me a file from this project"),
    BotCommand("archive", "Save this chat as a file, then close the topic"),
    BotCommand("reopen", "Reopen a closed topic so it can continue"),
    BotCommand("vscode", "Link that opens this chat in the editor"),
    BotCommand("status", "Which chat, which project, busy or not"),
    BotCommand("help", "List these commands"),
]


def _esc(text: str) -> str:
    return html.escape(text, quote=False)


MESSAGE_LIMIT = 3900        # Telegram allows 4096; leave room for safety


def _split(text: str) -> list[str]:
    """Break a long answer on line boundaries, keeping any <pre> block whole."""
    if len(text) <= MESSAGE_LIMIT:
        return [text]

    parts: list[str] = []
    current = ""
    for line in text.split("\n"):
        candidate = f"{current}\n{line}" if current else line
        if len(candidate) > MESSAGE_LIMIT and current:
            parts.append(current)
            current = line
        else:
            current = candidate
    if current:
        parts.append(current)

    # A split inside a <pre> would leave an unbalanced tag, so close and reopen.
    balanced = []
    for part in parts:
        if part.count("<pre>") > part.count("</pre>"):
            part += "</pre>"
        elif part.count("</pre>") > part.count("<pre>"):
            part = "<pre>" + part
        balanced.append(part)
    return balanced


def chat_path(config: Config, session_id: str) -> Path | None:
    for session in find_session_files(config.claude_home, config.watch_projects):
        if session.session_id == session_id:
            return session.path
    return None


class Inbox:
    def __init__(self, config: Config, state: State, runner: Runner,
                 echo: EchoGuard, approval: ApprovalService | None,
                 sink=None):
        self.config = config
        self.state = state
        self.runner = runner
        self.echo = echo
        self.approval = approval
        self.sink = sink        # for closing and reopening topics
        self.accounts = Accounts(config.cswap_cli)
        self.bot = None         # filled in once the Application is built

    # -- plumbing --------------------------------------------------------
    def _is_owner(self, update: Update) -> bool:
        user = update.effective_user
        if self.config.owner_id is None:
            log.warning("TELEGRAM_OWNER_ID is not set - ignoring everyone")
            return False
        return user is not None and user.id == self.config.owner_id

    async def _reply(self, update: Update, text: str) -> None:
        """Answer in the same topic, split if it is over Telegram's limit.

        A long answer - /usage with many projects, /accounts with several
        accounts - would otherwise be rejected outright.
        """
        message = update.effective_message
        if message is None:
            return
        for part in _split(text):
            await message.reply_text(part, parse_mode=ParseMode.HTML,
                                     message_thread_id=message.message_thread_id,
                                     disable_web_page_preview=True)

    def _session_for(self, update: Update) -> str | None:
        message = update.effective_message
        if message is None or message.message_thread_id is None:
            return None
        return self.state.session_for_thread(message.message_thread_id)

    def _project_for(self, session_id: str | None) -> Path | None:
        if session_id:
            remembered = self.state.project_for_session(session_id)
            if remembered:
                return Path(remembered)
        return self.config.default_project

    # -- permission questions --------------------------------------------
    async def send_approval(self, pending: Pending):
        """Post Allow / Deny buttons into the topic this chat belongs to."""
        if self.bot is None or self.config.group_id is None:
            raise RuntimeError("not connected to Telegram yet")

        thread_id, _ = self.state.get_topic(pending.session_id)
        name = pending.tool_name
        summary = describe_tool(name, pending.tool_input)
        detail = tool_input(name, pending.tool_input)
        _, rule_label = pending.rule

        lines = [f"{ASK} <b>Can Claude do this?</b>",
                 f"{tool_icon(name)} <b>{_esc(name)}</b> · <i>{_esc(summary)}</i>"]
        if detail:
            lines.append(f"<pre>{_esc(detail)}</pre>")

        request = pending.request_id
        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton(f"{ALLOWED} Allow",
                                     callback_data=f"{ALLOW}|{request}"),
                InlineKeyboardButton(f"{DENIED} Deny",
                                     callback_data=f"{DENY}|{request}"),
            ],
            [InlineKeyboardButton(f"{BOLT} Allow everything in this turn",
                                  callback_data=f"{ALLOW_ALL}|{request}")],
            [InlineKeyboardButton(f"{REPEAT} Always allow {rule_label}",
                                  callback_data=f"{REMEMBER}|{request}")],
        ])

        return await self.bot.send_message(
            chat_id=self.config.group_id,
            message_thread_id=thread_id,
            text="\n".join(lines),
            parse_mode=ParseMode.HTML,
            reply_markup=keyboard,
            disable_notification=False,     # this one should reach you
            disable_web_page_preview=True,
        )

    # -- questions Claude asks -------------------------------------------
    def _question_markup(self, asked) -> InlineKeyboardMarkup:
        rows = []
        for index, option in enumerate(asked.options):
            ticked = index in asked.picked
            mark = f"{TICK} " if ticked else ""
            rows.append([InlineKeyboardButton(
                f"{mark}{option}"[:64],
                callback_data=f"{QUESTION}|{asked.request_id}|{index}")])
        if asked.allow_multiple:
            count = len(asked.picked)
            # Always reads as a send button: labelling it "pick one first" hid
            # what it was for, so the selection looked impossible to confirm.
            label = f"{SEND} Send answer" if count == 1 else \
                f"{SEND} Send {count} answers" if count else f"{SEND} Send"
            rows.append([InlineKeyboardButton(
                label, callback_data=f"{QUESTION_DONE}|{asked.request_id}|0")])
        return InlineKeyboardMarkup(rows)

    async def send_question(self, asked) -> None:
        """Post a question from Claude, with a button for each answer."""
        if self.bot is None or self.config.group_id is None:
            raise RuntimeError("not connected to Telegram yet")
        thread_id, _ = self.state.get_topic(asked.session_id)
        hint = ("\n\n<i>Tap the ones that apply, then Send.</i>"
                if asked.allow_multiple else "")
        message = await self.bot.send_message(
            chat_id=self.config.group_id, message_thread_id=thread_id,
            text=f"{ASK} <b>Claude is asking</b>\n\n{_esc(asked.question)}{hint}",
            parse_mode=ParseMode.HTML, disable_notification=False,
            reply_markup=self._question_markup(asked))
        asked.message_id = message.message_id
        return message

    async def close_question(self, asked) -> None:
        """Nobody answered: take the buttons away and say what happened."""
        if self.bot is None or asked.message_id is None:
            return
        try:
            await self.bot.edit_message_text(
                chat_id=self.config.group_id, message_id=asked.message_id,
                text=f"{ASK} <s>{_esc(asked.question)}</s>\n\n"
                     f"{WARN} <i>No answer, so Claude carried on without "
                     f"one.</i>",
                parse_mode=ParseMode.HTML)
        except TelegramError:
            pass

    async def _toast(self, query, text: str = "") -> None:
        """Acknowledge a tap. Best effort: never let it break what follows."""
        try:
            await query.answer(text[:200], read_timeout=8, connect_timeout=5)
        except TelegramError as exc:
            log.info("could not acknowledge a tap: %s", exc)

    async def _redraw(self, query, asked) -> bool:
        """Redraw the keyboard, retrying once past a wobbly connection."""
        for attempt in range(2):
            try:
                await query.edit_message_reply_markup(
                    reply_markup=self._question_markup(asked))
                return True
            except BadRequest as exc:
                # Unchanged or gone; retrying cannot help, but say so - silence
                # here made a missing tick impossible to diagnose.
                log.info("keyboard not redrawn: %s", exc)
                return False
            except TelegramError as exc:
                log.info("redraw attempt %d failed: %s", attempt + 1, exc)
                await asyncio.sleep(1)
        return False

    async def _on_question_button(self, query, action: str, rest: str) -> None:
        request_id, _, raw_index = rest.partition("|")
        index = int(raw_index) if raw_index.isdigit() else 0
        approval = self.approval

        if approval is None:
            await query.answer()
            return

        asked = approval.questions.get(request_id)
        if asked is None:
            # Expired or already answered. Take the buttons away first, so it
            # stops looking like something that can be tapped.
            try:
                await query.edit_message_reply_markup(reply_markup=None)
            except TelegramError:
                pass
            await self._toast(query, "That question expired - Claude moved on.")
            return

        # Multiple choice: tick in place and wait for the send button.
        if asked.allow_multiple and action == QUESTION:
            approval.toggle(request_id, index)
            ticked = index in asked.picked
            option = asked.options[index] if index < len(asked.options) else ""
            # The tick is what you are waiting to see, so draw it first. A
            # callback must be acknowledged within about fifteen seconds, and on
            # a slow connection that call can fail - which used to take the
            # redraw down with it and leave the buttons looking inert.
            await self._redraw(query, asked)
            await self._toast(query,
                              f"{'Added' if ticked else 'Removed'}: {option}")
            return

        indexes = sorted(asked.picked) if action == QUESTION_DONE else [index]
        if not indexes:
            await self._toast(query, "Tap an option first, then Send.")
            return

        answered = approval.answer(request_id, indexes)
        if answered is None:
            await self._toast(query, "Too late - Claude moved on.")
            return
        chosen = [answered.options[i] for i in indexes
                  if 0 <= i < len(answered.options)]
        # Claude already has the answer; this only shows what was sent. Telegram
        # does not always attach the message, so do not assume it is there.
        original = getattr(query.message, "text_html", None)
        if original is not None:
            try:
                await query.edit_message_text(
                    text=f"{original}\n\n{ALLOWED} <i>"
                         f"{_esc(', '.join(chosen))}</i>",
                    parse_mode=ParseMode.HTML)
            except TelegramError as exc:
                log.info("could not mark the question answered: %s", exc)
        await self._toast(query, "Sent")

    async def on_button(self, update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        if query is None:
            return
        if not self._is_owner(update):
            await query.answer("Not for you.", show_alert=True)
            return
        if self.approval is None or not query.data or "|" not in query.data:
            await query.answer()
            return

        log.info("button tapped: %s", query.data)
        action, _, request_id = query.data.partition("|")

        if action in (QUESTION, QUESTION_DONE):
            await self._on_question_button(query, action, request_id)
            return

        if action == SEND_FILE:
            await query.answer("Sending...")
            path = self.state.attachment_path(int(request_id)) \
                if request_id.isdigit() else None
            await self._send_file(query, Path(path) if path else None)
            return

        allow = action in (ALLOW, ALLOW_ALL, REMEMBER)
        handled = self.approval.resolve(request_id, allow=allow,
                                        always=action == ALLOW_ALL,
                                        remember=action == REMEMBER)

        if not handled:
            await query.answer("Too late - that question already expired.")
            verdict = f"{WARN} <i>expired</i>"
        else:
            await query.answer("Allowed" if allow else "Denied")
            verdict = {
                ALLOW: f"{ALLOWED} <i>allowed</i>",
                DENY: f"{DENIED} <i>denied</i>",
                ALLOW_ALL: f"{BOLT} <i>allowed - and the rest of this turn</i>",
                REMEMBER: f"{REPEAT} <i>allowed - and always from now on</i>",
            }.get(action, "")

        try:
            await query.edit_message_text(
                text=f"{query.message.text_html}\n\n{verdict}",
                parse_mode=ParseMode.HTML,
            )
        except TelegramError:
            pass

    async def _keep_typing(self, chat_id, thread_id, stop: asyncio.Event) -> None:
        """Telegram's typing indicator lasts about five seconds, so refresh it
        until the turn finishes."""
        if self.bot is None or chat_id is None:
            return
        while not stop.is_set():
            try:
                await self.bot.send_chat_action(chat_id=chat_id,
                                                action=ChatAction.TYPING,
                                                message_thread_id=thread_id)
            except TelegramError:
                return          # not worth retrying; the answer still arrives
            try:
                await asyncio.wait_for(stop.wait(), timeout=4.0)
            except asyncio.TimeoutError:
                continue

    # -- sending files ---------------------------------------------------
    async def _send_file(self, source, path: Path | None,
                         thread_id: int | None = None) -> None:
        """Put a file into the topic: photos inline, everything else as a
        document."""
        message = getattr(source, "message", None) or source.effective_message
        thread_id = thread_id if thread_id is not None else \
            (message.message_thread_id if message else None)

        async def say(text: str) -> None:
            await self.bot.send_message(chat_id=self.config.group_id,
                                        message_thread_id=thread_id, text=text,
                                        parse_mode=ParseMode.HTML)

        if path is None:
            await say(f"{WARN} I no longer know which file that was.")
            return
        if not path.is_file():
            await say(f"{WARN} <code>{_esc(path.name)}</code> is not there any "
                      f"more.")
            return

        size = path.stat().st_size
        if size > self.config.max_attach_mb * 1e6:
            await say(f"{WARN} <code>{_esc(path.name)}</code> is "
                      f"{size / 1e6:.1f} MB, over the "
                      f"{self.config.max_attach_mb:.0f} MB limit.")
            return

        data = InputFile(BytesIO(path.read_bytes()), filename=path.name)
        caption = f"<code>{_esc(str(path))}</code>"
        try:
            if path.suffix.lower() in PHOTO_SUFFIXES:
                await self.bot.send_photo(chat_id=self.config.group_id,
                                          message_thread_id=thread_id,
                                          photo=data, caption=caption,
                                          parse_mode=ParseMode.HTML)
            else:
                await self.bot.send_document(chat_id=self.config.group_id,
                                             message_thread_id=thread_id,
                                             document=data, caption=caption,
                                             parse_mode=ParseMode.HTML)
        except TelegramError as exc:
            await say(f"{CROSS} Telegram refused it: {_esc(str(exc))}")

    async def on_file(self, update: Update,
                      context: ContextTypes.DEFAULT_TYPE) -> None:
        """/file <path> [force] - send any file, resolved against this project."""
        if not self._is_owner(update):
            return
        args = list(context.args or [])
        force = bool(args) and args[-1].lower() in ("force", "!")
        if force:
            args = args[:-1]
        wanted = " ".join(args).strip().strip('"')
        if not wanted:
            await self._reply(update, "Say which file: "
                                      "<code>/file README.md</code>")
            return

        path = Path(wanted)
        if not path.is_absolute():
            project = self._project_for(self._session_for(update))
            if project is None:
                await self._reply(update, "Send this from a chat's topic, or "
                                          "give the full path.")
                return
            path = project / path

        if looks_secret(path) and not force:
            await self._reply(
                update,
                f"{WARN} <code>{_esc(path.name)}</code> looks like it holds "
                f"credentials, and Telegram would keep a copy. Send "
                f"<code>/file {_esc(wanted)} force</code> if you mean it.")
            return

        await self._send_file(update, path)

    # -- the main path ---------------------------------------------------
    async def _send_prompt(self, update: Update, session_id: str | None,
                           prompt: str, project: Path | None) -> None:
        if not prompt.strip():
            await self._reply(update, "Nothing to send.")
            return
        if project is None:
            await self._reply(update, "I don't know which project to use. Send "
                                      "this from inside a chat's topic, or set "
                                      "WATCH_PROJECTS to a single folder.")
            return

        if session_id and self.runner.is_busy(session_id):
            await self._reply(update, f"{WARN} That chat is still working on the "
                                      f"previous message. Send /stop to cut it short.")
            return

        if is_project_open(self.config.claude_home, project):
            await self._reply(
                update,
                f"{WARN} <b>{_esc(leaf_name(project))}</b> is open in VS Code. If this "
                f"chat is open there too, close its tab first - otherwise the two "
                f"can write over each other."
            )

        self.echo.remember(session_id or "pending", prompt)

        # "typing..." rather than a message saying so: one less line of clutter
        # in every conversation, and it disappears on its own.
        message = update.effective_message
        stop_typing = asyncio.Event()
        typing = asyncio.create_task(
            self._keep_typing(message.chat_id if message else None,
                              message.message_thread_id if message else None,
                              stop_typing))
        try:
            result = await self.runner.run(session_id, prompt, project)
        finally:
            stop_typing.set()
            await typing

        if session_id is None and result.session_id:
            self.echo.rename("pending", result.session_id)
            self.state.set_topic(result.session_id, None, None, str(project))

        if result.stopped:
            await self._reply(update, f"{STOPPED} <i>stopped</i>")
        elif not result.ok:
            await self._reply(update, f"{CROSS} <b>Failed</b>\n"
                                      f"<pre>{_esc(result.error)}</pre>")

    # -- handlers --------------------------------------------------------
    async def on_text(self, update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._is_owner(update):
            return
        message = update.effective_message
        if message is None or not message.text:
            return
        session_id = self._session_for(update)
        if session_id is None:
            await self._reply(update, "I don't know which chat this topic is for. "
                                      "Use /new to start one.")
            return
        await self._send_prompt(update, session_id, message.text,
                                self._project_for(session_id))

    async def on_say(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """/c <text> - handy if a message ever gets swallowed."""
        if not self._is_owner(update):
            return
        session_id = self._session_for(update)
        if session_id is None:
            await self._reply(update, "I don't know which chat this topic is for. "
                                      "Use /new to start one.")
            return
        await self._send_prompt(update, session_id, " ".join(context.args or []),
                                self._project_for(session_id))

    async def on_new(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """/new <text> - start a fresh chat in this topic's project."""
        if not self._is_owner(update):
            return
        prompt = " ".join(context.args or [])
        if not prompt:
            await self._reply(update, "Say what to start with: <code>/new fix the "
                                      "login bug</code>")
            return
        project = self._project_for(self._session_for(update))
        if project is None:
            await self._reply(update, "Send /new from inside an existing chat's "
                                      "topic, so I know which project you mean. "
                                      "/projects shows what I can see.")
            return
        await self._reply(update, f"{SPARK} Starting a new chat in "
                                  f"<b>{_esc(leaf_name(project))}</b> - it will appear "
                                  f"as its own topic.")
        await self._send_prompt(update, None, prompt, project)

    async def on_stop(self, update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._is_owner(update):
            return
        session_id = self._session_for(update)
        if session_id is None:
            await self._reply(update, "This topic isn't linked to a chat.")
            return
        if self.runner.stop(session_id):
            await self._reply(update, f"{STOPPED} Stopping...")
        else:
            await self._reply(update, "Nothing is running in this chat.")

    async def on_projects(self, update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._is_owner(update):
            return
        projects = known_projects(self.config.claude_home)
        if not projects:
            await self._reply(update, "Claude Code has no chats on this PC yet.")
            return
        watched = {normalise(p) for p in self.config.watch_projects}
        lines = ["<b>Projects on this PC</b>"]
        for key, path in sorted(projects.items(), key=lambda kv: leaf_name(kv[1]).lower()):
            mark = "✅" if (self.config.watches_everything or key in watched) \
                else "—"
            lines.append(f"{mark} {_esc(leaf_name(path))}  <code>{_esc(str(path))}</code>")
        lines.append("")
        lines.append("✅ = mirrored. Change WATCH_PROJECTS in .env to adjust.")
        await self._reply(update, "\n".join(lines))

    async def on_rules(self, update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._is_owner(update):
            return
        rules = self.state.list_allow_rules()
        if not rules:
            await self._reply(update, "No standing permissions. Everything asks.")
            return
        lines = ["<b>Always allowed</b>"]
        lines += [f"• {_esc(label)}  <code>{_esc(key)}</code>"
                  for key, label in rules]
        lines.append("")
        lines.append("Remove one with <code>/forget &lt;key&gt;</code>, "
                     "or all with <code>/forget all</code>.")
        await self._reply(update, "\n".join(lines))

    async def on_forget(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._is_owner(update):
            return
        target = " ".join(context.args or []).strip()
        if not target:
            await self._reply(update, "Say what to forget: <code>/forget Bash:git"
                                      "</code> or <code>/forget all</code>")
            return
        if target.lower() == "all":
            count = self.state.clear_allow_rules()
            await self._reply(update, f"Forgot {count} standing permission(s).")
            return
        if self.state.drop_allow_rule(target):
            await self._reply(update, f"Forgot <code>{_esc(target)}</code>. "
                                      f"It will ask again.")
        else:
            await self._reply(update, "No such rule. /rules shows the list.")

    async def on_archive(self, update: Update,
                         context: ContextTypes.DEFAULT_TYPE) -> None:
        """/archive [md|html|both] - post the chat as files, then close it."""
        if not self._is_owner(update):
            return
        wanted = (context.args[0].lower() if context.args
                  else self.config.archive_format)
        if wanted not in ("md", "html", "both"):
            await self._reply(update, "Say <code>/archive md</code>, "
                                      "<code>/archive html</code> or "
                                      "<code>/archive both</code>.")
            return
        session_id = self._session_for(update)
        if session_id is None:
            await self._reply(update, "This topic isn't linked to a chat.")
            return
        if self.sink is None:
            await self._reply(update, "Not connected to Telegram properly.")
            return
        if self.runner.is_busy(session_id):
            await self._reply(update, f"{WARN} Still working. Try again when "
                                      f"it has finished, or send /stop.")
            return

        path = chat_path(self.config, session_id)
        if path is None:
            await self._reply(update, f"{WARN} The chat file is gone, so there "
                                      f"is nothing left to export. Telegram is "
                                      f"now the only copy.")
            return

        _, title = self.state.get_topic(session_id)
        project = self.state.project_for_session(session_id)
        try:
            script = read_transcript(path, title, project)
        except RuntimeError as exc:
            await self._reply(update, f"{CROSS} {_esc(str(exc))}")
            return

        builders = {"md": (render_markdown, ".md"),
                    "html": (render_html, ".html")}
        chosen = ["md", "html"] if wanted == "both" else [wanted]

        message = update.effective_message
        thread_id = message.message_thread_id if message else None
        sizes = []
        for index, key in enumerate(chosen):
            render, suffix = builders[key]
            text = render(script)
            sizes.append(f"{suffix.lstrip('.')} {len(text.encode()) // 1024} KB")
            document = BytesIO(text.encode("utf-8"))
            document.name = safe_filename(title or "", session_id, suffix)
            last = index == len(chosen) - 1
            await self.bot.send_document(
                chat_id=self.config.group_id,
                message_thread_id=thread_id,
                document=InputFile(document, filename=document.name),
                caption=(f"{BOX} <b>Archived</b> · {', '.join(sizes)}\n"
                         f"<i>The topic is now closed. Anything new in this "
                         f"chat reopens it, or use /reopen.</i>")
                        if last else None,
                parse_mode=ParseMode.HTML if last else None,
            )
            await asyncio.sleep(1)

        if await self.sink.close_topic(session_id):
            log.info("archived and closed %s", session_id[:8])

    async def on_reopen(self, update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        """/reopen - bring a closed topic back so it can be used again."""
        if not self._is_owner(update):
            return
        session_id = self._session_for(update)
        if session_id is None or self.sink is None:
            await self._reply(update, "This topic isn't linked to a chat.")
            return
        if await self.sink.reopen_topic(session_id):
            await self._reply(update, "Open again. Carry on.")
        else:
            await self._reply(update, "It was already open.")

    # -- usage, quota and accounts ---------------------------------------
    async def on_usage(self, update: Update,
                       context: ContextTypes.DEFAULT_TYPE) -> None:
        """/usage [all] - what your sessions have cost, from the chat files."""
        if not self._is_owner(update):
            return
        everything = bool(context.args) and context.args[0].lower() == "all"
        projects = None if everything else self.config.watch_projects
        report = await asyncio.to_thread(collect, self.config.claude_home,
                                         projects)
        if not report.sessions:
            await self._reply(update, "No chats to measure yet.")
            return

        today = datetime.now(timezone.utc).date()
        lines = [
            f"{CHART} <b>Usage</b> · {len(report.sessions)} chats",
            "",
            f"{BULLET} <b>Total</b> — ${report.cost:.2f}, "
            f"{human_tokens(report.tokens)} tokens",
            f"{BULLET} <b>From cache</b> — {report.cache_share:.0f}% "
            f"(roughly a tenth of the price)",
            f"{BULLET} <b>Today</b> — ${report.cost_since(today):.2f}",
            f"{BULLET} <b>Last 7 days</b> — "
            f"${report.cost_since(today - timedelta(days=6)):.2f}",
        ]

        lines += ["", "<b>By project</b>"]
        unpriced = False
        for name, cost, tokens in report.by_project()[:8]:
            note = ""
            if cost == 0 and tokens:
                note, unpriced = " (cost not recorded)", True
            lines.append(f"{BULLET} <b>{_esc(name)}</b> — ${cost:.2f}, "
                         f"{human_tokens(tokens)}{note}")

        daily = report.tokens_by_day(7)
        if daily:
            lines += ["", "<b>Tokens per day</b>",
                      "<pre>" + "\n".join(
                          f"{when:%a %d %b}  {human_tokens(tokens):>8}"
                          for when, tokens in daily) + "</pre>"]

        lines += ["", "<b>Most expensive chats</b>"]
        for session in report.top_sessions(5):
            label = session.title or session.session_id[:8]
            lines.append(f"{BULLET} ${session.cost:.2f} — {_esc(label[:46])}")

        footnotes = ["Cost is recorded per chat, so daily figures place a chat "
                     "on the day it was last active. Token counts are exact."]
        if unpriced:
            footnotes.append("Chats started before Claude Code recorded cost "
                             "show $0.00 — they were not free.")
        if not everything:
            footnotes.append("This covers mirrored projects only; "
                             "<code>/usage all</code> covers every project.")
        lines += ["", "<i>" + " ".join(footnotes) + "</i>"]

        await self._reply(update, "\n".join(lines))

    def _describe(self, account) -> str:
        shown = mask_email(account.email, self.config.show_emails)
        name = f"<b>{account.number}</b>"
        if shown:
            name += f" · <code>{_esc(shown)}</code>"
        if account.active:
            name += " ← <b>active</b>"
        lines = [f"{BULLET} {name}"]
        if account.needs_login:
            lines.append("   ⚠️ needs logging in again")
        for label, window in (("5h", account.five_hour),
                              ("7d", account.seven_day)):
            if window is None:
                continue
            bar = BAR_FULL * round(window.pct / 10) + \
                BAR_EMPTY * (10 - round(window.pct / 10))
            resets = f" resets {window.countdown}" if window.countdown else ""
            lines.append(f"   <code>{label} {bar} {window.pct:4.0f}%</code>"
                         f"{resets}")
        if account.stale:
            lines.append("   <i>figures are from its last good reading</i>")
        return "\n".join(lines)

    async def on_quota(self, update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        """/quota - how much of the rate-limit windows is used."""
        if not self._is_owner(update):
            return
        if not self.accounts.available:
            await self._reply(update, f"{WARN} cswap is not installed, so rate "
                                      f"limits are not visible. "
                                      f"<code>/usage</code> still works.")
            return
        account = await self.accounts.quota()
        if account is None:
            await self._reply(update, f"{CROSS} cswap did not report a status.")
            return
        ahead = account.seven_day.ahead_of_pace if account.seven_day else None
        pace = ""
        if ahead is not None:
            pace = ("\n\n<i>Ahead of pace for the week.</i>" if ahead
                    else "\n\n<i>On pace to last the week.</i>")
        await self._reply(update, f"{GAUGE} <b>Quota</b>\n\n"
                                  f"{self._describe(account)}{pace}")

    async def on_accounts(self, update: Update,
                          _: ContextTypes.DEFAULT_TYPE) -> None:
        """/accounts - every managed account and its remaining quota."""
        if not self._is_owner(update):
            return
        if not self.accounts.available:
            await self._reply(update, f"{WARN} cswap is not installed. Set "
                                      f"<code>CSWAP_CLI</code> in .env if it is "
                                      f"somewhere unusual.")
            return
        accounts = await self.accounts.all()
        if not accounts:
            await self._reply(update, f"{CROSS} cswap listed no accounts.")
            return
        body = "\n\n".join(self._describe(a) for a in accounts)
        await self._reply(update, f"{GAUGE} <b>Accounts</b>\n\n{body}\n\n"
                                  f"<i>Switch with "
                                  f"<code>/switch &lt;number&gt;</code>.</i>")

    async def on_switch(self, update: Update,
                        context: ContextTypes.DEFAULT_TYPE) -> None:
        """/switch <number|email> - change which account Claude uses."""
        if not self._is_owner(update):
            return
        if not self.accounts.available:
            await self._reply(update, f"{WARN} cswap is not installed.")
            return
        target = (context.args[0] if context.args else "").strip()
        if not target:
            await self._reply(update, "Say which one: <code>/switch 2</code>. "
                                      "<code>/accounts</code> lists them.")
            return

        # Credentials change underneath a running turn, so refuse rather than
        # half-apply it.
        busy = [sid for sid, _, _, _ in self.state.all_topics()
                if self.runner.is_busy(sid)]
        if busy:
            await self._reply(update, f"{WARN} A chat is still working. Wait for "
                                      f"it, or send /stop, then switch.")
            return

        ok, message = await self.accounts.switch(target)
        if not ok:
            await self._reply(update, f"{CROSS} Could not switch: "
                                      f"<pre>{_esc(message[:400])}</pre>")
            return
        account = await self.accounts.quota()
        detail = self._describe(account) if account else ""
        await self._reply(update, f"{ALLOWED} <b>Switched</b>\n\n{detail}")

    async def on_vscode(self, update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        """/vscode - a link that opens this chat in the editor."""
        if not self._is_owner(update):
            return
        session_id = self._session_for(update)
        if session_id is None:
            await self._reply(update, "This topic isn't linked to a chat.")
            return
        url = handoff_url(session_id)
        await self._reply(update, "Open this chat in VS Code - paste into your "
                                  f"browser or Run box:\n<code>{_esc(url)}</code>")

    async def on_status(self, update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._is_owner(update):
            return
        session_id = self._session_for(update)
        project = self._project_for(session_id)
        waiting = len(self.approval.pending) if self.approval else 0
        scope = ("every project" if self.config.watches_everything
                 else f"{len(self.config.watch_projects)} project(s)")
        lines = [
            f"<b>Watching:</b> {scope}",
            f"<b>This chat's project:</b> "
            f"<code>{_esc(str(project) if project else 'unknown')}</code>",
            f"<b>Chat:</b> <code>{_esc(session_id or 'not linked')}</code>",
            f"<b>Busy:</b> "
            f"{'yes' if session_id and self.runner.is_busy(session_id) else 'no'}",
            f"<b>Open in VS Code:</b> "
            f"{'yes' if project and is_project_open(self.config.claude_home, project) else 'no'}",
            f"<b>Permission mode:</b> {_esc(self.config.permission_mode)}",
            f"<b>Standing permissions:</b> {len(self.state.list_allow_rules())}",
            f"<b>Waiting on you:</b> {waiting}",
        ]
        await self._reply(update, "\n".join(lines))

    async def on_help(self, update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._is_owner(update):
            return
        await self._reply(update, (
            "<b>Just type in a topic</b> to continue that chat.\n\n"

            "<b>Chatting</b>\n"
            "/new &lt;text&gt; — start a new chat in this project\n"
            "/c &lt;text&gt; — send a message the long way round\n"
            "/stop — cut short whatever is running here\n\n"

            "<b>Files</b>\n"
            "/file &lt;path&gt; — send me a file from this project\n"
            "/archive [md|html|both] — save this chat, then close the topic\n"
            "/reopen — reopen a closed topic\n\n"

            "<b>Spending</b>\n"
            "/usage [all] — what your chats have cost\n"
            "/quota — how much of your rate limits is used\n"
            "/accounts — accounts and their remaining quota\n"
            "/switch &lt;n&gt; — change which account Claude uses\n\n"

            "<b>Everything else</b>\n"
            "/projects — which projects are mirrored\n"
            "/rules — things Claude may always do\n"
            "/forget &lt;key|all&gt; — undo one of those\n"
            "/vscode — link to open this chat in the editor\n"
            "/status — what is going on right now\n"
            "/help — this list\n\n"

            "<i>Claude can also ask you things directly — those arrive as "
            "buttons to tap.</i>"
        ))


def build_application(config: Config, state: State, runner: Runner,
                      echo: EchoGuard, request,
                      approval: ApprovalService | None) -> Application:
    inbox = Inbox(config, state, runner, echo, approval)
    app = (Application.builder()
           .token(config.bot_token)
           .request(request)
           # Without this, updates are handled strictly one at a time. A message
           # handler runs for as long as Claude's whole turn, so a button tapped
           # during that turn would wait in the queue until it finished - and a
           # question Claude asks mid-turn could never be answered at all, since
           # the answer arrives as a tap. Telegram gives up on an unanswered tap
           # after a few seconds, which is exactly what it looked like.
           .concurrent_updates(True)
           .build())
    inbox.bot = app.bot
    # The sink needs app.bot, so it is built after this and attached here.
    app.bot_data["inbox"] = inbox
    if approval is not None:
        approval.set_notifier(inbox.send_approval)
        approval.set_question_notifier(inbox.send_question)
        approval.set_question_closer(inbox.close_question)

    app.add_handler(CommandHandler("new", inbox.on_new))
    app.add_handler(CommandHandler("c", inbox.on_say))
    app.add_handler(CommandHandler("stop", inbox.on_stop))
    app.add_handler(CommandHandler("projects", inbox.on_projects))
    app.add_handler(CommandHandler("rules", inbox.on_rules))
    app.add_handler(CommandHandler("forget", inbox.on_forget))
    app.add_handler(CommandHandler("usage", inbox.on_usage))
    app.add_handler(CommandHandler("quota", inbox.on_quota))
    app.add_handler(CommandHandler("accounts", inbox.on_accounts))
    app.add_handler(CommandHandler("switch", inbox.on_switch))
    app.add_handler(CommandHandler("file", inbox.on_file))
    app.add_handler(CommandHandler("archive", inbox.on_archive))
    app.add_handler(CommandHandler("reopen", inbox.on_reopen))
    app.add_handler(CommandHandler("vscode", inbox.on_vscode))
    app.add_handler(CommandHandler("status", inbox.on_status))
    app.add_handler(CommandHandler("help", inbox.on_help))
    app.add_handler(CallbackQueryHandler(inbox.on_button))

    async def on_error(update, context) -> None:
        """Without this, a failed update prints a bare traceback and PTB warns
        that nobody is listening."""
        log.warning("handling an update failed: %s", context.error)

    app.add_error_handler(on_error)
    if config.replies_enabled:
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND,
                                       inbox.on_text))
    return app
