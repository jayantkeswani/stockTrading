# Backend - Python/FastAPI

## Tech Stack

- Python 3.11 (virtualenv at `backend/.venv`)
- FastAPI with async support, runs on port **8080**
- SQLAlchemy 2.0 (async engine via asyncpg, declarative models)
- Alembic for database migrations (PostgreSQL on port 5433)
- Pydantic v2 for schemas/settings
- Redis on port 6380 (via redis-py async)
- httpx for async HTTP
- fyers-apiv3 SDK for market data (REST + WebSocket); `websockets` library for simulated mode WS client
- APScheduler for periodic tasks
- python-telegram-bot for notifications

## Database Access (psql not on host PATH — use Docker)

`psql` is not installed on the host machine. Connect via the `st-postgres` container:

```bash
docker exec -i st-postgres psql -U trader -d stocktrading -c "<SQL>"
# Example: inspect strategy params
docker exec -i st-postgres psql -U trader -d stocktrading -c "SELECT strategy_name, parameters FROM strategy_configs;"
```

Credentials: user=`trader`, password=`trader_dev_123`, db=`stocktrading`, port=5433 (host) / 5432 (inside container). Defined in `.env` as `DATABASE_URL_SYNC`.

---

## Execution Architecture

### Shadow + YOLO Isolation

Three independent consumers of every signal, fully isolated:

1. **Shadow executor** (`shadow_executor.py`) — creates SHADOW trade/position based on global `min_confidence_for_shadow` and per-strategy `shadow_enabled` flag. Always 1 lot, no capital gates. Two dedup layers: one open shadow per signal_id, AND one open shadow per symbol+strategy (prevents stacking from different signals on same underlying). Closed shadows don't block (allows fresh shadow on Case-2 re-fire).
2. **YOLO executor** (`auto_executor.py`) — creates YOLO trade/position based on global `min_confidence_for_execution` and per-strategy `yolo_enabled` flag. Enforces drawdown, max-trades, and daily profit cap gates; lot sizing via `compute_lots_for_yolo`.
3. **Manual execution** (`signals.py`) — signal stays PENDING, user clicks EXEC. Lot sizing via `compute_lots_for_manual`; no blocking gates, warnings shown instead.

All three confidence thresholds (`min_confidence_to_persist`, `min_confidence_for_shadow`, `min_confidence_for_execution`) are global in `trading_config`. Cross-field validation enforces `persist < shadow <= execution`.

**WS subscription**: happens at resolve time (`_resolve_option` / `_resolve_futures` in strategy_runner), not at trade creation. This ensures ticks are flowing before shadow/YOLO open the position.

### Signal Dedup

"Acted on" = `executed_trade_id` set OR any Trade with `signal_id = existing.id` (catches shadow). Shadow trades never trigger Case 3. **Same-day scoping**: intraday strategies only dedup against `generated_at >= today 00:00`. **EOD signal expiry**: at 3:30 PM IST, `signal_expiry_task` bulk-expires all remaining PENDING intraday signals.

### Lot Sizing at Execution

Signals carry no `lots`/`quantity`/`sizing_meta`. Lot sizing happens at execution time only via `lot_sizing.py`:

- `compute_lots_for_shadow` — always 1 lot
- `compute_lots_for_yolo` — full risk-based sizing (capital, risk %, VIX multiplier, strategy-specific for S5)
- `compute_lots_for_manual` — same logic as YOLO; no blocking gates

### SL/Target Recomputation at Execution

All three paths fill at the live LTP (not the stale signal premium). SL/target are recomputed via `execution_utils.recompute_sl_target()`:

- **Options**: preserves original SL% and target%: `new_sl = live_entry × (1 - sl_pct)`, `new_target = live_entry × (1 + target_pct)`
- **Futures**: SL stays at structural level (ORB low/high, VWAP band, PDH/PDL); target recomputed using original R:R from `live_entry`
- Applies to: YOLO, Shadow, Manual execute, and the preview endpoint
- Fallback to originals on degenerate input (zero entry, entry == SL, live price past structural SL)

### Margin Tracking

`margin_required` stored on Trade and Position at execution time via `margin_calculator.py`:

- Options: full premium paid (`entry_price × quantity`)
- Futures: per-symbol tiered heuristic using `MARGIN_TIER_MAP` in `constants.py`
- Dashboard shows: NOTIONAL (`entry×qty`), RISK (`|entry−SL|×qty`), MARGIN (sum `margin_required`)
- `POST /api/v1/trades/margin-analysis` — peak concurrent margin calculation

### Risk Gate Responsibilities

- `_check_regulatory_limits` (`strategy_runner.py`) — F&O ban list only; all execution paths
- Drawdown / max-trades / **daily profit cap** — YOLO executor only (`_final_risk_check` in `auto_executor.py`). Profit cap block writes an `AgentLog` (no Telegram) — trade_monitor sends the one Telegram notification when it closes positions
- **Daily profit cap** (`max_daily_profit`, INR, 0 = disabled): when realized + unrealized PnL ≥ cap, monitor closes all open non-shadow positions (`ExitReason.PROFIT_CAP`) and blocks further YOLO executions
- Shadow executor — zero capital gates
- Manual executor — no blocking gates; warnings computed but not enforced

---

## Test Coverage

~999 tests in `backend/tests/`. See root CLAUDE.md for the full test index. Key patterns:

- All service tests mock DB/Redis via pytest fixtures; `conftest.py` clears per-candle in-memory caches before each test
- Integration tests for LLM calls (research module) auto-skipped without `GOOGLE_API_KEY`

---

## Module Map

### `app/config.py` — Application Settings

Pydantic Settings loading from `.env`. Key groups:

- **DB/Redis**: `DATABASE_URL`, `DATABASE_URL_SYNC`, `REDIS_URL`
- **Fyers**: `FYERS_APP_ID`, `FYERS_SECRET_KEY`, `FYERS_REDIRECT_URI`, `FYERS_USERNAME`, `FYERS_PIN`, `FYERS_TOTP_SECRET`
- **Trading**: `CAPITAL`, `MAX_DAILY_DRAWDOWN_PCT`, `MAX_RISK_PER_TRADE_PCT`, `MAX_TRADES_PER_DAY`
- **AI**: `GOOGLE_API_KEY` (AI Studio), `GCP_PROJECT_ID` (Vertex AI, takes precedence), `VERTEX_AI_LOCATION` (default "global"), `RESEARCH_LLM_MODEL` (flash), `RESEARCH_LLM_MODEL_PRO` (pro — for briefing, Stage 3, synthesis), `AI_CONFIDENCE_ENABLED`, `AI_CONFIDENCE_TIMEOUT_SECONDS` (25)
- **Telegram**: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `TELEGRAM_ENABLED` (set False to disable all Telegram I/O — single kill switch for local dev alongside production)
- **Market Simulator**: `MARKET_MODE` (`"live"` default, `"simulated"` for offline testing), `SIMULATOR_URL` (`http://localhost:8787`)
- **Version**: `APP_VERSION` (semver tag set by deploy pipeline), `DEPLOYED_AT`
- `model_config = extra="ignore"` — unrecognized `.env` vars (Telegram MTProto keys, etc.) don't crash startup

---

### `app/main.py` — FastAPI Application + Startup Sequence

**Startup (lifespan)**:

