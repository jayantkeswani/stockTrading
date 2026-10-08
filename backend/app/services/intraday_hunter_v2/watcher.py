"""v2 Call 2 cadence — fires on the NIFTY 1m candle close (same feed_manager hook as v1).

The candle stamped 09:15 closes at ~09:16:00, so decision time = candle minute + 1:
  - 09:16 (the 09:15 candle close): first Call 2.
  - every minute after: re-check while the decision is WAIT.
  - 09:25 (`call2_deadline`): last call; a WAIT there becomes SKIP.
ENTER / SKIP finalize the day. Single-flight: if a call is still running when the next minute
arrives, that minute is skipped — unless it is the deadline, in which case a final deadline call
runs as soon as the in-flight call returns (so a day can never end in WAIT). Finalization is
re-derived from the persisted run status, so a restart never re-decides a finalized day.
"""
from __future__ import annotations

import asyncio
import logging
import time as _time
from datetime import date, datetime, time

from app.config import settings
from app.core.constants import IST
from app.core.database import async_session_factory
from app.core.utils import is_trading_day
from app.services.intraday_hunter import store
from app.services.intraday_hunter_v2 import decision, thesis
from app.services.intraday_hunter_v2.params import VARIANT, parse_hhmm, v2_active, v2_params_async

logger = logging.getLogger(__name__)

LEAD_INDEX = "NIFTY"
_TERMINAL = ("ENTER", "SKIP")


class V2Watcher:
    """Per-day v2 Call 2 coordinator."""

    def __init__(self) -> None:
        self._day: date | None = None
        self._done = False
        self._running = False
        self._pending_deadline: time | None = None

    def _reset(self, d: date) -> None:
        self._day, self._done, self._running, self._pending_deadline = d, False, False, None

    async def on_candle_close(self, symbol: str, candle_ts: datetime) -> None:
        """feed_manager hook (NIFTY only). Fire-and-forget; never raises."""
        if symbol != LEAD_INDEX or not settings.intraday_hunter_v2_enabled:
            return
        t_hook = _time.monotonic()
        ts = candle_ts.astimezone(IST) if candle_ts.tzinfo else candle_ts
        await self.maybe_run(ts.date(), decision.decision_time_for_candle(ts), t_hook=t_hook)

    async def maybe_run(self, d: date, now: time, *, t_hook: float | None = None) -> None:
        """Decide whether to fire a v2 Call 2 at decision time `now` (injectable for tests)."""
        if self._day != d:
            self._reset(d)
        if self._done or not is_trading_day(d) or not await v2_active():
            return
        params = await v2_params_async()
        first, deadline = parse_hhmm(params["call2_first"]), parse_hhmm(params["call2_deadline"])
        if now < first or now > deadline:
            return
        if self._running:
            if now >= deadline:
                self._pending_deadline = deadline
            logger.info("ih_v2: Call 2 still running — skipping the %s check", now.strftime("%H:%M"))
            return
        self._running = True
        try:
            await self._run_once(d, now, t_hook)
            if self._pending_deadline and not self._done:
                dl, self._pending_deadline = self._pending_deadline, None
                await self._run_once(d, dl, None)
        except Exception:  # noqa: BLE001 — a watcher error must never kill the candle loop
            logger.exception("ih_v2: watcher run failed")
        finally:
            self._running = False

    async def _run_once(self, d: date, now: time, t_hook: float | None) -> None:
        async with async_session_factory() as session:
            run = await store.get_run(session, d, VARIANT)
            if run and run.status in _TERMINAL:
                self._done = True
                return
            if run is None:  # create the row first so the lazy Call 1 can't race an insert
                run = await store.get_or_create_run(session, d, VARIANT)
                await session.commit()
            if not run.call1_json:
                # Lazy Call 1 is too slow for the open (Opus + charts) — run it in the background
                # and decide on live facts now; later checks pick up the thesis.
                asyncio.create_task(_lazy_call1(d), name="ih_v2_lazy_call1")
            run = await decision.run_call2(session, d, now=now, t_hook=t_hook)
            await session.commit()
        if run is not None and (run.decision or "").upper() in _TERMINAL:
            self._done = True


async def _lazy_call1(d: date) -> None:
    try:
        async with async_session_factory() as session:
            run = await store.get_run(session, d, VARIANT)
            if run and run.call1_json:
                return
            await thesis.run_call1(session, d)
            await session.commit()
    except Exception:  # noqa: BLE001
        logger.exception("ih_v2: lazy Call 1 failed")


ih_v2_watcher = V2Watcher()
