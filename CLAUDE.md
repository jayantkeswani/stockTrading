# StockTrading - Indian Options Trading System

## Subagent Model Policy
Always spawn subagents with `model: "sonnet"` to reduce costs. Only use `model: "opus"` for subagents that require deep reasoning (complex architecture decisions, subtle multi-file bug diagnosis).

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
├── docker-compose.yml     # PostgreSQL (5433) + Redis (6380) — LOCAL dev only
├── docker-compose.prod.yml # PRODUCTION: all 5 services (backend, frontend, postgres, redis, nginx)
├── .dockerignore          # Excludes .git, .venv, node_modules, tests, docs from Docker build context
├── .env.example           # Environment template
├── .envrc                 # direnv: GCP project isolation (stock-trading config)
├── .github/workflows/     # CI/CD: deploy.yml (push-to-master auto-deploy to GCP VM)
├── .vscode/               # VS Code launch configs, tasks, settings
├── .claude/skills/        # Claude Code skill definitions (test-runner, review-code, build-strategy, etc.)
├── infrastructure/        # GCP deployment (Terraform, Docker, scripts)
│   ├── terraform/         # main.tf, vm.tf, network.tf, apis.tf, gemini.tf, variables.tf, outputs.tf
│   ├── bootstrap/         # bootstrap.sh (one-time: TF state bucket + API enable)
│   ├── docker/            # Dockerfile.backend, Dockerfile.frontend, nginx.conf
│   └── scripts/           # vm-startup.sh (first-boot: Docker install, deploy user, IST)
├── docs/
│   ├── strategies/        # One MD per strategy with full trading rules. Includes arjun-liquide-study.md — a full reverse-engineering study (signal parser, independent backtest of 630 trades, feature-importance analysis, implied strategy + edge filters). Not yet a registered strategy; precursor to strategy_5_breakout_momentum.
│   ├── backtest/          # Backtest harness docs (harness.md, option-data.md)
│   └── ai/                # AI agent docs (signal-confidence-agent.md)
├── scripts/               # dev.sh, stop.sh, reset.sh, backfill_for_backtest.py, backtest.py, replay_strategy5.py, backtest_strategy5.py (S5 signal exit simulator: queries live signals, walks 1m candles with trailing SL, reports P&L; reads trailing SL params from DB via `get_strategy_params` to match live agent; `--sl-mode close` (default) uses candle close for trailing SL trigger matching live tick-poll behavior, `--sl-mode wick` uses candle high/low (legacy); supports confidence threshold, date range, setup/symbol filters, lots override, sweep mode), backfill_daily_candles.py (one-time seed of market_data_daily from Fyers), audit_screener_data.py (read-only freshness check for Strategy 5 morning screener data), audit_vwap_data.py (read-only freshness check for Strategy 2 VWAP Pullback data: 10 checks — config, prev-day 1m candles, today 1m candles, CE/PE OI snapshots, index futures candles, India VIX cache, global cues, price cache, symbol master, pending signals)
│   └── telegram/          # MTProto client (Telethon) + signal parser + Fyers probe + independent verifier + setup analyzer. list_dialogs.py (discover chat IDs), fetch_history.py (pull signal-channel history to JSON), parse_signals.py (classifies messages into ENTRY / EXIT_FULL (Book Profit) / EXIT_FORCED (cost-to-cost or "at current price" — deliberately separate from EXIT_FULL so channel-claimed wins aren't inflated) / EXIT_PARTIAL / WATCHLIST / HOLD_OVERNIGHT / REPORT / UPDATE / CANCEL / OTHER; stdlib-only), probe_fyers_history.py (one-shot diagnostic: Fyers free plan serves 1m history only while contract is actively listed; expired contracts return s="error"), verify_signals.py (hybrid backtest: accurate mode for currently-live contracts via real option 1m bars, delta-approx for expired contracts via underlying spot 1m × moneyness-derived delta; channel's stated T1-then-C2C rule; NSE F&O lot sizing from the master CSV; outputs per-trade + aggregate hit rate / expectancy / profit factor in JSON + stdout), analyze_setups.py (reverse-engineers the implied strategy: fetches underlying 1m bars up to each entry minute, reuses backend.app.indicators (VWAP, CPR, previous_day, candle_patterns) to compute features at entry time, aggregates CE vs PE distributional stats — time-of-day buckets, VWAP/PDH/PDL/CPR position, candle patterns, volume ratio, moneyness — and prints an evidence-backed hypothesis; also dumps feature CSV + JSON), analyze_edge.py (feature-importance pass: joins setups_*.json with verification_*.json on msg_id, bucketizes each feature (bool/categorical/numeric-quartile), ranks by win-rate lift with min-bucket-size guard, then stress-tests top-3 filter combinations per direction — finds "filters that beat Arjun at his own strategy"; multiple win definitions via --win-def). Fetched JSON, symbol master cache, candle cache all in scripts/telegram/data/ (gitignored). Session files + data/ gitignored. Uses TELEGRAM_API_ID/API_HASH/PHONE/SESSION_NAME from .env
├── backend/               # Python FastAPI backend (see backend/CLAUDE.md)
│   ├── app/
│   │   ├── api/v1/        # REST endpoints (14 routers, incl. watchlist, strategies, tasks, research, options)
│   │   ├── websocket/     # WebSocket manager (single /ws endpoint)
│   │   ├── models/        # SQLAlchemy ORM models (16 tables incl. market_data_daily, global_market_snapshots, trading_config; signals has ai_* columns; signal_history archives Case-2 dedup snapshots)
│   │   ├── schemas/       # Pydantic request/response schemas
│   │   ├── services/      # Business logic (strategy_runner, option_resolver, futures_resolver, candle_backfill, strategy_params, morning_screener, agent_log)
│   │   ├── strategies/    # Strategy engine (base + 4 strategies incl. CAN SLIM, registry)
│   │   ├── indicators/    # Technical indicators (VWAP, CPR, OI, candle patterns, RS, volume, market levels, global_market, intraday_bias, confidence, ATR, gap_analysis, stock_trend)
│   │   ├── data_feed/     # Fyers API (auth, REST via API_URL/DATA_URL, WebSocket, feed manager, symbol master)
│   │   ├── research/      # AI research agent system (orchestrator, 6 sub-agents, LLM client, data gatherer)
│   │   ├── agent/         # AI trading agent (monitor, execute, notify, shadow_executor, telegram_bot, telegram_commands)
│   │   ├── backtest/      # Backtest module (context_builder, harness, exit_simulator, option_data_fetcher, strike_selector, report)
│   │   ├── core/          # Config, database, Redis, constants (FYERS_SYMBOL_MAP, NSE_HOLIDAYS), enums, utils, task_registry
│   │   └── tasks/         # Scheduled tasks (Fyers auto-login, symbol master refresh, global_market every 15m, Strategy 5 morning workflow, NSE bhav copy daily, F&O ban list 7:00 AM)
│   ├── tests/             # pytest test suite (829 tests, incl. signal_history archiving)
│   └── alembic/           # Database migrations
└── frontend/              # Next.js React frontend (see frontend/CLAUDE.md)
    └── src/
        ├── app/           # 9 pages (dashboard, trades, signals, research, settings, agent, chart, intraday-futures, options)
        ├── components/    # React components by domain (38 components incl. 8 intraday-futures/* (+ PermanentWatchlist), options/AgentLog, shared/SymbolSearchInput, research/ResearchSearch, ResearchProgress, ResearchReport)
        ├── hooks/         # useWebSocket (auto-reconnect, event subscriptions, research events)
        ├── lib/           # API client, types, formatters, constants
        └── store/         # Zustand store (prices, positions, signals, scanLogs, risk, agent, research)
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
4. **CAN SLIM Growth Breakout** - ACTIVE - `backend/app/strategies/strategy_4_canslim.py` — Stock futures, positional (multi-day), fundamental screening + chart pattern breakout
5. **Intraday Stock Futures** - IN DEVELOPMENT - `backend/app/strategies/strategy_5_intraday_futures.py` — AI-agent-driven intraday stock futures, ORB breakout, dynamic screener, phase state machine. `_compute_confidence()` stores 9-key `confidence_factors` dict in signal indicators JSONB. Full spec: `docs/strategies/strategy-5-intraday-futures.md`, phase 1 reference: `docs/strategies/strategy-5-phase1-reference.md`

