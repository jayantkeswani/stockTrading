"""EOD signal expiry — expires stale PENDING intraday signals at 3:30 PM IST.

Intraday strategies generate signals that are only valid for the current trading
day.  Any PENDING signal left over at market close is noise — expire it so it
doesn't leak into the next day's dedup window or confuse the dashboard.

Positional strategies (CAN SLIM) are exempt: their signals may legitimately
persist across multiple days waiting for a breakout confirmation.
"""

import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import and_, update

from app.core.constants import IST
from app.core.enums import SignalStatus, StrategyName
from app.core.utils import is_trading_day, now_ist

logger = logging.getLogger(__name__)
scheduler = AsyncIOScheduler(timezone=IST)

_INTRADAY_STRATEGIES = (
    StrategyName.VWAP_PULLBACK.value,
    StrategyName.INTRADAY_FUTURES.value,
    StrategyName.ORB.value,
    StrategyName.GAMMA_SCALPING.value,
)


async def expire_intraday_signals() -> None:
    """Bulk-reject all remaining PENDING signals from intraday strategies.

    Only runs on trading days.  Positional strategies are untouched.
    """
    now = now_ist()
    if not is_trading_day(now.date()):
        logger.debug("Signal expiry: not a trading day, skipping")
        return

    from app.core.database import async_session_factory
    from app.models.signal import Signal

    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    try:
        async with async_session_factory() as session:
            result = await session.execute(
                update(Signal)
                .where(
                    and_(
                        Signal.status == SignalStatus.PENDING.value,
                        Signal.strategy_name.in_(_INTRADAY_STRATEGIES),
                        Signal.generated_at >= today_start,
                    )
                )
                .values(status=SignalStatus.EXPIRED.value)
            )
            await session.commit()

            expired_count = result.rowcount
            if expired_count:
                logger.info(
                    "Signal expiry: expired %d PENDING intraday signal(s)", expired_count
                )
            else:
                logger.debug("Signal expiry: no PENDING intraday signals to expire")
    except Exception:
        logger.exception("Signal expiry task failed")


async def start_signal_expiry_scheduler() -> None:
    scheduler.add_job(
        expire_intraday_signals,
        CronTrigger(hour=15, minute=30, timezone=IST),
        id="signal_expiry",
        replace_existing=True,
    )
    scheduler.start()
    logger.info("Signal expiry scheduler started (3:30 PM IST)")


async def stop_signal_expiry_scheduler() -> None:
    if scheduler.running:
        scheduler.shutdown(wait=False)
        logger.info("Signal expiry scheduler stopped")
