"""The Call 2 watcher — drives the at-open decision over the 09:18-09:30 IST window.

Hooked into the existing 1m candle-close loop (via feed_manager, on the NIFTY candle):
  - 09:15-09:18  arm (status -> WATCHING).
  - >= 09:18      fire Call 2. ENTER/SKIP finalizes the day; WAIT schedules a recheck
                  `recheck_in_minutes` (1-2) later, capped at 09:30.
  - 09:30         backstop — one final Call 2, then finalize regardless of the verdict.

State is in-memory (per day) for cadence, but finalization is also re-derived from the
persisted run status, so a backend restart mid-window never re-decides a day that already
reached ENTER/SKIP. Single-flight: a long LLM call can't be re-entered by the next minute.

Mirrors the validated offline watcher loop in `scripts/intraday_hunter/prototype_agent.py`.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, time

from app.config import settings
from app.core.constants import IST
from app.core.database import async_session_factory
from app.core.utils import is_trading_day
from app.services.intraday_hunter import decision, store, thesis

logger = logging.getLogger(__name__)

LEAD_INDEX = "NIFTY"          # one tick per minute drives the watcher
MARKET_OPEN = time(9, 15)
CALL2_START = time(9, 18)     # first decision checkpoint
CALL2_DEADLINE = time(9, 30)  # backstop / last possible Call 2
_TERMINAL = ("ENTER", "SKIP")


def _add_minutes(t: time, mins: int) -> time:
    total = min(t.hour * 60 + t.minute + mins, 23 * 60 + 59)
    return time(total // 60, total % 60)


class IntradayHunterWatcher:
    """Per-day cadence coordinator for Call 2."""

    def __init__(self) -> None:
        self._day: date | None = None
        self._done = False
        self._watching_set = False
        self._next_check = CALL2_START
        self._running = False

    def _reset_for_day(self, day: date) -> None:
        self._day = day
        self._done = False
        self._watching_set = False
        self._next_check = CALL2_START
        self._running = False

    async def on_candle_close(self, symbol: str, candle_ts: datetime) -> None:
        """feed_manager hook — fired per completed 1m candle. Acts only on the lead index."""
        if symbol != LEAD_INDEX or not settings.intraday_hunter_enabled:
            return
        ts = candle_ts.astimezone(IST) if candle_ts.tzinfo else candle_ts
        await self.maybe_run(ts.date(), ts.time())

    async def maybe_run(
        self, today: date, now_t: time, *, variant: str | None = None
    ) -> None:
        """Decide whether to fire Call 2 now. `now_t` is IST clock time (injectable for tests)."""
        if self._day != today:
            self._reset_for_day(today)
        if self._done or not is_trading_day(today):
            return
        # arm: flip THESIS_READY -> WATCHING once between the open and the first checkpoint
        if not self._watching_set and MARKET_OPEN <= now_t < CALL2_START:
            await self._set_watching(today)
            return
        if now_t < self._next_check or now_t < CALL2_START:
            return
        if self._running:  # single-flight: don't re-enter a Call 2 still in progress
            return
        self._running = True
        try:
            await self._run_once(today, now_t, variant or settings.intraday_hunter_variant)
        except Exception:  # noqa: BLE001 — a watcher error must never kill the candle loop
            logger.exception("intraday_hunter: watcher run failed")
        finally:
            self._running = False

    async def _set_watching(self, today: date) -> None:
        try:
            async with async_session_factory() as session:
                run = await store.get_run(session, today)
                if run and run.status == "THESIS_READY":
                    run.status = "WATCHING"
                    await session.commit()
                self._watching_set = True
        except Exception:  # noqa: BLE001
            logger.exception("intraday_hunter: failed to set WATCHING")

    async def _run_once(self, today: date, now_t: time, variant: str) -> None:
        is_backstop = now_t >= CALL2_DEADLINE
        eff_now = min(now_t, CALL2_DEADLINE)

        async with async_session_factory() as session:
            run = await store.get_run(session, today)
            # already finalized (e.g. survived a restart) -> stop watching
            if run and (run.status in _TERMINAL):
                self._done = True
                return
            # ensure a thesis exists (lazy Call 1 if the 08:45 task didn't run)
            if not (run and run.call1_json and not run.call1_json.get("error")):
                logger.info("intraday_hunter: no thesis at %s — running Call 1 lazily", now_t)
                run = await thesis.run_call1(session, today, variant=variant)
                await session.commit()
                if run.status in _TERMINAL:  # Call 1 failed -> day finalized as SKIP
                    self._done = True
                    return

            run = await decision.run_call2(session, today, now=eff_now, variant=variant)
            await session.commit()

        dec = (run.decision or "").upper() if run else ""
        if dec in _TERMINAL or is_backstop:
            self._done = True
            return
        if dec == "WAIT":
            step = self._recheck_step(run.call2_json if run else None)
            nxt = _add_minutes(now_t, step)
            self._next_check = nxt if nxt <= CALL2_DEADLINE else CALL2_DEADLINE
        # run_call2 -> None (no opening data yet): leave _next_check, retry next candle

    @staticmethod
    def _recheck_step(call2_json: dict | None) -> int:
        try:
            return max(1, min(int((call2_json or {}).get("recheck_in_minutes") or 1), 5))
        except (TypeError, ValueError):
            return 1


# Module-level singleton — imported by feed_manager (candle hook) + the scheduled task.
intraday_hunter_watcher = IntradayHunterWatcher()
