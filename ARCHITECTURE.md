# Architecture

## System Overview

```
┌─────────────┐     ┌──────────────────────────────────────────────────┐
│  Fyers API  │────>│  Backend (Python 3.11 / FastAPI on :8080)        │
│  (Market    │ WS  │                                                  │
│   Data)     │     │  ┌──────────────┐  ┌─────────────────────────┐   │
└─────────────┘     │  │ Feed Manager │─>│ Redis (:6380)            │   │
                    │  │ (fyers_ws +  │  │ Price cache (24h TTL)    │   │
                    │  │ Symbol master (gzip)     │   │
                    │  │ Watchlist items (hash)    │   │
                    │  │  REST polls) │  └──────────┬──────────────┘   │
                    │  └──────┬───────┘             │                  │
                    │         │                     v                  │
                    │         v              ┌─────────────────────┐   │
                    │  ┌──────────────┐      │ WebSocket Manager   │──>│──> Frontend
                    │  │ Strategy     │      │ /ws (event-based)   │   │    (Next.js :3000)
                    │  │ Engine       │      └─────────────────────┘   │
                    │  └──────┬───────┘                                │
                    │         │                                        │
                    │         v                                        │
                    │  ┌──────────────┐  ┌─────────────────────────┐   │
                    │  │ Signal       │─>│ Trade Service            │   │
                    │  │ Service      │  │ (Paper / Live execution) │   │
                    │  └──────────────┘  └──────────┬──────────────┘   │
                    │                               │                  │
                    │  ┌──────────────┐              v                  │
                    │  │ AI Agent     │──> Position Monitor ──────────>│──> Telegram
                    │  │ (MANUAL /    │    SL auto-close               │    Notifications
                    │  │  SEMI / YOLO)│    Profit confirm/auto-book    │
                    │  └──────────────┘    Time exit (3:15 PM)         │
                    │                      Drawdown halt (5%)          │
                    │                                                  │
                    │  ┌─────────────────────────────────────────────┐  │
                    │  │  PostgreSQL (:5433)                         │  │
                    │  │  trades | signals | positions | market_data │  │
                    │  │  oi_snapshots | strategy_configs |          │  │
                    │  │  daily_summaries | agent_logs               │  │
                    │  └─────────────────────────────────────────────┘  │
                    └──────────────────────────────────────────────────┘
```

## Symbols Tracked
NIFTY (75), BANKNIFTY (30), FINNIFTY (25), SENSEX (10), MIDCPNIFTY (50) — lot sizes in parentheses.
India VIX is also tracked for risk gating (no lot size — informational only).

## Expiry Schedule (Post-SEBI Nov 2024)
- **NIFTY**: Weekly Tuesday (NSE)
- **SENSEX**: Weekly Thursday (BSE)
- **BANKNIFTY, FINNIFTY, MIDCPNIFTY**: Monthly only — last Tuesday of month (NSE)

## Data Flow

### 1. Price Data Pipeline
```
Fyers WebSocket ──> fyers_ws_client.py (FyersDataSocket, threaded → asyncio bridge)
                ──> feed_manager.py
                    ├── Redis PUBLISH "price:{symbol}" (real-time cache)
                    ├── Aggregate into 1m/5m candles
                    ├── Store completed candles in PostgreSQL (market_data_1m)
                    ├── WebSocket broadcast to frontend (price:update)
                    └── On candle close: check auto_mode strategies for this symbol
                        └── If any match → trigger strategy_runner.on_candle_close()

Subscribed symbols (all get live WebSocket ticks):
  - FYERS_SYMBOL_MAP (5 indices + India VIX) — always subscribed
  - Watchlist items from Redis — subscribed on startup and on add

Frontend also polls GET /api/v1/market/prices every 10s as fallback.

Chart data: GET /api/v1/market/ohlcv/{symbol}?resolution=5&days=15
  → Backend proxies to Fyers history API (pre-aggregated candles)
  → Paginated for large ranges, deduped, sorted ascending
  → Falls back to PostgreSQL (MarketData1m) if Fyers unavailable
  → Resolutions: "1" (1m), "5" (5m), "15" (15m), "60" (1h), "D" (daily)
```

### 2. Strategy Signal Flow

Strategy evaluation is **decoupled** from the candle pipeline. Two trigger paths:

