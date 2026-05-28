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
    """Send a Telegram message to all configured chat IDs. Returns True if at least one succeeds."""
    if not settings.telegram_bot_token or not settings.telegram_chat_id_set:
        logger.warning("Telegram not configured — skipping notification")
        return False
    if not settings.telegram_enabled:
        logger.debug("Telegram disabled (TELEGRAM_ENABLED=false) — skipping notification")
        return False
    url = _TELEGRAM_API.format(token=settings.telegram_bot_token)

    any_success = False
    for chat_id in settings.telegram_chat_id_set:
        # Sync httpx via asyncio.to_thread — avoids anyio async TLS failures on
        # macOS 15.2 + Python 3.11.x where httpx.AsyncClient raises ConnectError('').
        def _send_sync(cid: str = chat_id) -> bool:
            with httpx.Client(timeout=10) as client:
                r = client.post(
                    url,
                    json={"chat_id": cid, "text": message, "parse_mode": "HTML"},
                )
                r.raise_for_status()
                return True

        async def _send() -> bool:
            import asyncio
            return await asyncio.to_thread(_send_sync)

        try:
            result = await async_retry(_send, retries=3, base_delay=2.0, label="telegram_send")
            if result:
                any_success = True
        except Exception as e:
            logger.error("Telegram send to chat_id %s failed after retries: %s", chat_id, e)

    return any_success


def _paper() -> str:
    """Return '📄 ' prefix when paper trading is active, empty string otherwise."""
    from app.services.trading_config import _cache as _cfg_cache
    paper = _cfg_cache.paper_trading if _cfg_cache is not None else True
    return "📄 " if paper else ""


def _pnl_str(pnl: float, entry: float, exit_: float) -> str:
    """Format PnL as '+₹1,234  (+5.6%)' with sign and percentage."""
    sign = "+" if pnl >= 0 else "-"
    pct = ((exit_ - entry) / entry * 100) if entry else 0
    pct_sign = "+" if pct >= 0 else ""
    return f"{sign}₹{abs(pnl):,.0f}  ({pct_sign}{pct:.1f}%)"


def _strategy_label(name: str) -> str:
    """Return a human-readable strategy label for Telegram messages."""
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
    """Send a new signal notification via Telegram.

    Includes strike/expiry for options, expiry for futures, R:R ratio, confidence,
    and an optional blocked reason if the signal is not executable.
    """
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
    """Send a YOLO auto-execution notification via Telegram."""
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


