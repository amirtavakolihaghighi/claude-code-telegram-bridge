"""Run the bridge.

  python -m bridge --dry-run --backfill     show what would be posted
  python -m bridge --catch-up               send everything never sent, then follow
  python -m bridge --backfill               post the whole history, then follow
  python -m bridge                          follow, and accept replies
  python -m bridge --no-replies             follow only, ignore what I type
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path

from telegram import Update
from telegram.error import (BadRequest, Conflict, InvalidToken, NetworkError,
                            TelegramError)
from telegram.request import HTTPXRequest

from .approval import ApprovalService
from .config import load_config
from .echo import EchoGuard
from .inbox import COMMANDS, build_application
from .instance import InstanceLock
from .mirror import Mirror
from .runner import Runner
from .sinks import ConsoleSink, TelegramSink
from .state import State

# Pause before starting again after an unexpected error, so a fault that
# repeats does not spin.
RESTART_DELAY = 30.0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="bridge", description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true",
                        help="print to the console instead of Telegram")
    parser.add_argument("--backfill", action="store_true",
                        help="include everything already in the chat files")
    parser.add_argument("--catch-up", action="store_true",
                        help="also send everything never sent, oldest first: "
                             "chats followed part-way and chats never seen")
    parser.add_argument("--once", action="store_true",
                        help="do a single pass and exit")
    parser.add_argument("--no-replies", action="store_true",
                        help="mirror only; ignore messages typed in Telegram")
    parser.add_argument("--import-all", action="store_true",
                        help="give every existing chat a topic straight away")
    parser.add_argument("--restyle-topics", action="store_true",
                        help="give existing topics their project's icon, then exit")
    parser.add_argument("--log-file", metavar="PATH",
                        help="also write the log here (use when running hidden)")
    parser.add_argument("-v", "--verbose", action="store_true")
    return parser.parse_args()


def _setup_logging(verbose: bool, log_file: str | None) -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    level = logging.DEBUG if verbose else logging.INFO
    formatter = logging.Formatter("%(asctime)s  %(levelname)-7s %(message)s",
                                  datefmt="%Y-%m-%d %H:%M:%S")
    root = logging.getLogger()
    root.setLevel(level)

    console = logging.StreamHandler()
    console.setFormatter(formatter)
    root.addHandler(console)

    if log_file:
        # Started from Task Scheduler there is no window to read, so keep a
        # rolling file instead. Two spare copies is plenty to find a crash.
        path = Path(log_file)
        path.parent.mkdir(parents=True, exist_ok=True)
        rotating = RotatingFileHandler(path, maxBytes=2_000_000, backupCount=2,
                                       encoding="utf-8")
        rotating.setFormatter(formatter)
        root.addHandler(rotating)

    for noisy in ("httpx", "telegram.ext.Application", "telegram.ext.Updater"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


async def run_dry(args) -> int:
    config = load_config(require_token=False)
    state = State(config.state_db.with_name("state-dryrun.db"))
    mirror = Mirror(config, ConsoleSink(), state, backfill=args.backfill,
                    import_all=args.import_all, catch_up=args.catch_up)
    try:
        if args.once:
            logging.info("posted %d message(s)", await mirror.run(once=True))
        else:
            await mirror.run()
    finally:
        state.close()
    return 0


async def _connect(app) -> None:
    """Wait for Telegram rather than give up on it.

    At sign-in the network - or a VPN, if Telegram is reached through one - is
    often not up yet. Giving up then used to end the bridge before it logged a word,
    and Task Scheduler does not restart a program that started and then
    failed, so it stayed off until someone noticed.
    """
    delay = 5.0
    while True:
        try:
            await app.initialize()
            return
        except BadRequest:
            raise
        except NetworkError as exc:     # timeouts included
            logging.warning("cannot reach Telegram yet (%s); trying again in "
                            "%.0fs", exc, delay)
            await asyncio.sleep(delay)
            delay = min(delay * 2, 60.0)


async def _restyle(sink, state, style: str) -> None:
    try:
        changed, unreachable = await sink.restyle_topics()
    except Exception:
        logging.exception("could not restyle the topics")
        return
    logging.info("gave %d topic(s) the %s icon", changed,
                 "default" if style == "default" else "project")
    if not unreachable:
        state.set_topic_style(style)    # otherwise tried again next start


TWO_COPIES = ("⚠️ Two copies of this bot are running at once, most likely one "
              "on each computer. Replies can only reach one of them, so turn "
              "one off: bridge.bat, option 3.")


def polling_trouble(app, group_id: int):
    """Say what went wrong fetching messages - once in a while, not on every
    retry. Left to itself the library logs a full traceback for each attempt,
    which buried everything else in the log during an outage."""
    said: dict[str, float] = {}

    def due(what: str, every: float) -> bool:
        now = time.monotonic()
        if now - said.get(what, -every) < every:
            return False
        said[what] = now
        return True

    async def warn() -> None:
        try:
            await app.bot.send_message(chat_id=group_id, text=TWO_COPIES)
        except TelegramError as exc:
            logging.warning("could not post the two-copies warning: %s", exc)

    def report(error: TelegramError) -> None:
        if isinstance(error, Conflict):
            # The same bot fetching messages from somewhere else - the other
            # computer, if one bot is shared between two.
            if due("conflict", 600.0):
                logging.error("another copy of this bot is fetching messages; "
                              "replies reach only one of them")
                app.create_task(warn())
        elif due("network", 300.0):
            logging.warning("cannot fetch messages from Telegram (%s); "
                            "still trying", error)
    return report


async def run_live(args) -> int:
    config = load_config()
    if config.group_id is None:
        raise SystemExit(
            "TELEGRAM_GROUP_ID is not set yet.\n"
            "Run:  python -m tools.discover_ids   and follow the instructions."
        )

    replies = config.replies_enabled and not args.no_replies
    if replies and config.owner_id is None:
        raise SystemExit(
            "TELEGRAM_OWNER_ID is not set. Without it the bot cannot tell your\n"
            "messages from anyone else's, so replies stay off. Run:\n"
            "  python -m tools.discover_ids"
        )

    state = State(config.state_db)
    # Asked for here, or left for the next start by "bridge.bat" (option 10).
    catch_up = args.catch_up or state.catch_up_requested()
    echo = EchoGuard()
    # Permission questions only make sense if you can tap an answer.
    approval = (ApprovalService(state, timeout=config.approval_timeout,
                                question_timeout=config.question_timeout)
                if replies else None)
    runner = Runner(config, approval)

    # Defaults are tight (5s read); a slow network then times out on a send that
    # actually went through, and the retry posts it twice.
    request = HTTPXRequest(connect_timeout=15.0, read_timeout=30.0,
                           write_timeout=30.0, pool_timeout=15.0)

    app = build_application(config, state, runner, echo, request, approval)
    sink = TelegramSink(app.bot, config.group_id, state, config)
    app.bot_data["inbox"].sink = sink       # /archive and /reopen need it
    mirror = Mirror(config, sink, state, backfill=args.backfill, echo=echo,
                    import_all=args.import_all, catch_up=catch_up)
    app.bot_data["inbox"].mirror = mirror   # /resend starts a chat over

    try:
        await _connect(app)
    except BaseException:
        state.close()
        raise
    async with app:
        # Everything started below is stopped however this ends, so starting
        # again after an error does not leave the last attempt half-running.
        try:
            logging.info("connected as @%s", app.bot.username)
            if config.claude_cli:
                logging.info("using CLI %s", config.claude_cli)
            else:
                logging.warning("no claude.exe found - replies will fail. "
                                "Set CLAUDE_CLI in .env")

            if replies:
                try:
                    await app.bot.set_my_commands(COMMANDS)
                except Exception as exc:          # never block startup over this
                    logging.warning("could not register the command menu: %s",
                                    exc)
                if approval is not None:
                    await approval.start()
                await app.start()
                # bootstrap_retries=-1: a connection lost between connecting
                # and here is waited out instead of ending the bridge.
                await app.updater.start_polling(
                    allowed_updates=Update.ALL_TYPES, drop_pending_updates=True,
                    bootstrap_retries=-1,
                    error_callback=polling_trouble(app, config.group_id))
                logging.info("accepting replies from user %s (mode: %s)",
                             config.owner_id, config.permission_mode)
            else:
                logging.info("mirror only - replies are off")

            await sink.start()
            if args.restyle_topics:
                await _restyle(sink, state, config.topic_icons)
            elif args.once:
                logging.info("queued %d message(s)", await mirror.run(once=True))
                await sink.flush()
            else:
                # Topics made before TOPIC_ICONS last changed still wear the
                # old icons; put that right alongside everything else.
                restyling = None
                if (state.topic_style() or "project") != config.topic_icons:
                    restyling = asyncio.create_task(
                        _restyle(sink, state, config.topic_icons))
                try:
                    await mirror.run()
                finally:
                    if restyling is not None:
                        restyling.cancel()
        finally:
            # Give anything still queued a chance to go out before we close.
            # Whatever does not make it is sent next time: read positions
            # have not moved past it.
            try:
                await asyncio.wait_for(sink.flush(), timeout=30.0)
            except asyncio.TimeoutError:
                logging.warning("gave up waiting for the send queue to empty; "
                                "the rest goes out next time")
            await sink.stop()
            if approval is not None:
                await approval.stop()
            if app.updater is not None and app.updater.running:
                await app.updater.stop()
            if app.running:
                await app.stop()
            state.close()
    return 0


async def _supervise(args) -> int:
    """Run the bridge, starting it again after anything but a bad setting.

    Started hidden there is no window to show an error, and Task Scheduler
    does not restart a program that failed, so every way out is logged and
    only a configuration problem ends the run.
    """
    while True:
        try:
            return await run_live(args)
        except (KeyboardInterrupt, asyncio.CancelledError):
            logging.info("stopped")
            return 0
        except SystemExit as exc:
            if exc.code not in (None, 0):
                logging.error("%s", exc.code)
            raise
        except InvalidToken:
            logging.error("Telegram does not accept TELEGRAM_BOT_TOKEN - "
                          "check it in .env")
            return 1
        except Exception:
            logging.exception("the bridge stopped on an unexpected error")
            if args.once:
                return 1
            logging.info("starting again in %.0fs", RESTART_DELAY)
            await asyncio.sleep(RESTART_DELAY)


async def main() -> int:
    args = parse_args()
    _setup_logging(args.verbose, args.log_file)
    if args.dry_run:
        try:
            return await run_dry(args)
        except (KeyboardInterrupt, asyncio.CancelledError):
            logging.info("stopped")
            return 0

    lock = InstanceLock()
    if not lock.acquire():
        holder = lock.holder()
        logging.error(
            "the bridge is already running%s. Only one copy may run at a "
            "time: stop that one first (bridge.bat, option 3)",
            f" (process {holder})" if holder else "")
        return 2
    try:
        return await _supervise(args)
    finally:
        lock.release()


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(main()))
    except KeyboardInterrupt:
        raise SystemExit(0)