### Strategy Execution Modes
- **Auto mode**: Strategy evaluates automatically on every 1m candle close for its configured symbols. Controlled by `auto_mode` flag in `strategy_configs` table.
- **Manual mode**: User triggers evaluation via the Scanner header bar on the dashboard. Calls `POST /api/v1/strategies/evaluate/batch` which runs the strategy across its configured symbols on demand.
- Strategy configuration (active/auto_mode/symbols) is managed via Settings page → Strategies section.

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

# Strategy 5 replay (offline signal generation test from historical candles)
cd backend && source .venv/bin/activate
python scripts/replay_strategy5.py --date 2026-04-28              # all watchlist symbols
python scripts/replay_strategy5.py --date 2026-04-28 --symbols VEDL,SUNPHARMA  # subset

# One-time morning screener data freshness audit (read-only, no side effects)
python scripts/audit_screener_data.py

# One-time Strategy 2 (VWAP Pullback) data freshness audit (read-only, no side effects)
python scripts/audit_vwap_data.py

# One-time backfill of market_data_daily from Fyers (run once to seed historical daily candles)
# After this, nse_bhav_copy_task keeps the table current daily at 7:30 AM
python scripts/backfill_daily_candles.py              # last 60 days, all F&O stocks
python scripts/backfill_daily_candles.py --days 90    # longer lookback
python scripts/backfill_daily_candles.py --symbols TCS,RELIANCE  # subset

