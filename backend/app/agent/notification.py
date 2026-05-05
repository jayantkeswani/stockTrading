"""Telegram notification dispatcher for the trading agent.

All public functions take plain scalars — no ORM imports here.
Callers are responsible for extracting the fields they need from models.
"""

import html
import logging

import httpx

from app.config import settings
from app.core.retry import async_retry

logger = logging.getLogger(__name__)
_TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"


async def send_telegram(message: str) -> bool:
    """Send a Telegram message with up to 3 retries. Returns True on success."""
    if not settings.telegram_bot_token or not settings.telegram_chat_id:
        logger.warning("Telegram not configured — skipping notification")
        return False
    url = _TELEGRAM_API.format(token=settings.telegram_bot_token)

    # Sync httpx via asyncio.to_thread — avoids anyio async TLS failures on
    # macOS 15.2 + Python 3.11.x where httpx.AsyncClient raises ConnectError('').
    def _send_sync() -> bool:
        with httpx.Client(timeout=10) as client:
            r = client.post(
                url,
                json={"chat_id": settings.telegram_chat_id, "text": message, "parse_mode": "HTML"},
            )
            r.raise_for_status()
            return True

    async def _send() -> bool:
        import asyncio
        return await asyncio.to_thread(_send_sync)

    try:
        return await async_retry(_send, retries=3, base_delay=2.0, label="telegram_send")
    except Exception as e:
        logger.error("Telegram send failed after retries: %s", e)
        return False


def _paper() -> str:
    from app.services.trading_config import _cache as _cfg_cache
    paper = _cfg_cache.paper_trading if _cfg_cache is not None else True
    return "📄 " if paper else ""


def _pnl_str(pnl: float, entry: float, exit_: float) -> str:
    sign = "+" if pnl >= 0 else ""
    pct = ((exit_ - entry) / entry * 100) if entry else 0
    pct_sign = "+" if pct >= 0 else ""
    return f"{sign}₹{abs(pnl):,.0f}  ({pct_sign}{pct:.1f}%)"


def _strategy_label(name: str) -> str:
    labels = {
        "vwap_pullback": "VWAP Pullback",
        "orb": "ORB",
        "gamma_scalping": "Gamma Scalp",
        "can_slim": "CAN SLIM",
    }
    return labels.get(name, name.upper().replace("_", " "))


# ── Signal events ──────────────────────────────────────────────────────────────

async def notify_signal_generated(
    symbol: str,
    signal_type: str,
    strategy_name: str,
    entry: float,
    stop_loss: float,
    target: float,
    strike: float | None,
    expiry: str | None,
    confidence: float | None,
    instrument_type: str = "OPTION",
    blocked_reason: str | None = None,
) -> None:
    direction = signal_type.replace("BUY_", "")
    rr = ""
    if stop_loss and target and entry:
        risk = entry - stop_loss
        reward = target - entry
        if risk > 0:
            rr = f"R:R 1:{reward/risk:.1f}  ·  "

    conf = f"Conf {confidence:.0f}%" if confidence else ""
    strike_line = ""
    if instrument_type == "OPTION" and strike:
        expiry_str = f" · {expiry}" if expiry else ""
        strike_line = f"\nStrike {int(strike)} {direction}{expiry_str}"
    elif instrument_type == "FUTURE" and expiry:
        strike_line = f"\nExpiry {expiry}"

    blocked_line = f"\n⚠️ <i>{html.escape(blocked_reason)}</i>" if blocked_reason else ""

    msg = (
        f"{_paper()}📊 <b>Signal: {direction}</b>\n"
        f"{symbol}  ·  {_strategy_label(strategy_name)}"
        f"{strike_line}\n"
        f"Entry ₹{entry:.0f}  ·  SL ₹{stop_loss:.0f}  ·  Target ₹{target:.0f}\n"
        f"{rr}{conf}{blocked_line}"
    )
    await send_telegram(msg.strip())


# ── Trade execution ────────────────────────────────────────────────────────────

async def notify_auto_executed(
    symbol: str,
    signal_type: str,
    strategy_name: str,
    entry: float,
    stop_loss: float,
    target: float,
    strike: float | None,
    expiry: str | None,
    lots: int,
    quantity: int,
    instrument_type: str = "OPTION",
) -> None:
    direction = signal_type.replace("BUY_", "")
    strike_line = ""
    if instrument_type == "OPTION" and strike:
        expiry_str = f" · {expiry}" if expiry else ""
        strike_line = f"\n{int(strike)} {direction}{expiry_str}  ·  {lots} lot{'s' if lots > 1 else ''} ({quantity} qty)"
    else:
        expiry_str = f"  ·  {expiry}" if expiry else ""
        strike_line = f"\n{lots} lot{'s' if lots > 1 else ''} ({quantity} qty){expiry_str}"

    msg = (
        f"{_paper()}⚡ <b>Auto-Executed: {direction}</b>\n"
        f"{symbol}  ·  {_strategy_label(strategy_name)}"
        f"{strike_line}\n"
        f"Entry ₹{entry:.0f}  ·  SL ₹{stop_loss:.0f}  ·  Target ₹{target:.0f}"
    )
    await send_telegram(msg)


# ── Position exits ─────────────────────────────────────────────────────────────

