"""Scheduled task for automated Fyers login.

Runs:
- Once on application startup
- Daily at 8:55 AM IST (before market opens at 9:15 AM)

On success: stores token in Redis and sends Telegram notification.
On failure: schedules retries every 15 min (up to 10 attempts) before alerting.
"""

import logging
from datetime import datetime, timedelta

from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger

from app.agent.notification import send_telegram
from app.config import settings
from app.data_feed.fyers_auth import get_auth_url
from app.data_feed.fyers_auto_login import FyersAutoLoginError, auto_login_and_store

logger = logging.getLogger(__name__)

IST = ZoneInfo("Asia/Kolkata")

# Module-level scheduler instance
scheduler = AsyncIOScheduler(timezone=IST)

_MAX_LOGIN_RETRIES = 10
_RETRY_INTERVAL_MINUTES = 15


async def _start_data_feed_after_login() -> None:
    """Start the WS feed after a successful login if not already connected."""
    try:
        from app.data_feed.fyers_ws_client import fyers_ws_client

        if not fyers_ws_client.is_connected:
            await fyers_ws_client.start()
            logger.info("Data feed started after successful auto-login")
    except Exception as e:
        logger.error("Failed to start data feed after login: %s", e)


async def _schedule_login_retry(attempt: int) -> None:
    """Schedule the next login retry attempt via APScheduler DateTrigger."""
    next_run = datetime.now(IST) + timedelta(minutes=_RETRY_INTERVAL_MINUTES)
    scheduler.add_job(
        _retry_auto_login,
        args=[attempt],
        trigger=DateTrigger(run_date=next_run, timezone=IST),
        id=f"fyers_login_retry_{attempt}",
        name=f"Fyers Login Retry {attempt}",
        replace_existing=True,
    )
    logger.info(
        "Scheduled login retry %d at %s IST",
        attempt, next_run.strftime("%H:%M"),
    )


async def _retry_auto_login(attempt: int) -> None:
    """Single retry attempt; schedules the next one on failure.

    Args:
        attempt: Current retry number (1-based).
    """
    ist_now = datetime.now(IST).strftime("%H:%M:%S IST")
    logger.info("Fyers auto-login retry %d at %s", attempt, ist_now)

    try:
        await auto_login_and_store()
        await _start_data_feed_after_login()
        await send_telegram(f"✅ Fyers connected (retry {attempt}) — market data live")
        logger.info("Fyers auto-login retry %d succeeded", attempt)

    except Exception as e:
        logger.error("Fyers auto-login retry %d failed: %s", attempt, e)

        if attempt >= _MAX_LOGIN_RETRIES:
            logger.error("Fyers auto-login: exhausted %d retries — giving up", _MAX_LOGIN_RETRIES)
            await send_telegram(
                "❌ <b>Fyers login failed after all retries</b>\n"
                "Open the dashboard on your computer to log in."
            )
        else:
            await _schedule_login_retry(attempt + 1)


async def run_fyers_auto_login() -> None:
    """Execute auto-login and send appropriate notification."""
    ist_now = datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S IST")
    logger.info("Running Fyers auto-login at %s", ist_now)

    try:
        await auto_login_and_store()
        await _start_data_feed_after_login()

        msg = "✅ Fyers connected — market data live"
        logger.info("Fyers auto-login succeeded for user %s", settings.fyers_username)
        await send_telegram(msg)

    except FyersAutoLoginError as e:
        logger.error("Fyers auto-login failed: %s", e)
        await _schedule_login_retry(1)
        next_run_str = (datetime.now(IST) + timedelta(minutes=_RETRY_INTERVAL_MINUTES)).strftime("%H:%M IST")
        await send_telegram(
            f"⚠️ <b>Fyers login failed — retrying at {next_run_str}</b>\n"
            f"{e}"
        )

    except Exception as e:
        logger.exception("Unexpected error during Fyers auto-login: %s", e)
        await _schedule_login_retry(1)
        next_run_str = (datetime.now(IST) + timedelta(minutes=_RETRY_INTERVAL_MINUTES)).strftime("%H:%M IST")
        await send_telegram(
            f"⚠️ <b>Fyers login error — retrying at {next_run_str}</b>\n"
            f"{e}"
        )


def has_auto_login_credentials() -> bool:
    """Check if all required auto-login credentials are configured."""
    return bool(
        settings.fyers_app_id
        and settings.fyers_secret_key
        and settings.fyers_username
        and settings.fyers_pin
        and settings.fyers_totp_secret
    )


async def start_fyers_login_scheduler() -> None:
    """Initialize and start the Fyers login scheduler.

    - Runs auto-login immediately on startup
    - Schedules daily auto-login at 8:55 AM IST
    """
    if not has_auto_login_credentials():
        logger.warning(
            "Fyers auto-login credentials not fully configured. "
            "Skipping auto-login scheduler. Set FYERS_USERNAME, FYERS_PIN, "
            "and FYERS_TOTP_SECRET in .env to enable."
        )
        return

    # Schedule daily run at 8:55 AM IST (before market open at 9:15 AM)
    scheduler.add_job(
        run_fyers_auto_login,
        trigger=CronTrigger(hour=8, minute=55, timezone=IST),
        id="fyers_daily_login",
        name="Fyers Daily Auto-Login",
        replace_existing=True,
    )
    scheduler.start()
    logger.info("Fyers login scheduler started (daily at 8:55 AM IST)")

    # Run immediately on startup
    logger.info("Running Fyers auto-login on startup...")
    await run_fyers_auto_login()


async def stop_fyers_login_scheduler() -> None:
    """Shut down the scheduler gracefully."""
    if scheduler.running:
        scheduler.shutdown(wait=False)
        logger.info("Fyers login scheduler stopped")