# Strategy 5 signal backtest (replay exits against historical 1m candles)
python scripts/backtest_strategy5.py --confidence 60 --start 2026-05-05
python scripts/backtest_strategy5.py --confidence 70 --start 2026-05-01 --end 2026-05-05
python scripts/backtest_strategy5.py --confidence 60 --start 2026-05-05 --setup ORB
python scripts/backtest_strategy5.py --confidence 60 --start 2026-05-05 --symbols VEDL,TCS
python scripts/backtest_strategy5.py --confidence 70 --start 2026-05-05 --sl-mode wick  # legacy wick-based trailing SL
python scripts/backtest_strategy5.py --sweep --start 2026-05-01 --end 2026-05-05  # threshold sweep
python scripts/backtest_strategy5.py --sweep --start 2026-05-01 --end 2026-05-05 --lots 1  # normalize to 1 lot
```

## Deployment (GCP)

Single VM deployment on Google Cloud Platform (asia-south1, Mumbai). Terraform manages all infrastructure. Full details in `docs/deployment-architecture.md`.

### GCP Setup
- **Live URL**: http://8.231.84.44 (HTTP only, no HTTPS — single user, paper trading)
- **Project**: `stock-trading-prod` under org `kakwani-khayti-org`
- **Account**: `kakwani.khayti@gmail.com` (separate from penguin-bean project)
- **gcloud config**: `stock-trading` (isolated via `direnv` + `.envrc`)
- **ADC credentials**: `~/.gcp/stock-trading-adc.json`
- **SSH key**: `~/.ssh/st-deploy` (extracted from Terraform state; `ssh -i ~/.ssh/st-deploy deploy@8.231.84.44`)
- **Free trial**: ₹28,365 credits, expires Aug 17 2026

### Infrastructure Commands
```bash
# One-time bootstrap (enables APIs, creates TF state bucket)
./infrastructure/bootstrap/bootstrap.sh

# Terraform
make infra-plan                # Preview changes
make infra-up                  # Create/update infrastructure
make infra-down                # DESTROY everything (one command)

