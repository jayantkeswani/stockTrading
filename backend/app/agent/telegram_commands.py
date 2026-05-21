"""Telegram bot command handlers.

Each handler queries the DB/Redis and sends responses via send_telegram().
No ORM imports at module level — lazy imports keep startup clean.

Commands:
  /status  — system snapshot (market, agent, feed, trades)
  /market  — market overview (indices, VIX, global cues)
  /pnl     — real trade P&L (MANUAL + YOLO)
  /shadow  — shadow trade P&L
  /yolo    — YOLO trade P&L
  /signals — today's actionable signals
  /help    — list all commands
"""

import logging
from datetime import timedelta
from decimal import Decimal

logger = logging.getLogger(__name__)


# ── Shared helpers ─────────────────────────────────────────────────────────────

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


def _pnl_emoji(pnl) -> str:
    v = float(pnl) if pnl is not None else 0.0
    return "🟢" if v >= 0 else "🔴"


def _pct_str(pct) -> str:
    v = float(pct) if pct is not None else 0.0
    sign = "+" if v >= 0 else ""
    return f"{sign}{v:.1f}%"


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


def _strategy_short(name: str) -> str:
    return {
        "vwap_pullback": "S2",
        "intraday_futures": "S5",
        "can_slim": "S4",
        "orb": "S1",
    }.get(name or "", (name or "")[:6])


def _chg_emoji(pct) -> str:
    return "🟢" if float(pct or 0) >= 0 else "🔴"


# ── Shared trade report formatter ──────────────────────────────────────────────

async def _send_trade_report(
    title: str,
    open_positions: list,
    closed_trades: list,
) -> None:
    """Build and send a 1-3 message trade report (summary + open + closed)."""
    from app.agent.notification import send_telegram
    from app.core.utils import now_ist

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

    net_emoji = _pnl_emoji(net_pnl)
    net_str = _pnl_str(net_pnl)

    summary = (
        f"📊 <b>{title} — {date_str}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"Trades: <b>{total}</b> ({open_count} open · {closed_count} closed)\n"
        f"Hit rate: {hit_rate_str}\n"
        f"Net PnL: {net_emoji} <b>{net_str}</b>"
    )
    await send_telegram(summary)

    if total == 0:
        return

    if open_positions:
        items = []
        for p in open_positions:
            label = _instrument_label(p.symbol, p.strike_price, p.option_type)
            emoji = _pnl_emoji(p.unrealized_pnl)
            pnl = _pnl_str(p.unrealized_pnl)
            t = p.opened_at.strftime("%H:%M")
            entry = f"₹{float(p.entry_price):,.0f}"
            curr = f"₹{float(p.current_price):,.0f}" if p.current_price else "—"
            items.append(f"{emoji} {label} · {pnl}\n   {entry} → {curr} · {t}")
        msg = f"🔵 <b>OPEN ({open_count})</b>\n\n" + "\n\n".join(items)
        await send_telegram(msg)

    if closed_trades:
        items = []
        for t in closed_trades:
            label = _instrument_label(t.symbol, t.strike_price, t.option_type)
            emoji = _pnl_emoji(t.pnl)
            pnl = _pnl_str(t.pnl)
            reason = _exit_label(t.exit_reason)
            entry = f"₹{float(t.entry_price):,.0f}"
            exit_ = f"₹{float(t.exit_price):,.0f}" if t.exit_price else "—"
            pct = _pct_str(t.pnl_percent)
            items.append(f"{emoji} {label} · {pnl} · {reason}\n   {entry} → {exit_} · {pct}")
        msg = f"✅ <b>CLOSED ({closed_count})</b>\n\n" + "\n\n".join(items)
        await send_telegram(msg)


# ── Command handlers ───────────────────────────────────────────────────────────

async def handle_shadow(chat_id: str) -> None:
    """Today's shadow P&L report."""
    from app.core.database import async_session_factory
    from app.models.position import Position
    from app.models.trade import Trade
    from sqlalchemy import select, and_

    today_start, today_end = _ist_today_range()

    async with async_session_factory() as session:
        open_result = await session.execute(
            select(Position)
            .where(and_(Position.is_shadow == True, Position.opened_at >= today_start))  # noqa: E712
            .order_by(Position.opened_at)
        )
        open_positions = open_result.scalars().all()

        closed_result = await session.execute(
            select(Trade)
            .where(and_(
                Trade.source == "SHADOW",
                Trade.status == "CLOSED",
                Trade.exit_time >= today_start,
                Trade.exit_time < today_end,
            ))
            .order_by(Trade.exit_time)
        )
        closed_trades = closed_result.scalars().all()

    await _send_trade_report("Shadow PnL", open_positions, closed_trades)


