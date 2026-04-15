# StockTrading - Indian Options Trading System

Automated options trading system for Indian stock market (NSE/BSE). Focuses on **buying** index options (NIFTY, BANKNIFTY, FINNIFTY, SENSEX, MIDCPNIFTY) with systematic, rules-based strategies.

## Features

- **Strategy Engine**: VWAP Pullback + Previous Day Context + OI Confirmation (primary), with ORB and Gamma Scalping stubs
- **Live Dashboard**: Dark-mode hedge-fund style UI with TradingView charts, real-time P&L, active positions
- **AI Agent**: 3 autonomy levels — MANUAL (alerts only), SEMI (auto-close SL, confirm profits), YOLO (fully autonomous)
- **Notifications**: Telegram alerts for trade events and confirmations
- **Paper Trading**: Simulated execution by default — no real money until explicitly switched
- **Risk Management**: 5% daily drawdown limit, position sizing based on VIX, max 3 trades/day

## Tech Stack

| Component | Technology |
|-----------|-----------|
| Frontend | Next.js 15, React, TypeScript, Tailwind CSS v4, TradingView charts |
| Backend | Python 3.11, FastAPI, SQLAlchemy 2.0, Alembic |
| Database | PostgreSQL (port 5433) + Redis (port 6380) |
| Data Feed | Fyers API (free) |
| Broker | Zerodha/Kite (future) |
| Notifications | Telegram Bot API |

## Quick Start

### Prerequisites
- Docker & Docker Compose
- Python 3.11+
- Node.js 20+

### Setup

```bash
# 1. Clone and enter
cd stockTrading

# 2. Copy environment file
cp .env.example .env
# Edit .env with your Fyers API keys

# 3. Start PostgreSQL + Redis
docker compose up -d

# 4. Backend setup
cd backend
python3.11 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
alembic upgrade head

# 5. Frontend setup (in a new terminal)
cd frontend
npm install

# 6. Run everything
cd .. && make dev
```

### Individual Services

```bash
make dev          # Start everything (infra + backend + frontend)
make backend      # Backend only on :8080
make frontend     # Frontend only on :3000
make test         # Run backend tests
make migrate      # Run pending migrations
make stop         # Stop all services
```

Visit http://localhost:3000 for the dashboard. Backend API at http://localhost:8080/api/v1/.

## Project Structure

```
stockTrading/
├── CLAUDE.md          # AI instructions (start here)
├── ARCHITECTURE.md    # System design & data flows
├── Makefile           # Dev commands
├── docker-compose.yml # PostgreSQL + Redis
├── backend/           # Python FastAPI (see backend/CLAUDE.md)
│   ├── app/
│   │   ├── strategies/    # Trading strategies
│   │   ├── indicators/    # VWAP, CPR, OI, candle patterns
│   │   ├── agent/         # AI trading agent
│   │   ├── data_feed/     # Fyers API integration
│   │   ├── api/v1/        # REST endpoints
│   │   └── websocket/     # Real-time data
│   └── tests/
├── frontend/          # Next.js React (see frontend/CLAUDE.md)
│   └── src/
│       ├── app/           # Pages (dashboard, trades, signals, settings, agent, chart)
│       ├── components/    # UI components by domain
│       ├── hooks/         # WebSocket hook
│       ├── lib/           # API client, types, formatters
│       └── store/         # Zustand state
└── docs/
    └── strategies/    # Strategy specifications
```