# After terraform apply, retrieve sensitive outputs:
cd infrastructure/terraform
terraform output -raw vm_external_ip      # → add as GCP_VM_IP in GitHub Secrets
terraform output -raw deploy_private_key  # → add as SSH_PRIVATE_KEY in GitHub Secrets
terraform output -raw gemini_api_key      # → add as GOOGLE_API_KEY in GitHub Secrets

# Production management
make ssh                       # SSH into VM
make prod-up                   # Start containers on VM
make prod-down                 # Stop containers on VM
make prod-logs                 # Tail logs on VM

# Database migration (local → production)
make db-export                 # Dump local DB to dump.sql
make db-import                 # Upload and import dump.sql to VM
```

### CI/CD

> **WARNING: Pushing to `master` triggers an automatic production deploy.** Every push rebuilds Docker images and deploys to the live VM at http://8.231.84.44. Do NOT push unless you are certain the changes are correct. There is no staging environment.

Push to `master` → GitHub Actions two-job pipeline: (1) build Docker images on runner + push to ghcr.io, (2) SSH into VM + pull images + deploy. Images cached via GitHub Actions cache (`type=gha`). Workflow: `.github/workflows/deploy.yml`. Uses ~2-4 min per deploy (500 min/month free for private repos).

### Secrets (GitHub Repo Secrets)
All secrets stored in GitHub (Settings → Secrets), written to `.env` on VM during each deploy. Never in GCP Secret Manager or the codebase.

### Production Container Health
- **Backend**: `curl -sf http://localhost:8080/api/v1/tasks` healthcheck with 60s start_period, 10s interval
- **PostgreSQL**: `pg_isready` healthcheck
- **Redis**: `redis-cli ping` healthcheck
- **Nginx**: DNS re-resolution via `resolver 127.0.0.11 valid=5s` + variable-based `proxy_pass` — picks up new container IPs after deploys without manual `nginx -s reload`

### Directory Map (Infrastructure)
```
infrastructure/
├── terraform/          # All .tf files (VM, firewall, IP, APIs, Gemini key)
├── bootstrap/          # bootstrap.sh (one-time: state bucket + API enable)
├── docker/             # Dockerfile.backend, Dockerfile.frontend, nginx.conf
└── scripts/            # vm-startup.sh (first-boot: Docker, deploy user, IST)
.envrc                  # direnv: GCP config isolation (committed, no secrets)
.github/workflows/      # deploy.yml (push-to-master auto-deploy)
docker-compose.prod.yml # Production: all 5 services in containers
.dockerignore           # Excludes .git, .venv, node_modules, tests, docs from Docker build context
```

## Known Cleanup Tasks

Deferred work that is safe to do but not urgent. Each entry has a **why it's deferred** and **what to run**.

### 1. Purge stale daily-bar rows from `market_data_1m`
- **What**: ~9,600 rows stored at midnight UTC (hour=0, minute=0) — the old daily candle hack that predates `market_data_daily`. Nothing reads them anymore.
- **Why deferred**: Soak period — let `market_data_daily` run for a week to confirm it's being populated correctly before destroying the only fallback evidence.
- **When ready** (after ~2026-05-08): run directly against the DB:
  ```sql
  DELETE FROM market_data_1m
  WHERE EXTRACT(HOUR FROM timestamp AT TIME ZONE 'UTC') = 0
    AND EXTRACT(MINUTE FROM timestamp AT TIME ZONE 'UTC') = 0;
  ```

---