1. `ensure_seeded()` — insert singleton `trading_config` row if absent
2. Start config listener pubsub (`start_config_listener()`)
3. Start all schedulers: Fyers login, symbol master, OI snapshot, fundamental data, daily summary, global market, morning workflow, bhav copy, F&O ban list, signal expiry
4. Force-download fresh symbol master via `symbol_master.refresh()` (inline/blocking — ensures rolled contracts resolve on the very first candle)
5. Start fundamental data background task (5s delay, non-blocking)
6. Start global market background task (non-blocking)
7. Auto-start data feed if Fyers token in Redis — or unconditionally in simulated mode (subscribes indices + strategy symbols + watchlist + S5 watchlist + open positions + today's closed trades). In simulated mode: skips token check and candle backfill
8. Start deep backfill background task (10s delay, non-blocking)
9. Auto-start `agent_runner` (trade monitor at 500ms interval — autonomy from DB config)
10. Start Telegram bot polling (`start_telegram_bot()`)

**Key private helpers** (used by both startup and reauth):

- `_get_watchlist_symbols() -> list[str]` — loads dashboard watchlist Fyers symbols from Redis hash `watchlist:items`. Used by: main.py startup, fyers_login_task reauth
- `_get_strat5_watchlist_symbols() -> dict[str, str]` — loads S5 screener watchlist from Redis `strat5:watchlist:{today}`, returns `{short_name: fyers_symbol}`. Used by: main.py startup, fyers_login_task reauth
- `_get_position_symbols() -> list[str]` — queries open `Position.fyers_option_symbol` + today's closed `Trade.fyers_option_symbol` via SQL `union_all`. Today's closed trades included for hold analysis candle continuity. Used by: main.py startup, fyers_login_task reauth

**File logging**: `RotatingFileHandler` on root logger → `backend/logs/app.log` (10 MB × 5 rotations). All `logging.getLogger(__name__)` calls propagate automatically.

**CORS**: Enabled for all origins (single-user system).

---

### `app/core/` — Foundation

#### `database.py`

- `get_db() -> AsyncGenerator[AsyncSession, None]` — FastAPI dependency; yields async SQLAlchemy session. Used by: all API routers

#### `redis.py`

- `get_redis() -> redis.Redis` — returns shared Redis connection (pool capped at 50, decode_responses=True). Used by: all services/tasks reading Redis
- `publish_event(channel, message)` — publishes JSON string to Redis pub/sub. Used by: trading_config, strategy_runner
- `cache_price(symbol, price_data)` — writes price dict to `price:{symbol}` with 24h TTL. Used by: feed_manager, morning_screener
- `get_cached_price(symbol) -> dict | None` — reads `price:{symbol}`. Used by: signals.py, positions.py, risk.py

#### `constants.py`

Key constants (not functions):

- `FYERS_SYMBOL_MAP` — index short-name → Fyers symbol (NIFTY, BANKNIFTY, etc.)
- `NSE_HOLIDAYS: frozenset[date]` — 2024-2026 trading holidays
- `STRIKE_GAPS` — per-index strike increment
- `WEEKLY_EXPIRY_DAYS`, `MONTHLY_ONLY_INDICES` — expiry schedule
- `INDEX_FUTURES_EXPIRY_DOW` — index → last-DOW-of-month for near-month futures
- `INDEX_SYMBOLS` — frozenset of 5 tradeable indices
- `MARGIN_TIER_MAP` — per-symbol SPAN+exposure % for ~60 F&O stocks
- `MARGIN_TIER_DEFAULT = 0.20` — fallback for unknown futures symbols

#### `enums.py`

Key enums: `OptionType`, `OrderSide`, `TradeStatus`, `ExitReason` (incl. `TRAILING_SL`, `PROFIT_CAP`, `STALE_DATA`), `SignalStatus`, `SignalType`, `StrategyName` (incl. `CAN_SLIM`, `INTRADAY_FUTURES`), `IndexSymbol`, `InstrumentType`, `PositionType`, `AgentAutonomyLevel`, `AgentActionType` (incl. `SHADOW_EXECUTED`, `PROFIT_CAP_CLOSE`), `TradeSource` (`MANUAL`/`YOLO`/`SHADOW`)

#### `task_registry.py`

- `TaskRegistry.register(name, task_type, ...)` — registers a background task/scheduler with status tracking. Used by: all tasks/schedulers
- `TaskRegistry.track_asyncio_task(name, coro, ...)` — wraps a fire-and-forget asyncio task; auto-updates status via done callback. Used by: main.py (startup tasks)
- `TaskRegistry.update_status(name, status, ...)` — updates task status. Used by: all tasks
- `TaskRegistry.get_all() -> list[dict]` — all task statuses. Used by: `GET /api/v1/tasks`
- `TaskRegistry.get(name) -> dict | None` — single task status

#### `utils.py`

All window/deadline helpers accept optional `as_of: datetime | None` (defaults to `now_ist()` — backtest passes historical timestamps):

- `now_ist() -> datetime` — current time in IST. Used by: everywhere
- `is_trading_day(d: date) -> bool` — weekday + NSE_HOLIDAYS check; always True in simulated mode. Used by: candle_backfill, signal_expiry_task
- `is_market_open(as_of=None) -> bool` — 9:15–15:30 IST check; always True in simulated mode. Used by: feed_manager, trade_monitor, tasks
- `is_in_trading_window(as_of=None) -> bool` — within standard trade window (9:15-15:00). Used by: strategy_runner
- `get_window_state(as_of=None) -> str` — returns `"IN_WINDOW"` / `"DEAD_ZONE"` / `"OUT_OF_WINDOW"`. Used by: strategy_runner (default), confidence.py
- `is_in_dead_zone(as_of=None) -> bool` — 11:30-12:30 check. Used by: strategy_runner
- `is_past_close_deadline(as_of=None) -> bool` — after 3:15 PM; always False in simulated mode. Used by: shadow_executor, trade_monitor
- `time_to_market_close_minutes(as_of=None) -> int` — minutes until 3:30 PM. Used by: confidence.py (time_of_day factor)
- `is_in_custom_trading_window(as_of, windows) -> bool` — per-strategy window check. Used by: strategy_runner
- `get_custom_window_state(as_of, windows, dead_zone) -> str` — per-strategy window state. Used by: strategy_runner, options API

#### `retry.py`

- `async_retry(func, *args, retries, base_delay, max_delay, jitter, retry_on, ...) -> Any` — exponential backoff with jitter, no third-party deps. Used by: fyers_client (REST + 401 reauth), notification.send_telegram (3×), trade_monitor (option price REST fallback)
- `with_retry(**kwargs) -> Callable` — decorator form of async_retry

---

### `app/models/` — SQLAlchemy ORM (16 tables)

All models extend `BaseModel` (UUID PK, `created_at`/`updated_at` TIMESTAMPTZ).


| Model                  | Table                     | Key Columns                                                                                                                                                                                                                                                                                                                                                   |
| ---------------------- | ------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `Trade`                | `trades`                  | `entry_price`, `exit_price`, `pnl`, `net_pnl` (pnl minus charges), `charges_json` (JSONB breakdown), `source` (MANUAL/YOLO/SHADOW), `is_permanent_watchlist`, `margin_required`, `signal_confidence`, `signal_ai_action`, `signal_ai_summary`, `signal_instrument_type`, `signal_type`, `signal_snapshot` (JSONB full snapshot at execution)                  |
| `Position`             | `positions`               | `entry_price`, `stop_loss`, `target_price`, `is_shadow`, `high_since_entry` (HWM for trailing SL), `margin_required`, `signal_generated_at` (from Signal at execution, for fill latency)                                                                                                                                                                       |
| `Signal`               | `signals`                 | `entry_price`, `stop_loss`, `target_price`, `confidence`, `executable`, `blocked_reason`, `is_permanent_watchlist`, `ai_summary`, `ai_rationale`, `ai_adjustment`, `ai_action`, `indicators` (JSONB: includes `confidence_factors`, `intraday_bias`, `nifty_spot`, `nifty_day_change_pct`, `trigger_candle`, `minutes_since_open`, `_is_permanent_watchlist`) |
| `SignalHistory`        | `signal_history`          | Immutable snapshot before Case-2 dedup update. `version` (1-based), all volatile signal fields, `captured_at`                                                                                                                                                                                                                                                 |
| `MarketData1m`         | `market_data_1m`          | 1-minute OHLCV for intraday candles (9:15–15:30 IST)                                                                                                                                                                                                                                                                                                          |
| `MarketDataDaily`      | `market_data_daily`       | One OHLCV + `delivery_pct` per symbol per trading date. Unique on `(symbol, date)`. Populated by `nse_bhav_copy_task`. Used by morning screener for all 8 quant scoring factors.                                                                                                                                                                              |
| `OISnapshot`           | `oi_snapshots`            | `option_type` (`"CE"`, `"PE"`, or `"FUT"` for stock futures with `strike_price=0`)                                                                                                                                                                                                                                                                            |
| `StrategyConfig`       | `strategy_configs`        | `is_active`, `auto_mode`, `shadow_enabled`, `yolo_enabled`, `parameters` (JSONB), `symbols` (JSONB list), `symbol_map` (JSONB: short_name → fyers_symbol, stored at insertion time)                                                                                                                                                                           |
| `TradingConfig`        | `trading_config`          | Singleton row: `capital`, `max_daily_drawdown_pct`, `max_daily_profit` (INR, 0=disabled), `max_risk_per_trade_pct`, `max_trades_per_day`, `autonomy_level`, `min_confidence_to_persist`, `min_confidence_for_shadow`, `min_confidence_for_execution`, `shadow_skip_permanent_watchlist`, `yolo_skip_permanent_watchlist`                                      |
| `StockFundamental`     | `stock_fundamentals`      | CAN SLIM scores + raw fundamentals per stock                                                                                                                                                                                                                                                                                                                  |
| `FundamentalHistory`   | `fundamental_history`     | Quarterly snapshots for trend analysis                                                                                                                                                                                                                                                                                                                        |
| `GlobalMarketSnapshot` | `global_market_snapshots` | 15-min world indices + FX + commodities snapshot; unique on `timestamp`                                                                                                                                                                                                                                                                                       |
| `AgentLog`             | `agent_logs`              | Agent action audit trail with `action_type`, `details` (JSONB incl. `is_shadow`), `requires_confirmation`, `confirmation_status`                                                                                                                                                                                                                              |
| `DailySummary`         | `daily_summaries`         | Daily P&L, win/loss counts, drawdown                                                                                                                                                                                                                                                                                                                          |
| `ResearchReport`       | `research_reports`        | AI research report: recommendation, confidence, report_json/markdown                                                                                                                                                                                                                                                                                          |
| `ResearchAgentRun`     | `research_agent_runs`     | Per-agent run findings, summary, duration, data sources. FK cascade delete                                                                                                                                                                                                                                                                                    |


**Helper function** (module-level, `models/trade.py`):

- `build_signal_snapshot(signal) -> dict` — constructs JSONB dict from Signal ORM for `Trade.signal_snapshot`. Used by: all 3 signal-based Trade creation sites (manual, YOLO, shadow)

---

### `app/schemas/` — Pydantic Schemas

Convention: `{Entity}Create`, `{Entity}Response`, `{Entity}Update`.

Key additions (other schemas are standard CRUD):

- `trade.py`: `MarginAnalysisRequest(trade_ids: list[UUID])`, `MarginAnalysisResponse(peak_margin, peak_time, total_margin, trade_count)`, `HoldAnalysisRequest`, `PerTradeHoldResult`, `HoldAnalysisResponse`, `TradeResponse` includes `margin_required`, `signal_*` snapshot columns
- `signal.py`: `SignalPreviewResponse(risk, notional, margin_required, sizing_meta, warnings, entry_price, stop_loss, target_price, lots)`
- `risk.py`: `RiskDashboardResponse(notional, risk, margin_utilized, max_daily_profit, is_profit_capped, closed_pnl, total_pnl, drawdown_pct)`
- `position.py`: `PositionResponse` includes `margin_required`, `signal_confidence`, `signal_generated_at`, `unrealized_pnl`, `current_price`, `is_permanent_watchlist`

---

### `app/api/v1/` — REST API (14 routers, all under `/api/v1/`)


| Router           | File                                     | Key Endpoints                                                                                                                                                                                                                                                                                                               |
| ---------------- | ---------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Trades           | `trades.py`                              | `GET /trades` (sim filters on signal_* cols), `GET /trades/{id}`, `GET /trades/summary`, `POST /{id}/close`, `POST /close-all`, `POST /margin-analysis`, `POST /hold-analysis`                                                                                                                                              |
| Signals          | `signals.py`                             | `GET /signals`, `GET /{id}/preview` (live LTP + SL recompute), `POST /{id}/execute` (live LTP fill + SL recompute), `POST /{id}/reject`, `GET /{id}/history`                                                                                                                                                                |
| Positions        | `positions.py`                           | `GET /positions` (LEFT JOIN trades, enriched with live prices), `POST /{id}/close`, `PATCH /{id}/sl-target`                                                                                                                                                                                                                 |
| Agent            | `agent.py`                               | `POST /start`, `POST /stop`, `GET /status`, `PATCH /confirm/{log_id}`, `PATCH /yolo`, `GET /logs`                                                                                                                                                                                                                           |
| Risk             | `risk.py`                                | `GET /risk` (daily P&L, drawdown, notional, margin, profit cap state)                                                                                                                                                                                                                                                       |
| Market Data      | *collect*dynamic_symbols`market_data.py` | `GET /prices`, `POST /prices/batch`, `GET /ohlcv/{symbol}`, `POST /feed/start|stop|refresh`, `GET /symbols/search`                                                                                                                                                                                                          |
| Watchlist        | `watchlist.py`                           | `GET /watchlist`, `POST /watchlist`, `DELETE /watchlist/{symbol}` — Redis-backed, sorted by insertion time                                                                                                                                                                                                                  |
| Strategies       | `strategies.py`                          | `GET /strategies`, `PUT /strategies/{name}`, `POST /evaluate` (manual single), `POST /evaluate/batch` (all configured symbols), `GET /{name}/parameter-defaults`                                                                                                                                                            |
| Intraday Futures | `intraday_futures.py`                    | `GET /watchlist`, `GET /agent-log`, `GET /global-cues`, `GET /morning-briefing`, `GET /phase`, `GET /agent-status`, `GET /setup-performance`, `GET /daily-stats`, `POST /screener/run`, `POST /briefing/run`, `POST /preopen/run`, `POST /backfill-symbols`, `POST /agent/{action}`, `GET/POST/DELETE /permanent-watchlist` |
| Options          | `options.py`                             | `GET /window-state` (VWAP pullback custom window state), `GET /agent-log`                                                                                                                                                                                                                                                   |
| Settings         | `settings.py`                            | `GET /settings/trading`, `PATCH /settings/trading` (partial update; includes `shadow_skip_permanent_watchlist`, `yolo_skip_permanent_watchlist`)                                                                                                                                                                            |
| Tasks            | `tasks.py`                               | `GET /tasks` (all registered background tasks)                                                                                                                                                                                                                                                                              |
| Auth             | `auth.py`                                | Fyers OAuth: login redirect + callback + token storage                                                                                                                                                                                                                                                                      |
| Research         | `research.py`                            | `POST /start`, `GET /reports`, `GET /reports/{id}`, `DELETE /reports/{id}`                                                                                                                                                                                                                                                  |


**Key router behaviors**:

- `GET /trades` supports `source=SHADOW` to see shadow-only trades, `exclude_permanent=true` to hide permanent-watchlist trades, `min_lots`/`max_lots` filters
- `POST /close-all` registered BEFORE `{trade_id}` paths to avoid path conflict
- `POST /hold-analysis` — accepts `{trade_ids, scenario ("best"/"worst")}`; for each closed trade queries `market_data_1m` for `MAX(high)` / `MIN(low)` between `exit_time` and 15:30 IST same day. S5 futures queried via `trade.symbol` (short name); options queried via `trade.fyers_option_symbol` (full Fyers symbol, stored via `_fyers_to_internal()` pass-through). Returns `data_found=false` for open trades, missing symbols, or no data in window
- `GET /signals/{id}/preview` calls `compute_lots_for_manual` + `recompute_sl_target` + `compute_margin` so confirm modal shows correct risk
- `GET /intraday-futures/agent-log` returns `{entries, total}` when `limit > 0`; bare list when `limit=0` (internal callers)
- `POST /watchlist` stores full Fyers-format symbol (e.g. `NSE:RELIANCE26MAYFUT`) — never reconstruct from short name (causes double-prefix bug)

---

### `app/websocket/` — Real-Time Layer

#### `manager.py` — WebSocketManager

Single `/ws` endpoint (handler in `app/api/router.py`). Uses `receive_text()` + `json.loads()` instead of `receive_json()` to gracefully skip non-JSON frames (websockets v16 protocol pings). Events published via `broadcast_event(event, data)`:


| Event            | Published by                           | Payload                                                                                                              |
| ---------------- | -------------------------------------- | -------------------------------------------------------------------------------------------------------------------- |
| `price:update`   | feed_manager                           | `{symbol, ltp, change_pct, ...}`                                                                                     |
| `signal:new`     | strategy_runner                        | Full signal dict incl. `indicators`, `ai_*`, `fyers_option_symbol`, `fyers_futures_symbol`, `is_permanent_watchlist` |
| `signal:updated` | strategy_runner (Case-2 dedup)         | Same as `signal:new`                                                                                                 |
| `trade:open`     | auto_executor, shadow_executor, manual | Includes `margin_required`, `is_shadow`                                                                              |
| `trade:close`    | trade_monitor, positions API           | `{trade_id, position_id, pnl, exit_reason}`                                                                          |
| `position:pnl`   | trade_monitor                          | `{position_id, unrealized_pnl, current_price}`                                                                       |
| `agent:action`   | agent_runner                           | `AgentLogResponse` dict (id, action_type, trade_id, details, requires_confirmation, ...)                             |
| `market:status`  | startup                                | `{is_open: bool}`                                                                                                    |


---

### `app/services/` — Business Logic

#### `trading_config.py`

- `get_trading_config() -> TradingConfigDTO` — async; returns cached DTO (O(1) after startup). Used by: auto_executor, shadow_executor, strategy_runner, agent_runner
- `get_trading_config_sync() -> TradingConfigDTO | None` — sync; returns cache or None (no DB call). Used by: strategy_2 evaluate()
- `update_trading_config(**fields) -> TradingConfigDTO` — writes to DB, refreshes cache, publishes pubsub event. Used by: settings API
- `ensure_seeded() -> None` — inserts singleton row from `.env` on first boot. Used by: main.py
- `start_config_listener() -> None` — subscribes to pubsub, reloads cache on updates. Used by: main.py

`TradingConfigDTO` fields: `capital`, `max_daily_drawdown_pct`, `max_daily_profit` (INR, 0=disabled), `max_risk_per_trade_pct`, `max_trades_per_day`, `paper_trading`, `autonomy_level`, `min_confidence_to_persist`, `min_confidence_for_shadow`, `min_confidence_for_execution`, `shadow_skip_permanent_watchlist`, `yolo_skip_permanent_watchlist`.

#### `position_sizing.py`

- `calculate_lots(capital, risk_per_trade_pct, entry_price, stop_loss, lot_size, *, vix_multiplier=1.0, max_lots=None) -> int` — single source of truth for position sizing. Used by: lot_sizing.py
- `vix_to_multiplier(india_vix) -> float` — converts VIX level to 0.8–1.1 multiplier. Used by: lot_sizing.py

#### `lot_sizing.py`

- `compute_lots_for_yolo(signal, lot_size, india_vix) -> int` — reads capital/risk from trading_config; for INTRADAY_FUTURES delegates to `strategy._compute_lots()`. Used by: auto_executor
- `compute_lots_for_shadow(lot_size) -> int` — always returns 1. Used by: shadow_executor
- `compute_lots_for_manual(signal, lot_size, india_vix) -> int` — same logic as YOLO; no blocking gates. Used by: signals.py (preview + execute)

#### `margin_calculator.py`

- `compute_margin(symbol, entry_price, quantity, instrument_type) -> float` — Options: full premium (`entry_price × quantity`). Futures: `entry_price × quantity × tier_pct` (from `MARGIN_TIER_MAP`; defaults to 0.20 for unknowns; index symbols at 1.0). Used by: signals.py preview, auto_executor, shadow_executor, manual execute

#### `brokerage_calculator.py`

- `compute_charges(instrument_type, entry_price, exit_price, quantity, side) -> ChargesBreakdown` — Zerodha rate structure: brokerage (₹20/leg), STT, exchange txn, GST, SEBI, stamp duty, all in Decimal. `ChargesBreakdown.to_dict()` returns JSONB-safe float dict. Used by: trade_monitor._close_position(), positions.close_position()

#### `live_price.py`

- `get_live_price(fyers_symbol) -> float` — Redis cache first, falls back to Fyers REST `/quotes`. Raises `HTTPException(503)` if both fail. Used by: signals.py (manual execute), auto_executor, shadow_executor

#### `execution_utils.py`

- `recompute_sl_target(signal_entry, signal_sl, signal_target, live_entry, instrument_type, signal_type) -> (new_sl, new_target)` — pure function, no ORM or async. Preserves SL%/target% for options; keeps structural SL for futures. Fallback to originals on degenerate inputs. Used by: auto_executor, shadow_executor, signals.py (execute + preview)

#### `agent_log.py`

- `append_agent_log(prefix, today, category, message) -> None` — rpush to `{prefix}:agent_log:{today}` with 90-day TTL. Used by: strategy_2, strategy_5, strategy_runner
- `get_agent_log(prefix, date_str, offset, limit) -> list` — newest-first when `limit > 0`, oldest-first when `limit=0`. Used by: options.py, intraday_futures.py APIs

Strategy 2 uses prefix `"strat2"`, Strategy 5 uses `"strat5"`.

#### `strategy_params.py`

- `get_strategy_params(strategy_name, session=None) -> dict` — async; loads from DB `strategy_configs.parameters` JSONB, merges with defaults, caches in-memory. Returns a shallow copy so callers can safely mutate without cross-contaminating concurrent evaluations. Used by: strategy_runner, morning_screener
- `get_strategy_params_sync(strategy_name) -> dict` — sync; returns cache or defaults (no DB call). Used by: trade_monitor
- `clear_strategy_params_cache(strategy_name=None) -> None` — invalidates cache (all strategies if None). Used by: strategies API PUT endpoint
- `get_defaults_for_strategy(strategy_name) -> dict` — raw defaults for frontend form rendering. Used by: strategies API
- `parse_trading_windows(params) -> list[tuple[time, time]]` — parses `trading_windows` list from params dict. Used by: strategy_runner, options API
- `parse_dead_zone(params) -> tuple[time, time] | None` — parses `dead_zone` from params dict. Used by: strategy_runner

Default dicts: `VWAP_DEFAULTS`, `CANSLIM_DEFAULTS`, `INTRADAY_FUTURES_DEFAULTS`.

#### `option_resolver.py`

- `select_strike(index_price, option_type, symbol) -> int` — ATM or 1-strike ITM per delta target. Used by: resolve_option_details
- `select_expiry(symbol) -> date` — weekly for NIFTY/SENSEX, monthly for others. Used by: resolve_option_details
- `find_option_symbol(symbol, strike, expiry, option_type) -> str | None` — symbol master lookup. Used by: resolve_option_details
- `fetch_option_premium(fyers_symbol) -> float | None` — Redis → Fyers REST fallback. Used by: resolve_option_details
- `resolve_option_details(signal, ctx) -> signal | None` — full pipeline: strike → expiry → symbol → premium → SL/target. Only runs for `instrument_type=OPTION`. Used by: strategy_runner

#### `futures_resolver.py`

- `resolve_futures_contract(symbol, entry_price, from_date=None) -> dict | None` — stock symbol → nearest-month futures (last Thursday expiry). Pass `from_date=current_expiry + 1 day` for roll. Used by: strategy_runner, trade_monitor (expiry roll)
- `find_index_futures_expiry(index, from_date) -> date` — near-month expiry via `INDEX_FUTURES_EXPIRY_DOW`. Used by: resolve_index_futures_symbol
- `resolve_index_futures_symbol(index, from_date=None) -> (fyers_symbol, expiry) | None` — near-month index futures symbol lookup. Used by: strategy_runner (VWAP volume sourcing)

#### `candle_backfill.py`

- `backfill_previous_day() -> None` — previous trading day candles for PDH/PDL/PDC context. Per-symbol skip if already populated. Used by: main.py startup
- `backfill_today() -> None` — today's elapsed candles (skips weekends + NSE holidays; per-symbol freshness check: skips if latest candle < 2 min ago). Used by: main.py startup
- `backfill_deep_history(days=120) -> None` — 120-day deep backfill for CAN SLIM symbols with < 50 days. Used by: main.py startup

All three backfill from Fyers historical API + persist via `ON CONFLICT DO NOTHING`. Rate-limited: 0.3s between symbols, 1.0s every 5th. In simulated mode: uses `_fetch_history_simulated()` (httpx GET to simulator's `/data/history`) instead of Fyers SDK; token set to None.

#### `morning_screener.py`

- `run_morning_briefing(as_of=None, force=False) -> dict` — LLM synthesis of yesterday's trades (Pro model, `response_schema=_BRIEFING_SCHEMA`); stores in Redis `strat5:morning_briefing:{date}`. `force=True` bypasses cache. Used by: morning_workflow_task
- `run_morning_screener(as_of=None) -> list[dict]` — 3-stage pipeline (quant → news → LLM confidence); outputs ranked watchlist to Redis `strat5:watchlist:{date}`; then calls `_build_rvol_baselines()` + `_provision_watchlist_symbols()`. Used by: morning_workflow_task
- `_provision_watchlist_symbols(symbols, today) -> None` — post-screener WS provisioning: REST quote fetch (primes Redis price cache), backfills previous day + today's 1m candles per symbol, subscribes to WS via `fyers_ws_client.subscribe_symbols()`. If WS is down, `_collect_dynamic_symbols()` on next reconnect picks up the watchlist from Redis. Used by: run_morning_screener
- `snapshot_global_cues(today=None, force=False) -> dict` — VIX halt check, gap flag; reads `indicator:global:*` Redis keys; computes `global_score` and `overnight_bias`; `force=True` bypasses cache and re-reads live values. Used by: morning_workflow_task, intraday_futures API
- `run_preopen_reassessment(as_of=None) -> dict` — 9:08 AM: pre-open quotes, relative gap per stock vs Nifty, bias overrides, gap alignment bonus, watchlist re-sort; pure math, no LLM. Used by: morning_workflow_task
- `get_watchlist(date_str) -> list[dict]` — reads `strat5:watchlist:{date}`; enriches with ORB levels from `strat5:orb:{date}:{symbol}`. Used by: intraday_futures API
- `get_morning_briefing(date_str) -> dict` — reads `strat5:morning_briefing:{date}`. Used by: intraday_futures API
- `get_global_cues(date_str) -> dict` — reads `strat5:global_cues:{date}`. Used by: intraday_futures API, strategy_runner
- `get_agent_log(prefix, date_str, offset, limit) -> ...` — delegates to `agent_log.get_agent_log`. Used by: intraday_futures API
- `get_agent_status(date_str) -> str` — reads `strat5:agent_status:{date}`. Used by: intraday_futures API
- `set_agent_status(date_str, status) -> None` — writes `strat5:agent_status:{date}`. Used by: morning_workflow_task
- `get_setup_performance(end_date, days=5) -> dict` — queries trades JOIN signals for setup_type; per-setup win rate / net PnL. Used by: intraday_futures API

**Redis key prefix**: all S5 keys use `strat5:*` with 90-day TTL.

#### `strategy_runner.py`

Core evaluation engine. Two trigger paths: (1) auto-mode on candle close, (2) manual via strategies API.

- `get_auto_strategies_for_symbol(symbol) -> list[StrategyName]` — which strategies auto-evaluate for a symbol. Used by: feed_manager

Key private methods (documented because they're central to flow):

- `_build_market_context(symbol, candle_data)` — builds full `MarketContext` from in-memory buffers + Redis. `today_open` is the open of the first candle with timestamp >= MARKET_OPEN (skips pre-market candles)
- `_query_previous_day(session, symbol, today)` — queries last trading day's 1m candles; `yesterday_cutoff` is midnight IST (`time.min`), not MARKET_OPEN, so pre-open candles (08:42–09:07) don't bleed into today's query
- `_enrich_signal_snapshot(signal, ...)` — injects `nifty_spot`, `nifty_day_change_pct`, `trigger_candle`, `minutes_since_open` into every signal's indicators JSONB
- `_enrich_strategy5_params(symbol, params, india_vix=None)` — loads RVOL profiles, cross-position counts, Nifty bias, ORB levels, briefing, global cues shift, per-stock gap/trend data, FUT OI direction into strategy params; throttled to once per 5 min per symbol for global cues check
- `_dedup_signal(existing, new, ai_fields)` — Case-1 (noise: skip), Case-2 (meaningful: archive to `signal_history` → update all fields including `ai_*` → re-fire shadow), Case-3 (acted on: return None → create new signal)
- `_persist_signal(signal)` — writes signal to DB; copies `_is_permanent_watchlist` from indicators to `Signal.is_permanent_watchlist`; gates on `min_confidence_to_persist`
- `_check_regulatory_limits(symbol)` — F&O ban list check (reads `nse:fo_ban_list:{today}`)
- `_is_dedup_skip(existing, new)` — AI gate pre-check; if identical signal exists, skips Gemini call + DB write
- `_init_index_futures()` — on first candle close, resolves + subscribes near-month futures for each index (NIFTY_FUT, etc.)
- `_calculate_vwap_from_buffer(symbol)` — always uses futures candle volumes for index symbols (Fyers index volume is unreliable)
- `_is_permanent_watchlist` extraction: extracted from watchlist `manual` field into per-symbol `_s5_session_cache`, then injected into `signal.indicators` after `strategy.evaluate()` returns

**Per-symbol concurrency guard**: `_evaluating_symbols: set[str]` — busy flag checked at the top of `_evaluate_strategies`. If the symbol is already being evaluated by a prior candle's asyncio task, the new evaluation is skipped (not queued). Prevents concurrent candle tasks from racing on signal persist / YOLO execution. Different symbols are never blocked. Cleared in `finally` block to survive exceptions.

**Per-candle in-memory caches** (all reset on process restart):

- `_evaluating_symbols` — per-symbol busy flag for concurrency guard (see above)
- `_daily_candles_cache` — daily bars from `market_data_daily`, TTL = trading day
- `_s5_session_cache` — briefing + screener enrichment per symbol, TTL = trading day
- `_s5_oi_cache` — FUT OI direction per symbol, TTL = 600s
- `_s5_shift_last_checked` — global cues shift detection throttle, 5-min min between checks per symbol
- `_s5_counts_cache` — S5 position/trade counts, TTL = 60s
- `_oi_analysis_cache` — OI analysis for index symbols only, TTL = 180s
- `_canslim_symbol_cache` — CAN SLIM membership, TTL = trading day
- `_last_nifty_bias_score` — float | None, updated every NIFTY candle close

---

### `app/strategies/` — Strategy Engine

#### `base.py`

- `BaseStrategy.evaluate(ctx: MarketContext) -> StrategySignal | None` — abstract; implement in each strategy
- `BaseStrategy.should_exit(position, current_price, params) -> bool` — abstract; override for custom exit logic
- `BaseStrategy.get_symbols() -> list[str] | None` — override for dynamic symbol selection (default: None = use DB config)
- `BaseStrategy.max_lots: int | None = None` — class attribute; override to cap sizing (VWAP: 5, CAN SLIM: 2, S5: 2)
- `BaseStrategy.drain_pending_logs() -> list[tuple[str, str]]` — flushes the `_pending_logs` queue for flushing to Redis agent log. Used by: strategy_runner._flush_strategy_logs()

`StrategySignal` fields: `instrument_type`, `signal_type` (CE/PE), `entry_price`, `stop_loss`, `target_price`, `confidence`, `reason`, `indicators` (JSONB), `index_sl`, `index_target`, `fyers_option_symbol`, `fyers_futures_symbol`. No `lots`/`quantity`/`sizing_meta` — signals are bare opportunities.

`MarketContext` key fields: `symbol`, `current_price`, `candles_1m`, `candles_5m`, `candles_5m_futures_volume`, `vwap`, `prev_day`, `oi_analysis`, `india_vix`, `global_cues`, `canslim_data`, `strategy_params`, `intraday_bias`, `daily_candles`

#### `registry.py`

Auto-discovers and instantiates active strategies from DB config. Returns `dict[StrategyName, BaseStrategy]`.

#### `strategy_1_orb.py` — ORB (STUB, not implemented)

#### `strategy_2_vwap_pullback.py` — VWAP Pullback + PDH/PDL + OI (PRIMARY)

- `evaluate(ctx) -> StrategySignal | None` — checks VWAP proximity, bias alignment, reversal pattern, volume filter, OI support, confidence threshold
- `should_exit(position, current_price, params) -> bool` — SL/target based on option premium
- `drain_pending_logs() -> list[tuple[str, str]]` — GATE/SIGNAL logs flushed to `strat2:agent_log:{date}`

Sets `instrument_type=OPTION`. Volume filter uses `ctx.candles_5m_futures_volume` for index symbols. SL/target from `market_levels.select_index_sl_target()` (VWAP bands, PDH/PDL, CPR, OI walls, swing levels); falls back to `sl_pct`/`rr_multiplier`. Skips if R:R < 1:1.

#### `strategy_3_gamma_scalping.py` — Expiry Day Gamma (STUB, not implemented)

#### `strategy_4_canslim.py` — CAN SLIM Growth Breakout (ACTIVE, positional)

- `evaluate(ctx) -> StrategySignal | None` — checks CAN SLIM score, chart base pattern, volume breakout
- `should_exit(position, current_price, params) -> bool` — trailing stop after `trailing_sl_activation_pct` gain

Sets `instrument_type=FUTURE`, `holding_type=POSITIONAL`. Entry: BUY_FUT. SL at `base_low × 0.98` (capped at `sl_pct`); target from measured move (floored at `target_pct`). Same auto/manual pipeline as VWAP Pullback. Fundamental data from `stock_fundamentals` table (refreshed by `fundamental_data_task` every 6h). Chart patterns from daily bars. `futures_resolver` handles contract resolution + expiry roll.

**Architecture note**: same pattern as VWAP Pullback — `strategy_configs` with `auto_mode + symbols`, evaluated on candle close or manual scan. `PositionType.POSITIONAL` on Trade/Position gates trade_monitor's EOD exit skip and trailing stop behavior.

#### `strategy_5_intraday_futures.py` — Intraday Stock Futures (IN DEVELOPMENT)

- `evaluate(ctx) -> StrategySignal | None` — phase-based dispatch to 4 sub-setups
- `should_exit(position, current_price, params) -> bool` — 3:15 PM time exit; trailing SL handled by trade_monitor
- `get_symbols() -> list[str]` — reads Redis watchlist `strat5:watchlist:{today}` (60s cache); populated by morning screener
- `drain_pending_logs() -> list[tuple[str, str]]` — GATE/SIGNAL logs to `strat5:agent_log:{date}`
- `drain_pending_orb_writes() -> dict[str, dict]` — ORB levels to write to Redis after candle close
- `get_pending_phase() -> str | None` — phase transition to persist to Redis
- `load_orb_from_redis(symbol, orb_data) -> None` — restores ORB state from Redis on startup
- `get_current_phase(as_of=None) -> str` (module-level) — current phase string from 7-phase state machine

Sets `instrument_type=FUTURE`, `holding_type=INTRADAY`, `max_lots=2`. Full spec: `docs/strategies/strategy-5-intraday-futures.md`, phase 1 ref: `docs/strategies/strategy-5-phase1-reference.md`.

**Sub-setups**: ORB Breakout, VWAP Bounce, PDH/PDL Breakout, Gap Continuation. **Phase machine**: PRE_MARKET → ORB_FORMING → MORNING_ACTIVE → CAUTION_ZONE → AFTERNOON → CLOSING → DONE. **Price sourcing**: ORB, PDH/PDL, Gap Continuation use `last_candle.close`; VWAP Bounce uses `ctx.current_price`.

`**_compute_confidence(ctx, params, indicators=None) -> float`** — 9-factor composite (RVOL 0.15, setup quality 0.14, Nifty bias 0.12, phase 0.12, volume 0.10, gap alignment 0.10, stock trend 0.10, OI direction 0.10, screener rank 0.07); injects `confidence_factors` dict (9 keys) into signal indicators JSONB.

`**_compute_lots(signal, ctx, params) -> int**` — 6-condition sizing (RVOL, Nifty bias, screener score, briefing, enhanced ORB, trend STRONG/MODERATE, VIX cap); called by `lot_sizing.compute_lots_for_yolo`/`compute_lots_for_manual` at execution time only (not during evaluate).

#### `canslim/scoring.py` — CAN SLIM Factor Scoring (pure functions)

- `score_c(quarterly_eps, ...) -> float` — C factor: quarterly EPS acceleration
- `score_a(annual_eps, ...) -> float` — A factor: annual EPS growth
- `score_n(pct_from_52w_high) -> float` — N factor: new highs
- `score_s(volume_ratio, ...) -> float` — S factor: supply/demand (volume)
- `score_l(relative_strength_rating) -> float` — L factor: leader vs laggard
- `score_i(fii_change_pct, ...) -> float` — I factor: institutional sponsorship
- `score_m(nifty_above_50dma, india_vix) -> float` — M factor: market direction
- `compute_canslim_total(*factor_scores) -> float` — weighted composite. Used by: strategy_4, research/agents/fundamental.py

Re-exports `compute_rs_raw_score` and `percentile_rank_rs` from `indicators/relative_strength.py`.

#### `canslim/base_patterns.py` — Chart Base Pattern Detection

- `detect_cup_with_handle(daily_bars) -> BasePattern | None` — cup with handle pattern (includes `base_low` for SL placement)
- `detect_flat_base(daily_bars) -> BasePattern | None` — flat base pattern
- `detect_double_bottom(daily_bars) -> BasePattern | None` — double bottom pattern
- `detect_any_base_pattern(daily_bars) -> BasePattern | None` — tries all patterns, returns first match. Used by: strategy_4

---

### `app/indicators/` — Technical Indicators (pure functions, no side effects)

#### `vwap.py`

- `calculate_vwap(candles) -> float | None` — VWAP from OHLCV candle list. Used by: strategy_runner._calculate_vwap_from_buffer()
- `price_distance_from_vwap(price, vwap) -> float` — % distance from VWAP. Used by: strategy_2, strategy_5 (VWAP Bounce)
- `is_pullback_to_vwap(price, vwap, proximity_pct) -> bool` — proximity check for VWAP Bounce entry. Used by: strategy_2, strategy_5

#### `cpr.py`

- `calculate_cpr(prev_high, prev_low, prev_close) -> CPRLevels` — pivot, TC, BC, support/resistance. `CPRLevels.cpr_type` is `WIDE` or `NARROW`. Used by: strategy_runner (previous day context builder)

#### `previous_day.py`

- `analyze_previous_day(prev_candles) -> PreviousDay` — PDH, PDL, PDC, `day_bias` (BULLISH/BEARISH/NEUTRAL), range. Used by: strategy_runner._build_market_context()
- `is_gap_up(current_open, pdc, threshold_pct=0.3) -> bool`. Used by: strategy_5 (Gap Continuation)
- `is_gap_down(current_open, pdc, threshold_pct=0.3) -> bool`. Used by: strategy_5 (Gap Continuation)

#### `open_interest.py`

- `analyze_option_chain(oi_snapshots) -> OIAnalysis` — PCR ratio, max pain, support/resistance strikes. Used by: strategy_runner._get_oi_analysis()
- `is_oi_supporting_direction(oi_analysis, direction) -> bool` — checks PCR and max pain alignment. Used by: strategy_2, confidence.py

#### `candle_patterns.py`

- `is_bullish_engulfing(prev, curr) -> bool`
- `is_bearish_engulfing(prev, curr) -> bool`
- `is_bullish_pin_bar(candle) -> bool`
- `is_bearish_pin_bar(candle) -> bool`
- `is_doji(candle, threshold_pct=0.05) -> bool`
- `is_bullish_reversal(candles) -> bool` — checks last 2-3 candles for bullish patterns. Used by: strategy_2, confidence.py
- `is_bearish_reversal(candles) -> bool` — checks last 2-3 candles for bearish patterns. Used by: strategy_2, confidence.py
- `average_volume(candles, periods=20) -> float`. Used by: volume_analysis.py

#### `relative_strength.py`

- `compute_rs_raw_score(daily_closes) -> float` — IBD-style weighted return (recent quarters weighted more). Used by: canslim/scoring.py, morning_screener
- `percentile_rank_rs(raw_scores: dict[str, float]) -> dict[str, float]` — converts raw scores to 1-99 percentile across universe. Used by: fundamental_data_task (post-processing), morning_screener
- `compute_50_dma(daily_closes) -> float | None`. Used by: canslim/scoring.py, research/agents/technical.py
- `is_above_50_dma(current_price, daily_closes) -> bool | None`. Used by: canslim/scoring.py

#### `volume_analysis.py`

- `compute_avg_volume(daily_volumes, period=20) -> int`. Used by: strategy_2, strategy_5, research
- `is_volume_breakout(current_volume, avg_volume_20d, multiplier=1.5) -> bool`. Used by: strategy_4, strategy_5
- `volume_ratio(current_volume, avg_volume_20d) -> float`. Used by: strategy_2, strategy_5

#### `market_levels.py`

- `find_swing_low(candles, lookback=10) -> float | None`. Used by: select_index_sl_target()
- `find_swing_high(candles, lookback=10) -> float | None`. Used by: select_index_sl_target()
- `select_index_sl_target(direction, vwap, candles_1m, prev_day, cpr, oi_analysis, current_price) -> (sl, target) | None` — picks nearest support/resistance from VWAP bands, PDH/PDL, CPR, OI walls, swing levels. Used by: strategy_2
- `compute_rr_ratio(entry, sl, target) -> float`. Used by: strategy_2, strategy_5 (validation)

#### `global_market.py`

- `compute_pre_open_gap(sgx_or_nifty_price, prev_close) -> float` — gap % from previous close. Used by: morning_screener.snapshot_global_cues()
- `overnight_bias(dow_pct, sp500_pct, us_vix) -> DayBias`. Used by: morning_screener.snapshot_global_cues()
- `combined_global_score(cues: GlobalCues) -> float` — [-1, +1] composite. Used by: morning_screener, strategy_runner
- `global_alignment_factor(cues, direction) -> float` — [0, 1] alignment with trade direction. Used by: confidence.py

#### `intraday_bias.py`

- `compute_intraday_bias(prev_day, candles_1m, vwap, current_price, global_cues, as_of, nifty_bias_score) -> IntradayBias` — 8-factor composite: close_position (decays), gap_vs_pdc (decays), intraday_drift, VWAP slope, price-vs-VWAP, global, candle momentum, nifty_bias_score. Returns `IntradayBias(bias, score [-1,+1], strength STRONG/MODERATE/WEAK, components)`. Used by: strategy_runner (all strategies)
- `is_blocked_by_bias(direction, bias) -> bool` — True only when bias is STRONG and opposite direction. Used by: strategy_2, strategy_5

**Nifty benchmark**: `strategy_runner` caches `_last_nifty_bias_score` on every NIFTY candle close and passes it as `nifty_bias_score` for all non-NIFTY symbols. Pass None for NIFTY itself (avoids circular reference).

#### `adr.py`

- `compute_adr(daily_candles, period=20) -> float` — mean of (high-low)/close × 100 over N days. Used by: morning_screener, strategy_5
- `adr_qualifies(adr_pct, min_adr=1.5) -> bool` — minimum movement filter. Used by: strategy_5, morning_screener Stage 1

#### `rvol.py`

- `build_volume_profile(historical_5m) -> dict[str, float]` — avg volume per 5-min bucket from 20 days of intraday candles (~75 buckets). Used by: morning_screener._build_rvol_baselines()
- `compute_rvol(current_volume, bucket_avg) -> float` — ratio of current vs historical avg for same time bucket. Used by: strategy_5, morning_screener Stage 1
- `serialize_profile(profile) -> str` — JSON for Redis storage. Used by: morning_screener
- `deserialize_profile(json_str) -> dict[str, float]`. Used by: strategy_runner._enrich_strategy5_params()

#### `atr.py`

- `compute_atr(candles, period=14) -> float` — ATR from candles using Wilder's smoothing. Used by: strategy_5 (volatility-aware SL + sizing)

#### `gap_analysis.py`

- `detect_gap(prev_close, today_open) -> GapInfo` — gap direction and magnitude. Used by: strategy_5 (Gap Continuation)
- `is_gap_continuation(candles, gap_direction) -> bool` — price action continues in gap direction. Used by: strategy_5 (Gap Continuation setup)

#### `stock_trend.py`

- `compute_stock_trend(daily_candles) -> StockTrend` — 6-factor composite: price vs 20 DMA (0.25), 5/20 DMA crossover (0.20), HH/HL pattern (0.20), ADR trend (0.10), close position in range (0.15), RS momentum (0.10). Score [-1, +1]. Direction: BULLISH (>0.3) / BEARISH (<-0.3) / NEUTRAL. Strength: STRONG (>0.6) / MODERATE (>0.3) / WEAK. Gracefully degrades with <10 bars. Uses 20 DMA (not 50 — only ~45 daily bars available). Used by: morning_screener, strategy_5 (direction filter + confidence factor)

#### `vix.py`

- India VIX fetch from Redis (`price:INDIA VIX`) or mock. Used by: strategy_runner._build_market_context()

#### `confidence.py`

- `compute_confidence(prev_day, candles_1m, vwap, candles_5m, oi_analysis, india_vix, global_cues, signal_type, rr_ratio, as_of=None, window_state=None, candles_5m_futures_volume=None) -> ConfidenceResult` — 10-factor composite (bias_alignment 0.20, reversal_quality 0.15, global_alignment 0.10, vwap_slope 0.10, volume_quality 0.10, oi_support 0.10, rr_ratio 0.10, cpr_narrow 0.05, vix_regime 0.05, time_of_day 0.05). `window_state` influences time_of_day factor. `candles_5m_futures_volume` for reliable index volume. Used by: strategy_2

---

### `app/data/` — Static Data & Lookups

- `sector_classification.json` — ~180 F&O stocks → ~15 sectors
- `sectors.py`:
  - `get_sector(symbol) -> str | None`. Used by: morning_screener (correlated-duplicate drop), daily_summary_task (sector P&L)
  - `get_sector_stocks(sector) -> list[str]`. Used by: morning_screener
  - `get_all_sectors() -> list[str]`. Used by: morning_screener

---

### `app/data_feed/` — Fyers API Integration

#### `fyers_auth.py`

OAuth flow using `SessionModel` from fyers_apiv3 SDK. Used by: `auth.py` API router.

#### `fyers_auto_login.py`

- `auto_login() -> str` — runs full TOTP headless login (sync httpx in asyncio.to_thread, BrokenResourceError workaround). Used by: auto_login_and_store()
- `trigger_reauth() -> str` — lock-guarded entry point for mid-session auth recovery; 60s cooldown + asyncio.Lock to collapse concurrent 401s into a single TOTP login. Used by: fyers_client._request_with_auth(), fyers_ws_client._on_error()
- `auto_login_and_store() -> str` — runs TOTP flow, caches token in Redis (`fyers:access_token`, 10h TTL). Used by: fyers_login_task

**TOTP window guard**: if < 5s remain in the current 30s window, sleeps into the next window before calling `verify_otp` to prevent code expiring in transit.

**App consent gate**: Fyers v3 `/api/v3/token` returns HTTP 308 with `Url` containing auth code when app is approved. If not approved (e.g. after SEBI compliance resets for new deployments), returns HTTP 200 with consent page — `data.auth` JWT is NOT usable as auth code. Error message includes the exact `generate-authcode` URL to open in a browser. After one-time browser approval, headless login works normally. `FYERS_REDIRECT_URI` must point to production callback: `http://8.231.84.44/api/v1/auth/fyers/callback`.

#### `fyers_client.py` — REST Client

Class: `FyersClient(access_token=None)`

- `get_quotes(symbols) -> dict` — batch quotes. Used by: option_resolver, live_price fallback, market_data API
- `get_historical_data(symbol, resolution, date_from, date_to) -> list[Candle]` — historical OHLCV. Used by: candle_backfill, option_data_fetcher
- `get_option_chain(symbol, expiry_date=None) -> dict` — option chain (Fyers v3 endpoint). Used by: oi_snapshot_task
- `get_market_depth(symbol) -> dict` — Level 2 order book

All public methods route through `_request_with_auth()`: (1) reads freshest token from Redis per call, (2) detects HTTP 401 or Fyers JSON auth errors (-16, -17, -300), (3) calls `trigger_reauth()` once + retries, (4) retries transient 5xx via `async_retry` (3×). In simulated mode (`MARKET_MODE=simulated`): routes REST calls to `SIMULATOR_URL` with no auth headers or reauth logic.

#### `fyers_ws_client.py` — WebSocket Client

Module-level singleton `fyers_ws_client` is either `FyersWSClient` (live) or `SimulatedWSClient` (simulated) — selected at import time via `_create_ws_client()` based on `MARKET_MODE`. All 13+ import sites get the right client with zero changes.

Class: `FyersWSClient`

- `is_connected() -> bool`
- `fetch_quotes_rest(extra_symbols=None)` — fetches live prices via REST on connect. Used by: managed_reconnect
- `start(symbols, extra_symbols=None)` — connects WS, subscribes symbols, starts watchdog. Used by: main.py, fyers_login_task
- `stop()` — disconnects, cancels reconnect task, nulls `_ws` BEFORE close to break reconnect cascade loop
- `register_symbol_map(symbol_map: dict[str, str])` — extends `_reverse_map` for new symbols. Used by: strategy_runner (when new S5 symbols added)
- `is_symbol_subscribed(fyers_symbol) -> bool`
- `subscribe_symbols(symbols, symbol_map=None)` — deduplicates; extends reverse map. Used by: strategy_runner (_resolve_option, _resolve_futures), market_data API, main.py startup

**Reconnect architecture**:

- SDK's `reconnect=True` disabled (causes stale topic_id → symbol mapping). Own managed reconnect via `_on_close` → `_managed_reconnect()` (stop() + start() after 5s backoff)
- `_managed_reconnect()` merges existing `_symbols` with `_collect_dynamic_symbols()` output, then stop()+start()
- `_collect_dynamic_symbols() -> list[str]` — reads 3 Redis keys: `strat5:watchlist:{today}`, `strat5:watchlist:permanent`, `watchlist:items`. Ensures screener-provisioned symbols survive reconnects. Used by: `_managed_reconnect()`, `_premarket_reconnect()`
- `_on_close` records `_last_disconnect_at`, calls `feed_manager.clear_in_progress_candles()`
- `_on_connect` schedules gap backfill for the disconnect window
- `_watchdog_loop()` polls every 30s; triggers reauth+restart if no tick in >90s (catches silent server-side hangs)
- **Self-cancellation guard**: both reconnect methods null `self._reconnect_task` BEFORE calling `stop()` to prevent killing themselves mid-flight
- **Orphan prevention**: `start()` always calls `stop()` first if a live `FyersDataSocket` exists

**Auth error -300**: only treat as auth failure when `invalid_symbols` is absent (Fyers uses -300 for both "invalid token" AND "invalid symbol" — treating the latter as auth caused reauth storms).

#### `simulated_ws_client.py` — Simulated WebSocket Client

Drop-in replacement for `FyersWSClient` when `MARKET_MODE=simulated`. Same public interface (`is_connected`, `start`, `stop`, `register_symbol_map`, `is_symbol_subscribed`, `subscribe_symbols`, `fetch_quotes_rest`). Connects to Market Simulator at `ws://{SIMULATOR_URL}/ws/ticks` via `websockets` library. Sends JSON subscribe messages. Feeds ticks into `feed_manager.process_tick()` with same `tick_data` dict format. Has auto-reconnect on disconnect. `fetch_quotes_rest` hits simulator's `/data/quotes` endpoint. Singleton: `simulated_ws_client = SimulatedWSClient()`.

#### `symbol_master.py`

Class: `SymbolMaster`

- `load()` — downloads NSE_CM/FO + BSE_CM/FO CSVs, parses ~127K symbols, stores as JSON in Redis (`symbols:master`, 24h TTL). CSV source URL swaps to `SIMULATOR_URL/sym_details/` in simulated mode
- `refresh()` — re-downloads (called inline at startup, daily at 8:00 AM)
- `search(query, min_score=20) -> list[dict]` — in-memory search: exact/prefix/substring on short name + display name + Fyers symbol. Used by: market_data API (`GET /symbols/search`)
- `is_loaded -> bool`, `count -> int`

#### `feed_manager.py`

Class: `FeedManager`

- `start(symbols) -> None` — initializes in-progress candle state. Used by: fyers_ws_client.start()
- `stop() -> None`. Used by: fyers_ws_client.stop()
- `process_tick(symbol, tick_data, fyers_alias=None) -> None` — aggregates tick into in-progress candle; on minute boundary emits + persists + triggers strategy eval. `tick_data` is a dict with keys: `ltp`, `bid`, `ask`, `volume`, `change`, `change_pct`, `high`, `low`, `open`, `prev_close`. `fyers_alias` causes dual-name publish (short name + Fyers alias). Used by: fyers_ws_client._on_message(), simulated_ws_client._receive_loop()
- `clear_in_progress_candles() -> None` — resets in-progress candle state on WS disconnect (preserves `_last_vol_today` baselines — Fyers cumulative volume continues from where it left off). Used by: fyers_ws_client._on_close()

**Volume delta tracking**: Fyers sends `vol_traded_today` (cumulative). `_aggregate_candle` computes `max(0, current − last)` delta per tick. First-tick seeding produces zero delta on restart (prevents entire morning's volume dumping into one candle — the RVOL spike bug).

**Market hours guard**: `_run_auto_strategy_evaluation` returns early when `is_market_open()` is False — prevents GATE log spam from REST quote fetches outside market hours. `_emit_candle` also guards DB persistence: pre-market candles (before 09:15 IST, from the pre-open auction) are published to Redis/WS for display but not written to `market_data_1m`, preventing them from polluting `_query_previous_day`.

---

### `app/research/` — AI Research Agent System

Multi-agent research: user searches stock → orchestrator spawns 6 agents in parallel → synthesis → persisted report.

#### `orchestrator.py`

- `start_research(symbol, report_id)` — creates asyncio.Task, launches 6 agents via `asyncio.gather(return_exceptions=True)`, broadcasts progress via WebSocket, persists to `ResearchReport`/`ResearchAgentRun`. Max 3 concurrent sessions. Used by: research API
- `get_active_research_count() -> int`. Used by: research API (429 gate)

Synthesis uses Pro model (`create_llm_client(pro=True)`); sub-agents use Flash. `_sanitize_for_jsonb()` cleans NaN/Infinity/Decimal/datetime before JSONB storage.

#### `llm_client.py`

Abstract: `LLMClient` with `generate()`, `generate_with_search()`, `generate_json()`.
Implementation: `GeminiClient`

- `generate(prompt, system=None, max_tokens=2048) -> str` — plain text generation. Used by: all 6 research agents
- `generate_with_search(prompt, system=None) -> str` — Gemini Google Search grounding for real-time news. Used by: news_sentiment agent
- `generate_json(prompt, system=None, max_tokens=2048, response_schema=None) -> dict` — extracts JSON; retry-once on empty/unparseable; guards against Gemini returning JSON array instead of object. Used by: signal_confidence, morning_screener (briefing + Stage 3)

Factory:

- `create_llm_client(pro=False) -> LLMClient` — `pro=True` uses `RESEARCH_LLM_MODEL_PRO` (gemini-3.1-pro-preview); Vertex AI takes precedence over API key. Used by: orchestrator (synthesis), morning_screener (briefing + Stage 3), signal_confidence

**Auth modes**: `GCP_PROJECT_ID` set → Vertex AI with ADC; only `GOOGLE_API_KEY` → AI Studio.
**Critical**: `generate_json` params are `prompt` and `system` — NOT `user_prompt`/`system_prompt` (wrong names cause silent TypeError → FALLBACK).

#### `data_gatherer.py`

- Prefetches shared context (`StockInfo`, 1Y price history, existing fundamentals) into `ResearchContext`. On-demand fundamental fetch if row missing/stale. Used by: orchestrator

#### `report_builder.py`

- Template-based fallback report if LLM synthesis fails. Used by: orchestrator

#### `agents/`

All extend `BaseResearchAgent` (`agents/base.py`), return `AgentResult(findings: dict, summary: str)`.


| Agent                  | Description                                  | Data Sources                                                  |
| ---------------------- | -------------------------------------------- | ------------------------------------------------------------- |
| `fundamental.py`       | Quarterly earnings, CAN SLIM scores          | yfinance, `canslim/scoring.py`                                |
| `technical.py`         | Trend, RS rating, RSI, chart patterns        | `indicators/`, `canslim/base_patterns.py`                     |
| `oi_derivatives.py`    | PCR, max pain, OI buildup                    | Fyers option chain (F&O stocks only; graceful skip otherwise) |
| `institutional.py`     | FII/DII/MF shareholding, QoQ trend           | NSE API (`nse_client.get_shareholding_pattern()`)             |
| `news_sentiment.py`    | Real-time Indian stock news (30-day window)  | Gemini grounded search                                        |
| `valuation.py`         | PE/PB/PEG, dividend yield, sector comparison | yfinance                                                      |
| `signal_confidence.py` | LLM overlay ±30 confidence adjustment        | Gemini (per-strategy system + user prompts)                   |


`**signal_confidence.py`** (`score_signal(signal, ctx, prior_signals=None) -> SignalConfidence`):

- Per-strategy prompts (`_SYSTEM_PROMPTS` / `_USER_PROMPT_TEMPLATES` keyed by strategy name)
- Sends full indicator JSON context + up to 5 prior signals today for same symbol+strategy
- 25s timeout; never blocks signal on failure; controlled by `settings.ai_confidence_enabled`
- Per-symbol 5-min cooldown in strategy_runner prevents rate-limit storms
- Adjustment scale: -30 to -20 = fundamental flaw; -10 to +10 = normal; +20 to +30 = exceptional
- SKIP suggested when adjustment ≤ -20
- Uses `response_schema=_SIGNAL_CONFIDENCE_SCHEMA` for structured output

---

### `app/agent/` — AI Trading Agent

#### `agent_runner.py`

- `is_running: bool` — property. Used by: agent API status
- `started_at: datetime | None` — property
- `start()` — starts 500ms trade monitor loop. Used by: main.py, agent API
- `stop()`. Used by: agent API
- `on_new_signal(signal_id)` — fire-and-forget (called via `create_task`); gates Telegram notification on confidence floor (`signal.confidence >= min_confidence_for_execution`; None confidence still notifies); auto-executes if YOLO + running + executable. Top-level try/except prevents silent exception loss. Used by: strategy_runner._handle_signal()
- `_handle_new_signal(signal_id)` — internal impl of on_new_signal; Telegram + YOLO auto-execution logic. Used by: on_new_signal()

#### `trade_monitor.py`

- `monitor_positions(db, yolo_mode=False) -> list[dict]` — called every 500ms by agent_runner; checks profit cap first, then monitors each open position. Used by: agent_runner

Internal flow per position check:

1. `_check_profit_cap(db)` — if cap enabled and realized+unrealized ≥ cap: close all non-shadow positions (`ExitReason.PROFIT_CAP`), send Telegram, short-circuit loop
2. `_check_position(db, pos, yolo_mode)` — SL hit? Target hit? Time exit? Trailing SL update?
3. `_close_position(db, pos, exit_price, exit_reason, ...)` — closes trade, computes `brokerage_calculator.compute_charges()`, stores in `Trade.charges_json`, broadcasts `trade:close` + `agent:action`
4. `_request_profit_confirmation(db, pos)` — for SEMI mode target hits; guards against duplicate confirmation requests
5. `_roll_futures_position(db, pos)` — 3 days before expiry: close old + open next month via `futures_resolver`; preserves `source=SHADOW` and `is_shadow=True`

**Direction detection**: uses `target_price < entry_price` (target below entry = SHORT). **SHORT position support**: direction-aware SL hit, target hit, unrealized PnL, HWM (lowest price for shorts), trailing SL direction.

**Trailing SL**: POSITIONAL always trails; INTRADAY trails when `trailing_sl_enabled=True`. Breakeven at `trailing_sl_breakeven_pct` (S5: 0.5%); progressive trail when `trailing_sl_trail_pct` is set. SL only moves favorably. `ExitReason.TRAILING_SL` vs `ExitReason.AGENT_SL` distinguished by comparing `trade.stop_loss` (original) vs `pos.stop_loss` (live, trailed).

**Stale data grace period**: positions < 5 minutes old skip the stale-data closure check (both shadow and non-shadow). After grace period, shadow positions with no price are closed with `ExitReason.STALE_DATA` and PnL zeroed. Non-shadow positions continue to return None (no action).

`**agent:action` broadcast shape**: must match `AgentLogResponse` (id, action_type, trade_id, details, requires_confirmation, confirmation_status, confirmed_at, created_at) — frontend `AgentFeed` reads `log.id` for React keys and `log.details.{symbol,pnl,strategy_name}` for display. Do NOT change to a flat dict.

#### `auto_executor.py`

- `auto_execute_signal(signal_id) -> dict | None` — YOLO gate order: (1) PENDING + executable; (2) confidence gate (`min_confidence_for_execution`); (3) per-strategy `yolo_enabled` gate (from `strategy_configs`); (4) permanent watchlist gate (`yolo_skip_permanent_watchlist`); (5) open YOLO trade dedup per signal_id (prevents re-executing same signal); (6) open position dedup per symbol+direction (non-shadow only); (7) `_final_risk_check` (drawdown, max-trades, daily profit cap). Sets `Trade.source = "YOLO"`. Used by: agent_runner.on_new_signal()

Lot sizing via `compute_lots_for_yolo()`. SL/target recomputed from live LTP via `recompute_sl_target()`. Sets `margin_required` on Trade + Position. Broadcasts `trade:open` (includes `margin_required`) + `agent:auto_executed`.

#### `shadow_executor.py`

- `shadow_execute_signal(signal_id) -> None` — fire-and-forget; gate order: (1) PENDING or EXECUTED (allows shadow mirroring of YOLO-executed signals); (2) open shadow trade dedup per signal_id (CLOSED shadows don't block — allows fresh shadow on Case-2 re-fire); (3) open shadow position dedup per symbol+strategy (prevents stacking shadow positions from different signals on same underlying); (4) past close deadline; (5) F&O ban; (6) resolution failure; (7) per-strategy `shadow_enabled` gate (from `strategy_configs`); (8) confidence gate (`min_confidence_for_shadow`); (9) permanent watchlist gate (`shadow_skip_permanent_watchlist`). Creates `Trade(source="SHADOW")` + `Position(is_shadow=True)`. Used by: strategy_runner._handle_signal(), strategy_runner._dedup_signal() (Case-2)

Always 1 lot. No capital gates (even VIX extreme, drawdown, max-trades, outside window — these blocked signals are shadow-executed to measure what would have happened). SL/target recomputed from live LTP. See `docs/ai/shadow-agent.md` for isolation guarantees.

#### `notification.py`

All outbound Telegram messages. No ORM imports — callers pass plain scalars.

- `send_telegram(message) -> bool` — early-returns False when `TELEGRAM_ENABLED=false`; retries 3× with 2s base delay. Uses sync `httpx.Client` via `asyncio.to_thread` (TLS fix for macOS 15.2 async TLS regression)
- `notify_signal_generated(signal, ...)` — confidence floor gate; `blocked_reason` HTML-escaped before embedding
- `notify_auto_executed(trade, signal)` — YOLO execution notification
- `notify_manual_executed(trade, signal)` — ✋ Manual Exec notification; called from signals.py
- `notify_sl_hit(position, pnl, is_trailing=False)` — 🟡 "Trailing Stop Hit" when `is_trailing=True`
- `notify_profit_booked(position, pnl)` — target hit notification
- `notify_time_exit(position, pnl)` — 3:15 PM time exit
- `notify_confirmation_request(log)` — SEMI mode profit confirmation
- `notify_expiry_roll(old_trade, new_trade)` / `notify_expiry_roll_failed(symbol, expiry)` — futures roll
- `notify_drawdown_halt(daily_pnl, limit)` — drawdown gate triggered
- `notify_profit_cap_halt(daily_pnl, limit, positions_closed)` — daily profit cap hit. Only called by trade_monitor (not auto_executor)
- `notify_daily_summary(trades, market_data, global_cues, briefing)` — 3:35 PM EOD report (excludes shadow, uses `net_pnl` when available, LLM-drafted market wrap with Pro model)
- `notify_morning_premarket(briefing, global_cues)` — 8:00 AM pre-market report with Nifty + BankNifty previous close, LLM-drafted global cues, sector bias, F&O build-up, OI levels. BankNifty sourced from `price:BANKNIFTY` Redis cache (24h TTL)
- `notify_morning_preopen(watchlist, global_cues)` — 9:08 AM pre-open update with watchlist + gap commentary; pure formatting, no LLM

**P&L safety**: P&L label written as "PnL" (not "P&L") — literal `&` in HTML parse_mode causes Telegram 400. `blocked_reason` wrapped with `html.escape()` before embedding.

#### `telegram_bot.py`

- `start_telegram_bot()` / `stop_telegram_bot()` — wired into main.py lifespan; task tracked in TaskRegistry as `telegram_bot_poll`
- Long-polls `getUpdates` (timeout=30); on startup advances past queued messages (avoids replaying stale commands after restart)
- Security gate: ignores messages from any `chat_id` ≠ `settings.telegram_chat_id`
- Skips startup if `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` missing, or `TELEGRAM_ENABLED=false`

#### `telegram_commands.py`

- `handle_command(cmd, chat_id)` — dispatches via `_HANDLERS` dict. Used by: telegram_bot

Commands: `/status` (market open, agent mode, feed liveness, S2 window, S5 phase, counts), `/market` (indices + VIX, global cues score, briefing approach), `/shadow` (shadow trade P&L), `/yolo` (YOLO trade P&L), `/signals` (today's signals above execution threshold, grouped PENDING then EXECUTED), `/help` (command list)

Phone-friendly card format (no monospace blocks). Futures show LONG/SHORT via `_direction()`; options hide it (CE/PE implies direction). All timestamps via `_to_ist()`. Each section wrapped in try/except for graceful degradation.

---

### `app/tasks/` — Scheduled Tasks


| Task                       | Schedule                                                                                                   | What it does                                                                                                                                                                                                                                                                                                                                                                                                      |
| -------------------------- | ---------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `fyers_login_task.py`      | 7:45 AM IST daily                                                                                          | TOTP auto-login; skipped entirely in simulated mode. On startup: blocks until token obtained (up to 10 retries × 2 min) or raises RuntimeError. Daily: non-blocking, retries up to 10× via DateTrigger on failure. **Terminal error detection**: if error contains "block", "invalid pin", or "consent required", stops all retries immediately and sends Telegram alert (prevents burning through Fyers' 5-attempt PIN limit). Reauth: `_start_data_feed_after_login()` does fresh symbol assembly (same 4-source logic as startup — strategy configs, watchlist, S5 watchlist, positions), not `_symbols` carryover.         |
| `symbol_master_task.py`    | 8:00 AM IST daily                                                                                          | Refreshes symbol master CSVs                                                                                                                                                                                                                                                                                                                                                                                      |
| `oi_snapshot_task.py`      | Every 3 min market hours (CE/PE); 3:25 PM daily (stock futures); Every 10 min 9:20-15:30 (S5 watchlist OI) | Fyers option chain OI → `oi_snapshots` table. `fetch_stock_futures_oi()` fetches ~180 F&O stocks' FUT OI at EOD. `fetch_s5_watchlist_oi()` fetches targeted 10-15 S5 watchlist symbols' live OI every 10 min. **Fyers field**: use `"oi"` not `"open_interest"` in the `v` dict. Rate-limited: semaphore(2) + 0.3s delay. Gap-fill on startup via `_fill_stock_futures_oi_gaps()` from NSE FO bhav copy archives. |
| `nse_bhav_copy_task.py`    | 7:30 AM IST daily                                                                                          | Downloads NSE CM bhav copy CSV → (1) Redis `nse:bhav_copy:{date}` (90-day TTL, slim payload for delivery % scoring), (2) `market_data_daily` table (full OHLCV upsert). Gap-fills last 7 trading days on startup. Cookie session required (preflight GET to nseindia.com).                                                                                                                                        |
| `global_market_task.py`    | Every 15 min; once on startup                                                                              | yfinance 8 tickers (Dow, S&P500, Nasdaq, Nifty, Crude, USDINR, DXY, VIX); 0.5s inter-ticker. Writes `indicator:global:{field}` Redis keys (20-min TTL) + `GlobalMarketSnapshot` DB row. **NIFTY override**: after yfinance, reads `price:NIFTY` from Redis (Fyers live) to override — yfinance `^NSEI` has 1-day lag.                                                                                             |
| `fundamental_data_task.py` | 06:00, 12:00, 18:00 IST; once on startup                                                                   | Fetches CAN SLIM fundamentals (yfinance + NSE) for all CAN SLIM symbols; 5s inter-symbol. Staleness guard: skips symbols with `last_refreshed_at` < 6h old (`STALENESS_THRESHOLD_HOURS`). `_fetch_and_store_symbol(symbol, lot_sizes)` — also called on demand by morning screener for S5 watchlist symbols.                                                                                                       |
| `daily_summary_task.py`    | 3:35 PM IST daily                                                                                          | EOD Telegram report (excludes shadow); LLM-drafted market wrap + trading assessment with graceful fallback. Uses `net_pnl` when available. Nifty/BN change sourced from `change`/`change_pct` in Redis price cache (not computed from prev_close). Skips non-trading days.                                                                                                                                         |
| `morning_workflow_task.py` | 8:00 AM, 8:30 AM, 9:08 AM, 9:31 AM, 3:15 PM IST                                                            | Strategy 5 daily workflow: briefing → screener → pre-open reassessment → ORB level logging → EOD summary. Telegram hooks wrapped in try/except. All idempotent per day, skip non-trading days.                                                                                                                                                                                                                    |
| `fo_ban_list_task.py`      | 7:00 AM IST daily; once on startup                                                                         | Fetches and caches NSE F&O ban list to `nse:fo_ban_list:{date}` (24h TTL). JSON primary source, CSV fallback. Returns empty set on failure (graceful degradation).                                                                                                                                                                                                                                                |
| `signal_expiry_task.py`    | 3:30 PM IST daily                                                                                          | Bulk-expires ALL remaining PENDING intraday strategy signals (`_INTRADAY_STRATEGIES`: VWAP Pullback, Intraday Futures, ORB, Gamma Scalping). No date filter — cleans up stale signals from prior days too. Positional strategies (CAN SLIM) exempt. Only runs on trading days.                                                                                                                                    |


---

### `app/data_sources/` — External Data Sources

#### `yfinance_client.py`

All use `asyncio.to_thread` (yfinance is sync). Rate-limited: semaphore(2) + 1.0s delay + 3× retry with exponential backoff.

- `get_quarterly_earnings(symbol) -> list[QuarterlyEarnings]` — uses `.NS` suffix. Used by: fundamental_data_task, research/fundamental
- `get_annual_financials(symbol) -> list[AnnualFinancials]`. Used by: fundamental_data_task, research/fundamental
- `get_stock_info(symbol) -> StockInfo | None`. Used by: fundamental_data_task, research/fundamental
- `get_price_history(symbol, period="1y") -> list[PriceHistory]`. Used by: fundamental_data_task, research/technical

#### `nse_client.py`

- `get_shareholding_pattern(symbol) -> list[ShareholdingPattern]` — two-step: master endpoint + XBRL XML for FII/DII/MF breakdown. Used by: research/institutional
- `get_fo_lot_sizes() -> dict[str, int]` — primary: Fyers symbol master (Redis); fallback: NSE JSON API. Used by: morning_screener, option_resolver
- `get_fo_ban_list(trade_date=None) -> set[str]` — Redis cache (`nse:fo_ban_list:{date}`, 24h TTL) first; fetches NSE JSON or CSV on miss. Returns empty set on failure. Used by: fo_ban_list_task, strategy_runner._check_regulatory_limits(), morning_screener Stage 1

---

### `app/backtest/` — Backtest Harness

Bypasses `strategy_runner` — calls `strategy.evaluate(ctx)` directly. No DB writes, WS events, or auto-execution.

#### `context_builder.py`

- `build_historical_context(symbol, as_of: datetime, session) -> MarketContext | None` — builds MarketContext from historical `MarketData1m` + `OISnapshot` + `GlobalMarketSnapshot` rows filtered by `timestamp <= as_of`. Replays VWAP from today's candles up to `as_of`. Returns None if insufficient data. Used by: harness.py

#### `harness.py`

Class: `Backtester(mode, window_filter)`

- `run(strategy, symbol, start, end) -> BacktestReport` — walks minute-by-minute. `accurate` mode: real option premiums via Fyers. `fast` mode: delta approximation. `window_filter=True`: only evaluates within trade windows (loaded from `get_strategy_params()`). Used by: `scripts/backtest.py`

#### `exit_simulator.py`

- `simulate_exit(signal, entry_ts, spot_candles_after, fyers_option_symbol, entry_premium, mode) -> SimulatedTrade` — accurate mode walks option 1m candles; fast mode delta-approximates (ATM δ=0.50, ITM δ=0.60). Returns `SimulatedTrade(pnl_per_lot, pnl_pct, exit_reason)`. Used by: harness.py

#### `option_data_fetcher.py`

- `ensure_option_candles(fyers_option_symbol, start_ts, end_ts) -> list[Candle]` — DB first; fetches from Fyers SDK and persists if missing; in-memory cache per run. **Fyers free plan only serves 1m history while contract is actively listed** — expired contracts return `s="error"`. Historical replay of expired signals relies on DB-cached candles from live WS feed or falls through to fast mode. Used by: exit_simulator (accurate mode)

#### `strike_selector.py`

- `select_expiry_as_of(symbol, as_of_date) -> date` — holiday-aware historical expiry selection
- `resolve_option_symbol(symbol, index_price, signal_type, as_of_date) -> str | None` — historical option symbol resolution. Used by: harness.py

#### `report.py`

- `build_report(trades, signals_meta) -> BacktestReport` — metrics: hit rate, avg win/loss, expectancy, profit factor, CE/PE breakdown, `oi_coverage_pct`, confidence-bucket calibration. Used by: harness.py
- `print_report(r: BacktestReport) -> None` — formatted console output. Used by: `scripts/backtest.py`

#### `result_saver.py`

- `save_run(run_dir, report, signals_meta, run_ts) -> None` — saves backtest results: summary JSON + trades CSV + signals CSV into a timestamped run directory. Used by: `scripts/backtest.py`, `scripts/backtest_strategy5.py`

---

## Conventions

- All async functions use `async def`
- Database sessions via FastAPI dependency injection (`Depends(get_db)`)
- Type hints on all function signatures
- IST timezone for all market-related times, stored as TIMESTAMPTZ (UTC in DB; use `AT TIME ZONE 'Asia/Kolkata'` in raw SQL)
- UUID for all primary keys
- Services never import from `api/` — only the reverse
- Indicators are pure functions: candle data in, values out, no DB/Redis access
- Config loaded from `.env` via Pydantic Settings (see `.env.example`)
- When updating docs: add new functions to the registry in the relevant section above, update `Used by:` lists for callers, update the API endpoint table for new routes

---

## How-To Guides

### Add a New Strategy

1. Create `app/strategies/strategy_N_name.py` extending `BaseStrategy`
2. Implement: `evaluate(ctx: MarketContext) -> StrategySignal | None`, `should_exit()`, `get_position_size()`
3. Add name to `StrategyName` enum in `app/core/enums.py`
4. Strategy is auto-discovered by `registry.py` — just needs a `strategy_configs` DB row
5. Add strategy doc in `docs/strategies/strategy-N-name.md`
6. Add function registry entry in this CLAUDE.md under `app/strategies/`

### Add a New API Endpoint

1. Add or edit router in `app/api/v1/{resource}.py`
2. Add Pydantic schemas in `app/schemas/{resource}.py`
3. If new router file, include in `app/api/router.py`
4. Service logic goes in `app/services/`, not in the router
5. Update the endpoint table in this CLAUDE.md

### Add a New Indicator

1. Create pure function in `app/indicators/{name}.py`
2. Add fields to `MarketContext` in `app/strategies/base.py`
3. Populate in `app/services/strategy_runner.py` when building context
4. Write tests in `tests/test_indicators/test_{name}.py`
5. Add function registry entry in this CLAUDE.md under `app/indicators/`

### Add a New Database Table

1. Create model in `app/models/{name}.py` extending `BaseModel`
2. Import in `app/models/__init__.py` so Alembic discovers it
3. Run `make migration msg="add {name} table"`
4. Run `make migrate`
5. Add model to the table in this CLAUDE.md under `app/models/`

### Run Tests

```bash
cd backend && source .venv/bin/activate && python -m pytest tests/ -v --tb=short
```

