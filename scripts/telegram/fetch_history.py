"""Fetch full message history of a Telegram chat to a JSON file.

Run `list_dialogs.py` first to find the `chat_id`. Then:

    python scripts/telegram/fetch_history.py --chat-id -1001234567890
    python scripts/telegram/fetch_history.py --chat-id -1001234567890 --since 2025-01-01
    python scripts/telegram/fetch_history.py --chat-id -1001234567890 --limit 500

Output: scripts/telegram/data/{chat_id}_{timestamp}.json
Each record: id, date (ISO IST), sender_id, sender_name, text, reply_to_id, media_type.
Media bodies are NOT downloaded — only the type (photo/document/poll/etc.) is recorded.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from telethon import TelegramClient
from telethon.tl.types import (
    MessageMediaDocument,
    MessageMediaPhoto,
    MessageMediaPoll,
    MessageMediaWebPage,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
SESSION_DIR = Path(__file__).resolve().parent
DATA_DIR = SESSION_DIR / "data"
IST = ZoneInfo("Asia/Kolkata")

load_dotenv(REPO_ROOT / ".env")


def _require_env(name: str) -> str:
    v = os.getenv(name)
    if not v:
        sys.exit(f"Missing {name} in .env")
    return v


def _media_kind(media) -> str | None:
    if media is None:
        return None
    if isinstance(media, MessageMediaPhoto):
        return "photo"
    if isinstance(media, MessageMediaDocument):
        return "document"
    if isinstance(media, MessageMediaPoll):
        return "poll"
    if isinstance(media, MessageMediaWebPage):
        return "webpage"
    return type(media).__name__


def _parse_since(s: str | None) -> datetime | None:
    if not s:
        return None
    # Accept YYYY-MM-DD or full ISO; assume IST if date-only.
    try:
        if len(s) == 10:
            return datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=IST)
        return datetime.fromisoformat(s).astimezone(timezone.utc)
    except ValueError:
        sys.exit(f"--since must be YYYY-MM-DD or ISO 8601, got: {s}")


async def main(chat_id: int, since: datetime | None, limit: int | None, out: Path | None) -> None:
    api_id = int(_require_env("TELEGRAM_API_ID"))
    api_hash = _require_env("TELEGRAM_API_HASH")
    phone = _require_env("TELEGRAM_PHONE")
    session_name = os.getenv("TELEGRAM_SESSION_NAME", "claude_access")
    session_path = SESSION_DIR / session_name

    client = TelegramClient(str(session_path), api_id, api_hash)
    await client.start(phone=phone)

    entity = await client.get_entity(chat_id)
    title = getattr(entity, "title", None) or getattr(entity, "username", None) or str(chat_id)
    print(f"# Fetching from: {title} (id={chat_id})")
    if since:
        print(f"# Since: {since.isoformat()}")
    if limit:
        print(f"# Limit: {limit}")

    DATA_DIR.mkdir(exist_ok=True)
    if out is None:
        ts = datetime.now(IST).strftime("%Y%m%d_%H%M%S")
        out = DATA_DIR / f"{chat_id}_{ts}.json"

    records: list[dict] = []
    # Telethon iter_messages yields newest-first; we stream, check `since`, and reverse at end.
    kwargs = {"entity": entity}
    if limit:
        kwargs["limit"] = limit
    async for msg in client.iter_messages(**kwargs):
        if since and msg.date.astimezone(timezone.utc) < since.astimezone(timezone.utc):
            break
        sender = None
        sender_name = None
        if msg.sender_id:
            sender = msg.sender_id
            try:
                s = await msg.get_sender()
                if s is not None:
                    parts = [getattr(s, "first_name", None), getattr(s, "last_name", None)]
                    sender_name = " ".join(p for p in parts if p) or getattr(s, "title", None) or getattr(s, "username", None)
            except Exception:
                pass
        records.append({
            "id": msg.id,
            "date_ist": msg.date.astimezone(IST).isoformat(),
            "date_utc": msg.date.astimezone(timezone.utc).isoformat(),
            "sender_id": sender,
            "sender_name": sender_name,
            "text": msg.message or "",
            "reply_to_id": msg.reply_to_msg_id,
            "media": _media_kind(msg.media),
            "views": getattr(msg, "views", None),
            "forwards": getattr(msg, "forwards", None),
        })
        if len(records) % 500 == 0:
            print(f"  … {len(records)} messages", flush=True)

    records.reverse()  # chronological order

    out.write_text(json.dumps({
        "chat_id": chat_id,
        "chat_title": title,
        "fetched_at_ist": datetime.now(IST).isoformat(),
        "message_count": len(records),
        "messages": records,
    }, ensure_ascii=False, indent=2))

    print(f"# Wrote {len(records)} messages → {out}")
    await client.disconnect()


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--chat-id", type=int, required=True, help="numeric chat id from list_dialogs.py")
    p.add_argument("--since", help="YYYY-MM-DD (IST) or ISO 8601; only fetch messages on/after this")
    p.add_argument("--limit", type=int, help="max messages to fetch (newest first)")
    p.add_argument("--out", type=Path, help="output JSON path (default: scripts/telegram/data/{chat_id}_{ts}.json)")
    args = p.parse_args()
    asyncio.run(main(args.chat_id, _parse_since(args.since), args.limit, args.out))
