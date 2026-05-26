# Permanent Watchlist & "P" Badge

## What is the Permanent Watchlist?

A manually curated list of F&O stocks that are always included in the Strategy 5 (Intraday Futures) daily watchlist, regardless of whether the morning quant screener selects them. Stored in Redis key `strat5:watchlist:permanent` and mirrored to `strategy_configs.symbols` for the `intraday_futures` strategy.

Managed via:
- **UI**: Intraday Futures page > Permanent Watchlist card
- **API**: `GET/POST/DELETE /api/v1/intraday-futures/permanent-watchlist`

## Three Stock Categories

When the morning screener runs, each stock in the final watchlist falls into one of three categories:

| Category | How it gets there | Watchlist flag | `is_permanent_watchlist` on signals/trades |
|---|---|---|---|
| **Permanent-only** | In permanent list, screener did NOT independently find it | `manual: true` | `true` |
| **Overlap** | In permanent list AND screener independently found it | `from_permanent: true` (no `manual`) | `false` |
| **Screener-only** | Not in permanent list, screener found it on merit | Neither flag | `false` |

### Key distinction

Overlap stocks are treated as screener-found stocks for execution purposes. The screener independently validated them, so they don't need the permanent watchlist gates.

## "P" Badge Display Rules

| View | Shows "P"? | Condition |
|---|---|---|
| **Intraday Futures Watchlist** | Yes | `item.manual === true` (permanent-only and overlap items both show in the watchlist, but only permanent-only items get the badge) |
| **Signals** (Scanner Panel, Signals page) | Yes | `signal.is_permanent_watchlist === true` |
| **Trades** (Trades Table) | Yes | `trade.is_permanent_watchlist \|\| trade.signal_is_permanent_watchlist` |
| **Positions** (Active Positions, Closed Today) | Yes | `position.is_permanent_watchlist` (open) or trade flags (closed) |

Overlap stocks never show "P" in signals, trades, or positions because `is_permanent_watchlist` is `false` for them.

## Execution Gates

Two toggle gates in `trading_config` control whether permanent-only signals are auto-executed:

| Gate | Default | Effect |
|---|---|---|
| `shadow_skip_permanent_watchlist` | `true` | Shadow executor skips signals where `is_permanent_watchlist = true` |
| `yolo_skip_permanent_watchlist` | `true` | YOLO executor skips signals where `is_permanent_watchlist = true` |

These gates only affect permanent-only stocks. Overlap stocks pass through because their `is_permanent_watchlist` is `false`.

Configurable via Settings page > "Skip Pinned Signals" row.

## Screener Bypass Rules

Permanent-only stocks bypass two screener filters that could otherwise drop them:

- **Stage 2** (News): news-flagged stocks are normally dropped, but `manual` stocks are kept (line 990)
- **Stage 3** (LLM Confidence): LOW confidence and correlated-drop logic skip `manual` stocks (lines 1138, 1141)

Overlap stocks go through all screener stages normally since they don't have `manual: true`.

## Data Flow

```
Redis: strat5:watchlist:permanent (list of short names)
  |
  v
morning_screener.py — run_morning_screener()
  |-- Stock NOT in screener candidates:
  |     candidate["manual"] = True        --> permanent-only
  |-- Stock IS in screener candidates:
  |     candidate["from_permanent"] = True --> overlap
  |-- Stock not in permanent list:
  |     no flag                            --> screener-only
  |
  v
Redis: strat5:watchlist:{date} (final watchlist JSON)
  |
  v
strategy_runner.py — _enrich_strategy5_params()
  |-- Reads item.get("manual", False)
  |-- Stores as params["_is_permanent_watchlist"]
  |
  v
strategy_runner.py — after strategy.evaluate() [both auto and manual paths]
  |-- signal.indicators["is_permanent_watchlist"] = params["_is_permanent_watchlist"]
  |
  v
Signal DB row: is_permanent_watchlist column
  |
  v
Trade DB row: is_permanent_watchlist (copied from signal at execution)
  |
  v
Position API response: is_permanent_watchlist (joined from trade)
```

## Related Files

- `backend/app/services/morning_screener.py` — merge logic (lines 485-531)
- `backend/app/services/strategy_runner.py` — enrichment + signal injection
- `backend/app/agent/auto_executor.py` — YOLO skip gate
- `backend/app/agent/shadow_executor.py` — shadow skip gate
- `backend/app/api/v1/positions.py` — `_to_response()` joins trade flag
- `frontend/src/components/positions/ActivePositions.tsx` — open + closed badge
- `frontend/src/components/trades/TradesTable.tsx` — trades badge
- `frontend/src/components/dashboard/ScannerPanel.tsx` — signal badge
- `frontend/src/components/intraday-futures/Watchlist.tsx` — watchlist badge
- `frontend/src/app/signals/page.tsx` — signals page badge
