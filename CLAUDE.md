# StockTrading - Indian Options Trading System

## Project Overview
Automated options trading system for Indian stock market (NSE/BSE) focused on **buying** index options (NIFTY, BANKNIFTY, FINNIFTY, SENSEX, MIDCPNIFTY). Uses Strategy 2 (VWAP Pullback + Previous Day Context + OI Confirmation) as the primary active strategy.

## Architecture
- **Monorepo**: `backend/` (Python/FastAPI) + `frontend/` (Next.js/React/TypeScript)
- **Database**: PostgreSQL on port 5433 + Redis on port 6380 (non-default to avoid local conflicts)
- **Data Feed**: Fyers API (free) for market data; Zerodha/Kite for trade execution (future)
- **AI Agent**: Python asyncio background task with 3 autonomy levels (MANUAL / SEMI / YOLO)
- **Notifications**: Telegram Bot API for alerts and trade confirmations
- **Paper trading** by default — no real money until explicitly switched

## Key Conventions
- Backend: Python 3.11, virtualenv at `backend/.venv`, FastAPI, SQLAlchemy 2.0 (async), Pydantic v2, Alembic
- Frontend: Next.js 15 (App Router), TypeScript strict, Tailwind CSS v4 (dark theme only), Zustand, TradingView lightweight-charts
- All times in IST (Asia/Kolkata), stored as TIMESTAMPTZ in DB
- Market hours: 9:15 AM - 3:30 PM IST
- Backend runs on port **8080**, frontend on port **3000**
- Single user, no auth in V1

## Directory Map
```
stockTrading/
├── CLAUDE.md              # THIS FILE - project overview + AI instructions
├── ARCHITECTURE.md        # System design, data flow diagrams
├── README.md              # Setup and run instructions
├── Makefile               # Dev commands (make dev, make test, make migrate)
├── docker-compose.yml     # PostgreSQL (5433) + Redis (6380)
├── .env.example           # Environment template
├── .vscode/               # VS Code launch configs, tasks, settings
├── .claude/skills/        # Claude Code skill definitions (test-runner, review-code, etc.)
├── docs/
│   └── strategies/        # One MD per strategy with full trading rules
├── scripts/               # dev.sh, stop.sh, reset.sh
├── backend/               # Python FastAPI backend (see backend/CLAUDE.md)
│   ├── app/
│   │   ├── api/v1/        # REST endpoints (9 routers, incl. watchlist)
│   │   ├── websocket/     # WebSocket manager (single /ws endpoint)
│   │   ├── models/        # SQLAlchemy ORM models (8 tables)
│   │   ├── schemas/       # Pydantic request/response schemas
│   │   ├── services/      # Business logic (strategy_runner, option_resolver, candle_backfill)
│   │   ├── strategies/    # Strategy engine (base + 3 strategies)
│   │   ├── indicators/    # Technical indicators (VWAP, CPR, OI, candle patterns)
│   │   ├── data_feed/     # Fyers API (auth, REST, WebSocket, feed manager, symbol master)
│   │   ├── agent/         # AI trading agent (monitor, execute, notify)
│   │   ├── core/          # Config, database, Redis, constants, enums, utils
│   │   └── tasks/         # Scheduled tasks (Fyers auto-login, symbol master refresh)
│   ├── tests/             # pytest test suite
│   └── alembic/           # Database migrations
└── frontend/              # Next.js React frontend (see frontend/CLAUDE.md)
    └── src/
        ├── app/           # 6 pages (dashboard, trades, signals, settings, agent, chart)
        ├── components/    # React components by domain (13 components)
        ├── hooks/         # useWebSocket (auto-reconnect, event subscriptions)
        ├── lib/           # API client, types, formatters, constants
        └── store/         # Zustand store (prices, positions, signals, risk, agent)
```

## Trading Parameters
- Capital: 10 Lakhs INR (Rs 10,00,000)
- Max daily drawdown: 5% (Rs 50,000)
- Risk per trade: 1.5-2% (Rs 15,000-20,000)
- Max trades/day: 2-3
- Lot sizes: NIFTY=75, BANKNIFTY=30, FINNIFTY=25, SENSEX=10, MIDCPNIFTY=50
- Strike selection: ATM or 1-strike ITM (Delta 0.45-0.60), resolved by `option_resolver.py`
- Strike gaps: NIFTY=50, BANKNIFTY=100, FINNIFTY=50, SENSEX=100, MIDCPNIFTY=25
- Preferred premium range: Rs 150-400
- SL/target computed on option premium (not index price), 30-35% SL, 1:1.5 R:R
- Expiry: NIFTY weekly Tuesday, SENSEX weekly Thursday, others monthly only (post-SEBI Nov 2024)

