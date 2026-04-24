# Backtest Harness — How to Run

## Prerequisites

1. **Fyers authentication**: a valid Fyers token must be in Redis (authenticate via the dashboard or run the auto-login task).
2. **Historical candle data** in `market_data_1m` for the backtest period.
3. **Python virtualenv active**: `source backend/.venv/bin/activate`

## Step 1 — Backfill historical spot candles

```bash
python scripts/backfill_for_backtest.py \
    --symbols NIFTY,BANKNIFTY,SENSEX \
    --start 2025-10-01 \
    --end 2026-04-24

# Dry-run to count rows without inserting:
python scripts/backfill_for_backtest.py --symbols NIFTY --start 2025-10-01 --end 2026-04-24 --dry-run
```

Expects ~375 candles per symbol per trading day (09:15–15:30, 1m resolution).

## Step 2 — Run the backtest

```bash
# Accurate mode (default): fetches real 1m option premium history from Fyers
python scripts/backtest.py \
    --strategy vwap_pullback \
    --symbol NIFTY \
    --start 2025-10-01 \
    --end 2026-04-24

# Fast mode: delta-approximation, no Fyers API calls (good for quick sweeps)
python scripts/backtest.py --strategy vwap_pullback --symbol NIFTY \
    --start 2025-10-01 --end 2026-04-24 --mode fast

# Include out-of-window signals (default: only trade-window minutes)
python scripts/backtest.py --strategy vwap_pullback --symbol NIFTY \
    --start 2025-10-01 --end 2026-04-24 --no-window-filter

# Save report as JSON
python scripts/backtest.py --strategy vwap_pullback --symbol NIFTY \
    --start 2025-10-01 --end 2026-04-24 --save-json /tmp/report.json
```

## Reading the report

```
============================================================
  Backtest Report: vwap_pullback | NIFTY
  2025-10-01 → 2026-04-24  [mode: accurate]
============================================================
  Signals generated : 42
  Trades simulated  : 38  (CE: 28, PE: 10)
  No-data skips     : 4
  Wins / Losses     : 22 / 16  (hit rate: 57.9%)
  Avg win           : +28.4%
  Avg loss          : 19.2%
  Expectancy        : +5.2% per trade
  Profit factor     : 1.71
  Total P&L pts     : +4,320
  OI coverage       : 62%

  Confidence calibration:
    [ <55]: 0 trades,   0.0% hit rate
    [55-64]: 8 trades,  50.0% hit rate
    [65-74]: 18 trades, 55.6% hit rate
    [75-84]: 10 trades, 70.0% hit rate
    [ 85+]: 2 trades,  100.0% hit rate
```

**Key metrics to watch:**
- `OI coverage` < 50% means most signals ran without OI context (pre-task period) — re-run with a narrower date range for a fair comparison.
- Confidence calibration should show higher hit rates in higher buckets — if not, the confidence formula needs tuning.
- Compare CE vs PE counts to verify PE signals are firing after bias fixes.
