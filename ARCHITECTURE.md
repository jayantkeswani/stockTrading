# Architecture

## System Overview

```
┌─────────────┐     ┌──────────────────────────────────────────────────┐
│  Fyers API  │────>│  Backend (Python 3.11 / FastAPI on :8080)        │
│  (Market    │ WS  │                                                  │
│   Data)     │     │  ┌──────────────┐  ┌─────────────────────────┐   │
└─────────────┘     │  │ Feed Manager │─>│ Redis (:6380)            │   │
                    │  │ (fyers_ws +  │  │ Price cache + pub/sub    │   │
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
NIFTY (75), BANKNIFTY (30), FINNIFTY (25), SENSEX (10), MIDCPNIFTY (75) — lot sizes in parentheses.

## Data Flow

### 1. Price Data Pipeline
```
Fyers WebSocket ──> fyers_ws_client.py (FyersDataSocket, threaded → asyncio bridge)
                ──> feed_manager.py
                    ├── Redis PUBLISH "price:{symbol}" (real-time cache)
                    ├── Aggregate into 1m/5m candles
                    ├── Store completed candles in PostgreSQL (market_data_1m)
                    └── WebSocket broadcast to frontend (price:update)

Frontend also polls GET /api/v1/market/prices every 10s as fallback.
```

### 2. Strategy Signal Flow
```
5m candle close event ──> strategy_runner.on_candle_close()
                          ├── Build MarketContext (price, VWAP, PDH/PDL, CPR, OI, VIX)
                          ├── Evaluate all active strategies
                          └── If signal generated:
                              ├── Save to signals table (with executable flag)
                              ├── Broadcast via WebSocket (signal:new)
                              ├── YOLO mode → auto_executor.execute()
                              └── MANUAL/SEMI → Telegram alert, wait for user
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
    1. Get current price from Redis
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

## Key Design Decisions
- **Single process**: Agent runs as asyncio task within FastAPI (not a separate service)
- **Paper trading first**: All trades are simulated until explicit switch to live mode (`PAPER_TRADING=true`)
- **Redis for real-time**: Prices cached in Redis, strategy engine reads from Redis (not DB)
- **PostgreSQL for persistence**: All trades, signals, candles stored for future backtesting
- **WebSocket for UI**: Single `/ws` endpoint with event-based routing (price:update, signal:new, etc.)
- **No auth V1**: Single user, localhost only
- **Non-default ports**: PostgreSQL 5433, Redis 6380 (avoid conflicts with local instances)
- **Fyers SDK**: Uses `fyers-apiv3` package — WebSocket via threaded `FyersDataSocket` bridged to asyncio