```
PATH A — Auto Mode (candle-driven):
  1m candle close ──> feed_manager._run_auto_strategy_evaluation()
                      ├── get_auto_strategies_for_symbol(symbol)
                      │   queries strategy_configs WHERE auto_mode=True AND symbol IN symbols
                      └── if matches → strategy_runner.on_candle_close(symbol, candle, strategy_filter=[...])

PATH B — Manual Mode (API-driven):
  POST /api/v1/strategies/evaluate/batch {strategy_name}
      ├── Reads configured symbols for this strategy from strategy_configs
      └── For each symbol → strategy_runner.evaluate_manual(symbol, strategy_name)
          ├── Builds MarketContext from DB candles + Redis price cache
          └── No candle close event needed — works anytime

SHARED PIPELINE (both paths converge here):
  strategy_runner evaluates strategy
      ├── Build MarketContext (price, VWAP, PDH/PDL, CPR, OI, VIX)
      ├── strategy.evaluate(ctx) → StrategySignal | None
      └── If signal generated:
          ├── [OPTION signals only] option_resolver enriches:
          │   ├── Select ATM/ITM strike (STRIKE_GAPS per index)
          │   ├── Select nearest expiry (weekly NIFTY/SENSEX, monthly others)
          │   ├── Look up Fyers symbol via symbol master
          │   ├── Fetch option premium (Redis cache → Fyers REST fallback)
          │   └── Compute SL/target on premium (not index price)
          ├── [FUTURE signals] pass through as-is (SL/target on futures price)
          ├── Save to signals table (with executable flag)
          ├── Broadcast via WebSocket (signal:new)
          ├── YOLO mode → auto_executor.execute()
          └── MANUAL/SEMI → Telegram alert, wait for user
```

### Strategy Configuration (strategy_configs table)
```
strategy_name | is_active | auto_mode | symbols              | symbol_map                          | parameters | risk_params
--------------+-----------+-----------+----------------------+-------------------------------------+------------+------------
vwap_pullback | true      | true      | ["NIFTY","BANKNIFTY"]| {"NIFTY":"NSE:NIFTY50-INDEX",...}   | {...}      | {...}
orb           | false     | false     | ["NIFTY"]            | {...}                               | {...}      | {...}
can_slim      | true      | true      | ["TCS","RELIANCE"]   | {"TCS":"NSE:TCS-EQ","RELIANCE":...} | {...}      | {...}

is_active   = strategy is available for evaluation (manual or auto)
auto_mode   = strategy runs automatically on every candle close for its configured symbols
symbols     = which symbols this strategy evaluates on (configurable via Settings page)
symbol_map  = maps short_name → fyers_symbol, populated at insertion time from symbol master search results. Used by backfill, provisioning, and WS subscription — no Fyers symbol reconstruction needed.

When symbols are added via Settings:
  PUT /api/v1/strategies/{name} → detects new symbols → background task:
    1. Fetch REST quote → Redis price cache
    2. Backfill previous day + today candles → PostgreSQL (MarketData1m)
    3. Subscribe on Fyers WebSocket → live ticks going forward

On startup:
  All strategy-configured symbols get REST quotes fetched → Redis
  + candle backfill → PostgreSQL
  + WebSocket subscription (alongside watchlist + default indices)
```

### 2b. OI Data Flow
```
APScheduler (every 3 minutes during market hours):
  oi_snapshot_task.fetch_oi_snapshots()
    ├── For each index (NIFTY, BANKNIFTY, FINNIFTY, SENSEX, MIDCPNIFTY):
    │   ├── Fyers REST: GET /option-chain (20 strikes around ATM)
    │   ├── Parse CE/PE OI per strike
    │   └── INSERT into oi_snapshots (ON CONFLICT DO NOTHING)
    │
    └── Strategy runner reads latest snapshot on each evaluation:
        strategy_runner._get_oi_analysis(symbol)
          → SELECT from oi_snapshots WHERE symbol AND max(timestamp)
          → analyze_option_chain() → OIAnalysis (PCR, max pain, sentiment)
          → MarketContext.oi_analysis
```

### 2c. Candle Backfill (Startup)
```
Symbols backfilled: FYERS_SYMBOL_MAP (5 indices, no VIX) + strategy_configs symbols
Stock symbols resolved to NSE:{SYMBOL}-EQ format.

Phase 1: Previous Trading Day (for PDH/PDL/PDC context)
  For each symbol: skip if DB already has candles, else fetch via Fyers SDK history()

Phase 2: Today's Elapsed Candles (for late-start scenarios)
  For each symbol: fetch 9:15–now via Fyers SDK history()
```

### 3. Trade Lifecycle
```
Signal ──> Trade Created (OPEN) ──> Position Created
                                    ├── Agent monitors (2s loop)
                                    ├── SL hit → auto-close (all modes)
                                    ├── Target hit:
                                    │   ├── YOLO → auto-book profit
                                    │   ├── SEMI → Telegram confirmation request
                                    │   └── MANUAL → Telegram alert only
                                    ├── Time exit → auto-close at 3:15 PM
                                    └── Drawdown → close all, halt if daily loss >= 5%
       ──> Trade Updated (CLOSED) ──> Position deleted
                                  ──> Daily summary updated
                                  ──> P&L broadcast via WebSocket
```