async def notify_sl_hit(
    symbol: str,
    strategy_name: str,
    entry: float,
    exit_price: float,
    pnl: float,
    lots: int,
    instrument_type: str = "OPTION",
    is_trailing: bool = False,
) -> None:
    emoji = "🟡" if is_trailing else "🔴"
    title = "Trailing Stop Hit" if is_trailing else "Stop Loss Hit"
    msg = (
        f"{_paper()}{emoji} <b>{title}</b>\n"
        f"{symbol}  ·  {_strategy_label(strategy_name)}\n"
        f"Entry ₹{entry:.0f}  →  Exit ₹{exit_price:.0f}\n"
        f"PnL  {_pnl_str(pnl, entry, exit_price)}  ·  {lots} lot{'s' if lots > 1 else ''}"
    )
    await send_telegram(msg)


async def notify_profit_booked(
    symbol: str,
    strategy_name: str,
    entry: float,
    exit_price: float,
    pnl: float,
    lots: int,
) -> None:
    msg = (
        f"{_paper()}🟢 <b>Profit Booked</b>\n"
        f"{symbol}  ·  {_strategy_label(strategy_name)}\n"
        f"Entry ₹{entry:.0f}  →  Exit ₹{exit_price:.0f}\n"
        f"PnL  {_pnl_str(pnl, entry, exit_price)}  ·  {lots} lot{'s' if lots > 1 else ''}"
    )
    await send_telegram(msg)


async def notify_time_exit(
    symbol: str,
    strategy_name: str,
    entry: float,
    exit_price: float,
    pnl: float,
    lots: int,
) -> None:
    msg = (
        f"{_paper()}🕐 <b>EOD Exit</b>\n"
        f"{symbol}  ·  {_strategy_label(strategy_name)}\n"
        f"Entry ₹{entry:.0f}  →  Exit ₹{exit_price:.0f}\n"
        f"PnL  {_pnl_str(pnl, entry, exit_price)}  ·  {lots} lot{'s' if lots > 1 else ''}"
    )
    await send_telegram(msg)


async def notify_confirmation_request(
    symbol: str,
    strategy_name: str,
    entry: float,
    current_price: float,
    pnl: float,
) -> None:
    """SEMI mode — target reached, waiting for manual confirmation."""
    msg = (
        f"🎯 <b>Target Reached — Confirm?</b>\n"
        f"{symbol}  ·  {_strategy_label(strategy_name)}\n"
        f"Entry ₹{entry:.0f}  →  Current ₹{current_price:.0f}\n"
        f"PnL  {_pnl_str(pnl, entry, current_price)}\n"
        f"Open the dashboard to confirm."
    )
    await send_telegram(msg)


# ── Expiry roll ────────────────────────────────────────────────────────────────

async def notify_expiry_roll(
    symbol: str,
    old_expiry: str,
    new_expiry: str,
    old_pnl: float,
    new_entry: float,
    new_sl: float,
    new_target: float,
) -> None:
    pnl_sign = "+" if old_pnl >= 0 else ""
    msg = (
        f"{_paper()}🔄 <b>Expiry Roll</b>\n"
        f"{symbol}  {old_expiry}  →  {new_expiry}\n"
        f"Closed PnL  {pnl_sign}₹{old_pnl:,.0f}\n"
        f"New entry ₹{new_entry:.0f}  ·  SL ₹{new_sl:.0f}  ·  Target ₹{new_target:.0f}"
    )
    await send_telegram(msg)


async def notify_expiry_roll_failed(symbol: str, expiry: str) -> None:
    msg = (
        f"⚠️ <b>Expiry Roll Failed</b>\n"
        f"{symbol}  ·  Expiry {expiry}\n"
        f"Could not find next contract — manual action required."
    )
    await send_telegram(msg)


# ── Risk events ────────────────────────────────────────────────────────────────

async def notify_drawdown_halt(daily_pnl: float, limit: float) -> None:
    pct = abs(daily_pnl) / limit * 5 if limit else 0  # approximate
    msg = (
        f"🚨 <b>Trading Halted — Drawdown Limit Hit</b>\n"
        f"Daily PnL  −₹{abs(daily_pnl):,.0f}\n"
        f"Limit  ₹{limit:,.0f}  ({pct:.1f}%)\n"
        f"No new trades today."
    )
    await send_telegram(msg)


# ── Daily summary ──────────────────────────────────────────────────────────────

async def notify_daily_summary(
    total: int,
    wins: int,
    losses: int,
    net_pnl: float,
    best_symbol: str | None,
    best_pnl: float | None,
    worst_symbol: str | None,
    worst_pnl: float | None,
) -> None:
    emoji = "📈" if net_pnl >= 0 else "📉"
    sign = "+" if net_pnl >= 0 else ""
    lines = [
        f"{emoji} <b>Daily Summary</b>  {_paper().strip()}",
        f"Trades {total}  ·  W {wins}  L {losses}",
        f"Net PnL  {sign}₹{net_pnl:,.0f}",
    ]
    if best_symbol and best_pnl is not None:
        lines.append(f"Best   {best_symbol}  +₹{best_pnl:,.0f}")
    if worst_symbol and worst_pnl is not None:
        lines.append(f"Worst  {worst_symbol}  −₹{abs(worst_pnl):,.0f}")
    await send_telegram("\n".join(lines))
