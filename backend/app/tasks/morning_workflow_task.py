"""Strategy 5 daily workflow scheduler.

8:00 AM IST  — Morning briefing (LLM synthesis of yesterday's performance)
8:30 AM IST  — Morning screener (3-stage pipeline: quant → news → LLM confidence)
9:08 AM IST  — Pre-open reassessment (gap-adjusted bias, live VIX, watchlist re-rank)
9:31 AM IST  — ORB level logging
15:15 PM IST — End-of-day summary agent log entry

All are idempotent per day (Redis key check prevents re-run).
Only runs on trading days.
"""

import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from app.core.constants import IST
from app.core.utils import is_trading_day, now_ist

logger = logging.getLogger(__name__)

scheduler = AsyncIOScheduler()


async def _run_briefing() -> None:
    today = now_ist().date()
    if not is_trading_day(today):
        logger.debug("Not a trading day — skipping morning briefing")
        return

    from app.services.morning_screener import run_morning_briefing, snapshot_global_cues

    try:
        await snapshot_global_cues(today)
        await run_morning_briefing(today)
        logger.info("Morning briefing completed for %s", today)
    except Exception:
        logger.exception("Morning briefing failed")


async def _run_screener() -> None:
    today = now_ist().date()
    if not is_trading_day(today):
        logger.debug("Not a trading day — skipping morning screener")
        return

    from app.services.morning_screener import run_morning_screener

    try:
        watchlist = await run_morning_screener(today)
        logger.info("Morning screener completed: %d stocks on watchlist", len(watchlist))
    except Exception:
        logger.exception("Morning screener failed")


async def _run_preopen_reassessment() -> None:
    today = now_ist().date()
    if not is_trading_day(today):
        logger.debug("Not a trading day — skipping pre-open reassessment")
        return

    from app.services.morning_screener import run_preopen_reassessment

    try:
        await run_preopen_reassessment(today)
        logger.info("Pre-open reassessment completed for %s", today)
    except Exception:
        logger.exception("Pre-open reassessment failed")


async def _log_orb_levels() -> None:
    """Log ORB high/low for each watchlist stock after ORB formation completes."""
    today = now_ist().date()
    if not is_trading_day(today):
        return

    from app.services.morning_screener import _append_agent_log, get_watchlist

    try:
        from app.strategies.registry import get_strategy
        from app.core.enums import StrategyName

        strategy = get_strategy(StrategyName.INTRADAY_FUTURES)
        if strategy is None:
            return

        orb_levels = getattr(strategy, "_orb_levels", {})
        watchlist = await get_watchlist(str(today))
        watchlist_symbols = {w["symbol"] for w in watchlist}

        logged = 0
        for symbol, levels in orb_levels.items():
            if symbol not in watchlist_symbols:
                continue
            orb_range = levels["high"] - levels["low"]
            msg = f"ORB set {symbol}: H={levels['high']:.2f} L={levels['low']:.2f} range={orb_range:.2f}"
            await _append_agent_log(today, "ORB", msg)
            logged += 1

        if logged > 0:
            await _append_agent_log(today, "PHASE", f"ORB formation complete — {logged} stocks tracked")
        logger.info("Logged ORB levels for %d stocks", logged)
    except Exception:
        logger.exception("ORB level logging failed")


async def _run_eod_summary() -> None:
    today = now_ist().date()
    if not is_trading_day(today):
        return

    from app.services.morning_screener import _append_agent_log

    try:
        from app.core.database import async_session_factory
        from app.models.trade import Trade
        from sqlalchemy import select, and_, func

        async with async_session_factory() as session:
            result = await session.execute(
                select(
                    func.count().label("total"),
                    func.count().filter(Trade.status == "CLOSED").label("closed"),
                    func.sum(Trade.pnl).filter(Trade.status == "CLOSED").label("net_pnl"),
                    func.count().filter(and_(Trade.status == "CLOSED", Trade.pnl > 0)).label("wins"),
                    func.count().filter(and_(Trade.status == "CLOSED", Trade.pnl <= 0)).label("losses"),
                ).where(
                    and_(
                        Trade.strategy_name == "intraday_futures",
                        func.date(Trade.entry_time) == today,
                    )
                )
            )
            row = result.one()

        total = row.total or 0
        closed = row.closed or 0
        net_pnl = float(row.net_pnl or 0)
        wins = row.wins or 0
        losses = row.losses or 0
        win_rate = round(wins / closed * 100, 1) if closed > 0 else 0

        msg = f"EOD: {total} trades, {closed} closed, {wins}W/{losses}L ({win_rate}%), P&L {net_pnl:+,.0f}"
        await _append_agent_log(today, "SYSTEM", msg)
        logger.info("Strategy 5 EOD summary: %s", msg)
    except Exception:
        logger.exception("EOD summary failed")


async def start_morning_workflow_scheduler() -> None:
    scheduler.add_job(
        _run_briefing,
        CronTrigger(hour=8, minute=0, timezone=IST),
        id="morning_briefing",
        replace_existing=True,
    )
    scheduler.add_job(
        _run_screener,
        CronTrigger(hour=8, minute=30, timezone=IST),
        id="morning_screener",
        replace_existing=True,
    )
    scheduler.add_job(
        _run_preopen_reassessment,
        CronTrigger(hour=9, minute=8, timezone=IST),
        id="strat5_preopen_reassessment",
        replace_existing=True,
    )
    scheduler.add_job(
        _log_orb_levels,
        CronTrigger(hour=9, minute=31, timezone=IST),
        id="strat5_orb_log",
        replace_existing=True,
    )
    scheduler.add_job(
        _run_eod_summary,
        CronTrigger(hour=15, minute=15, timezone=IST),
        id="strat5_eod_summary",
        replace_existing=True,
    )
    scheduler.start()
    logger.info("Morning workflow scheduler started (briefing 8:00, screener 8:30, preopen 9:08, EOD 15:15 IST)")


async def stop_morning_workflow_scheduler() -> None:
    if scheduler.running:
        scheduler.shutdown(wait=False)
        logger.info("Morning workflow scheduler stopped")
