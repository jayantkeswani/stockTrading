# Option Data in Backtesting

## Two modes

### Accurate mode (default)

Fetches 1-minute candle history for the **specific option contract** the strategy would pick:

1. `strike_selector.resolve_option_symbol()` picks strike + expiry using historical spot price and the `as_of_date` (not `now_ist()`).
2. `option_data_fetcher.ensure_option_candles()` checks `market_data_1m` for existing rows; if missing, fetches from Fyers SDK and persists (idempotent `ON CONFLICT DO NOTHING`).
3. `exit_simulator` walks forward on real option 1m candles — wick-based SL/target detection (detects intrabar SL touches that delta-approx misses).

**Fyers retention:** ~6 months of 1m data for option contracts. Backtests older than ~6 months must use fast mode.

**Rate limiting:** fetches are chunked in ≤6-day windows with 0.5s sleep between chunks. A 6-month backtest generating ~40 signals results in ~40 Fyers API calls (manageable in minutes).

**Caching:** candles persist in `market_data_1m` after first fetch. Subsequent runs reuse DB data (instant).

### Fast mode (`--mode fast`)

Delta-approximates option P&L from spot moves:

- ATM strike: δ = 0.50
- 1-ITM strike: δ = 0.60
- `approx_premium = entry_premium + spot_move × delta`
- No Fyers API calls during replay.

Useful for: quick parameter sweeps, dates older than 6 months, running without a live Fyers token.

## OI data limitation

`oi_snapshots` is populated only from when `oi_snapshot_task` began running. For earlier dates:

- `ctx.oi_analysis = None` — the strategy's soft-filter already handles this gracefully.
- The report shows `oi_coverage_pct` so you can quantify how much of the backtest lacked OI.
- For a fair pre/post comparison, use the same date range for both baseline and post-fix runs.

## Option candles stored in `market_data_1m`

Option symbols (e.g. `NSE:NIFTY26APR24000CE`) are stored in the same `market_data_1m` table as spot candles. The `symbol` column is `VARCHAR(30)` which fits Fyers option symbol format. They're distinguishable by prefix (`NSE:NIFTY...CE/PE`). No schema migration needed.

## No full option chain history

Fetching all strikes for every OI level at every historical timestep would require hundreds of API calls per trading day and isn't necessary — we only need the one or two strikes the strategy would pick. Full chain history is not in scope.
