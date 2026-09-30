"""Run the bridge.

  python -m bridge --dry-run --backfill     show what would be posted
  python -m bridge --backfill               post the whole history, then follow
  python -m bridge                          follow, and accept replies
  python -m bridge --no-replies             follow only, ignore what I type
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from telegram import Update
from telegram.request import HTTPXRequest

from .approval import ApprovalService
from .config import load_config
from .echo import EchoGuard
from .inbox import build_application
from .mirror import Mirror
from .runner import Runner
from .sinks import ConsoleSink, TelegramSink
from .state import State


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="bridge", description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true",
                        help="print to the console instead of Telegram")
    parser.add_argument("--backfill", action="store_true",
                        help="include everything already in the chat files")
    parser.add_argument("--once", action="store_true",
                        help="do a single pass and exit")
    parser.add_argument("--no-replies", action="store_true",
                        help="mirror only; ignore messages typed in Telegram")
    parser.add_argument("--import-all", action="store_true",
                        help="give every existing chat a topic straight away")
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
                    import_all=args.import_all)
    try:
        if args.once:
            logging.info("posted %d message(s)", await mirror.tick())
        else:
            await mirror.run()
    finally:
        state.close()
    return 0


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
    echo = EchoGuard()
    # Permission questions only make sense if you can tap an answer.
    approval = (ApprovalService(state, timeout=config.approval_timeout)
                if replies else None)
    runner = Runner(config, approval)

    # Defaults are tight (5s read); a slow network then times out on a send that
    # actually went through, and the retry posts it twice.
    request = HTTPXRequest(connect_timeout=15.0, read_timeout=30.0,
                           write_timeout=30.0, pool_timeout=15.0)

    app = build_application(config, state, runner, echo, request, approval)
    sink = TelegramSink(app.bot, config.group_id, state)
    mirror = Mirror(config, sink, state, backfill=args.backfill, echo=echo,
                    import_all=args.import_all)

    async with app:
        me = await app.bot.get_me()
        logging.info("connected as @%s", me.username)
        if config.claude_cli:
            logging.info("using CLI %s", config.claude_cli)
        else:
            logging.warning("no claude.exe found - replies will fail. "
                            "Set CLAUDE_CLI in .env")

        if replies:
            if approval is not None:
                await approval.start()
            await app.start()
            await app.updater.start_polling(allowed_updates=Update.ALL_TYPES,
                                            drop_pending_updates=True)
            logging.info("accepting replies from user %s (mode: %s)",
                         config.owner_id, config.permission_mode)
        else:
            logging.info("mirror only - replies are off")

        await sink.start()
        try:
            if args.once:
                logging.info("queued %d message(s)", await mirror.tick())
                await sink.flush()
            else:
                await mirror.run()
        finally:
            # Give anything still queued a chance to go out before we close.
            try:
                await asyncio.wait_for(sink.flush(), timeout=30.0)
            except asyncio.TimeoutError:
                logging.warning("gave up waiting for the send queue to empty")
            await sink.stop()
            if replies:
                if approval is not None:
                    await approval.stop()
                await app.updater.stop()
                await app.stop()
            state.close()
    return 0


async def main() -> int:
    args = parse_args()
    _setup_logging(args.verbose, args.log_file)
    try:
        return await (run_dry(args) if args.dry_run else run_live(args))
    except (KeyboardInterrupt, asyncio.CancelledError):
        logging.info("stopped")
        return 0


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(main()))
    except KeyboardInterrupt:
        raise SystemExit(0)