## Strategies
1. **ORB (Opening Range Breakout)** - STUB - `backend/app/strategies/strategy_1_orb.py`
2. **VWAP Pullback + PDH/PDL + OI** - PRIMARY/ACTIVE - `backend/app/strategies/strategy_2_vwap_pullback.py`
3. **Expiry Day Gamma Scalping** - STUB - `backend/app/strategies/strategy_3_gamma_scalping.py`

## Agent Autonomy Levels
- **MANUAL**: Alerts only via Telegram, user executes manually
- **SEMI**: Auto-closes on SL hit, requests confirmation for profit booking
- **YOLO**: Fully autonomous — auto-executes signals, auto-books profits, auto-closes on SL

## Commands
```bash
# One-command start (infrastructure + backend + frontend)
make dev

# Or individually:
docker compose up -d          # Start PostgreSQL + Redis
make backend                  # Backend on :8080
make frontend                 # Frontend on :3000
make test                     # Run pytest suite
make migrate                  # Run Alembic migrations
make migration msg="desc"     # Generate new migration

# First-time setup
cd backend && python3.11 -m venv .venv && source .venv/bin/activate && pip install -e ".[dev]"
cd frontend && npm install
cp .env.example .env          # Then fill in Fyers API keys
```

## Test Coverage
Tests live in `backend/tests/`. Currently covered:
- `test_core/` - IST timezone utils, market hour checks
- `test_indicators/` - VWAP, CPR, previous day, OI, VIX, candle patterns (comprehensive)
- `test_strategies/` - VWAP Pullback signal generation, entry/exit, confidence scoring, instrument_type
- `test_services/` - Option resolver: strike selection (ATM/ITM), expiry selection (weekly/monthly), SL/target on premium, fallback behavior

Not yet covered (stubs only):
- `test_api/` - API endpoint tests
- `test_agent/` - Agent runner, trade monitor, auto-executor tests

## AI Documentation Protocol (MANDATORY)
This is an AI-first project. Documentation ships WITH every code change — not as an afterthought.

Every major directory has a CLAUDE.md with its purpose, files, conventions, and how-to guides. A new AI session should be able to understand the entire project from these files alone.

**On EVERY code change, you MUST:**
1. Update the relevant CLAUDE.md (root, backend/, frontend/) if the change adds/removes/renames files, changes conventions, or adds new patterns
2. Update ARCHITECTURE.md if the change affects data flow, system design, or ports
3. Update docs/strategies/*.md if strategy logic changes
4. Update project memories if project-level decisions change (ports, versions, indices, conventions)
5. Run `/test-runner` to validate tests pass
6. Run `/review-code` to check quality and consistency
7. Run `/update-docs` as a final verification that docs match code

**Documentation is not optional.** If you add a new file and don't update CLAUDE.md, the next session won't know it exists.

## How-To Guides

### Add a New Strategy
1. Create `backend/app/strategies/strategy_N_name.py` extending `BaseStrategy`
2. Implement `evaluate()`, `should_exit()`, `get_position_size()`
3. Add strategy name to `StrategyName` enum in `backend/app/core/enums.py`
4. Register in `backend/app/strategies/registry.py`
5. Add strategy doc in `docs/strategies/strategy-N-name.md`
6. Seed a `strategy_configs` row (see existing configs)
7. Add label in `frontend/src/lib/constants.ts` → `STRATEGY_LABELS`

### Add a New API Endpoint
1. Create or edit router file in `backend/app/api/v1/`
2. Add Pydantic schemas in `backend/app/schemas/`
3. Include router in `backend/app/api/router.py`
4. Add corresponding API function in `frontend/src/lib/api.ts`
5. Add TypeScript types in `frontend/src/lib/types.ts`

### Add a New Frontend Page
1. Create `frontend/src/app/{page-name}/page.tsx` with `'use client'` directive
2. Add nav link in `frontend/src/components/layout/Sidebar.tsx`
3. Create domain components in `frontend/src/components/{domain}/`
4. Add store slice if needed in `frontend/src/store/index.ts`

### Add a New Technical Indicator
1. Create pure function file in `backend/app/indicators/`
2. Add to `MarketContext` in `backend/app/strategies/base.py` if strategies need it
3. Wire into `strategy_runner.py` to populate context
4. Write tests in `backend/tests/test_indicators/`