### 4. Agent Decision Flow
```
Every 2 seconds (agent_runner main loop):
  For each open position:
    1. Get current price from Redis (option premium via fyers_option_symbol, index fallback)
    2. Check SL: price <= stop_loss → AUTO CLOSE (no confirmation needed)
    3. Check Target: price >= target
       - YOLO: auto-book profit
       - SEMI: send Telegram confirmation, wait for approve/reject
       - MANUAL: send alert only
    4. Check Time: time >= 15:15 IST → AUTO CLOSE all positions
    5. Check Drawdown: daily_loss >= 5% → CLOSE ALL, HALT TRADING for the day
    6. Update unrealized P&L → broadcast via WebSocket (position:pnl)
```

### 5. Fyers Authentication Flow
```
Option A (Manual): User visits /api/v1/auth/fyers/login → Fyers OAuth → callback → token stored in Redis
Option B (Auto):   APScheduler job → fyers_auto_login.py → base64 credentials + TOTP → token stored in Redis
                   Runs on startup + periodic refresh
```

### 6. Symbol Master Flow
```
Startup (background task, non-blocking):
  1. Check Redis for cached symbol master (gzip JSON at "symbols:master")
  2. If fresh (< 24h old) → decompress + load into memory
  3. If stale/missing → download 4 CSVs from Fyers public endpoint:
     - https://public.fyers.in/sym_details/NSE_CM.csv  (~9K equities)
     - https://public.fyers.in/sym_details/NSE_FO.csv  (~96K F&O)
     - https://public.fyers.in/sym_details/BSE_CM.csv  (~12K equities)
     - https://public.fyers.in/sym_details/BSE_FO.csv  (~9K F&O)
  4. Parse CSVs → build in-memory index (by short name)
  5. Store gzip-compressed JSON in Redis

Daily refresh: APScheduler at 8:00 AM IST (before login at 8:55 AM)

Search: GET /api/v1/market/symbols/search?q=TCS
  → In-memory filter (no API call) → returns symbols with metadata
```

### 7. Watchlist Flow
```
Redis key: "watchlist:items" (hash: symbol → JSON metadata)

Startup:
  main.py loads watchlist symbols from Redis → passes as extra_symbols to
  fyers_ws_client.start() → subscribed on WebSocket alongside FYERS_SYMBOL_MAP

Frontend:
  Load: GET /api/v1/watchlist → fetch items → POST /prices/batch for prices
  Add:  Search symbol → select → POST /api/v1/watchlist + fetch price
        Backend also calls fyers_ws_client.subscribe_symbols() for live ticks
  Remove: DELETE /api/v1/watchlist/{symbol}
  Poll: Every 10s → POST /prices/batch for all watchlist symbols

Backend (agent can also add):
  POST /api/v1/watchlist {"symbol": "NSE:TCS-EQ", "display": "TCS LTD"}

Price fetch for custom symbols:
  POST /api/v1/market/prices/batch {"symbols": ["NSE:TCS-EQ", ...]}
  → Check Redis cache → fetch uncached from Fyers REST quotes() → cache + return

Note: Watchlist symbols get live WebSocket ticks (prices auto-update via FeedManager),
but the REST batch endpoint remains as fallback for symbols not yet subscribed.
```

## Key Design Decisions
- **Single process**: Agent runs as asyncio task within FastAPI (not a separate service)
- **Paper trading first**: All trades are simulated until explicit switch to live mode (`PAPER_TRADING=true`)
- **Redis for real-time**: Prices cached in Redis, strategy engine reads from Redis (not DB)
- **PostgreSQL for persistence**: All trades, signals, candles stored for future backtesting
- **WebSocket for UI**: Single `/ws` endpoint with event-based routing (price:update, signal:new, etc.)
- **No auth V1**: Single user, localhost only
- **Non-default ports**: PostgreSQL 5433, Redis 6380 (avoid conflicts with local instances)
- **Fyers SDK**: Uses `fyers-apiv3` package — WebSocket via threaded `FyersDataSocket` bridged to asyncio
- **Decoupled strategy evaluation**: FeedManager only produces candles. Strategy evaluation is triggered by auto_mode config (per strategy + per symbol) or manual API call. Both paths share the same MarketContext builder and signal pipeline.
- **Watchlist on WebSocket**: Watchlist symbols are subscribed on the Fyers WebSocket at startup and on add. Not just REST polling.
