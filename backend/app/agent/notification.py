"""Notification dispatcher for Telegram alerts."""

import logging

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"


async def send_telegram(message: str) -> bool:
    """Send a message via Telegram Bot API."""
    if not settings.telegram_bot_token or not settings.telegram_chat_id:
        logger.warning("Telegram not configured, skipping notification")
        return False

    url = TELEGRAM_API.format(token=settings.telegram_bot_token)
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                url,
                json={
                    "chat_id": settings.telegram_chat_id,
                    "text": message,
                    "parse_mode": "HTML",
                },
            )
            response.raise_for_status()
            return True
    except Exception as e:
        logger.error("Failed to send Telegram message: %s", e)
        return False


async def notify_sl_hit(symbol: str, pnl: float):
    """Notify when stop loss is triggered."""
    emoji = "🔴"
    msg = (
        f"{emoji} <b>SL Hit</b>\n"
        f"Symbol: {symbol}\n"
        f"P&L: ₹{pnl:,.2f}\n"
        f"Action: Auto-closed by agent"
    )
    await send_telegram(msg)


async def notify_target_hit(symbol: str, unrealized_pnl: float, log_id: str):
    """Notify when target is reached, asking for confirmation."""
    emoji = "🟢"
    msg = (
        f"{emoji} <b>Target Reached</b>\n"
        f"Symbol: {symbol}\n"
        f"Unrealized P&L: ₹{unrealized_pnl:,.2f}\n"
        f"Action required: Approve profit booking in the UI"
    )
    await send_telegram(msg)


async def notify_daily_summary(
    total_trades: int, winning: int, losing: int, net_pnl: float
):
    """Send end-of-day summary."""
    emoji = "🟢" if net_pnl >= 0 else "🔴"
    msg = (
        f"📊 <b>Daily Summary</b>\n"
        f"Trades: {total_trades} (W: {winning} / L: {losing})\n"
        f"Net P&L: {emoji} ₹{net_pnl:,.2f}"
    )
    await send_telegram(msg)


async def notify_drawdown_halt(drawdown_pct: float, daily_pnl: float):
    """Notify when daily drawdown limit is hit."""
    msg = (
        f"🚨 <b>TRADING HALTED</b>\n"
        f"Daily drawdown: {drawdown_pct:.1f}%\n"
        f"Daily P&L: ₹{daily_pnl:,.2f}\n"
        f"All positions closed. No more trades today."
    )
    await send_telegram(msg)
