"""Handling what you type and tap in Telegram.

Every message is checked against TELEGRAM_OWNER_ID first. This bot can run
commands on your PC, so nobody else gets to talk to it - not even other members
of the group.

Which chat a message belongs to comes from the topic it was sent in, and which
project that chat lives in is remembered alongside it, so a reply always runs
in the right folder.
"""
from __future__ import annotations

import html
import logging
from pathlib import Path

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.error import TelegramError
from telegram.ext import (Application, CallbackQueryHandler, CommandHandler,
                          ContextTypes, MessageHandler, filters)

from .approval import ApprovalService, Pending
from .config import Config
from .echo import EchoGuard
from .locator import known_projects, leaf_name, normalise
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
BOLT = "⚡"
REPEAT = "\U0001f501"

ALLOW = "a"
DENY = "d"
ALLOW_ALL = "A"
REMEMBER = "R"


def _esc(text: str) -> str:
    return html.escape(text, quote=False)


class Inbox:
    def __init__(self, config: Config, state: State, runner: Runner,
                 echo: EchoGuard, approval: ApprovalService | None):
        self.config = config
        self.state = state
        self.runner = runner
        self.echo = echo
        self.approval = approval
        self.bot = None         # filled in once the Application is built

    # -- plumbing --------------------------------------------------------
    def _is_owner(self, update: Update) -> bool:
        user = update.effective_user
        if self.config.owner_id is None:
            log.warning("TELEGRAM_OWNER_ID is not set - ignoring everyone")
            return False
        return user is not None and user.id == self.config.owner_id

    async def _reply(self, update: Update, text: str) -> None:
        message = update.effective_message
        if message is None:
            return
        await message.reply_text(text, parse_mode=ParseMode.HTML,
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

        action, _, request_id = query.data.partition("|")
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
        await self._reply(update, f"{WORKING} <i>working...</i>")

        result = await self.runner.run(session_id, prompt, project)

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
            "<b>Commands</b>\n"
            "Just type in a topic to continue that chat.\n\n"
            "/new &lt;text&gt; - start a new chat in this project\n"
            "/c &lt;text&gt; - send a message the long way\n"
            "/stop - cut short whatever is running here\n"
            "/projects - which projects are mirrored\n"
            "/rules - things Claude may always do\n"
            "/forget &lt;key|all&gt; - undo one of those\n"
            "/vscode - link to open this chat in the editor\n"
            "/status - what is going on right now\n"
            "/help - this list"
        ))


def build_application(config: Config, state: State, runner: Runner,
                      echo: EchoGuard, request,
                      approval: ApprovalService | None) -> Application:
    inbox = Inbox(config, state, runner, echo, approval)
    app = (Application.builder()
           .token(config.bot_token)
           .request(request)
           .build())
    inbox.bot = app.bot
    if approval is not None:
        approval.set_notifier(inbox.send_approval)

    app.add_handler(CommandHandler("new", inbox.on_new))
    app.add_handler(CommandHandler("c", inbox.on_say))
    app.add_handler(CommandHandler("stop", inbox.on_stop))
    app.add_handler(CommandHandler("projects", inbox.on_projects))
    app.add_handler(CommandHandler("rules", inbox.on_rules))
    app.add_handler(CommandHandler("forget", inbox.on_forget))
    app.add_handler(CommandHandler("vscode", inbox.on_vscode))
    app.add_handler(CommandHandler("status", inbox.on_status))
    app.add_handler(CommandHandler("help", inbox.on_help))
    app.add_handler(CallbackQueryHandler(inbox.on_button))
    if config.replies_enabled:
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND,
                                       inbox.on_text))
    return app
