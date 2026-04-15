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
- `constants.py` - Market hours (9:15-15:30 IST), lot sizes (NIFTY=75, BANKNIFTY=30, FINNIFTY=25, SENSEX=10, MIDCPNIFTY=75), exchange codes, VWAP_PROXIMITY_PCT
- `enums.py` - All enums: OptionType, OrderSide, TradeStatus, ExitReason, SignalStatus, SignalType, StrategyName, IndexSymbol, AgentAutonomyLevel, AgentActionType, ConfirmationStatus, DayBias, CPRType
- `exceptions.py` - Custom exception hierarchy
- `utils.py` - IST timezone helpers (`now_ist()`, `is_market_open()`), market hour checks

### `app/models/` - SQLAlchemy ORM (8 tables)
- `base.py` - BaseModel: UUID primary key, created_at/updated_at timestamps
- `trade.py` - Trade: entry/exit prices, P&L, status, strategy link
- `signal.py` - Signal: strategy output, strike, expiry, confidence, `executable` flag, `blocked_reason`
- `position.py` - Position: active positions with unrealized P&L tracking
- `market_data.py` - MarketData1m: 1-minute OHLCV candles
- `oi_snapshot.py` - OISnapshot: open interest by strike price
- `agent_log.py` - AgentLog: agent action audit trail
- `daily_summary.py` - DailySummary: daily P&L, win/loss counts, drawdown
- `strategy_config.py` - StrategyConfig: per-index strategy enable/disable + parameters

### `app/schemas/` - Pydantic Schemas
Request/response schemas. Convention: `{Entity}Create`, `{Entity}Response`, `{Entity}Update`.
- `trade.py`, `signal.py`, `position.py`, `agent.py`, `market_data.py`, `risk.py`, `websocket.py`

### `app/api/v1/` - REST API (8 routers, all under `/api/v1/`)
- `trades.py` - CRUD for trades (create from signal, close, list, history)
- `signals.py` - List/filter signals by strategy, status, date
- `positions.py` - Active positions, close, update SL/target
- `agent.py` - Agent start/stop/status, confirm actions, YOLO toggle (`PATCH /yolo`)
- `risk.py` - Daily P&L, drawdown %, configured limits
- `market_data.py` - `GET /prices` (all symbols, auto-refreshes via REST if cache empty), `POST /prices/batch` (fetch prices for arbitrary Fyers symbols, used by watchlist), `POST /feed/start|stop|refresh`, `GET /symbols/search` (local symbol master, supports stocks/futures/options: "TCS", "NIFTY 24000CE", "RELIANCE FUT")
- `watchlist.py` - `GET /watchlist`, `POST /watchlist`, `DELETE /watchlist/{symbol}` — Redis-backed watchlist (agent can add symbols programmatically)
- `strategies.py` - List/update strategy configs
- `auth.py` - Fyers OAuth flow (login redirect, callback, token storage)

### `app/websocket/` - Real-Time Layer
- `manager.py` - WebSocketManager: connect/disconnect/broadcast. Single `/ws` endpoint. Events: `price:update`, `signal:new`, `trade:open`, `trade:close`, `position:pnl`, `agent:action`, `market:status`

### `app/services/` - Business Logic
- `strategy_runner.py` - On each candle close: evaluates all active strategies, persists signals to DB, broadcasts via WebSocket, triggers agent if YOLO mode

### `app/strategies/` - Strategy Engine
- `base.py` - `BaseStrategy` ABC with `evaluate(ctx) -> StrategySignal | None`, `should_exit()`, `get_position_size()`. Defines `MarketContext` (current price, candles, VWAP, PDH/PDL, CPR, OI, VIX)
- `registry.py` - Discovers and instantiates active strategies from DB config
- `strategy_1_orb.py` - Opening Range Breakout (STUB - not implemented)
- `strategy_2_vwap_pullback.py` - VWAP Pullback + Previous Day Bias + OI (PRIMARY - fully implemented)
- `strategy_3_gamma_scalping.py` - Expiry Day Gamma Scalping (STUB - not implemented)

### `app/indicators/` - Technical Indicators (pure functions, no side effects)
- `vwap.py` - VWAP calculation from candles, `is_pullback_to_vwap()`, `price_distance_from_vwap()`, VWAP bands
- `cpr.py` - Central Pivot Range: pivot, TC, BC, support/resistance levels, CPR type (WIDE/NARROW)
- `previous_day.py` - PDH, PDL, PDC, day bias (BULLISH/BEARISH/NEUTRAL), range calculation
- `open_interest.py` - PCR ratio, max pain, `is_oi_supporting_direction()`, sentiment analysis
- `vix.py` - India VIX fetch/mock
- `candle_patterns.py` - `is_bullish_reversal()`, `is_bearish_reversal()`, `average_volume()`

### `app/data_feed/` - Fyers API Integration
- `fyers_auth.py` - OAuth flow using `SessionModel` from fyers_apiv3 SDK
- `fyers_auto_login.py` - Headless auto-login: base64-encoded credentials, TOTP generation via pyotp
- `fyers_client.py` - REST client: quotes, historical data, option chain, OI
- `fyers_ws_client.py` - WebSocket client: `FyersDataSocket` (threaded SDK bridged to asyncio), auto-fetches prices on start
- `symbol_master.py` - Downloads Fyers symbol master CSVs (NSE_CM/FO, BSE_CM/FO), parses ~127K symbols, stores gzip-compressed in Redis, provides in-memory search. Refreshed daily.
- `feed_manager.py` - Aggregates ticks into candles, publishes to Redis, triggers `strategy_runner.on_candle_close()`

### `app/agent/` - AI Trading Agent
- `agent_runner.py` - Main agent loop (2s interval). Singleton. Manages YOLO mode toggle, dispatches to monitor/executor
- `trade_monitor.py` - Checks open positions: SL hit → auto-close, target hit → confirm (SEMI) or auto-book (YOLO), 3:15 PM time exit, 5% drawdown halt
- `auto_executor.py` - Executes signals automatically in YOLO mode
- `notification.py` - Telegram Bot API: trade alerts, confirmation requests, P&L summaries

### `app/tasks/` - Scheduled Tasks
- `fyers_login_task.py` - APScheduler job: auto-refreshes Fyers token via TOTP login
- `symbol_master_task.py` - APScheduler job: refreshes symbol master daily at 8:00 AM IST

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
