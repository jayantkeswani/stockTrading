"""Telegram bot long-polling loop.

Polls getUpdates in a background asyncio task. On startup, advances past any
old messages so stale commands aren't replayed. Ignores messages from any
chat_id other than settings.telegram_chat_id (single-user safety gate).

Commands supported:
  /status  — system snapshot (market, agent, feed, trades)
  /market  — market overview (indices, VIX, global cues)
  /pnl     — real trade P&L (MANUAL + YOLO)
  /shadow  — shadow trade P&L
  /yolo    — YOLO trade P&L
  /signals — today's actionable signals
  /help    — list all commands
"""

import asyncio
import logging

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

_TELEGRAM_API = "https://api.telegram.org/bot{token}"
_update_offset: int = 0
_polling_task: asyncio.Task | None = None


def _api_url(method: str) -> str:
    return f"{_TELEGRAM_API.format(token=settings.telegram_bot_token)}/{method}"


def _fetch_updates_sync(offset: int, timeout: int = 30, limit: int = 100) -> list[dict]:
    with httpx.Client(timeout=timeout + 5) as client:
        r = client.get(
            _api_url("getUpdates"),
            params={"timeout": timeout, "offset": offset, "limit": limit, "allowed_updates": ["message"]},
        )
        r.raise_for_status()
        return r.json().get("result", [])


async def _get_updates(offset: int, timeout: int = 30) -> list[dict]:
    try:
        return await asyncio.to_thread(_fetch_updates_sync, offset, timeout)
    except Exception as e:
        logger.debug("getUpdates error: %s", e)
        return []


async def _register_commands() -> None:
    """Register all commands in the Telegram command menu."""
    def _send():
        with httpx.Client(timeout=10) as client:
            client.post(
                _api_url("setMyCommands"),
                json={"commands": [
                    {"command": "status",  "description": "System snapshot"},
                    {"command": "market",  "description": "Market overview"},
                    {"command": "pnl",     "description": "Real trade P&L"},
                    {"command": "shadow",  "description": "Shadow trade P&L"},
                    {"command": "yolo",    "description": "YOLO trade P&L"},
                    {"command": "signals", "description": "Today's signals"},
                    {"command": "help",    "description": "List all commands"},
                ]},
            )
    try:
        await asyncio.to_thread(_send)
    except Exception as e:
        logger.debug("setMyCommands failed: %s", e)


async def _skip_old_updates() -> int:
    """Advance offset past any queued messages so stale commands are ignored."""
    updates = await _get_updates(offset=-1, timeout=0)
    if updates:
        return updates[-1]["update_id"] + 1
    return 0


async def _poll_loop() -> None:
    global _update_offset
    from app.agent.telegram_commands import handle_command

    _update_offset = await _skip_old_updates()
    logger.info("Telegram bot polling started (offset=%d)", _update_offset)

    while True:
        try:
            updates = await _get_updates(_update_offset)
            for update in updates:
                _update_offset = update["update_id"] + 1
                msg = update.get("message") or update.get("edited_message")
                if not msg:
                    continue
                chat_id = str(msg.get("chat", {}).get("id", ""))
                if chat_id != settings.telegram_chat_id:
                    logger.warning("Ignoring message from unknown chat_id %s", chat_id)
                    continue
                text = (msg.get("text") or "").strip()
                if not text.startswith("/"):
                    continue
                cmd = text.split()[0].split("@")[0].lstrip("/").lower()
                logger.info("Telegram command: /%s", cmd)
                asyncio.create_task(handle_command(cmd, chat_id))
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error("Telegram poll loop error: %s", e)
            await asyncio.sleep(5)


def start_telegram_bot() -> asyncio.Task | None:
    global _polling_task
    if not settings.telegram_bot_token or not settings.telegram_chat_id:
        logger.info("Telegram not configured — bot polling skipped")
        return None
    if not settings.telegram_enabled:
        logger.info("Telegram disabled via TELEGRAM_ENABLED=false")
        return None
    asyncio.create_task(_register_commands(), name="telegram_register_commands")
    _polling_task = asyncio.create_task(_poll_loop(), name="telegram_bot_poll")
    return _polling_task


async def stop_telegram_bot() -> None:
    global _polling_task
    if _polling_task and not _polling_task.done():
        _polling_task.cancel()
        try:
            await _polling_task
        except asyncio.CancelledError:
            pass
    _polling_task = None
    logger.info("Telegram bot polling stopped")