async def handle_yolo(chat_id: str) -> None:
    """Today's YOLO trade P&L report."""
    from app.core.database import async_session_factory
    from app.models.position import Position
    from app.models.trade import Trade
    from sqlalchemy import select, and_

    today_start, today_end = _ist_today_range()

    async with async_session_factory() as session:
        yolo_trade_ids = (
            select(Trade.id).where(Trade.source == "YOLO").correlate(None)
        )
        open_result = await session.execute(
            select(Position)
            .where(and_(
                Position.is_shadow == False,  # noqa: E712
                Position.trade_id.in_(yolo_trade_ids),
                Position.opened_at >= today_start,
            ))
            .order_by(Position.opened_at)
        )
        open_positions = open_result.scalars().all()

        closed_result = await session.execute(
            select(Trade)
            .where(and_(
                Trade.source == "YOLO",
                Trade.status == "CLOSED",
                Trade.exit_time >= today_start,
                Trade.exit_time < today_end,
            ))
            .order_by(Trade.exit_time)
        )
        closed_trades = closed_result.scalars().all()

    await _send_trade_report("YOLO PnL", open_positions, closed_trades)


async def handle_pnl(chat_id: str) -> None:
    """Today's real (non-shadow) P&L report."""
    from app.core.database import async_session_factory
    from app.models.position import Position
    from app.models.trade import Trade
    from sqlalchemy import select, and_

    today_start, today_end = _ist_today_range()

    async with async_session_factory() as session:
        open_result = await session.execute(
            select(Position)
            .where(and_(
                Position.is_shadow == False,  # noqa: E712
                Position.opened_at >= today_start,
            ))
            .order_by(Position.opened_at)
        )
        open_positions = open_result.scalars().all()

        closed_result = await session.execute(
            select(Trade)
            .where(and_(
                Trade.source != "SHADOW",
                Trade.status == "CLOSED",
                Trade.exit_time >= today_start,
                Trade.exit_time < today_end,
            ))
            .order_by(Trade.exit_time)
        )
        closed_trades = closed_result.scalars().all()

    await _send_trade_report("PnL", open_positions, closed_trades)


