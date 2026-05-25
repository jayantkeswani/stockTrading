"""Daily P&L summary — sent at 3:35 PM IST after market close.

Rich AI-drafted post-market report with market close, sector P&L,
F&O build-up, OI levels, trade summary, and LLM narrative.
Shadow trades are excluded.
"""

import json
import logging
from datetime import datetime

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from app.core.constants import IST
from app.core.utils import is_trading_day, now_ist

logger = logging.getLogger(__name__)
scheduler = AsyncIOScheduler(timezone=IST)

_EOD_SCHEMA = {
    "type": "object",
    "properties": {
        "market_wrap": {"type": "string"},
        "trading_assessment": {"type": "string"},
    },
    "required": ["market_wrap", "trading_assessment"],
}

_EOD_SYSTEM_PROMPT = (
    "You are a market analyst summarizing the day for an Indian stock futures "
    "intraday trading system. Write two concise paragraphs:\n"
    "1. market_wrap: 2-3 sentences about the market day (Nifty/BankNifty movement, "
    "key themes, sector rotation). Reference specific numbers.\n"
    "2. trading_assessment: 2-3 sentences about our trading performance "
    "(what worked, what didn't, how the morning plan played out vs actual results).\n"
    "Be factual and specific. No fluff."
)


async def send_daily_summary() -> None:
    """Query today's closed trades (excluding shadow) and send a rich EOD report."""
    from app.core.constants import MARKET_OPEN
    from app.core.database import async_session_factory
    from app.core.enums import TradeSource
    from app.core.redis import get_redis
    from app.data.sectors import get_sector
    from app.models.trade import Trade
    from sqlalchemy import and_, select

    today = now_ist().date()
    if not is_trading_day(today):
        logger.debug("Not a trading day — skipping daily summary")
        return

    today_start = datetime.combine(today, MARKET_OPEN, tzinfo=IST)

    try:
        async with async_session_factory() as session:
            result = await session.execute(
                select(Trade).where(
                    and_(
                        Trade.entry_time >= today_start,
                        Trade.status == "CLOSED",
                        Trade.source != TradeSource.SHADOW.value,
                    )
                )
            )
            trades = result.scalars().all()

        # Gather market data
        redis = get_redis()

        # Nifty/BN closing prices
        nifty_data = await redis.get("price:NIFTY")
        bn_data = await redis.get("price:BANKNIFTY")
        nifty_parsed = json.loads(nifty_data) if nifty_data else {}
        nifty_ltp = nifty_parsed.get("ltp", 0)
        nifty_change = nifty_parsed.get("change", 0)
        nifty_pct = nifty_parsed.get("change_pct", 0)
        bn_parsed = json.loads(bn_data) if bn_data else {}
        bn_ltp = bn_parsed.get("ltp", 0)
        bn_change = bn_parsed.get("change", 0)
        bn_pct = bn_parsed.get("change_pct", 0)

        # Global cues and briefing from Redis
        from app.services.morning_screener import get_global_cues, get_morning_briefing, get_watchlist
        global_cues = await get_global_cues(str(today)) or {}
        briefing = await get_morning_briefing(str(today)) or {}

        if not trades:
            # No trades — send minimal message with market close
            lines = [f"📊 <b>Post-Market Report – {today.strftime('%d %b %Y')}</b>", ""]
            if nifty_ltp:
                n_arrow = "▲" if nifty_change >= 0 else "▼"
                lines.append(f"Nifty: {nifty_ltp:,.0f} {n_arrow} {nifty_change:+,.0f} pts ({nifty_pct:+.2f}%)")
            lines.append("")
            lines.append("📊 No trades today.")
            from app.agent.notification import send_telegram, _paper
            lines[0] = f"{_paper()}{lines[0]}"
            await send_telegram("\n".join(lines))
            return

        # Compute trade stats
        total = len(trades)
        wins = sum(1 for t in trades if (t.pnl or 0) > 0)
        losses = sum(1 for t in trades if (t.pnl or 0) <= 0)
        def _trade_pnl(t) -> float:
            return float(t.net_pnl) if t.net_pnl is not None else float(t.pnl or 0)

        net_pnl = sum(_trade_pnl(t) for t in trades)
        best = max(trades, key=_trade_pnl)
        worst = min(trades, key=_trade_pnl)

        # Sector breakdown
        sector_pnl: dict[str, float] = {}
        for t in trades:
            sector = get_sector(t.symbol) or "OTHER"
            sector_pnl.setdefault(sector, 0.0)
            sector_pnl[sector] += _trade_pnl(t)

        # F&O build-up and OI levels
        from app.agent.notification import _classify_fo_buildup, _get_nifty_bn_oi_levels
        watchlist = await get_watchlist(str(today)) or []
        wl_symbols = [w.get("symbol", "") for w in watchlist if isinstance(w, dict)]
        fo_buildup = await _classify_fo_buildup(wl_symbols) if wl_symbols else {}
        oi_levels = await _get_nifty_bn_oi_levels()

        # LLM draft
        llm_result = {}
        try:
            from app.research.llm_client import create_llm_client
            llm = create_llm_client(pro=True)

            trade_details = [
                {"symbol": t.symbol, "side": t.side, "pnl": float(t.pnl or 0),
                 "exit_reason": t.exit_reason, "source": t.source}
                for t in trades
            ]

            eod_data = {
                "date": str(today),
                "trades": trade_details,
                "stats": {"total": total, "wins": wins, "losses": losses, "net_pnl": net_pnl},
                "sector_pnl": {k: round(v, 0) for k, v in sorted(sector_pnl.items(), key=lambda x: x[1], reverse=True)},
                "nifty_close": nifty_ltp,
                "nifty_change_pct": nifty_pct,
                "bn_close": bn_ltp,
                "bn_change_pct": bn_pct,
                "morning_approach": briefing.get("approach"),
                "morning_summary": briefing.get("summary"),
                "vix": global_cues.get("india_vix_live"),
            }

            llm_result = await llm.generate_json(
                prompt=f"Generate post-market analysis from this data:\n{json.dumps(eod_data, default=str)}",
                system=_EOD_SYSTEM_PROMPT,
                max_tokens=1024,
                response_schema=_EOD_SCHEMA,
            )
        except Exception:
            logger.warning("LLM failed for EOD summary, using basic format")

        # Format message
        msg = _format_eod_message(
            today=today,
            nifty_ltp=nifty_ltp, nifty_change=nifty_change, nifty_pct=nifty_pct,
            bn_ltp=bn_ltp, bn_change=bn_change, bn_pct=bn_pct,
            total=total, wins=wins, losses=losses, net_pnl=net_pnl,
            best_symbol=best.symbol, best_pnl=_trade_pnl(best),
            worst_symbol=worst.symbol, worst_pnl=_trade_pnl(worst),
            sector_pnl=sector_pnl,
            fo_buildup=fo_buildup,
            oi_levels=oi_levels,
            llm_result=llm_result,
        )
        from app.agent.notification import send_telegram
        await send_telegram(msg)

    except Exception:
        logger.exception("Error sending daily summary")


