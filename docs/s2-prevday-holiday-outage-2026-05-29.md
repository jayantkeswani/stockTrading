# Strategy 2 prev-day outage after the 2026-05-28 holiday

**Date:** 2026-05-29 (first trading day after the Bakri Id holiday on 2026-05-28)
**Impact:** Strategy 2 (VWAP Pullback) generated **0 signals all day** — every evaluation was gated out. Strategy 5 was unaffected (23 signals). Paper trading only; no capital impact.

## Symptom

Production S2 agent log for 2026-05-29 showed 1,800 GATE rejections, every one:

```
<SYMBOL>: missing indicators (vwap=True, prev_day=False, cpr=False)
```

`prev_day=True` count was 0 — i.e. `MarketContext.previous_day` was `None` for every index on every candle, so `evaluate()` returned early.

## Root cause

`strategy_runner._query_previous_day()` finds the previous trading day by taking
`MAX(timestamp)` of `market_data_1m` rows before today's midnight, then reads that
day's 09:15–15:30 session for OHLC. It is meant to be holiday-robust (a holiday has
no candles, so it falls back to the prior real session).

That robustness was defeated by a **stray off-hours candle**: 57 symbols
(NIFTY, BANKNIFTY, FINNIFTY, INDIA VIX + bank stocks) had a single row stamped
**2026-05-28 17:44 IST (12:14 UTC)** — on the closed-market holiday. For those
symbols, `MAX(timestamp)` before 2026-05-29 landed on that 17:44 holiday row →
derived previous day = 2026-05-28 → the 09:15–15:30 query for that date returned
nothing → `_query_previous_day` returned `None` → S2 blocked.

Stock-futures symbols (NAUKRI, JSWENERGY, SAMMAANCAP…) had no such row, so they
correctly fell back to 2026-05-27 — which is why S5 kept working.

How the stray row got persisted: `feed_manager._emit_candle` only skipped
candles **before** 09:15 IST. It had no upper bound and no trading-day check, so a
stray tick (REST quote / reconnect) at 17:44 IST on a holiday passed the guard and
was written to `market_data_1m`.

Note: this is **distinct** from the documented "9,600 midnight-UTC rows" cleanup
(those are at 00:00 UTC); that purge would not have fixed this.

## Fix

1. **Prevention** — `feed_manager._emit_candle` now gates persistence on
   `is_market_open(ts)` (trading day **and** 09:15–15:30 IST, True in simulated
   mode). Off-hours / holiday / weekend ticks are still published to Redis/WS for
   display but never written to `market_data_1m`.
2. **Defense-in-depth** — `_query_previous_day` now restricts its "last trading
   day" search to in-session candles via
   `cast(func.timezone('Asia/Kolkata', timestamp), Time) BETWEEN 09:15 AND 15:30`,
   so any stray off-hours row that slips in (e.g. via a backfill path) can no
   longer make it latch onto a non-session date.
3. **Tooling** — `scripts/audit_vwap_data.py` and `scripts/audit_screener_data.py`
   were importing a stale hardcoded `NSE_HOLIDAYS` (missing 2026-05-28); both now
   import the canonical set from `app.core.constants`.

## Verification

- 6/6 `test_feed_manager.py` tests pass (incl. new in-session vs holiday persist guard).
- 739 service/strategy/indicator tests pass.
- End-to-end against the local DB: seeding the exact poison shape (May 27 session +
  a May 28 17:44 row) and running the real `_query_previous_day(today=2026-05-29)`
  returns the correct May 27 levels (PDH/PDL/PDC) instead of `None`.

## Cleanup (done 2026-05-30)

- Purged all **70,980 off-session rows** from prod `market_data_1m` (IST time
  outside 09:15–15:30): 38,526 post-close quote-snapshot rows, 13,323 pre-open
  rows, 9,475 stale 00:00-IST daily-proxy rows, and the 9,656 midnight-UTC
  daily-hack rows. In-session rows (2,377,857) untouched. A CSV backup of the
  deleted rows is retained on the VM under `/tmp`.
- Migrated `scripts/replay_strategy5.py::fetch_daily_candles` to read daily bars
  from `market_data_daily` — it still read the midnight-UTC rows in
  `market_data_1m`, orphaned by the 2026-05-01 daily-table migration, so the
  purge would otherwise have starved it of daily context.
- Added an explicit `is_trading_day()` guard to `candle_backfill._persist_candles`
  (deep-history + WS-reconnect gap backfill) so non-trading-day rows are
  impossible from that writer too — every `market_data_1m` writer is now
  session-guarded.
