"""Daily P&L summary — sent at 3:35 PM IST after market close."""

import logging
from datetime import datetime

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from app.core.constants import IST
from app.core.utils import now_ist

logger = logging.getLogger(__name__)
scheduler = AsyncIOScheduler(timezone=IST)


async def send_daily_summary() -> None:
    """Query today's closed trades and send a P&L summary via Telegram."""
    from app.core.constants import MARKET_OPEN
    from app.core.database import async_session_factory
    from app.models.trade import Trade
    from sqlalchemy import and_, select

    today = now_ist().date()
    today_start = datetime.combine(today, MARKET_OPEN, tzinfo=IST)

    try:
        async with async_session_factory() as session:
            result = await session.execute(
                select(Trade).where(
                    and_(
                        Trade.entry_time >= today_start,
                        Trade.status == "CLOSED",
                    )
                )
            )
            trades = result.scalars().all()

        if not trades:
            from app.agent.notification import send_telegram
            await send_telegram("📊 <b>Daily Summary</b>\nNo trades today.")
            return

        total = len(trades)
        wins = sum(1 for t in trades if (t.pnl or 0) > 0)
        losses = sum(1 for t in trades if (t.pnl or 0) <= 0)
        net_pnl = sum(float(t.pnl or 0) for t in trades)

        best = max(trades, key=lambda t: float(t.pnl or 0))
        worst = min(trades, key=lambda t: float(t.pnl or 0))

        from app.agent.notification import notify_daily_summary
        await notify_daily_summary(
            total=total,
            wins=wins,
            losses=losses,
            net_pnl=net_pnl,
            best_symbol=best.symbol,
            best_pnl=float(best.pnl or 0),
            worst_symbol=worst.symbol,
            worst_pnl=float(worst.pnl or 0),
        )
    except Exception:
        logger.exception("Error sending daily summary")


async def start_daily_summary_scheduler() -> None:
    scheduler.add_job(
        send_daily_summary,
        CronTrigger(hour=15, minute=35, timezone=IST),
        id="daily_summary",
        replace_existing=True,
    )
    scheduler.start()
    logger.info("Daily summary scheduler started (3:35 PM IST)")


async def stop_daily_summary_scheduler() -> None:
    if scheduler.running:
        scheduler.shutdown(wait=False)
        logger.info("Daily summary scheduler stopped")
