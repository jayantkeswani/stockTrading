# StockTrading — Indian Options & Futures Trading System

Automated trading system for Indian stock market (NSE/BSE). Focuses on **buying** index options and stock futures with rules-based strategies and an AI agent layer.

## What it does

- Runs 5 trading strategies (see below) with configurable auto-mode or manual scanner
- AI agent monitors open positions 24/7: SL hits, profit booking, EOD exits, positional rolls
- Paper trading by default — no real money until explicitly switched
- Sends Telegram alerts for every signal, trade event, and daily summary
- Telegram `/shadow` command for on-demand shadow performance from your phone
- Live dashboard with TradingView charts, real-time P&L, agent feed

## Tech Stack

| Layer | Technology |
|---|---|
| Frontend | Next.js 15, TypeScript, Tailwind CSS v4, TradingView charts |
| Backend | Python 3.11, FastAPI (port 8080) |
| Database | PostgreSQL (port 5433) + Redis (port 6380) |
| Data feed | Fyers API (free) |
| Notifications | Telegram Bot API |

## Prerequisites

- Docker & Docker Compose
- Python 3.11+
- Node.js 20+
- Fyers account (free) — for live market data
- Telegram bot token + chat ID — for alerts and `/shadow` command

## Setup

```bash
# 1. Copy and fill environment file
cp .env.example .env
# Required: FYERS_APP_ID, FYERS_SECRET_KEY, FYERS_USERNAME, FYERS_PIN, FYERS_TOTP_SECRET
# Required for alerts: TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID

# 2. Start PostgreSQL + Redis
docker compose up -d

# 3. Backend
cd backend
python3.11 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
alembic upgrade head
cd ..

# 4. Frontend
cd frontend && npm install && cd ..

# 5. Run everything
make dev
```

Visit **http://localhost:3000** for the dashboard. API at **http://localhost:8080/api/v1/**.

## Commands

```bash
make dev        # Start infra + backend + frontend
make backend    # Backend only (:8080)
make frontend   # Frontend only (:3000)
make test       # Run pytest suite (786 tests)
make migrate    # Apply pending Alembic migrations
make stop       # Stop all services
```

## Strategies

| # | Name | Instruments | Status |
|---|---|---|---|
| 1 | ORB — Opening Range Breakout | Index options | Stub |
| 2 | VWAP Pullback + PDH/PDL + OI | Index options (NIFTY, BANKNIFTY, FINNIFTY, SENSEX, MIDCPNIFTY) | **Active** |
| 3 | Expiry Day Gamma Scalping | Index options | Stub |
| 4 | CAN SLIM Growth Breakout | Stock futures (positional) | **Active** |
| 5 | Intraday Stock Futures | Stock futures (intraday, AI-driven) | In development |

Strategy 2 is the primary live strategy. Strategy 5 uses a morning screener (8:30 AM), LLM briefing, and 4 sub-setups (ORB, VWAP Bounce, PDH/PDL, Gap Continuation).

## Agent Autonomy

Set via Settings page or `.env`:

| Level | Behaviour |
|---|---|
| MANUAL | Telegram alerts only — you execute trades manually |
| SEMI | Auto-closes on SL hit, asks for confirmation on profit booking |
| YOLO | Fully autonomous — auto-executes signals, books profits, closes SL |

## Ports

| Service | Port |
|---|---|
| Frontend | 3000 |
| Backend API | 8080 |
| PostgreSQL | 5433 |
| Redis | 6380 |

## Key scripts

```bash
# Backfill daily candles from Fyers (run once to seed market_data_daily)
python scripts/backfill_daily_candles.py

# Replay Strategy 5 signals from historical candles (offline test)
python scripts/replay_strategy5.py --date 2026-04-28

# Audit Strategy 2 data freshness (read-only)
python scripts/audit_vwap_data.py

# Audit Strategy 5 screener data freshness (read-only)
python scripts/audit_screener_data.py
```

## Docs

- `CLAUDE.md` — AI instructions and full project map (start here for code navigation)
- `ARCHITECTURE.md` — system design and data flow diagrams
- `docs/strategies/` — full spec for each strategy
- `backend/CLAUDE.md` — backend module map
- `frontend/CLAUDE.md` — frontend component map
