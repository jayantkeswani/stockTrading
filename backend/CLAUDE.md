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
Pydantic Settings loading from `.env`. Key settings: database URLs, Redis URL, Fyers credentials, trading capital, risk limits, agent mode, ports. AI Research: `google_api_key`, `research_llm_provider` (default "gemini"), `research_llm_model` (default "gemini-2.5-flash"), `research_agent_timeout_seconds` (90), `research_max_concurrent` (3). **Phase 2**: `ai_confidence_enabled` (bool, default True — LLM overlay for signals), `fire_confidence_threshold` (float, default 55.0 — minimum deterministic score to generate a signal), `ai_confidence_timeout_seconds` (int, default 8).

### `app/main.py` - FastAPI Application
Lifespan startup: starts Fyers login scheduler, auto-starts data feed if token exists in Redis. CORS enabled for frontend (localhost:3000).

### `app/core/` - Foundation
- `database.py` - Async SQLAlchemy engine + session factory (`get_db` dependency)
- `redis.py` - Redis connection pool + pub/sub helpers (`get_redis`, `publish_event`). Price cache uses 24h TTL (`price:{symbol}`)
- `constants.py` - Market hours (9:15-15:30 IST), lot sizes, strike gaps per index (`STRIKE_GAPS`), expiry schedule (`WEEKLY_EXPIRY_DAYS`, `MONTHLY_ONLY_INDICES`, `MONTHLY_EXPIRY_DOW`), option exchange mapping, premium range (150-400), exchange codes, VWAP_PROXIMITY_PCT, `NSE_HOLIDAYS: frozenset[date]` (2024-2026 NSE trading holidays for holiday-aware backfill and `_previous_trading_day`)
- `enums.py` - All enums: OptionType, OrderSide, TradeStatus, ExitReason (incl. TRAILING_SL, EXPIRY_ROLL, MARKET_EXIT), SignalStatus, SignalType, StrategyName (incl. CAN_SLIM), IndexSymbol, InstrumentType (OPTION/FUTURE/EQUITY), PositionType (INTRADAY/POSITIONAL), AgentAutonomyLevel, AgentActionType (incl. MANUAL_EXECUTED, AUTO_EXECUTED, EXPIRY_ROLL, **SHADOW_EXECUTED**), ConfirmationStatus, DayBias, CPRType, **TradeSource** (MANUAL/YOLO/SHADOW)
- `task_registry.py` - Singleton `TaskRegistry` tracking all background tasks/schedulers/services with status, timestamps, errors. `task_registry.register()` for schedulers/services, `task_registry.track_asyncio_task()` for fire-and-forget tasks (auto-updates via done callback). Queryable via `GET /api/v1/tasks`.
- `exceptions.py` - Custom exception hierarchy
- `utils.py` - IST timezone helpers (`now_ist()`, `is_market_open()`), market hour checks. **All window/deadline helpers accept an optional `as_of: datetime | None` param** (defaults to `now_ist()` so live paths are unchanged; backtest passes historical timestamps). New: `is_trading_day(d: date)` (checks weekday + NSE_HOLIDAYS), `get_window_state(as_of)` (returns `"IN_WINDOW" | "DEAD_ZONE" | "OUT_OF_WINDOW"`)
- `retry.py` - Async retry helper. `async_retry(func, *args, retries, base_delay, max_delay, jitter, retry_on, should_retry, on_retry, label, **func_kwargs)` — exponential backoff with jitter, no third-party deps. `with_retry(**kwargs)` is the decorator form. Used by: `fyers_client` (REST + 401 reauth), `notification.send_telegram` (3×), `trade_monitor._fetch_option_price_rest` (2×), `fyers_ws_client._run_gap_backfill` (per-symbol range fetch).

