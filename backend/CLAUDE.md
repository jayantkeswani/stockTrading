# Backend - Python/FastAPI

## Tech Stack
- Python 3.11 (virtualenv at `backend/.venv`)
- FastAPI with async support, runs on port **8080**
- SQLAlchemy 2.0 (async engine via asyncpg, declarative models)
- Alembic for database migrations (PostgreSQL on port 5433)
- Pydantic v2 for schemas/settings
- Redis on port 6380 (via redis-py async)
- httpx for async HTTP
- fyers-apiv3 SDK for market data (REST + WebSocket)
- APScheduler for periodic tasks
- python-telegram-bot for notifications

## Module Map

### `app/config.py` - Application Settings
Pydantic Settings loading from `.env`. Key settings: database URLs, Redis URL, Fyers credentials, trading capital, risk limits, agent mode, ports.

### `app/main.py` - FastAPI Application
Lifespan startup: starts Fyers login scheduler, auto-starts data feed if token exists in Redis. CORS enabled for frontend (localhost:3000).

### `app/core/` - Foundation
- `database.py` - Async SQLAlchemy engine + session factory (`get_db` dependency)
- `redis.py` - Redis connection pool + pub/sub helpers (`get_redis`, `publish_event`). Price cache uses 24h TTL (`price:{symbol}`)
- `constants.py` - Market hours (9:15-15:30 IST), lot sizes, strike gaps per index (`STRIKE_GAPS`), expiry schedule (`WEEKLY_EXPIRY_DAYS`, `MONTHLY_ONLY_INDICES`, `MONTHLY_EXPIRY_DOW`), option exchange mapping, premium range (150-400), exchange codes, VWAP_PROXIMITY_PCT
- `enums.py` - All enums: OptionType, OrderSide, TradeStatus, ExitReason (incl. TRAILING_SL, EXPIRY_ROLL, MARKET_EXIT), SignalStatus, SignalType, StrategyName (incl. CAN_SLIM), IndexSymbol, InstrumentType (OPTION/FUTURE/EQUITY), PositionType (INTRADAY/POSITIONAL), AgentAutonomyLevel, AgentActionType, ConfirmationStatus, DayBias, CPRType
- `task_registry.py` - Singleton `TaskRegistry` tracking all background tasks/schedulers/services with status, timestamps, errors. `task_registry.register()` for schedulers/services, `task_registry.track_asyncio_task()` for fire-and-forget tasks (auto-updates via done callback). Queryable via `GET /api/v1/tasks`.
- `exceptions.py` - Custom exception hierarchy
- `utils.py` - IST timezone helpers (`now_ist()`, `is_market_open()`), market hour checks

### `app/models/` - SQLAlchemy ORM (10 tables)
- `base.py` - BaseModel: UUID primary key, created_at/updated_at timestamps
- `trade.py` - Trade: entry/exit prices, P&L, status, strategy link
- `signal.py` - Signal: strategy output, strike, expiry, confidence, `executable` flag, `blocked_reason`
- `position.py` - Position: active positions with unrealized P&L tracking
- `market_data.py` - MarketData1m: 1-minute OHLCV candles
- `oi_snapshot.py` - OISnapshot: open interest by strike price
- `agent_log.py` - AgentLog: agent action audit trail
- `daily_summary.py` - DailySummary: daily P&L, win/loss counts, drawdown
- `strategy_config.py` - StrategyConfig: per-index strategy enable/disable + parameters + `auto_mode` (bool) + `symbols` (JSONB list) + `symbol_map` (JSONB dict: short_name → fyers_symbol, populated at insertion time from symbol master search results)
- `fundamental_data.py` - StockFundamental (CAN SLIM scores + raw fundamentals per stock, updated by periodic task) + FundamentalHistory (quarterly snapshots for trend analysis)

### `app/schemas/` - Pydantic Schemas
Request/response schemas. Convention: `{Entity}Create`, `{Entity}Response`, `{Entity}Update`.
- `trade.py`, `signal.py`, `position.py`, `agent.py`, `market_data.py`, `risk.py`, `websocket.py`

