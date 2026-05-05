"""Telegram bot command handlers.

Each handler queries the DB and sends multi-message responses via send_telegram().
No ORM imports at module level — lazy imports keep startup clean.
"""

import logging
from datetime import timedelta
from decimal import Decimal

logger = logging.getLogger(__name__)


def _ist_today_range():
    from app.core.utils import now_ist
    now = now_ist()
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return start, start + timedelta(days=1)


def _instrument_label(symbol: str, strike_price, option_type: str | None) -> str:
    if option_type in ("CE", "PE"):
        return f"{symbol} {int(float(strike_price))} {option_type}"
    return symbol


def _pnl_str(pnl) -> str:
    v = float(pnl) if pnl is not None else 0.0
    sign = "+" if v >= 0 else "-"
    return f"{sign}₹{abs(v):,.0f}"


def _exit_label(reason: str | None) -> str:
    return {
        "AGENT_PROFIT": "TARGET",
        "TARGET_HIT":   "TARGET",
        "AGENT_SL":     "SL",
        "SL_HIT":       "SL",
        "TRAILING_SL":  "TRAIL",
        "TIME_EXIT":    "EOD",
        "EOD":          "EOD",
        "MANUAL":       "MANUAL",
        "DRAWDOWN_HALT":"HALT",
        "EXPIRY_ROLL":  "ROLL",
        "MARKET_EXIT":  "EXIT",
    }.get(reason or "", reason or "?")


async def handle_shadow(chat_id: str) -> None:
    """Send a 3-message shadow performance report for today."""
    from app.core.database import async_session_factory
    from app.core.utils import now_ist
    from app.agent.notification import send_telegram
    from app.models.position import Position
    from app.models.trade import Trade
    from sqlalchemy import select, and_

    today_start, today_end = _ist_today_range()

    async with async_session_factory() as session:
        open_result = await session.execute(
            select(Position)
            .where(and_(Position.is_shadow == True, Position.opened_at >= today_start))
            .order_by(Position.opened_at)
        )
        open_positions = open_result.scalars().all()

        closed_result = await session.execute(
            select(Trade)
            .where(
                and_(
                    Trade.source == "SHADOW",
                    Trade.status == "CLOSED",
                    Trade.exit_time >= today_start,
                    Trade.exit_time < today_end,
                )
            )
            .order_by(Trade.exit_time)
        )
        closed_trades = closed_result.scalars().all()

    date_str = now_ist().strftime("%d %b %Y")
    open_count = len(open_positions)
    closed_count = len(closed_trades)
    total = open_count + closed_count
    wins = sum(1 for t in closed_trades if t.pnl and float(t.pnl) > 0)

    closed_pnl = sum(float(t.pnl) for t in closed_trades if t.pnl)
    open_pnl = sum(float(p.unrealized_pnl) for p in open_positions if p.unrealized_pnl)
    net_pnl = closed_pnl + open_pnl

    if closed_count:
        hit_rate_str = f"{wins}/{closed_count} → {int(wins / closed_count * 100)}%"
    else:
        hit_rate_str = "—"

    net_str = _pnl_str(net_pnl)

    # ── Message 1: Summary ──────────────────────────────────────────────────
    summary = (
        f"📊 <b>Shadow P&amp;L — {date_str}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"Trades today:  <b>{total}</b>  ({open_count} open · {closed_count} closed)\n"
        f"Hit rate:      {hit_rate_str}\n"
        f"Net P&amp;L:       <b>{net_str}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━"
    )
    await send_telegram(summary)

    if total == 0:
        return

    # ── Message 2: Open positions ───────────────────────────────────────────
    if open_positions:
        rows = []
        for i, p in enumerate(open_positions, 1):
            label = _instrument_label(p.symbol, p.strike_price, p.option_type)
            pnl = _pnl_str(p.unrealized_pnl)
            t = p.opened_at.strftime("%H:%M")
            rows.append(f"{i:2}. {label:<24} {pnl:>9}  {t}")
        msg2 = f"🟢 <b>OPEN POSITIONS ({open_count})</b>\n<pre>" + "\n".join(rows) + "</pre>"
        await send_telegram(msg2)

    # ── Message 3: Closed trades ────────────────────────────────────────────
    if closed_trades:
        rows = []
        for i, t in enumerate(closed_trades, 1):
            label = _instrument_label(t.symbol, t.strike_price, t.option_type)
            pnl = _pnl_str(t.pnl)
            reason = _exit_label(t.exit_reason)
            rows.append(f"{i:2}. {label:<24} {pnl:>9}  {reason}")
        msg3 = f"✅ <b>CLOSED TODAY ({closed_count})</b>\n<pre>" + "\n".join(rows) + "</pre>"
        await send_telegram(msg3)


# ── Command dispatch table ──────────────────────────────────────────────────

_HANDLERS = {
    "shadow": handle_shadow,
}


async def handle_command(cmd: str, chat_id: str) -> None:
    handler = _HANDLERS.get(cmd)
    if handler is None:
        return
    try:
        await handler(chat_id)
    except Exception as e:
        logger.error("Command /%s failed: %s", cmd, e, exc_info=True)
        from app.agent.notification import send_telegram
        await send_telegram(f"⚠️ /{cmd} failed — check backend logs.")