### `app/models/` - SQLAlchemy ORM (10 tables)
- `base.py` - BaseModel: UUID primary key, created_at/updated_at timestamps
- `trade.py` - Trade: entry/exit prices, P&L, status, strategy link. **`source` column** (`VARCHAR(20)`, default `"MANUAL"`, indexed): `"MANUAL"` = user clicked EXEC, `"YOLO"` = agent auto-executed, `"SHADOW"` = ghost agent for accuracy testing. Backfilled on migration from `agent_logs`.
- `signal.py` - Signal: strategy output, strike, expiry, confidence, `executable` flag, `blocked_reason`
- `position.py` - Position: active positions with unrealized P&L tracking. **`is_shadow` column** (`BOOLEAN`, default `false`, indexed): `true` for shadow agent positions — excluded from all default API queries and the Active Positions widget.
- `market_data.py` - MarketData1m: 1-minute OHLCV candles
- `oi_snapshot.py` - OISnapshot: open interest by strike price
- `agent_log.py` - AgentLog: agent action audit trail
- `daily_summary.py` - DailySummary: daily P&L, win/loss counts, drawdown
- `strategy_config.py` - StrategyConfig: per-index strategy enable/disable + parameters + `auto_mode` (bool) + `symbols` (JSONB list) + `symbol_map` (JSONB dict: short_name → fyers_symbol, populated at insertion time from symbol master search results)
- `fundamental_data.py` - StockFundamental (CAN SLIM scores + raw fundamentals per stock, updated by periodic task) + FundamentalHistory (quarterly snapshots for trend analysis)
- `research_report.py` - ResearchReport (persisted AI research report: recommendation, confidence, report_json/markdown, agent tracking) + ResearchAgentRun (individual sub-agent run: findings_json, summary, duration, data sources). FK cascade delete.
- `signal.py` - Signal: strategy output, strike, expiry, confidence, `executable` flag, `blocked_reason`. **Phase 2 additions**: `ai_summary String(300)`, `ai_rationale Text`, `ai_adjustment Numeric(4,1)` (LLM ±15 adj), `ai_action String(30)`. `indicators` JSONB also carries `confidence_factors`, `intraday_bias`, `window_state`, `ai_key_supports`, `ai_key_risks`.
- `global_market_snapshot.py` - **GlobalMarketSnapshot** (Phase 1): 15-min snapshots of world indices + FX + commodities. Columns: `timestamp, dow_futures_pct, sp500_close_pct, nasdaq_close_pct, nifty_pct, crude_pct, usdinr_pct, dxy_pct, us_vix, pre_open_gap_pct, global_score` (derived [-1,+1]) + raw absolute prices. Unique constraint on `timestamp`. Used by backtest `context_builder` for historical `global_cues`.

### `app/schemas/` - Pydantic Schemas
Request/response schemas. Convention: `{Entity}Create`, `{Entity}Response`, `{Entity}Update`.
- `trade.py`, `signal.py`, `position.py`, `agent.py`, `market_data.py`, `risk.py`, `websocket.py`

