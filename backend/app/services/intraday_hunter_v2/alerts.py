"""IH v2 operational alerts — log + Telegram, at most once per (day, kind, key).

Always logged as `IH v2 ALERT [kind] ...` (visible locally with TELEGRAM_ENABLED=false).
"""
from __future__ import annotations

import logging

from app.core.utils import now_ist

logger = logging.getLogger(__name__)

_alerted: set[tuple] = set()


async def alert(kind: str, message: str, key: str = "") -> None:
    """Log + Telegram an operational alert, at most once per (day, kind, key). Never raises."""
    k = (now_ist().date(), kind, key)
    if k in _alerted:
        return
    _alerted.add(k)
    logger.warning("IH v2 ALERT [%s] %s", kind, message)
    try:
        from app.agent.notification import send_telegram
        await send_telegram(f"⚠️ <b>IH v2</b> {message}")
    except Exception:  # noqa: BLE001
        logger.exception("IH v2 alert Telegram failed")
