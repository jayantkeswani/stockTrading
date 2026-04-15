"""Scheduled task for automated Fyers login.

Runs:
- Once on application startup
- Daily at 8:55 AM IST (before market opens at 9:15 AM)

On success: stores token in Redis and sends Telegram notification.
On failure: sends Telegram alert with the manual login URL as fallback.
"""

import logging
from datetime import datetime

from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from app.agent.notification import send_telegram
from app.config import settings
from app.data_feed.fyers_auth import get_auth_url
from app.data_feed.fyers_auto_login import FyersAutoLoginError, auto_login_and_store

logger = logging.getLogger(__name__)

IST = ZoneInfo("Asia/Kolkata")

# Module-level scheduler instance
scheduler = AsyncIOScheduler(timezone=IST)


async def run_fyers_auto_login() -> None:
    """Execute auto-login and send appropriate notification."""
    ist_now = datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S IST")
    logger.info("Running Fyers auto-login at %s", ist_now)

    try:
        access_token = await auto_login_and_store()

        # Auto-start the data feed after successful login
        try:
            from app.data_feed.fyers_ws_client import fyers_ws_client

            if not fyers_ws_client.is_connected:
                await fyers_ws_client.start()
                logger.info("Data feed started after successful auto-login")
        except Exception as e:
            logger.error("Failed to start data feed after login: %s", e)

        msg = (
            f"<b>Fyers Auto-Login Successful</b>\n"
            f"Time: {ist_now}\n"
            f"User: {settings.fyers_username}\n"
            f"Token stored in Redis (TTL: 10 hours)\n"
            f"Data feed: starting"
        )
        logger.info("Fyers auto-login succeeded for user %s", settings.fyers_username)
        await send_telegram(msg)

    except FyersAutoLoginError as e:
        logger.error("Fyers auto-login failed: %s", e)
        manual_url = get_auth_url()
        msg = (
            f"<b>Fyers Auto-Login Failed</b>\n"
            f"Time: {ist_now}\n"
            f"Error: {e}\n\n"
            f"<b>Manual login required:</b>\n"
            f"<a href=\"{manual_url}\">Click here to login manually</a>"
        )
        await send_telegram(msg)

    except Exception as e:
        logger.exception("Unexpected error during Fyers auto-login: %s", e)
        manual_url = get_auth_url()
        msg = (
            f"<b>Fyers Auto-Login Error</b>\n"
            f"Time: {ist_now}\n"
            f"Error: {e}\n\n"
            f"<b>Manual login required:</b>\n"
            f"<a href=\"{manual_url}\">Click here to login manually</a>"
        )
        await send_telegram(msg)


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