### `app/api/v1/` - REST API (8 routers, all under `/api/v1/`)
- `trades.py` - CRUD for trades (create from signal, close, list, history). `GET /trades` supports optional query params: `status`, `strategy`, `source` (default excludes SHADOW; pass `source=SHADOW` to see only shadow trades), `closed_since`, `entry_since`, `entry_until`; `limit` up to 1000. `GET /trades/summary` also accepts `?source=SHADOW` to compute hit rate / P&L stats over shadow trades only.
- `signals.py` - List/filter signals by strategy, status, date. `GET /{id}/preview` returns live entry price + lot sizing for the confirm modal. `POST /{id}/execute` accepts optional `{lots: int}` override, uses snapshotted `signal.lots` if available, fetches live LTP via `live_price.get_live_price()` as the trade entry price. `POST /{id}/reject` marks signal as rejected.
- `positions.py` - Active positions, close, update SL/target. `GET /positions` enriches positions with live prices from Redis cache (computes `current_price` and `unrealized_pnl` at query time).
- `agent.py` - Agent start/stop/status, confirm actions, YOLO toggle (`PATCH /yolo`)
- `risk.py` - Daily P&L (total + `closed_pnl` separated for frontend live calculation), drawdown %, configured limits
- `market_data.py` - `GET /prices` (all symbols incl. watchlist items, auto-refreshes via REST if cache empty), `POST /prices/batch` (fetch prices for arbitrary Fyers symbols, used by watchlist), `GET /ohlcv/{symbol}?resolution=5&days=15` (chart data — proxies to Fyers history API with pagination for large ranges, deduped + sorted ascending; resolutions: "1"/"5"/"15"/"60"/"D"; falls back to PostgreSQL if Fyers unavailable; `_resolve_symbol_for_chart` handles short names, Fyers symbols, and strategy symbol_map), `POST /feed/start|stop|refresh`, `GET /symbols/search` (local symbol master, min 1 char, supports stocks/futures/options: "TCS", "NIFTY 24000CE", "RELIANCE FUT", "industries" via display name substring)
- `watchlist.py` - `GET /watchlist`, `POST /watchlist`, `DELETE /watchlist/{symbol}` — Redis-backed watchlist (agent can add symbols programmatically). Adding a symbol also subscribes it on the Fyers WebSocket for live ticks. Each item stores `added_at: time.time()` in its metadata; `GET` sorts by `added_at` ascending so insertion order is preserved across reloads (Redis hashes don't guarantee order).
- `strategies.py` - List/update strategy configs, toggle `is_active`/`auto_mode`, `POST /evaluate` (manual single symbol), `POST /evaluate/batch` (manual all configured symbols). When symbols are added to a strategy via PUT, the `symbol_map` (short_name → fyers_symbol) is stored alongside; any symbols missing from the map are auto-resolved server-side via the symbol master. Background task provisions new symbols: REST quote fetch → Redis, candle backfill → PostgreSQL, WebSocket subscription — using the stored Fyers symbols (no reconstruction).
- `settings.py` - `GET /settings/trading` (returns current `TradingConfigDTO`), `PATCH /settings/trading` (partial update; validates field names + autonomy_level enum; all changes take effect immediately via pubsub cache invalidation).
- `tasks.py` - `GET /tasks` — lists all registered background tasks with type, status, timestamps, errors, metadata
- `auth.py` - Fyers OAuth flow (login redirect, callback, token storage)
- `research.py` - `POST /start` (validate symbol, create report, launch orchestrator task), `GET /reports` (list past reports, ?symbol filter), `GET /reports/{id}` (full report with agent runs), `DELETE /reports/{id}`. Returns 429 if max concurrent sessions reached.

### `app/websocket/` - Real-Time Layer
- `manager.py` - WebSocketManager: connect/disconnect/broadcast. Single `/ws` endpoint. Events: `price:update`, `signal:new`, `signal:updated` (dedup update), `trade:open`, `trade:close`, `position:pnl`, `agent:action`, `market:status`

### `app/services/` - Business Logic
- `trading_config.py` - Single source of truth for user-editable trading parameters. `TradingConfigDTO` (frozen dataclass) with `capital`, `max_daily_drawdown_pct`, `max_risk_per_trade_pct`, `max_trades_per_day`, `paper_trading`, `autonomy_level`. `get_trading_config()` returns cached DTO (O(1) after startup). `update_trading_config(**fields)` writes to DB, refreshes cache, publishes `config:trading:updated` pubsub event. `ensure_seeded()` inserts the singleton row from `.env` on first boot. `start_config_listener()` subscribes to pubsub and reloads cache when any writer publishes.
- `position_sizing.py` - `calculate_lots(capital, risk_per_trade_pct, entry_price, stop_loss, lot_size, *, vix_multiplier=1.0, max_lots=None) -> int`. Single source of truth for position sizing — absorbed VIX-aware and max-lots-cap behavior. `vix_to_multiplier(india_vix)` converts a VIX level to a 0.8–1.1 multiplier. Each strategy sets `max_lots` as a class attribute on `BaseStrategy`.
- `live_price.py` - `get_live_price(fyers_symbol) -> float`. Fetches live LTP: tries Redis price cache first, falls back to Fyers REST `/quotes` API. Raises `HTTPException(503)` if both fail. Used by `signals.py` (manual execute) and `auto_executor.py` (YOLO) so trades are always filled at current market price, never a stale signal premium.
- `strategy_runner.py` - Strategy evaluation engine. Two trigger paths: (1) **auto-mode**: FeedManager checks `strategy_configs` for `auto_mode=True` + symbol match on each candle close, (2) **manual**: `evaluate_manual()` called by the strategies API endpoint. Both paths share the same MarketContext builder, signal deduplication, persistence, and broadcast pipeline. **Open position check**: `_has_open_position()` skips signal generation entirely if an open position exists for the same symbol+direction. **Signal dedup** (all strategies): if an identical PENDING signal exists (same strategy + symbol + direction + prices) → skip; if values changed → update in place; if prior was EXECUTED → create new. `get_auto_strategies_for_symbol()` queries which strategies should auto-evaluate for a given symbol.
- `option_resolver.py` - Resolves index-level signals to tradeable option contracts: strike selection (ATM/ITM), expiry selection (weekly for NIFTY/SENSEX, monthly for BANKNIFTY/FINNIFTY/MIDCPNIFTY), symbol master lookup, premium fetch (Redis → Fyers REST). SL/target computed via delta approximation from index-level SL/target (ATM delta=0.50, ITM delta=0.60); falls back to fixed-percentage on premium if index levels unavailable. Only runs for `instrument_type=OPTION` signals.
- `futures_resolver.py` - Resolves stock symbols to nearest-month futures contracts: expiry (last Thursday), Fyers symbol lookup, LTP fetch, lot size, margin estimation. Only runs for `instrument_type=FUTURE` signals. `resolve_futures_contract(symbol, entry_price, from_date=None)` — pass `from_date=current_expiry + 1 day` to resolve the *next* month's contract (used by expiry roll).
- `candle_backfill.py` - On startup, backfills candles from Fyers historical API into `MarketData1m`: (1) previous trading day — so strategies have PDH/PDL/PDC/CPR context, (2) today's elapsed candles — so a late start doesn't miss the 9:45 trading window, (3) deep history (120 days in weekly chunks) for CAN SLIM symbols with < 50 days of data — needed for chart pattern detection. Backfills for all symbols: FYERS_SYMBOL_MAP indices (minus VIX) + symbols from active strategy configs. Uses `symbol_map` from strategy_configs for correct Fyers symbols (stored at insertion time); falls back to `NSE:{SYM}-EQ` only for legacy data. Uses ON CONFLICT DO NOTHING for idempotency.

### `app/strategies/` - Strategy Engine
- `base.py` - `BaseStrategy` ABC with `evaluate(ctx) -> StrategySignal | None`, `should_exit()`. Class attribute `max_lots: int | None = None` — subclasses override to cap position size (VWAP: 5, CAN SLIM: 2). `StrategySignal` carries `instrument_type` (OPTION/FUTURE/EQUITY) to control post-processing; also carries `lots`, `quantity`, `sizing_meta` set by `strategy_runner._snapshot_sizing()` after resolution.
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
- `global_market.py` - **Global market context** (Phase 1). Pure functions + `GlobalCues` dataclass. `compute_pre_open_gap(sgx_nifty, prev_close)`, `overnight_bias(dow_pct, sp500_pct, us_vix) -> DayBias`, `combined_global_score(cues) -> float [-1,+1]`, `global_alignment_factor(cues, direction) -> float [0,1]`. Injected into `MarketContext.global_cues`. No DB/Redis access (pure).
- `intraday_bias.py` - **Composite intraday directional bias** (Phase 2). `compute_intraday_bias(prev_day, candles_1m, vwap, current_price, global_cues) -> IntradayBias(bias, score [-1,+1], strength STRONG/MODERATE/WEAK, components)`. Weights: yesterday close_position 0.25, gap 0.20, VWAP slope 0.25, price-vs-VWAP 0.10, global 0.10, candle momentum 0.10. `is_blocked_by_bias(direction, bias)` — returns True only when bias is STRONG and opposite direction. Replaces yesterday-only hard gate.
- `confidence.py` - **Deterministic confidence composite** (Phase 2). `compute_confidence(...) -> ConfidenceResult(score 0-100, factors dict, rationale_short str)`. 10 weighted factors: bias_alignment 0.20, vwap_slope 0.10, reversal_quality 0.15, volume_quality 0.10, rr_ratio 0.10, oi_support 0.10, cpr_narrow 0.05, vix_regime 0.05, global_alignment 0.10, time_of_day 0.05. Replaces inline `base=70 ± constants`.

### `app/data_feed/` - Fyers API Integration
- `fyers_auth.py` - OAuth flow using `SessionModel` from fyers_apiv3 SDK
- `fyers_auto_login.py` - Headless auto-login: base64-encoded credentials, TOTP generation via pyotp. `auto_login_and_store()` runs the full 5-step TOTP flow and caches the token in Redis (`fyers:access_token`, 10h TTL). `trigger_reauth()` is the lock-guarded public entry point for mid-session auth recovery — uses a 60s cooldown + `asyncio.Lock` to collapse concurrent 401s into a single TOTP login.
- `fyers_client.py` - REST client: quotes, historical data, option chain (v3 endpoint), OI. Uses separate `API_URL` and `DATA_URL` base URLs. `get_option_chain()` resolves short names via `FYERS_SYMBOL_MAP`. All four public methods route through `_request_with_auth()` which: (1) reads the freshest token from Redis each call, (2) detects HTTP 401 or Fyers JSON auth error codes (`-16`, `-17`, `-300`), (3) calls `trigger_reauth()` once and retries, (4) retries transient 5xx/network errors up to 3× via `async_retry`.
- `fyers_ws_client.py` - WebSocket client: `FyersDataSocket` (threaded SDK bridged to asyncio), auto-fetches prices on start. Maintains `_reverse_map` (Fyers symbol → internal short name) seeded from `FYERS_SYMBOL_MAP` (indices) and extended via `register_symbol_map()` when stock symbols are subscribed. `_fyers_to_internal()` converts all incoming ticks to short names (O(1) lookup). `subscribe_symbols()` accepts optional `symbol_map` for reverse lookup registration. **Important**: `connect()` must be called before `subscribe()` — the SDK's subscribe silently no-ops if the token hasn't been validated yet (which happens during connect). **Reconnect + backfill**: `_on_close` records `_last_disconnect_at` during market hours and calls `feed_manager.clear_in_progress_candles()`. `_on_connect` detects a reconnect (vs initial connect) by checking `_last_disconnect_at`, then schedules `_run_gap_backfill(disconnect_at, reconnect_at)` which fetches the candle gap from Fyers using `candle_backfill._fetch_history_range_via_sdk` and persists via `candle_backfill._persist_candles` (idempotent, no strategy re-eval). **Auth-error recovery**: `_on_error` pattern-matches Fyers auth failure strings and schedules `_trigger_reauth_and_restart()`.
- `symbol_master.py` - Downloads Fyers symbol master CSVs (NSE_CM/FO, BSE_CM/FO), parses ~127K symbols, stores as plain JSON in Redis (keys: `symbols:master`, `symbols:master:updated_at`; 24h TTL; plain JSON required because the shared Redis pool uses `decode_responses=True`), provides in-memory search. Search supports: exact/prefix/substring on short name, substring on display name + Fyers symbol (scored 100→20). Refreshed daily.
- `feed_manager.py` - Aggregates ticks into candles, persists completed 1m candles to `MarketData1m`, publishes to Redis, triggers `strategy_runner.on_candle_close()`. `process_tick()` accepts optional `fyers_alias` — when provided, caches and broadcasts the price under **both** the internal short name and the Fyers-qualified name (dual-name publishing). This ensures DB/strategies use short names while watchlist/positions can look up prices by Fyers symbol. `clear_in_progress_candles()` wipes the in-progress candle dict on WS disconnect so the seam candle doesn't emit with stale data on reconnect.

### `app/research/` - AI Research Agent System
Multi-agent stock research system. User searches for any stock → orchestrator spawns 6 specialized agents in parallel → synthesis agent combines findings → report persisted to DB.
- `orchestrator.py` - Coordinates research: creates `asyncio.Task`, launches agents via `asyncio.gather(return_exceptions=True)`, broadcasts progress via WebSocket, persists to `ResearchReport`/`ResearchAgentRun` tables. Supports up to 3 concurrent sessions. Registered in `TaskRegistry`. Persist phase wrapped in try/except with fallback minimal persist. `_sanitize_for_jsonb()` cleans NaN/Infinity/Decimal/datetime before JSONB storage (yfinance returns NaN for missing ratios, PostgreSQL JSONB rejects NaN).
- `llm_client.py` - Provider-agnostic LLM wrapper. `LLMClient` ABC with `generate()` and `generate_with_search()`. Default impl: `GeminiClient` (google-generativeai SDK). `generate_with_search()` uses Gemini's Google Search grounding for real-time news. Factory: `create_llm_client()` from config.
- `data_gatherer.py` - Pre-fetches shared context (stock info, 1Y price history, existing fundamentals) into `ResearchContext` dataclass. Runs once before agents to avoid redundant API calls. **On-demand fundamental fetch**: if `stock_fundamentals` row is missing or stale (>24h), calls `_fetch_and_store_symbol()` from `fundamental_data_task` to populate it — so research works for any stock, not just CAN SLIM-configured ones.
- `report_builder.py` - Template-based fallback report if LLM synthesis fails.
- `agents/base.py` - `BaseResearchAgent` ABC, `AgentResult` dataclass, `ResearchContext` dataclass. Each agent: fetch data → LLM interpret → return structured findings + summary.
- `agents/fundamental.py` - Quarterly earnings, annual financials, CAN SLIM scores (reuses `canslim/scoring.py`)
- `agents/technical.py` - Trend (50/200 DMA), RS rating, RSI, chart patterns (reuses `indicators/` + `base_patterns.py`), support/resistance
- `agents/oi_derivatives.py` - PCR, max pain, OI buildup. Only runs for F&O-eligible stocks (graceful skip otherwise).
- `agents/institutional.py` - FII/DII/MF shareholding from NSE API, QoQ trend analysis
- `agents/news_sentiment.py` - Uses Gemini grounded search for real-time Indian stock news. Returns articles with URLs + sentiment scores.
- `agents/valuation.py` - PE/PB/PEG ratios, dividend yield, sector comparison from yfinance
- `agents/synthesis.py` - Combines all agent findings via LLM → executive summary, BUY/HOLD/SELL recommendation, confidence score, actionable entry/SL/target levels

### `app/agent/` - AI Trading Agent
- `agent_runner.py` - Main agent loop (2s interval). Singleton. Manages YOLO mode toggle, dispatches to monitor/executor. `on_new_signal()` always sends a Telegram signal alert (all modes), then auto-executes only if YOLO + running.
- `trade_monitor.py` - Checks open positions: fetches price via `fyers_option_symbol` (Redis cache → Fyers REST fallback with 2 retries via `async_retry`). SL hit → auto-close, target hit → confirm (SEMI) or auto-book (YOLO). **INTRADAY** positions: 3:15 PM time exit. **POSITIONAL** positions: trailing stop (SL moves to breakeven after 10% gain), expiry roll 3 days before expiry (`_roll_futures_position` closes old contract + opens next month via `futures_resolver`, broadcasts `trade:open`). `_close_position` returns dict with `"action_type"` key; sends typed Telegram notification per exit reason. `_request_profit_confirmation` guards against repeat requests (checks for existing PENDING log before creating another). Shadow positions (is_shadow=True) are monitored exactly like real paper positions — same SL/target/EOD logic applies.
- `auto_executor.py` - Executes signals automatically in YOLO mode. Checks duplicate open positions + final risk check (POSITIONAL and **SHADOW** trades excluded from daily trade count and drawdown P&L). Sets `Trade.source = "YOLO"`. Broadcasts `trade:open` (position in UI immediately) then `agent:auto_executed`. Sends Telegram via `notify_auto_executed`. Drawdown breach triggers `notify_drawdown_halt`. All action dicts use `"action_type"` key.
- `shadow_executor.py` - **Shadow agent** for signal accuracy measurement. `shadow_execute_signal(signal_id)` is called fire-and-forget from `strategy_runner._handle_signal` on every signal regardless of `executable` flag. No gating: no `executable` check, no position dedup, no risk check. Creates `Trade(source="SHADOW", is_paper=True)` + `Position(is_shadow=True)`. Logs `AgentLog(SHADOW_EXECUTED)`. Falls back to `signal.entry_price` if live price unavailable. Broadcasts `trade:open` with `is_shadow=True` so the frontend drops it from the main positions slice. See `docs/ai/shadow-agent.md` for isolation guarantees and month-end query templates.
- `notification.py` - All Telegram notifications. **No ORM imports** — callers pass plain scalars. Functions: `notify_signal_generated`, `notify_auto_executed`, `notify_sl_hit`, `notify_profit_booked`, `notify_time_exit`, `notify_confirmation_request`, `notify_expiry_roll`, `notify_expiry_roll_failed`, `notify_drawdown_halt`, `notify_daily_summary`. `send_telegram(message)` retries up to 3× with 2s base delay via `async_retry` — Telegram 429/5xx are handled silently (log-and-return-False). Paper trading prefixes messages with 📄.

### `app/tasks/` - Scheduled Tasks
- `fyers_login_task.py` - APScheduler job: auto-refreshes Fyers token via TOTP login at 8:55 AM IST. On success sends `✅ Fyers connected — market data live`. On failure: schedules retry jobs every 15 min (up to 10 attempts via `_retry_auto_login(attempt)` + `DateTrigger`), Telegram alert only after exhausting all retries.
- `symbol_master_task.py` - APScheduler job: refreshes symbol master daily at 8:00 AM IST.
- `oi_snapshot_task.py` - APScheduler job: fetches option chain OI data from Fyers v3 API every 3 minutes during market hours. Parses flat option chain format (per-row `option_type`/`oi`/`oich` fields, DD-MM-YYYY expiry dates). Persists CE/PE OI per strike to `oi_snapshots` table. Only runs when market is open. Feeds `strategy_runner._get_oi_analysis()`.
- `signal_confidence.py` (in `research/agents/`) - **LLM confidence overlay** (Phase 2). `score_signal(signal, ctx) -> SignalConfidence`. Builds complete JSON context (all indicator values), sends to Gemini with a structured system prompt, returns `confidence_adjustment ±15`, `summary`, `rationale`, `key_supports`, `key_risks`, `recommended_action`, `suggested_lot_adjustment`. Called in `strategy_runner._run_ai_confidence_overlay` after option/futures resolve, before `_persist_signal`. 8-second timeout; never blocks signal on failure. Controlled by `settings.ai_confidence_enabled`.
- `global_market_task.py` - **APScheduler job every 15 minutes** (Phase 1). Fetches 8 yfinance tickers (Dow futures, S&P 500, Nasdaq, Nifty proxy, crude, USD/INR, DXY, US VIX). Writes to Redis keys `indicator:global:{field}` (TTL 20 min) AND inserts `GlobalMarketSnapshot` row to DB. Runs once on startup immediately. `_get_global_cues_from_redis()` is called by `strategy_runner._build_market_context` to populate `MarketContext.global_cues`.
- `fundamental_data_task.py` - APScheduler job: fetches CAN SLIM fundamental data (yfinance + NSE) every 6 hours for all CAN SLIM-configured symbols. Computes individual factor scores and composite CAN SLIM score. Post-processing: percentile-ranks RS ratings across the full stock universe, fetches F&O lot sizes from NSE. Persists to `stock_fundamentals` table. Also runs on startup. Feeds `strategy_runner._get_canslim_fundamentals()`.
- `daily_summary_task.py` - APScheduler job: sends daily P&L summary via Telegram at 3:35 PM IST. Queries today's closed trades, computes wins/losses/net P&L, best/worst trade. Calls `notify_daily_summary`.

### `app/data_sources/` - External Data Sources for Fundamentals
- `yfinance_client.py` - Async wrappers around yfinance for quarterly earnings, annual financials, stock info, price history. Uses `.NS` suffix for NSE. All sync calls dispatched via `asyncio.to_thread`.
- `nse_client.py` - Fetches FII/DII/MF shareholding patterns from NSE India API, F&O lot sizes from NSE CSV. Rate-limited (2s between requests). Uses httpx with NSE-compatible headers. Two-step shareholding fetch: (1) master endpoint (`/api/corporate-share-holdings-master`) for quarterly records with promoter/public percentages + XBRL URLs, (2) XBRL XML parsing for detailed FII/DII/MF breakdown. Enriches latest 2 quarters automatically.
- `schemas.py` - Data transfer objects: `QuarterlyEarnings`, `AnnualFinancials`, `ShareholdingPattern`, `PriceHistory`, `StockInfo`.

### `app/backtest/` - Backtest Harness (Phase 1)
Common replay framework for all strategies. Bypasses `strategy_runner` entirely — calls `strategy.evaluate(ctx)` directly, so no DB writes, no WS events, no agent auto-execution.

- `context_builder.py` - `build_historical_context(symbol, as_of: datetime, session) -> MarketContext | None`. Builds MarketContext from historical `MarketData1m` + `OISnapshot` + `GlobalMarketSnapshot` rows filtered by `timestamp <= as_of`. Replays VWAP calculation from today's candles up to `as_of`; previous-day from `_previous_trading_day()` (holiday-aware); OI from latest snapshot `<= as_of`; global_cues from latest `GlobalMarketSnapshot`. Returns None if insufficient data.
- `harness.py` - `Backtester(mode, window_filter).run(strategy, symbol, start, end) -> BacktestReport`. Walks minute-by-minute through historical data. Supports `accurate` mode (real option premiums via Fyers history) and `fast` mode (delta approximation). `window_filter=True` only evaluates within Strategy 2 trade windows.
- `exit_simulator.py` - `simulate_exit(signal, entry_ts, spot_candles_after, fyers_option_symbol, entry_premium, mode)`. Accurate mode: walks option 1m candles via `ensure_option_candles`; wick-based SL/target detection. Fast mode: delta-approximates from spot moves (ATM δ=0.50, ITM δ=0.60). Returns `SimulatedTrade` with `pnl_per_lot`, `pnl_pct`, `exit_reason`.
- `option_data_fetcher.py` - `ensure_option_candles(fyers_option_symbol, start_ts, end_ts)`. Checks DB first (option candles stored in `MarketData1m`); fetches from Fyers SDK and persists if missing. In-memory cache per symbol within a backtest run.
- `strike_selector.py` - `select_expiry_as_of(symbol, as_of_date)` and `resolve_option_symbol(symbol, index_price, signal_type, as_of_date)`. Holiday-aware expiry selection using historical date instead of `now_ist()`.
- `report.py` - `BacktestReport` + `build_report()` + `print_report()`. Metrics: hit rate, avg win/loss, expectancy, profit factor, CE/PE breakdown, `oi_coverage_pct`, confidence-bucket calibration (does a 90-conf signal win more often than a 70-conf one?).

**CLI usage:**
```bash
source backend/.venv/bin/activate

# Run historical data backfill first (requires Fyers token in Redis)
python scripts/backfill_for_backtest.py --symbols NIFTY,BANKNIFTY --start 2025-10-01 --end 2026-04-24

# Run backtest
python scripts/backtest.py --strategy vwap_pullback --symbol NIFTY --start 2025-10-01 --end 2026-04-24

# Fast mode (no Fyers API calls)
python scripts/backtest.py --strategy vwap_pullback --symbol NIFTY --start 2025-10-01 --end 2026-04-24 --mode fast
```

**OI coverage:** OI snapshots only exist from when `oi_snapshot_task` began running. Backtest reports `oi_coverage_pct` so you know how much of the replay had OI context. Pre-task dates use `ctx.oi_analysis = None` (strategy's soft filter allows this gracefully).

**Option history:** Fyers retains ~6 months of 1m option contract data. Accurate mode requires a Fyers token in Redis. Candles are persisted to `MarketData1m` after first fetch so subsequent runs are instant.

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