async def notify_manual_executed(
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
    """Send a manual execution notification via Telegram."""
    direction = signal_type.replace("BUY_", "")
    strike_line = ""
    if instrument_type == "OPTION" and strike:
        expiry_str = f" · {expiry}" if expiry else ""
        strike_line = f"\n{int(strike)} {direction}{expiry_str}  ·  {lots} lot{'s' if lots > 1 else ''} ({quantity} qty)"
    else:
        expiry_str = f"  ·  {expiry}" if expiry else ""
        strike_line = f"\n{lots} lot{'s' if lots > 1 else ''} ({quantity} qty){expiry_str}"

    msg = (
        f"{_paper()}✋ <b>Manual Exec: {direction}</b>\n"
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
    """Send a stop-loss hit notification via Telegram.

    Uses yellow emoji and 'Trailing Stop Hit' title when is_trailing=True,
    red emoji and 'Stop Loss Hit' otherwise.
    """
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
    """Send a profit-booked (target hit) notification via Telegram."""
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
    """Send a 3:15 PM time-exit notification via Telegram."""
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
    """Send a futures expiry roll notification via Telegram."""
    pnl_sign = "+" if old_pnl >= 0 else ""
    msg = (
        f"{_paper()}🔄 <b>Expiry Roll</b>\n"
        f"{symbol}  {old_expiry}  →  {new_expiry}\n"
        f"Closed PnL  {pnl_sign}₹{old_pnl:,.0f}\n"
        f"New entry ₹{new_entry:.0f}  ·  SL ₹{new_sl:.0f}  ·  Target ₹{new_target:.0f}"
    )
    await send_telegram(msg)


async def notify_expiry_roll_failed(symbol: str, expiry: str) -> None:
    """Send a failed expiry roll alert via Telegram — requires manual intervention."""
    msg = (
        f"⚠️ <b>Expiry Roll Failed</b>\n"
        f"{symbol}  ·  Expiry {expiry}\n"
        f"Could not find next contract — manual action required."
    )
    await send_telegram(msg)


# ── Risk events ────────────────────────────────────────────────────────────────

async def notify_drawdown_halt(daily_pnl: float, limit: float) -> None:
    """Send a drawdown-limit-hit halt notification via Telegram."""
    pct = abs(daily_pnl) / limit * 5 if limit else 0  # approximate
    msg = (
        f"🚨 <b>Trading Halted — Drawdown Limit Hit</b>\n"
        f"Daily PnL  −₹{abs(daily_pnl):,.0f}\n"
        f"Limit  ₹{limit:,.0f}  ({pct:.1f}%)\n"
        f"No new trades today."
    )
    await send_telegram(msg)


async def notify_profit_cap_halt(
    daily_pnl: float,
    limit: float,
    positions_closed: int,
    profile_name: str | None = None,
) -> None:
    """Send a daily-profit-cap-hit halt notification via Telegram.

    When profile_name is provided, the title includes the profile name so the
    user knows which YOLO profile hit its cap.
    """
    title = f"Profit Cap HIT — {profile_name}" if profile_name else "Daily Profit Target Hit"
    msg = (
        f"🔒 <b>Trading Halted — {title}</b>\n"
        f"Daily PnL  +₹{daily_pnl:,.0f}\n"
        f"Target  ₹{limit:,.0f}\n"
        f"Closed {positions_closed} position(s). No new trades today."
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
    """Send the 3:35 PM EOD daily summary via Telegram (excludes shadow trades)."""
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


# ── Data helpers for rich messages ────────────────────────────────────────────


async def _classify_fo_buildup(symbols: list[str]) -> dict[str, list[str]]:
    """Classify watchlist symbols into F&O build-up categories using OI snapshots.

    Returns {"long_buildup": [...], "short_buildup": [...],
             "short_covering": [...], "long_unwinding": [...]}.
    """
    from app.core.database import async_session_factory
    from app.core.redis import get_redis
    from app.models.oi_snapshot import OISnapshot
    from sqlalchemy import and_, select

    result: dict[str, list[tuple[str, float]]] = {
        "long_buildup": [],
        "short_buildup": [],
        "short_covering": [],
        "long_unwinding": [],
    }
    if not symbols:
        return {k: [] for k in result}

    try:
        redis = get_redis()
        async with async_session_factory() as session:
            for symbol in symbols:
                rows = await session.execute(
                    select(OISnapshot.open_interest, OISnapshot.timestamp)
                    .where(
                        and_(
                            OISnapshot.symbol == symbol,
                            OISnapshot.option_type == "FUT",
                        )
                    )
                    .order_by(OISnapshot.timestamp.desc())
                    .limit(2)
                )
                snapshots = rows.all()
                if len(snapshots) < 2:
                    continue

                latest_oi = snapshots[0].open_interest
                prev_oi = snapshots[1].open_interest
                if not prev_oi or prev_oi <= 0:
                    continue

                oi_change_pct = (latest_oi - prev_oi) / prev_oi * 100
                oi_up = oi_change_pct > 1.0
                oi_down = oi_change_pct < -1.0
                if not oi_up and not oi_down:
                    continue

                # Price direction from Redis cache
                import json
                price_data = await redis.get(f"price:{symbol}")
                price_up = True  # default
                if price_data:
                    p = json.loads(price_data)
                    ltp = p.get("ltp", 0)
                    prev_close = p.get("prev_close_price", 0)
                    if ltp and prev_close:
                        price_up = ltp > prev_close

                abs_change = abs(oi_change_pct)
                if oi_up and price_up:
                    result["long_buildup"].append((symbol, abs_change))
                elif oi_up and not price_up:
                    result["short_buildup"].append((symbol, abs_change))
                elif oi_down and price_up:
                    result["short_covering"].append((symbol, abs_change))
                elif oi_down and not price_up:
                    result["long_unwinding"].append((symbol, abs_change))
    except Exception:
        logger.exception("Error classifying F&O build-up")

    # Sort by abs OI change descending, return top 5 symbol names
    return {
        k: [s[0] for s in sorted(v, key=lambda x: x[1], reverse=True)[:5]]
        for k, v in result.items()
    }


async def _get_nifty_bn_oi_levels() -> dict:
    """Extract support/resistance levels from Nifty/BankNifty OI snapshots.

    Returns {"nifty_support": [s1, s2], "nifty_resistance": [r1, r2],
             "bn_support": [s1, s2], "bn_resistance": [r1, r2]}.
    """
    import json

    from app.core.database import async_session_factory
    from app.core.redis import get_redis
    from app.models.oi_snapshot import OISnapshot
    from sqlalchemy import and_, func, select

    levels: dict[str, list[int]] = {
        "nifty_support": [],
        "nifty_resistance": [],
        "bn_support": [],
        "bn_resistance": [],
    }

    try:
        redis = get_redis()

        for symbol, prefix in [("NIFTY", "nifty"), ("BANKNIFTY", "bn")]:
            # Get current price for proximity filter
            price_data = await redis.get(f"price:{symbol}")
            current_price = 0.0
            if price_data:
                current_price = json.loads(price_data).get("ltp", 0)
            if not current_price:
                continue

            price_range_low = current_price * 0.97
            price_range_high = current_price * 1.03

            async with async_session_factory() as session:
                # Find latest OI snapshot timestamp for this symbol
                ts_result = await session.execute(
                    select(func.max(OISnapshot.timestamp)).where(
                        OISnapshot.symbol == symbol,
                    )
                )
                latest_ts = ts_result.scalar_one_or_none()
                if latest_ts is None:
                    continue

                # Fetch CE/PE rows at that timestamp within ±3% of current price
                rows = await session.execute(
                    select(OISnapshot).where(
                        and_(
                            OISnapshot.symbol == symbol,
                            OISnapshot.timestamp == latest_ts,
                            OISnapshot.strike_price >= price_range_low,
                            OISnapshot.strike_price <= price_range_high,
                        )
                    )
                )
                snapshots = rows.scalars().all()

            ce_rows = [(s.strike_price, s.open_interest) for s in snapshots if s.option_type == "CE"]
            pe_rows = [(s.strike_price, s.open_interest) for s in snapshots if s.option_type == "PE"]

            # Top 2 PE strikes by OI = support, top 2 CE strikes by OI = resistance
            pe_sorted = sorted(pe_rows, key=lambda x: x[1], reverse=True)[:2]
            ce_sorted = sorted(ce_rows, key=lambda x: x[1], reverse=True)[:2]

            levels[f"{prefix}_support"] = sorted([int(s[0]) for s in pe_sorted])
            levels[f"{prefix}_resistance"] = sorted([int(s[0]) for s in ce_sorted])
    except Exception:
        logger.exception("Error fetching OI levels")

    return levels


# ── Morning market snapshot ───────────────────────────────────────────────────

_MORNING_SCHEMA = {
    "type": "object",
    "properties": {
        "market_overview": {"type": "string"},
        "sectors_long": {"type": "array", "items": {"type": "string"}},
        "sectors_short": {"type": "array", "items": {"type": "string"}},
        "outlook": {"type": "string"},
    },
    "required": ["market_overview", "sectors_long", "sectors_short", "outlook"],
}

_MORNING_SYSTEM_PROMPT = (
    "You are a market analyst for an Indian stock futures intraday trading system. "
    "Draft a concise pre-market telegram message. Keep the overview to 2-3 sentences "
    "covering key global cues (US markets, crude oil, VIX). The outlook should be 2-3 "
    "sentences with actionable insights for today's session. Return at most 3 sectors "
    "per direction.\n\n"
    "The input uses internal codes — always translate in your prose:\n"
    "- overnight_bias: BULLISH/BEARISH/NEUTRAL — describe the sentiment, "
    "don't write the label\n"
    "- global_score: -1.0 to +1.0 composite — describe the sentiment, "
    "don't cite the raw number\n"
    "- approach: aggressive/normal/conservative — today's risk posture\n"
    "- setup_priority: ORB = opening range breakout, VWAP_BOUNCE = VWAP pullback "
    "reversal, PDH_PDL = previous day high/low breakout, "
    "GAP_CONTINUATION = gap follow-through\n\n"
    "Be specific with numbers. Write like a trader briefing a colleague, "
    "not a data readout."
)


async def notify_morning_premarket(
    briefing: dict,
    global_cues: dict,
) -> None:
    """Send the 8:00 AM pre-market report via Telegram.

    Uses LLM to draft market overview and outlook, combines with
    briefing data, F&O build-up, and OI levels.
    """
    import json
    from datetime import date

    from app.core.redis import get_redis
    from app.core.utils import now_ist

    today = now_ist().date()

    # Gather Nifty previous close from global cues
    nifty_price = global_cues.get("nifty_price", 0)
    nifty_pct = global_cues.get("nifty_pct", 0)
    nifty_change = nifty_price * (nifty_pct / 100) if nifty_price and nifty_pct else 0

    # BankNifty previous close from Redis price cache (24h TTL survives overnight)
    bn_price = 0
    bn_pct = 0.0
    bn_change = 0.0
    try:
        from app.core.redis import get_redis
        r = get_redis()
        bn_raw = await r.get("price:BANKNIFTY")
        if bn_raw:
            bn_data = json.loads(bn_raw)
            bn_price = bn_data.get("ltp", 0)
            bn_change = bn_data.get("change", 0)
            bn_pct = bn_data.get("change_pct", 0)
    except Exception:
        pass

    # LLM draft for market overview and outlook
    llm_result = {}
    try:
        from app.research.llm_client import create_llm_client
        llm = create_llm_client(pro=True)

        prompt_data = {
            "global_cues": {
                "dow_futures_pct": global_cues.get("dow_futures_pct"),
                "sp500_pct": global_cues.get("sp500_close_pct"),
                "nasdaq_pct": global_cues.get("nasdaq_close_pct"),
                "crude_pct": global_cues.get("crude_pct"),
                "usdinr_pct": global_cues.get("usdinr_pct"),
                "us_vix": global_cues.get("us_vix"),
                "global_score": global_cues.get("global_score"),
                "overnight_bias": global_cues.get("overnight_bias"),
            },
            "briefing": {
                "approach": briefing.get("approach"),
                "summary": briefing.get("summary"),
                "sector_bias": briefing.get("sector_bias"),
                "sector_avoid": briefing.get("sector_avoid"),
                "setup_priority": briefing.get("setup_priority"),
                "flags": briefing.get("flags"),
            },
        }
        llm_result = await llm.generate_json(
            prompt=f"Generate pre-market analysis from this data:\n{json.dumps(prompt_data, default=str)}",
            system=_MORNING_SYSTEM_PROMPT,
            max_tokens=1024,
            response_schema=_MORNING_SCHEMA,
        )
    except Exception:
        logger.warning("LLM failed for morning pre-market message, using briefing summary")

    # F&O build-up from previous day OI snapshots
    fo_buildup = {"long_buildup": [], "short_buildup": []}
    oi_levels: dict = {}
    try:
        # Get watchlist symbols for F&O classification
        redis = get_redis()
        wl_raw = await redis.get(f"strat5:watchlist:permanent")
        wl_symbols = []
        if wl_raw:
            wl_data = json.loads(wl_raw)
            if isinstance(wl_data, list):
                wl_symbols = [w.get("symbol", w) if isinstance(w, dict) else w for w in wl_data]

        if wl_symbols:
            fo_buildup = await _classify_fo_buildup(wl_symbols)
        oi_levels = await _get_nifty_bn_oi_levels()
    except Exception:
        logger.warning("Failed to get F&O/OI data for morning message")

    # Build message
    nifty_arrow = "▲" if (nifty_pct or 0) >= 0 else "▼"
    lines = [
        f"{_paper()}📊 <b>Pre-Market Report – {today.strftime('%d %b %Y')}</b>",
        "",
    ]

    if nifty_price or bn_price:
        lines.append(f"📉 <b>Previous Close</b>")
        if nifty_price:
            lines.append(f"Nifty 50: {nifty_price:,.0f} {nifty_arrow} {nifty_change:+,.0f} pts ({nifty_pct:+.2f}%)")
        if bn_price:
            bn_arrow = "▲" if bn_change >= 0 else "▼"
            lines.append(f"Bank Nifty: {bn_price:,.0f} {bn_arrow} {bn_change:+,.0f} pts ({bn_pct:+.2f}%)")
        lines.append("")

    lines.append("━━━━━━━━━━━━━━━━")

    # Global cues / market overview
    overview = llm_result.get("market_overview", "")
    if overview:
        lines.append(f"🌍 <b>Global Cues</b>")
        lines.append(overview)
        lines.append("")

    # Sectors
    sectors_long = llm_result.get("sectors_long") or []
    sectors_short = llm_result.get("sectors_short") or []
    if not sectors_long and briefing.get("sector_bias") and briefing["sector_bias"] != "none":
        sectors_long = [briefing["sector_bias"]]
    if not sectors_short and briefing.get("sector_avoid") and briefing["sector_avoid"] != "none":
        sectors_short = [briefing["sector_avoid"]]

    if sectors_long or sectors_short:
        lines.append(f"📈 <b>Sector to Watch</b>")
        if sectors_long:
            lines.append(f"🔥 Long: {', '.join(sectors_long[:3])}")
        if sectors_short:
            lines.append(f"⚠️ Weak: {', '.join(sectors_short[:3])}")
        lines.append("")

    # Approach and briefing
    approach = briefing.get("approach", "normal")
    max_lots = briefing.get("max_lots_recommendation", 2)
    lines.append(f"📋 <b>Approach: {approach.upper()}</b> | Max Lots: {max_lots}")
    summary = briefing.get("summary", "")
    if summary:
        lines.append(summary)
    flags = briefing.get("flags", [])
    if flags:
        for flag in flags[:3]:
            lines.append(f"⚠️ {flag}")
    lines.append("")

    # F&O build-up
    long_bu = fo_buildup.get("long_buildup", [])
    short_bu = fo_buildup.get("short_buildup", [])
    if long_bu or short_bu:
        lines.append(f"📊 <b>F&amp;O Build-Up</b>")
        if long_bu:
            lines.append(f"📈 Long: {', '.join(long_bu[:5])}")
        if short_bu:
            lines.append(f"📉 Short: {', '.join(short_bu[:5])}")
        lines.append("")

    # OI levels
    n_sup = oi_levels.get("nifty_support", [])
    n_res = oi_levels.get("nifty_resistance", [])
    bn_sup = oi_levels.get("bn_support", [])
    bn_res = oi_levels.get("bn_resistance", [])
    if n_sup or n_res or bn_sup or bn_res:
        lines.append(f"🔑 <b>Key Levels (OI)</b>")
        if n_sup or n_res:
            sup_str = " / ".join(f"{s:,}" for s in n_sup) if n_sup else "—"
            res_str = " / ".join(f"{s:,}" for s in n_res) if n_res else "—"
            lines.append(f"Nifty S: {sup_str} | R: {res_str}")
        if bn_sup or bn_res:
            sup_str = " / ".join(f"{s:,}" for s in bn_sup) if bn_sup else "—"
            res_str = " / ".join(f"{s:,}" for s in bn_res) if bn_res else "—"
            lines.append(f"BN S: {sup_str} | R: {res_str}")
        lines.append("")

    # Outlook
    outlook = llm_result.get("outlook", "")
    if outlook:
        lines.append("━━━━━━━━━━━━━━━━")
        lines.append(f"📍 {outlook}")

    msg = "\n".join(lines)
    # Telegram 4096 char limit
    if len(msg) > 4000:
        msg = msg[:3997] + "..."
    await send_telegram(msg)


async def notify_morning_preopen(
    watchlist: list[dict],
    global_cues: dict,
) -> None:
    """Send the 9:08 AM pre-open update via Telegram.

    Pure formatting, no LLM call (time-critical).
    """
    from app.core.utils import now_ist

    today = now_ist().date()

    gap_pct = global_cues.get("nifty_gap_pct", 0)
    vix = global_cues.get("india_vix_live", 0)

    lines = [
        f"{_paper()}📊 <b>Pre-Open Update – {today.strftime('%d %b %Y')}</b>",
        "",
        f"📈 Nifty Gap: {gap_pct:+.2f}% | India VIX: {vix:.2f}" if gap_pct else f"📈 India VIX: {vix:.2f}",
        "",
        "━━━━━━━━━━━━━━━━",
    ]

    # Watchlist (top 8)
    if watchlist:
        lines.append(f"📋 <b>Today's Watchlist (Top {min(len(watchlist), 8)})</b>")
        for item in watchlist[:8]:
            symbol = item.get("symbol", "?")
            bias = item.get("bias", "NEUTRAL")
            gap = item.get("gap_pct")
            score = item.get("composite_score", 0)
            bias_emoji = "🟢" if bias == "BULLISH" else ("🔴" if bias == "BEARISH" else "⚪")
            gap_str = f" | Gap {gap:+.1f}%" if gap else ""
            lines.append(f"{bias_emoji} {symbol} · {bias}{gap_str} · Score {score:.0f}")
        lines.append("")

    # Gap commentary
    if gap_pct:
        if gap_pct > 0.5:
            commentary = "Gap-up opening expected — avoid chasing, wait for confirmation"
        elif gap_pct < -0.5:
            commentary = "Gap-down opening expected — watch for reversal setups"
        else:
            commentary = "Flat opening expected — ORB breakout setups in focus"
        lines.append("━━━━━━━━━━━━━━━━")
        lines.append(f"🔔 {commentary}")

    msg = "\n".join(lines)
    if len(msg) > 4000:
        msg = msg[:3997] + "..."
    await send_telegram(msg)