def _format_eod_message(
    *,
    today,
    nifty_ltp: float, nifty_change: float, nifty_pct: float,
    bn_ltp: float, bn_change: float, bn_pct: float,
    total: int, wins: int, losses: int, net_pnl: float,
    best_symbol: str, best_pnl: float,
    worst_symbol: str, worst_pnl: float,
    sector_pnl: dict[str, float],
    fo_buildup: dict,
    oi_levels: dict,
    llm_result: dict,
) -> str:
    """Format the full EOD Telegram message from pre-computed stats and LLM result."""
    from app.agent.notification import _paper

    pnl_emoji = "📈" if net_pnl >= 0 else "📉"
    lines = [
        f"{_paper()}📊 <b>Post-Market Report – {today.strftime('%d %b %Y')}</b>",
        "",
    ]

    # Market close
    if nifty_ltp:
        n_arrow = "▲" if nifty_change >= 0 else "▼"
        lines.append(f"{pnl_emoji} <b>Market Close</b>")
        lines.append(f"Nifty 50: {nifty_ltp:,.0f} {n_arrow} {nifty_change:+,.0f} pts ({nifty_pct:+.2f}%)")
    if bn_ltp:
        bn_arrow = "▲" if bn_change >= 0 else "▼"
        lines.append(f"Bank Nifty: {bn_ltp:,.0f} {bn_arrow} {bn_change:+,.0f} pts ({bn_pct:+.2f}%)")
    lines.append("")

    # Market wrap (LLM)
    market_wrap = llm_result.get("market_wrap", "")
    if market_wrap:
        lines.append("━━━━━━━━━━━━━━━━")
        lines.append(f"📌 <b>Market Wrap</b>")
        lines.append(market_wrap)
        lines.append("")

    # Sector P&L
    if sector_pnl:
        sorted_sectors = sorted(sector_pnl.items(), key=lambda x: x[1], reverse=True)
        top = [(k, v) for k, v in sorted_sectors if v > 0][:3]
        bottom = [(k, v) for k, v in sorted_sectors if v < 0][-3:]
        if top or bottom:
            lines.append(f"📊 <b>Sector PnL</b>")
            for sector, pnl in top:
                lines.append(f"  {sector}: +₹{pnl:,.0f}")
            for sector, pnl in bottom:
                lines.append(f"  {sector}: −₹{abs(pnl):,.0f}")
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

    # Trading summary
    lines.append("━━━━━━━━━━━━━━━━")
    lines.append(f"📊 <b>Trading Summary</b>")
    lines.append(f"Trades: {total} | W: {wins} L: {losses}")
    net_pnl_str = f"+₹{net_pnl:,.0f}" if net_pnl >= 0 else f"−₹{abs(net_pnl):,.0f}"
    lines.append(f"Net PnL: {net_pnl_str}")
    best_str = f"+₹{best_pnl:,.0f}" if best_pnl >= 0 else f"−₹{abs(best_pnl):,.0f}"
    worst_str = f"+₹{worst_pnl:,.0f}" if worst_pnl >= 0 else f"−₹{abs(worst_pnl):,.0f}"
    lines.append(f"Best: {best_symbol} {best_str} | Worst: {worst_symbol} {worst_str}")

    # Trading assessment (LLM)
    assessment = llm_result.get("trading_assessment", "")
    if assessment:
        lines.append("")
        lines.append(f"💡 {assessment}")

    msg = "\n".join(lines)
    if len(msg) > 4000:
        msg = msg[:3997] + "..."
    return msg


async def start_daily_summary_scheduler() -> None:
    """Start the daily P&L summary scheduler (3:35 PM IST)."""
    scheduler.add_job(
        send_daily_summary,
        CronTrigger(hour=15, minute=35, timezone=IST),
        id="daily_summary",
        replace_existing=True,
    )
    scheduler.start()
    logger.info("Daily summary scheduler started (3:35 PM IST)")


async def stop_daily_summary_scheduler() -> None:
    """Shut down the daily summary scheduler gracefully."""
    if scheduler.running:
        scheduler.shutdown(wait=False)
        logger.info("Daily summary scheduler stopped")