## Test Coverage
Tests live in `backend/tests/`. 829 tests, all passing. Currently covered:
- `test_core/` - IST timezone utils, market hour checks
- `test_indicators/` - VWAP, CPR, previous day, OI, VIX, candle patterns, relative strength (raw score + percentile ranking), volume analysis, market levels (swing detection, index SL/target selection), ADR (computation, threshold), RVOL (profile building, computation, serialization), ATR (computation, Wilder's smoothing), gap analysis (detection, continuation, edge cases), stock trend (6 factors individually, composite direction/strength classification, graceful degradation with <10 candles, V-reversal, flat market)
- `test_strategies/` - VWAP Pullback signal generation, entry/exit, confidence scoring, instrument_type (uses `strategy_params` in MarketContext); CAN SLIM scoring, base pattern detection, strategy evaluate/exit/sizing; Intraday Futures phase machine, ORB breakout detection (5-min candle close confirmation, ORB range min/max validation), 4 sub-setups (ORB/VWAP Bounce/PDH-PDL/Gap Continuation), caution zone confirmation, multi-factor confidence (9 factors incl. stock trend alignment + direction-aware Nifty bias + 4-way OI classification), full position sizing (RVOL/confidence/screener/VIX/briefing/trend), filters (ADR/RVOL/VWAP/price/volume/Nifty bias/stock trend direction), stock trend filter (STRONG opposing blocks, MODERATE allows with risk_warning, NEUTRAL passes), cross-position checks, `get_symbols()` dynamic Redis
- `test_services/` - Option resolver (strike/expiry/SL/target, uses `strategy_params` in MarketContext), futures resolver (expiry calculation), strategy runner (auto filter, manual eval, strategy filter, signal dedup — Case-2 archives to `signal_history` before update + `session.add` assertion, Case-2 also fires shadow_execute regression test, `_is_dedup_skip` AI-gate pre-check, `_check_global_risk_limits` + `get_strategy_params` patches), FeedManager decoupling (incl. index futures volume symbol forwarding), OI snapshot parsing, candle backfill symbol resolution, morning screener (quant scoring, news integration, LLM enrichment, briefing, OI scoring, delivery % scoring), briefing per-setup win rates (4 tests: Trade→Signal join for setup_type extraction, ORB fallback, empty trades, win rate accuracy), global cues mid-day shift (7 tests: crude/VIX shift detection, threshold filtering, debounce, simultaneous shifts, missing morning/current cues), pre-open reassessment (22 tests: gap-adjusted bias override/nudge/no-change, gap alignment bonus computation/cap/neutral/misaligned, integration: watchlist bias override, VIX update in global cues, score bonus, re-sort, skip on missing watchlist, pdc=None skips without crash — covers permanent-watchlist stocks injected with pdc=None). **`test_services/conftest.py`** — `autouse` fixture that clears all StrategyRunner per-candle in-memory caches (`_s5_shift_last_checked`, `_s5_session_cache`, `_s5_oi_cache`, `_s5_counts_cache`, `_oi_analysis_cache`, `_daily_candles_cache`, `_canslim_symbol_cache`) before each test to prevent singleton state leaking between tests.
- `test_tasks/` - NSE bhav copy (20 tests: CSV parsing with new NSE format, URL construction, Redis storage, retry logic, date handling, gap-fill); stock futures OI (8 tests: FUT row persistence, OI change calculation, scheduling, symbol resolution); S5 intraday watchlist OI (5 tests: market-closed skip, no-token skip, no-watchlist skip, happy-path persist with FUT/strike_price=0, all-resolutions-fail skip)
- `test_api/` - Strategy endpoints (evaluate, batch evaluate, auto-mode toggle)
- `test_agent/` - Shadow executor (8 tests: PENDING→trade/position creation, no dedup, non-pending skip, missing signal, price fallback, confidence gate skip low, confidence gate fire at exact threshold); Trade monitor (20 tests: SL hit, target hit YOLO/SEMI/dedup, time exit intraday/positional, trailing SL positional + intraday breakeven/progressive/HWM/SL-never-moves-down, expiry roll, shadow positions, no price, no exit)

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

## graphify
This project has a graphify knowledge graph at graphify-out/.

Rules:
- Before answering architecture or codebase questions, read graphify-out/GRAPH_REPORT.md for god nodes and community structure
- If graphify-out/wiki/index.md exists, navigate it instead of reading raw files
- For cross-module "how does X relate to Y" questions, prefer `graphify query "<question>"`, `graphify path "<A>" "<B>"`, or `graphify explain "<concept>"` over grep — these traverse the graph's EXTRACTED + INFERRED edges instead of scanning files
- After modifying code files in this session, run `graphify update .` to keep the graph current (AST-only, no API cost)