### `app/api/v1/` - REST API (8 routers, all under `/api/v1/`)
- `trades.py` - CRUD for trades (create from signal, close, list, history)
- `signals.py` - List/filter signals by strategy, status, date
- `positions.py` - Active positions, close, update SL/target
- `agent.py` - Agent start/stop/status, confirm actions, YOLO toggle (`PATCH /yolo`)
- `risk.py` - Daily P&L, drawdown %, configured limits
- `market_data.py` - `GET /prices` (all symbols, auto-refreshes via REST if cache empty), `POST /prices/batch` (fetch prices for arbitrary Fyers symbols, used by watchlist), `GET /ohlcv/{symbol}?resolution=5&days=15` (chart data — proxies to Fyers history API with pagination for large ranges, deduped + sorted ascending; resolutions: "1"/"5"/"15"/"60"/"D"; falls back to PostgreSQL if Fyers unavailable; resolves symbol via symbol_map), `POST /feed/start|stop|refresh`, `GET /symbols/search` (local symbol master, supports stocks/futures/options: "TCS", "NIFTY 24000CE", "RELIANCE FUT")
- `watchlist.py` - `GET /watchlist`, `POST /watchlist`, `DELETE /watchlist/{symbol}` — Redis-backed watchlist (agent can add symbols programmatically). Adding a symbol also subscribes it on the Fyers WebSocket for live ticks.
- `strategies.py` - List/update strategy configs, toggle `is_active`/`auto_mode`, `POST /evaluate` (manual single symbol), `POST /evaluate/batch` (manual all configured symbols). When symbols are added to a strategy via PUT, the `symbol_map` (short_name → fyers_symbol) is stored alongside; any symbols missing from the map are auto-resolved server-side via the symbol master. Background task provisions new symbols: REST quote fetch → Redis, candle backfill → PostgreSQL, WebSocket subscription — using the stored Fyers symbols (no reconstruction).
- `tasks.py` - `GET /tasks` — lists all registered background tasks with type, status, timestamps, errors, metadata
- `auth.py` - Fyers OAuth flow (login redirect, callback, token storage)

### `app/websocket/` - Real-Time Layer
- `manager.py` - WebSocketManager: connect/disconnect/broadcast. Single `/ws` endpoint. Events: `price:update`, `signal:new`, `signal:updated` (dedup update), `trade:open`, `trade:close`, `position:pnl`, `agent:action`, `market:status`

### `app/services/` - Business Logic
- `strategy_runner.py` - Strategy evaluation engine. Two trigger paths: (1) **auto-mode**: FeedManager checks `strategy_configs` for `auto_mode=True` + symbol match on each candle close, (2) **manual**: `evaluate_manual()` called by the strategies API endpoint. Both paths share the same MarketContext builder, signal deduplication, persistence, and broadcast pipeline. **Signal dedup** (all strategies): if an identical PENDING signal exists (same strategy + symbol + direction + prices) → skip; if values changed → update in place; if prior was EXECUTED → create new. `get_auto_strategies_for_symbol()` queries which strategies should auto-evaluate for a given symbol.
- `option_resolver.py` - Resolves index-level signals to tradeable option contracts: strike selection (ATM/ITM), expiry selection (weekly for NIFTY/SENSEX, monthly for BANKNIFTY/FINNIFTY/MIDCPNIFTY), symbol master lookup, premium fetch (Redis → Fyers REST). SL/target computed via delta approximation from index-level SL/target (ATM delta=0.50, ITM delta=0.60); falls back to fixed-percentage on premium if index levels unavailable. Only runs for `instrument_type=OPTION` signals.
- `futures_resolver.py` - Resolves stock symbols to nearest-month futures contracts: expiry (last Thursday), Fyers symbol lookup, LTP fetch, lot size, margin estimation. Only runs for `instrument_type=FUTURE` signals.
- `candle_backfill.py` - On startup, backfills candles from Fyers historical API into `MarketData1m`: (1) previous trading day — so strategies have PDH/PDL/PDC/CPR context, (2) today's elapsed candles — so a late start doesn't miss the 9:45 trading window, (3) deep history (120 days in weekly chunks) for CAN SLIM symbols with < 50 days of data — needed for chart pattern detection. Backfills for all symbols: FYERS_SYMBOL_MAP indices (minus VIX) + symbols from active strategy configs. Uses `symbol_map` from strategy_configs for correct Fyers symbols (stored at insertion time); falls back to `NSE:{SYM}-EQ` only for legacy data. Uses ON CONFLICT DO NOTHING for idempotency.