async def handle_status(chat_id: str) -> None:
    """System snapshot: market, agent, feed, trades."""
    from app.agent.notification import send_telegram
    from app.core.utils import now_ist, is_market_open

    now = now_ist()
    date_str = now.strftime("%d %b %Y")
    today_start, today_end = _ist_today_range()

    # Market status
    market_open = is_market_open()
    if market_open:
        close_time = now.replace(hour=15, minute=30, second=0, microsecond=0)
        mins_left = max(0, int((close_time - now).total_seconds() // 60))
        market_line = f"🕐 Market: <b>OPEN</b> · {mins_left} min left"
    else:
        market_line = "🕐 Market: <b>CLOSED</b>"

    # Agent + config
    try:
        from app.agent.agent_runner import agent_runner
        from app.services.trading_config import get_trading_config
        cfg = await get_trading_config()
        agent_status = "Running" if agent_runner.is_running else "Stopped"
        agent_line = f"🤖 Agent: <b>{agent_status}</b> · {cfg.autonomy_level}"
    except Exception:
        agent_line = "🤖 Agent: —"
        cfg = None

    # Data feed
    try:
        from app.data_feed.feed_manager import feed_manager
        if feed_manager._last_tick_at:
            secs = int((now - feed_manager._last_tick_at).total_seconds())
            if secs < 90:
                feed_line = f"📡 Feed: <b>Live</b> ({secs}s ago)"
            else:
                feed_line = f"📡 Feed: <b>Stale</b> ({secs}s ago)"
        else:
            feed_line = "📡 Feed: <b>No ticks</b>"
    except Exception:
        feed_line = "📡 Feed: —"

    # Strategy windows / phases
    try:
        from app.services.strategy_params import get_strategy_params, parse_trading_windows, parse_dead_zone
        from app.core.utils import get_custom_window_state
        params = await get_strategy_params("vwap_pullback")
        windows = parse_trading_windows(params)
        dead_zone = parse_dead_zone(params)
        s2_state = get_custom_window_state(windows=windows, dead_zone=dead_zone)
        s2_line = f"S2 Window: {s2_state}"
    except Exception:
        s2_line = "S2 Window: —"

    try:
        from app.core.redis import get_redis
        r = get_redis()
        today_str = str(now.date())
        s5_phase = await r.get(f"strat5:phase:{today_str}") or "—"
        s5_line = f"S5 Phase: {s5_phase}"
    except Exception:
        s5_line = "S5 Phase: —"

    # Signals + trades from DB
    try:
        from app.core.database import async_session_factory
        from app.models.signal import Signal
        from app.models.trade import Trade
        from sqlalchemy import select, and_, func

        min_conf = cfg.min_confidence_for_execution if cfg else 70

        async with async_session_factory() as session:
            sig_count = (await session.execute(
                select(func.count(Signal.id)).where(and_(
                    Signal.generated_at >= today_start,
                    Signal.generated_at < today_end,
                    Signal.confidence >= min_conf,
                ))
            )).scalar() or 0

            open_trades = (await session.execute(
                select(func.count(Trade.id)).where(and_(
                    Trade.status == "OPEN",
                    Trade.source != "SHADOW",
                ))
            )).scalar() or 0

            closed_row = (await session.execute(
                select(
                    func.count(Trade.id),
                    func.coalesce(func.sum(Trade.pnl), 0),
                ).where(and_(
                    Trade.status == "CLOSED",
                    Trade.source != "SHADOW",
                    Trade.exit_time >= today_start,
                    Trade.exit_time < today_end,
                ))
            )).one()
            closed_count = closed_row[0] or 0
            closed_pnl = float(closed_row[1])

        conf_label = int(min_conf)
        signals_line = f"Signals: <b>{sig_count}</b> (≥{conf_label}% conf)"
        open_line = f"Open trades: <b>{open_trades}</b>"
        if closed_count:
            closed_line = f"Closed: <b>{closed_count}</b> · {_pnl_emoji(closed_pnl)} {_pnl_str(closed_pnl)}"
        else:
            closed_line = "Closed: <b>0</b>"
    except Exception:
        signals_line = "Signals: —"
        open_line = "Open trades: —"
        closed_line = "Closed: —"

    msg = (
        f"📋 <b>Status — {date_str}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"{market_line}\n"
        f"{agent_line}\n"
        f"{feed_line}\n\n"
        f"{s2_line}\n"
        f"{s5_line}\n\n"
        f"{signals_line}\n"
        f"{open_line}\n"
        f"{closed_line}"
    )
    await send_telegram(msg)


async def handle_market(chat_id: str) -> None:
    """Market overview: indices, VIX, global cues."""
    import json
    from app.agent.notification import send_telegram
    from app.core.utils import now_ist
    from app.core.redis import get_redis

    date_str = now_ist().strftime("%d %b %Y")
    today_str = str(now_ist().date())

    r = get_redis()

    # Index prices from Redis
    def _format_index(data: dict | None, name: str) -> str:
        if not data:
            return f"{name:<8} —"
        ltp = float(data.get("ltp", 0))
        chp = float(data.get("change_pct", 0))
        emoji = _chg_emoji(chp)
        return f"{name:<8} <b>{ltp:,.0f}</b>  {emoji} {chp:+.2f}%"

    nifty_raw = await r.get("price:NIFTY")
    bnifty_raw = await r.get("price:BANKNIFTY")
    vix_raw = await r.get("price:INDIA VIX")

    nifty = json.loads(nifty_raw) if nifty_raw else None
    bnifty = json.loads(bnifty_raw) if bnifty_raw else None
    vix = json.loads(vix_raw) if vix_raw else None

    nifty_line = _format_index(nifty, "NIFTY")
    bnifty_line = _format_index(bnifty, "BNIFTY")
    if vix:
        vix_ltp = float(vix.get("ltp", 0))
        vix_line = f"VIX      <b>{vix_ltp:.1f}</b>"
    else:
        vix_line = "VIX      —"

    # Global cues
    try:
        from app.services.morning_screener import get_global_cues
        cues = await get_global_cues(today_str)
        if cues:
            g_score = float(cues.get("global_score", 0))
            bias = cues.get("overnight_bias", "—")
            dow = float(cues.get("dow_futures_pct", 0))
            crude = float(cues.get("crude_pct", 0))
            dxy = float(cues.get("dxy_pct", 0))
            global_line = f"Global: <b>{g_score:.2f}</b> · {bias}"
            details_line = f"Dow {_chg_emoji(dow)} {dow:+.1f}%  Crude {_chg_emoji(crude)} {crude:+.1f}%  DXY {dxy:+.1f}%"
        else:
            global_line = "Global: —"
            details_line = ""
    except Exception:
        global_line = "Global: —"
        details_line = ""

    # Morning briefing approach
    try:
        from app.services.morning_screener import get_morning_briefing
        briefing = await get_morning_briefing(today_str)
        approach = briefing.get("approach", "—") if briefing else "—"
        approach_line = f"S5 Approach: <b>{approach}</b>"
    except Exception:
        approach_line = "S5 Approach: —"

    parts = [
        f"🌍 <b>Market — {date_str}</b>",
        "━━━━━━━━━━━━━━━━━━━━━",
        "",
        nifty_line,
        bnifty_line,
        vix_line,
        "",
        global_line,
    ]
    if details_line:
        parts.append(details_line)
    parts.append("")
    parts.append(approach_line)

    await send_telegram("\n".join(parts))


async def handle_signals(chat_id: str) -> None:
    """Today's actionable signals (above YOLO confidence threshold)."""
    from app.core.database import async_session_factory
    from app.agent.notification import send_telegram
    from app.core.utils import now_ist
    from app.models.signal import Signal
    from app.services.trading_config import get_trading_config
    from sqlalchemy import select, and_

    today_start, today_end = _ist_today_range()
    date_str = now_ist().strftime("%d %b %Y")
    cfg = await get_trading_config()
    min_conf = cfg.min_confidence_for_execution

    async with async_session_factory() as session:
        result = await session.execute(
            select(Signal)
            .where(and_(
                Signal.generated_at >= today_start,
                Signal.generated_at < today_end,
                Signal.confidence >= min_conf,
            ))
            .order_by(Signal.generated_at)
        )
        signals = result.scalars().all()

    if not signals:
        await send_telegram(
            f"🎯 <b>Signals — {date_str}</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"No actionable signals today."
        )
        return

    pending = [s for s in signals if s.status == "PENDING"]
    executed = [s for s in signals if s.status == "EXECUTED"]
    other = [s for s in signals if s.status not in ("PENDING", "EXECUTED")]

    total = len(signals)
    summary = f"Total: <b>{total}</b> ({len(pending)} pending · {len(executed)} executed)"

    parts = [
        f"🎯 <b>Signals — {date_str}</b>",
        "━━━━━━━━━━━━━━━━━━━━━",
        summary,
    ]

    def _sig_line(s):
        conf = int(float(s.confidence)) if s.confidence else 0
        strat = _strategy_short(s.strategy_name)
        t = s.generated_at.strftime("%H:%M")
        sig_type = (s.signal_type or "").replace("_", " ")
        return f"{s.symbol} · {sig_type} · {conf}% · {strat} · {t}"

    if pending:
        parts.append("")
        parts.append("⏳ <b>PENDING</b>")
        for s in pending:
            parts.append(_sig_line(s))

    if executed:
        parts.append("")
        parts.append("✅ <b>EXECUTED</b>")
        for s in executed:
            parts.append(_sig_line(s))

    if other:
        parts.append("")
        parts.append(f"📌 <b>OTHER ({len(other)})</b>")
        for s in other:
            parts.append(f"{_sig_line(s)} · {s.status}")

    await send_telegram("\n".join(parts))


async def handle_help(chat_id: str) -> None:
    """List all available commands."""
    from app.agent.notification import send_telegram

    msg = (
        "📱 <b>Commands</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        "/status — System snapshot\n"
        "/market — Market overview\n"
        "/pnl — Real trade PnL\n"
        "/shadow — Shadow trade PnL\n"
        "/yolo — YOLO trade PnL\n"
        "/signals — Today's signals\n"
        "/help — This message"
    )
    await send_telegram(msg)


# ── Command dispatch table ──────────────────────────────────────────────────

_HANDLERS = {
    "status":  handle_status,
    "market":  handle_market,
    "pnl":     handle_pnl,
    "shadow":  handle_shadow,
    "yolo":    handle_yolo,
    "signals": handle_signals,
    "help":    handle_help,
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
