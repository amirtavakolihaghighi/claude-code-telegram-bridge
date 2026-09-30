"""Find the two IDs the bot needs: your group's, and your own.

  python -m tools.discover_ids

Then, in Telegram, send  /id  inside the group (in any topic) and the bot
prints the values to paste into .env. Ctrl+C to stop.
"""
from __future__ import annotations

import asyncio
import logging
import sys

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

from bridge.config import load_config


async def on_id(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    user = update.effective_user
    message = update.effective_message
    if chat is None or user is None or message is None:
        return

    lines = [
        "Paste these into your .env file:",
        "",
        f"TELEGRAM_GROUP_ID={chat.id}",
        f"TELEGRAM_OWNER_ID={user.id}",
        "",
        f"(chat type: {chat.type}, forum/topics: {bool(getattr(chat, 'is_forum', False))})",
    ]
    text = "\n".join(lines)

    print("\n" + "=" * 60)
    print(text)
    print("=" * 60 + "\n")

    await message.reply_text(text, message_thread_id=message.message_thread_id)


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)

    config = load_config()
    app = Application.builder().token(config.bot_token).build()
    app.add_handler(CommandHandler("id", on_id))

    print("\nListening. Now do this in Telegram:")
    print("  1. Create a group, open its settings and turn ON 'Topics'")
    print("  2. Add your bot to the group and make it an admin")
    print("     (it needs the 'Manage Topics' permission)")
    print("  3. Send  /id  in the group")
    print("\nCtrl+C to stop.\n")

    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