### `app/strategies/` - Strategy Engine
- `base.py` - `BaseStrategy` ABC with `evaluate(ctx) -> StrategySignal | None`, `should_exit()`, `get_position_size()`. Defines `MarketContext` (current price, candles, VWAP, PDH/PDL, CPR, OI, VIX). `StrategySignal` carries `instrument_type` (OPTION/FUTURE/EQUITY) to control post-processing.
- `registry.py` - Discovers and instantiates active strategies from DB config
- `strategy_1_orb.py` - Opening Range Breakout (STUB - not implemented)
- `strategy_2_vwap_pullback.py` - VWAP Pullback + Previous Day Bias + OI (PRIMARY - fully implemented). Sets `instrument_type=OPTION`. Computes index-level SL/target from market structure via `market_levels.select_index_sl_target()` (VWAP bands, PDH/PDL, CPR, OI walls, swing levels); falls back to fixed `sl_pct`/`rr_multiplier` if no valid levels found. Skips signal if R:R < 1:1.
- `strategy_3_gamma_scalping.py` - Expiry Day Gamma Scalping (STUB - not implemented)
- `strategy_4_canslim.py` - CAN SLIM Growth Breakout (ACTIVE). Sets `instrument_type=FUTURE`, `holding_type=POSITIONAL`. Reads pre-fetched fundamentals from `stock_fundamentals` table, detects chart patterns from daily bars, confirms volume breakout. Entry: BUY_FUT. SL/target derived from pattern structure: SL at base_low × 0.98 (capped at 8%), target from measured move (floored at 20%). Trailing stop at breakeven after 10% gain.
- `canslim/` - CAN SLIM sub-package:
  - `scoring.py` - Pure scoring functions for each CAN SLIM factor (C/A/N/S/L/I/M), composite score. Re-exports `compute_rs_raw_score` and `percentile_rank_rs` from `app.indicators.relative_strength`
  - `base_patterns.py` - Chart base pattern detection: cup-with-handle, flat base, double bottom. `BasePattern` includes `base_low` (pattern's lowest price) used for SL placement.

### `app/indicators/` - Technical Indicators (pure functions, no side effects)
- `vwap.py` - VWAP calculation from candles, `is_pullback_to_vwap()`, `price_distance_from_vwap()`, VWAP bands
- `cpr.py` - Central Pivot Range: pivot, TC, BC, support/resistance levels, CPR type (WIDE/NARROW)
- `previous_day.py` - PDH, PDL, PDC, day bias (BULLISH/BEARISH/NEUTRAL), range calculation
- `open_interest.py` - PCR ratio, max pain, `is_oi_supporting_direction()`, sentiment analysis
- `vix.py` - India VIX fetch/mock
- `candle_patterns.py` - `is_bullish_reversal()`, `is_bearish_reversal()`, `average_volume()`
- `relative_strength.py` - IBD-style RS: `compute_rs_raw_score()` returns raw weighted return, `percentile_rank_rs()` converts to 1-99 percentile across stock universe. Also: 50-day moving average, `is_above_50_dma()`
- `volume_analysis.py` - Volume breakout detection (`is_volume_breakout()`), `compute_avg_volume()`, `volume_ratio()`
- `market_levels.py` - Index-level SL/target selection from market structure. `select_index_sl_target()` picks nearest support/resistance from VWAP bands, PDH/PDL, CPR levels, OI walls, swing highs/lows. `find_swing_low()`/`find_swing_high()` for intraday swing detection. Used by option-based strategies to emit meaningful SL/target for delta-based premium conversion.

### `app/data_feed/` - Fyers API Integration
- `fyers_auth.py` - OAuth flow using `SessionModel` from fyers_apiv3 SDK
- `fyers_auto_login.py` - Headless auto-login: base64-encoded credentials, TOTP generation via pyotp
- `fyers_client.py` - REST client: quotes, historical data, option chain, OI
- `fyers_ws_client.py` - WebSocket client: `FyersDataSocket` (threaded SDK bridged to asyncio), auto-fetches prices on start. **Important**: `connect()` must be called before `subscribe()` — the SDK's subscribe silently no-ops if the token hasn't been validated yet (which happens during connect).
- `symbol_master.py` - Downloads Fyers symbol master CSVs (NSE_CM/FO, BSE_CM/FO), parses ~127K symbols, stores gzip-compressed in Redis, provides in-memory search. Refreshed daily.
- `feed_manager.py` - Aggregates ticks into candles, persists completed 1m candles to `MarketData1m`, publishes to Redis, triggers `strategy_runner.on_candle_close()`

### `app/agent/` - AI Trading Agent
- `agent_runner.py` - Main agent loop (2s interval). Singleton. Manages YOLO mode toggle, dispatches to monitor/executor
- `trade_monitor.py` - Checks open positions: fetches price via `fyers_option_symbol` (Redis cache → Fyers REST fallback). SL hit → auto-close, target hit → confirm (SEMI) or auto-book (YOLO). **INTRADAY** positions: 3:15 PM time exit. **POSITIONAL** positions: trailing stop (SL moves to breakeven after 10% gain), expiry rollover alert (3 days before). 5% drawdown halt (all types).
- `auto_executor.py` - Executes signals automatically in YOLO mode. Subscribes to option symbol on websocket feed when position is opened.
- `notification.py` - Telegram Bot API: trade alerts, confirmation requests, P&L summaries

### `app/tasks/` - Scheduled Tasks
- `fyers_login_task.py` - APScheduler job: auto-refreshes Fyers token via TOTP login
- `symbol_master_task.py` - APScheduler job: refreshes symbol master daily at 8:00 AM IST
- `oi_snapshot_task.py` - APScheduler job: fetches option chain OI data from Fyers every 3 minutes during market hours. Persists CE/PE OI per strike to `oi_snapshots` table. Only runs when market is open. Feeds `strategy_runner._get_oi_analysis()`.
- `fundamental_data_task.py` - APScheduler job: fetches CAN SLIM fundamental data (yfinance + NSE) every 6 hours for all CAN SLIM-configured symbols. Computes individual factor scores and composite CAN SLIM score. Post-processing: percentile-ranks RS ratings across the full stock universe, fetches F&O lot sizes from NSE. Persists to `stock_fundamentals` table. Also runs on startup. Feeds `strategy_runner._get_canslim_fundamentals()`.

### `app/data_sources/` - External Data Sources for Fundamentals
- `yfinance_client.py` - Async wrappers around yfinance for quarterly earnings, annual financials, stock info, price history. Uses `.NS` suffix for NSE. All sync calls dispatched via `asyncio.to_thread`.
- `nse_client.py` - Fetches FII/DII/MF shareholding patterns from NSE India API, F&O lot sizes from NSE CSV. Rate-limited (2s between requests). Uses httpx with NSE-compatible headers. Two-step shareholding fetch: (1) master endpoint (`/api/corporate-share-holdings-master`) for quarterly records with promoter/public percentages + XBRL URLs, (2) XBRL XML parsing for detailed FII/DII/MF breakdown. Enriches latest 2 quarters automatically.
- `schemas.py` - Data transfer objects: `QuarterlyEarnings`, `AnnualFinancials`, `ShareholdingPattern`, `PriceHistory`, `StockInfo`.

## Conventions
- All async functions use `async def`
- Database sessions via FastAPI dependency injection (`Depends(get_db)`)
- Type hints on all function signatures
- IST timezone for all market-related times, stored as TIMESTAMPTZ
- UUID for all primary keys
- Services never import from `api/` — only the reverse
- Indicators are pure functions: candle data in, values out, no DB/Redis access
- Config loaded from `.env` via Pydantic Settings (see `.env.example`)

## How-To Guides

### Add a New Strategy
1. Create `app/strategies/strategy_N_name.py` extending `BaseStrategy`
2. Implement: `evaluate(ctx: MarketContext) -> StrategySignal | None`, `should_exit()`, `get_position_size()`
3. Add name to `StrategyName` enum in `app/core/enums.py`
4. Strategy is auto-discovered by `registry.py` — just needs a `strategy_configs` DB row
5. Add strategy doc in `docs/strategies/strategy-N-name.md`

### Add a New API Endpoint
1. Add or edit router in `app/api/v1/{resource}.py`
2. Add Pydantic schemas in `app/schemas/{resource}.py`
3. If new router file, include in `app/api/router.py`
4. Service logic goes in `app/services/`, not in the router

### Add a New Indicator
1. Create pure function in `app/indicators/{name}.py`
2. Add fields to `MarketContext` in `app/strategies/base.py`
3. Populate in `app/services/strategy_runner.py` when building context
4. Write tests in `tests/test_indicators/test_{name}.py`

### Add a New Database Table
1. Create model in `app/models/{name}.py` extending `BaseModel`
2. Import in `app/models/__init__.py` so Alembic discovers it
3. Run `make migration msg="add {name} table"`
4. Run `make migrate`

### Run Tests
```bash
cd backend && source .venv/bin/activate && python -m pytest tests/ -v --tb=short
```
