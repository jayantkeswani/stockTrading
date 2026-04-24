"""List all Telegram dialogs (groups, channels, DMs) for the logged-in user.

Run this once to discover the numeric `chat_id` of the group you want to
ingest (e.g. "Arjun - Options by Liquidate"). Feed that id into
`fetch_history.py` afterwards.

First run will prompt for the login code Telegram sends to your account,
and 2FA password if you have one enabled. Session is cached to
`scripts/telegram/{TELEGRAM_SESSION_NAME}.session` — subsequent runs are silent.

Usage:
    source backend/.venv/bin/activate
    python scripts/telegram/list_dialogs.py                     # all dialogs
    python scripts/telegram/list_dialogs.py --match arjun       # filter by substring
    python scripts/telegram/list_dialogs.py --groups-only       # skip DMs and bots
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from telethon import TelegramClient
from telethon.tl.types import Channel, Chat, User

REPO_ROOT = Path(__file__).resolve().parents[2]
SESSION_DIR = Path(__file__).resolve().parent

load_dotenv(REPO_ROOT / ".env")


def _require_env(name: str) -> str:
    v = os.getenv(name)
    if not v:
        sys.exit(f"Missing {name} in .env")
    return v


def _entity_kind(e) -> str:
    if isinstance(e, User):
        return "bot" if getattr(e, "bot", False) else "user"
    if isinstance(e, Chat):
        return "group"
    if isinstance(e, Channel):
        return "channel" if getattr(e, "broadcast", False) else "supergroup"
    return "unknown"


async def main(match: str | None, groups_only: bool) -> None:
    api_id = int(_require_env("TELEGRAM_API_ID"))
    api_hash = _require_env("TELEGRAM_API_HASH")
    phone = _require_env("TELEGRAM_PHONE")
    session_name = os.getenv("TELEGRAM_SESSION_NAME", "claude_access")
    session_path = SESSION_DIR / session_name

    client = TelegramClient(str(session_path), api_id, api_hash)
    await client.start(phone=phone)

    me = await client.get_me()
    print(f"# Logged in as: {me.first_name or ''} {me.last_name or ''} (@{me.username}) id={me.id}\n")
    print(f"{'kind':<12} {'chat_id':>16}  title")
    print("-" * 80)

    match_lc = match.lower() if match else None
    count = 0
    async for dialog in client.iter_dialogs():
        kind = _entity_kind(dialog.entity)
        if groups_only and kind not in ("group", "supergroup", "channel"):
            continue
        title = dialog.name or "(no title)"
        if match_lc and match_lc not in title.lower():
            continue
        print(f"{kind:<12} {dialog.id:>16}  {title}")
        count += 1

    print(f"\n# Total: {count} dialog(s)")
    await client.disconnect()


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--match", help="case-insensitive substring to filter chat titles")
    p.add_argument("--groups-only", action="store_true", help="hide DMs and bots")
    args = p.parse_args()
    asyncio.run(main(args.match, args.groups_only))
