# StockTrading - Indian Options & Futures Trading System

> **Detailed docs:** [`backend/CLAUDE.md`](backend/CLAUDE.md) (API, services, strategies) | [`frontend/CLAUDE.md`](frontend/CLAUDE.md) (pages, components, stores). Read these only when working on the respective layer.

## Git Worktree Policy

**Before making any code changes, ask the user whether other Claude Code sessions are active on this repo.** If yes, create a worktree under /Users/jaykeswani/projects/stockTrading/.claude/worktrees from the current branch and work inside it. Merge results back when done. This does NOT apply to subagents spawned within a single session.

## Subagent Model Policy

Always spawn subagents with `model: "sonnet"` to reduce costs. Only use `model: "opus"` for subagents that require deep reasoning (complex architecture decisions, subtle multi-file bug diagnosis).

## Project Overview

General-purpose strategy scanner, executor, and backtester for the Indian stock market (NSE/BSE). Currently focused on buying index options (NIFTY, BANKNIFTY, FINNIFTY, SENSEX, MIDCPNIFTY) and stock futures. Uses Strategy 2 (VWAP Pullback) as the primary active strategy. Architecture is strategy-agnostic — each strategy is a plug-in to shared scanning, execution, and backtesting infrastructure. Paper trading by default.

## Coding Guidelines

### Documentation Updates (MANDATORY)

This is an AI-first project. Documentation ships WITH every code change — not as an afterthought. A new AI session should understand the entire project from CLAUDE.md files alone.

**On EVERY code change:**

1. Update the relevant CLAUDE.md (root, backend/, frontend/) if the change adds/removes/renames files, changes conventions, or adds new patterns
2. Update ARCHITECTURE.md if the change affects data flow, system design, or ports
3. Update docs/strategies/*.md if strategy logic changes
4. Update docs/*md if any code changed that has reference over there
5. Run tests to validate tests pass

**Function registry format** — every public function in backend/frontend CLAUDE.md uses this format:
`- \`functionName(params) — one-liner. Used by: caller1.py, caller2.py`

**When updating docs:**

- Edit/restructure in place — never append to the bottom
- Check for stale references (counts, file names, function names) across all CLAUDE.md + strategy docs
- Every new file needs a section in the appropriate CLAUDE.md
- Every new/changed function needs its registry entry updated
- Every new/changed API endpoint needs the endpoint table updated
- When a function's callers change, update its `Used by:` list
- When you read `backend/CLAUDE.md` or `frontend/CLAUDE.md` and notice function entries missing `Used by:`, backfill them by grepping for callers — even if those functions aren't part of your current task
- Prefer editing existing sections over adding new ones — restructure if needed
- Documentation must describe the current state of the system — never write "previously X, now Y", "replaced X with Y", or "deprecated in favor of Z". Just describe what IS

### Code Style

- Every public function should have a docstring (one-liner minimum). Add param/return/edge-case detail for complex functions. CLAUDE.md registries are the index — docstrings in code are the source of truth.

### Testing Rules

- **NEVER change code solely to make a test pass** — if the test fails, either the test is wrong (fix the test) or there's a real bug (fix the bug)
- Modify existing tests when the tested behavior has changed — don't always add new ones
- Mock external dependencies when needed for isolation
- Run research integration tests only when changing research module files

### Operational Rules

- **Market hours (9:15-15:30 IST)**: Do NOT restart the backend, push to master, create releases, or restart production containers
- **Production DB is read-only** — cannot insert or modify data. Use local DB for verification
- **Port 8080 is Docker-internal** — from VM host, use `http://localhost/` (nginx on port 80 proxies to backend)
- **Production VM logs are UTC** — add 5:30 for IST. All `docker logs st-backend` timestamps are UTC
- **Local testing is always safe** — local DB (port 5433) and Redis (port 6380) are separate from production, even during market hours
- **Ground changes in code** — when changing core modules (`services/`, `agent/`, `data_feed/`, `strategies/`), read the actual source first. Don't rely on docs or memory alone
- **Fix root causes** — don't add fallbacks or workarounds to mask bugs
- **Verify fixes end-to-end** — create test data in the local DB, run the changed code path against it, and confirm data flows through without errors. Use the browser to verify UI changes. Safe during market hours (local infra is isolated)
- **Always check current time** (`date`) before making time-sensitive decisions (market hours, deployments)

## Architecture

- **Monorepo**: `backend/` (Python/FastAPI) + `frontend/` (Next.js/React/TypeScript)
- **Database**: PostgreSQL on port 5433 + Redis on port 6380 (non-default to avoid local conflicts)
- **Data Feed**: Fyers API (free) for market data; Zerodha/Kite for trade execution (future). `MARKET_MODE=simulated` swaps to Market Simulator (port 8787) for offline testing
- **Market Simulator**: Standalone service at `../marketSimulator/` — replays synthetic ticks, REST quotes, historical candles, option chains, and symbol master CSVs. Enables full-stack testing outside market hours
- **LLM**: Gemini via `google-genai` SDK — Vertex AI in production (ADC via GCE SA), AI Studio locally (API key)
- **AI Agent**: Python asyncio background task with 3 autonomy levels (MANUAL / SEMI / YOLO)
- **Notifications**: Telegram Bot API for alerts and trade confirmations

See `ARCHITECTURE.md` for full system design and data flow diagrams.

## Key Conventions

- Backend: Python 3.11, virtualenv at `backend/.venv`, FastAPI, SQLAlchemy 2.0 (async), Pydantic v2, Alembic
- Frontend: Next.js 15 (App Router), TypeScript strict, Tailwind CSS v4 (dark theme only), Zustand, TradingView lightweight-charts
- All timestamps stored as TIMESTAMPTZ (UTC internally). Display/query in IST: `AT TIME ZONE 'Asia/Kolkata'`. Production VM logs are UTC (IST = UTC + 5:30). Note: `WHERE timestamp >= '2026-05-01'` matches UTC midnight (= 05:30 AM IST), not IST midnight.
- Market hours: 9:15 AM - 3:30 PM IST. Pre-Open: 9:00 AM - 9:07 AM IST
- Backend runs on port **8080**, frontend on port **3000**
- Single user, no auth in V1

## Directory Map

```
stockTrading/
├── CLAUDE.md              # THIS FILE
├── ARCHITECTURE.md        # System design, data flow diagrams
├── README.md              # Setup and run instructions
├── Makefile               # Dev commands (make dev, make test, make migrate)
├── docker-compose.yml     # PostgreSQL (5433) + Redis (6380) — LOCAL dev only
├── docker-compose.prod.yml # PRODUCTION: all 5 services (backend, frontend, postgres, redis, nginx)
├── .env.example           # Environment template
├── .envrc                 # direnv: GCP project isolation
├── .github/workflows/     # CI/CD: ci.yml (build on master push), deploy.yml (tag-based release)
├── .claude/skills/        # Claude Code skill definitions
├── infrastructure/        # GCP deployment: terraform/, bootstrap/, docker/, scripts/
├── docs/                  # See "Documentation Reference" section below
├── scripts/               # Dev + analysis scripts (see below)
├── backend/               # Python FastAPI backend (see backend/CLAUDE.md)
└── frontend/              # Next.js React frontend (see frontend/CLAUDE.md)
```

### scripts/

- `dev.sh`, `stop.sh`, `reset.sh` — local dev lifecycle
- `backfill_for_backtest.py` — seed backtest data
- `backtest.py` — run backtests
- `replay_strategy5.py` — offline S5 signal generation from historical candles
- `replay_strategy6.py` — offline S6 (Breakout-Retest) signal generation. Drives the **stateful** strategy minute-by-minute over a window (universe = the symbols S5 fired on each day, for a clean head-to-head), persisting `breakout_retest` signals into the DB so `analyze_strategy5_signal_accuracy.py --strategy breakout_retest` measures their first-touch accuracy. Stamps signals with the real reclaim-candle timestamp (basis ~0). `--no-persist` to dry-run. Validation result in `docs/strategies/strategy-6-breakout-retest.md`
- `replay_strategy2_reclaim.py` — offline S7 (VWAP Reclaim, index options) signal generation — the S2 entry-redesign analogue of `replay_strategy6.py`. Drives the **live** `VWAPReclaimStrategy` minute-by-minute over the staged index spot candles (universe = the index-days the live `vwap_pullback` fired on, for a clean head-to-head vs S2), building the index VWAP from `{index}_FUT` volume (spot volume ~zero). Persists `vwap_reclaim` signals (stamped with the real reclaim-candle timestamp) so `analyze_strategy2_signal_accuracy.py --strategy vwap_reclaim` scores first-touch. `--no-persist` to dry-run; `--target-mode {rr,structure}`, `--reclaim-ref {reversal_extreme,vwap}`, `--reclaim-timeout`, `--rr` sweep the spec's open questions. Confidence is UNGATED (the S2 composite is inverted). Ship-or-kill gate PASSED — result in `docs/strategies/strategy-7-vwap-reclaim.md`
- `backtest_strategy5.py` — S5 signal exit simulator (trailing SL, P&L, sweep mode). Reads trailing SL params from DB via `get_strategy_params`. `--invalidation` adds a thesis-invalidation exit (early exit when the NIFTY intraday-bias flips against the trade for N candles) and simulates each trade twice — baseline vs invalidation — reporting reversal savings vs retracement cost; guards: `--inval-persist N`, `--inval-quorum` (require stock to lose/reclaim its own VWAP), `--inval-moderate`. `--inval-sweep` sweeps persistence × quorum. `--inval-score <t>` overrides the trigger to fire on opposing NIFTY bias with `|score| >= t` (e.g. 0.25 = recalibrated MODERATE band; 0 = use the STRONG label) — for band-edge calibration; finding: looser triggers are net-negative, so the live exit stays decoupled at ~0.50 (study doc "Bias band recalibration"). The index bias VWAP is weighted by `{index}_FUT` futures volume (index spot volume is ~zero), matching live. Exposes `build_index_bias_series()` reused by `backtest_strategy2.py`. Signals carry no `lots` (sizing is at execution) — 1-lot baseline, override via `--lots`. `--source shadow` replays the actual deduped SHADOW trades (real fills + entry timestamps) instead of raw signals and prints the re-sim baseline vs the real realized P&L — an **engine-fidelity check**: the 1m-candle engine runs ~3.6× optimistic vs live tick exits, so trust the *direction*, not the absolute magnitude
- `analyze_strategy5_signal_accuracy.py` — **signal-accuracy** measurement (separate from exits): pure first-touch read (did the underlying reach TARGET before STOP, engine-independent — no ~3.6× exit-engine gap) plus exit-free forward-direction at +15/30/60 min. Reports confidence calibration, per-setup accuracy, 9-factor importance (point-biserial r), regime/direction conditioning, re-fire effect, and filter-lift re-runs. `--strategy intraday_futures|breakout_retest` (the same analyzer validated S6 Breakout-Retest). `--basis-adjust` anchors spot candles to the futures entry (canonical mode); drops wrong-instrument (`--max-basis-pct`, default 3%) and inverted-stop signals. Reuses `fetch_candles_after` from `backtest_strategy5.py`. `--strategy vwap_pullback` is intentionally redirected to the S2 script below (S2 SL/target are option premium, scale-mismatched against index spot). Findings in `docs/backtest/s5-signal-accuracy-study.md`
- `analyze_strategy2_signal_accuracy.py` — **S2 signal-accuracy** measurement (sibling of the S5 analyzer, for index options). Faithful, engine-independent: walks the traded INDEX's spot 1m candles and reads first-touch of `index_target` vs `index_sl` (the index-level barriers in the signal JSONB — NOT the option-premium SL/target), plus exit-free forward-direction at +15/30/60 min + EOD. Only ~half of S2 signals carry index barriers (the rest used the premium %-fallback), so target-first is reported on the resolved subset while the binary hit label is barrier-independent (EOD/forward direction) to keep full n. Per-signal random target-first baseline (`stop_dist/(stop_dist+tgt_dist)` — S2 takes variable market-structure R:R). Reports confidence calibration, CE/PE + per-index, 10-factor importance, regime conditioning (own intraday_bias, NIFTY day-trend, window-state), S2 cuts (VWAP distance, PDH/PDL proximity, OI-confirmed, CPR, VIX, reversal_quality), re-fire, filter lifts, and a count-balanced within-window split. `--strategy vwap_reclaim` scores the S7 reclaim-entry redesign on the same first-touch read (it carries the same index_sl/index_target barriers) — a head-to-head vs `vwap_pullback`. No futures-basis adjustment (index spot is the underlying). Caveat: index direction ≠ live option win-rate (theta + trailing). Findings in `docs/backtest/s2-signal-accuracy-study.md`
- `research_strategy2_edge.py` — mines the S2 **SHADOW** trades (real option fills + trade_monitor exits) for a profitable subset on **realized P&L** — the actual objective, not the index proxy (Shadow takes every signal >~31% conf, so it already contains the "YOLO conf>70, no filter" population — no live A/B needed). Reads bt table `s2_shadow` (staged from prod `trades` JOIN `signals`, source=SHADOW/vwap_pullback/CLOSED). Scans single entry filters + 2-way combos scored by kept-subset win%/return%/net, with a train/test date split to guard overfit, plus the exit-reason mix. Headline edge: the confidence composite is **inverted** — `conf≥70` hits target 4/62 (6%) and loses; `conf<50` returns +7.8% (robust train/test). Findings folded into `docs/backtest/s2-signal-accuracy-study.md`
- `research_strategy2_target_resim.py` — faithful target-distance re-sim: walks each shadow trade's **real option 1m premium candles** (bt tables `s2_rsim` + `s2_opt`, staged from prod) and first-touches a capped target vs the same SL, original re-sim'd on the same engine for apples-to-apples. **Negative result** (folded into the study doc): nearer targets raise win-rate (40%→62% at +10%) and convert losers (8/49 stops + 11/30 time-exits at +25%) but **lower expectancy at every cap** — they chop the fat-tail winners (+46%+) that carry the options book; capping doesn't rescue the inverted gate either. The leak is the 47 stop-outs at −33%, not the target. Engine caveat: 1m first-touch is optimistic (flips orig to +₹20k vs live −₹27k); trust the relative cap-vs-orig direction, not the absolute
- `research_strategy2_stopout_probe.py` — characterizes the 47 stop-outs on real option candles (bt `s2_rsim` w/ indicators + `s2_opt`). **Decisive result** (folded into the study doc): **55% of stop-outs never go green** (MFE <+5%, median time-to-stop 21 min) → it's an **entry-quality** problem, not a stop problem. Stop-management re-sim (breakeven/trailing) — none beats the original (breakeven scratches winners 47→72 SL). Entry separators of stop-outs vs target-hits: higher confidence (60 vs 48), higher reversal_quality (0.71 vs 0.64), higher premium. Conclusion: S2 buys exhaustion reversals at VWAP that immediately bleed (the S5→S6 parallel) — the fix is an **entry redesign** (reversal must hold/reclaim), not targets or stops
- `backtest_strategy2.py` — S2 (index options) thesis-invalidation study, mirroring the S5 script. Replays `vwap_pullback` signals; values exits on the OPTION PREMIUM via delta-approximation from the index move (fast mode — needs only index candles, covers expired contracts); trigger = the traded index's own bias. Reports premium points (size-independent). Same `--invalidation`/`--inval-*`/`--inval-sweep` flags + `--delta`. Finding: invalidation helps S5 (momentum) but hurts S2 (mean-reversion) — see `docs/backtest/s5-invalidation-exit-study.md`
- `backfill_daily_candles.py` — one-time seed of `market_data_daily` from Fyers
- `backfill_futures_1m.py` — backfills 1m SPOT candles (`NSE:{sym}-EQ`, under short name) for the full F&O stock universe (`get_fo_lot_sizes`) into `market_data_1m` of the DATABASE_URL DB (use the bt DB), default last 50 days. For replaying S5 setups (esp. PDH_PDL) across the whole universe to test whether the edge generalises beyond the ~15 screener picks. Spot not futures (continuous + always served; expired contracts error); Fyers serves 50d 1m in one call; batched inserts (asyncpg 32767-param cap); idempotent. Needs a Fyers token in Redis
- `pdhpdl_retest_oos.py` — pre-registered OUT-OF-SAMPLE test of CHASE (current S5 PDH_PDL entry) vs RETEST (S6-style: arm on the break, fire on a 1m retest+reclaim, tight swing stop, 1.8 R:R — params are S6's frozen `BREAKOUT_RETEST_DEFAULTS`, untuned) over the backfilled universe, scored engine-independent (first-touch + fwd +30m), split chronologically train/test. **Finding (KILL): retest fixes the R:R geometry (target-first lift −24→−2) but adds NO directional skill — fwd-favorable ~47% for BOTH entries in BOTH halves; fires on only ~30% of breakouts. Tempers the original S6 result (which was the screener subset, doesn't generalize to the universe).** Run with DATABASE_URL=bt
- `pdhpdl_universe_eval.py` — replays the REAL S5 PDH_PDL entry geometry (`_check_pdh_pdl_breakout`, reusing the real indicators) across the full universe backfilled by `backfill_futures_1m.py`; scores engine-independent first-touch + a faithful trailing-exit R (be 0.5% / trail 0.3%, mirrors trade_monitor) + forward direction, segmented by ADR/liquidity decile, and tags signals against the live shadow PDH_PDL (symbol,day) set (`SUBSET_FILE`, default `/tmp/shadow_pdhpdl_subset.csv`) to isolate screener-selection. Ungated on confidence (composite is inverted). **Finding: PDH_PDL does NOT generalise — full-universe R_trl≈0 (3750 sigs). Clean ADR cutoff ~2.8% (below it a significant net loser t−3.13, above +0.064R t+3.44); deployable edge is the INTERSECTION ADR≥2.8% ∩ screener-selected (+0.229R t+3.51 win76%); liquidity flat. An exclusion+screener rule, NOT a universe expansion.** Run with DATABASE_URL=bt
- `sim_s5_riskcap.py` — simulates the curated S5 SHADOW book (`--setups ORB,PDH_PDL --min-adr 2.8 --confidence 40`) under two risk overlays: a per-trade `--per-trade-cap` ₹/lot MTM stop + a `--daily-stop` realized-loss day breaker. Stages prod shadow trades into bt table `s5_shadow` (real fills + signal snapshot). Applies overlays to REAL outcomes (faithful: keep `net_pnl`, override to −cap only when the trade's real MAE breached the floor — brackets WICK vs CLOSE basis) alongside a re-sim ladder; a wrong-instrument guard drops the `BSE`→BANKEX mis-resolution (index-scale entry vs stock candles). Finding (`docs/backtest/s5-riskcap-overlay-study.md`): the 8K/lot cap is net-NEGATIVE (filters already removed big losers, so it only chops dip-and-recover winners); the −20K daily breaker is the value-add. Run with DATABASE_URL=bt
- `audit_screener_data.py` — read-only freshness check for S5 morning screener data
- `audit_vwap_data.py` — read-only freshness check for S2 VWAP Pullback data (10 checks)
- `telegram/` — MTProto client (Telethon) + signal parser + verifier + setup analyzer. Scripts: `list_dialogs.py`, `fetch_history.py`, `parse_signals.py`, `probe_fyers_history.py`, `verify_signals.py`, `analyze_setups.py`, `analyze_edge.py`. Session files + data/ gitignored. Uses TELEGRAM_API_ID/API_HASH/PHONE/SESSION_NAME from .env

## Strategies


| #   | Name                         | Status  | Instrument    | File                             | Spec                                             |
| --- | ---------------------------- | ------- | ------------- | -------------------------------- | ------------------------------------------------ |
| 1   | ORB (Opening Range Breakout) | STUB    | Index Options | `strategy_1_orb.py`              | `docs/strategies/strategy-1-orb.md`              |
| 2   | VWAP Pullback + PDH/PDL + OI | ACTIVE | Index Options | `strategy_2_vwap_pullback.py`    | `docs/strategies/strategy-2-vwap-pullback.md`    |
| 3   | Expiry Day Gamma Scalping    | STUB    | Index Options | `strategy_3_gamma_scalping.py`   | `docs/strategies/strategy-3-gamma-scalping.md`   |
| 4   | CAN SLIM Growth Breakout     | ACTIVE  | Stock Futures | `strategy_4_canslim.py`          | `docs/strategies/strategy-4-canslim.md`          |
| 5   | Intraday Stock Futures       | ACTIVE  | Stock Futures | `strategy_5_intraday_futures.py` | `docs/strategies/strategy-5-intraday-futures.md` |
| 6   | Breakout-Retest              | ACTIVE  | Stock Futures | `strategy_6_breakout_retest.py`  | `docs/strategies/strategy-6-breakout-retest.md`  |
| 7   | VWAP Reclaim                 | ACTIVE (dark) | Index Options | `strategy_7_vwap_reclaim.py`     | `docs/strategies/strategy-7-vwap-reclaim.md`     |


All strategy files in `backend/app/strategies/`. See `docs/strategies/` for full trading rules per strategy.

### Strategy Execution Modes

- **Auto mode**: Evaluates on every 1m candle close. Controlled by `auto_mode` in `strategy_configs` table.
- **Manual mode**: User triggers via Scanner header bar → `POST /api/v1/strategies/evaluate/batch`.
- Configuration (active/auto_mode/symbols) managed via Settings page → Strategies section.

## Agent Autonomy Levels

- **MANUAL**: Alerts via Telegram, user executes manually. No capital gates.
- **SEMI**: Auto-closes on SL hit, requests confirmation for profit booking.
- **YOLO**: Fully autonomous — auto-executes, auto-books profits, auto-closes on SL. Enforces drawdown, max-trades, and daily profit cap gates.

See `backend/CLAUDE.md` for execution architecture details (shadow/YOLO isolation, lot sizing, SL/target recomputation, margin tracking, risk gates, signal dedup).

## Trading Parameters

- Capital: Set in DB
- Max daily drawdown: Set in DB
- Risk per trade: Set in DB
- Max trades/day: Set in DB
- **YOLO Profit Caps**: Configurable via `yolo_profiles` table (Settings page). Multiple profiles run simultaneously (e.g. 5K, 10K, 15K) — each signal creates one Trade+Position per active uncapped profile. Trade monitor checks caps per profile independently, closing only that profile's positions when its cap is hit. Trades carry `yolo_profile_id` FK; `source` stays `"YOLO"` for all profile trades, `"MANUAL"` for user-executed trades.
- **Thesis-Invalidation Exit (per-profile, S5 only)**: each `yolo_profiles` row carries `invalidation_persist`/`invalidation_quorum`/`invalidation_strong_only` (Settings page → YOLO Profiles → "Inval" toggle). When enabled (`invalidation_persist > 0`, default 3), the trade monitor closes that profile's open S5 positions early (`ExitReason.INVALIDATION`) once the live NIFTY intraday bias flips STRONG-against the position direction for N consecutive 1m candles. Run an enabled profile beside an identical control for live A/B (paper). Helps momentum (S5) — do NOT use for S2 (mean-reversion). See `docs/backtest/s5-invalidation-exit-study.md`.
- **YOLO execution confidence is per-profile**: each `yolo_profiles` row has `min_confidence_for_execution` (NULL = inherit the global `min_confidence_for_execution` default in `trading_config`); set via Settings → YOLO Profiles → "MinConf". Lets a vwap_reclaim profile run a lower confidence bar (e.g. 40) without lowering the global bar for S2/S5 profiles. The signal `executable` flag and the Telegram notification gate both use the **lowest** threshold among active profiles that subscribe to the signal's strategy/setup — so "executable by at least one profile" is computed strategy-aware.
- **YOLO bias gate is per-profile (S5)**: each `yolo_profiles` row has `min_bias_strength` (NULL = no gate; "WEAK"/"MODERATE"/"STRONG"), set via Settings → YOLO Profiles → "Bias". When set, the profile only executes signals whose stored stock `intraday_bias.strength` meets the bar (ordinal WEAK<MODERATE<STRONG; a signal with no bias is rejected) — effectively S5-only, since only `intraday_futures` carries a bias. Promotes the existing intraday bias from a soft confidence factor into a hard precondition for one book (the validated S5 PDH_PDL/ORB + STRONG-bias setup). The `executable` flag + Telegram gate are bias-aware too (a STRONG-gated profile does not mark non-STRONG signals executable). Clear it by PATCHing an empty string `""`.
- Strike selection: ATM or 1-strike ITM (Delta 0.45-0.60), resolved by `option_resolver.py`
- Strike gaps: NIFTY=50, BANKNIFTY=100, FINNIFTY=50, SENSEX=100, MIDCPNIFTY=25
- Preferred premium range: Rs 150-400
- SL/target computed on option premium (not index price), 30-35% SL, 1:1.5 R:R
- **Paper fill model**: `trading_config.fill_model` (`BID_ASK` default | `LTP`, Settings toggle) — BUYs fill at ask, SELLs at bid on every paper entry AND exit; SL/target triggers + display MTM stay LTP; the profit-cap valuation is exit-side (bid/ask) so caps trigger on bookable P&L. Per-fill quote snapshot in `trades.fill_meta`; each trade stamped with `trades.fill_model` — **pre/post-cutover P&L comparisons must filter on it** (NULL = pre-cutover LTP fills; BID_ASK cuts paper P&L ~25–35%). Details + spread-cost SQL: `docs/signal-to-trade-flow.md` §4.4
- Expiry: NIFTY weekly Tuesday, SENSEX weekly Thursday, others monthly only (post-SEBI Nov 2024)

## Commands

```bash
# One-command start (infrastructure + backend + frontend)
make dev

# Or individually:
docker compose up -d          # Start PostgreSQL + Redis
make backend                  # Backend on :8080 (dual-stack IPv4+IPv6)
make frontend                 # Frontend on :3000

# IMPORTANT: Always use `make backend` or `--host ::` when starting uvicorn manually.
# `--host 0.0.0.0` is IPv4-only — macOS resolves localhost to IPv6, causing 503s from the browser.
make test                     # Run pytest suite
make migrate                  # Run Alembic migrations
make migration msg="desc"     # Generate new migration

# First-time setup
cd backend && python3.11 -m venv .venv && source .venv/bin/activate && pip install -e ".[dev]"
cd frontend && npm install
cp .env.example .env          # Then fill in Fyers API keys

# Strategy 5 replay (offline signal generation test)
cd backend && source .venv/bin/activate
python scripts/replay_strategy5.py --date 2026-04-28
python scripts/replay_strategy5.py --date 2026-04-28 --symbols VEDL,SUNPHARMA

# Data freshness audits (read-only, no side effects)
python scripts/audit_screener_data.py     # S5 morning screener
python scripts/audit_vwap_data.py         # S2 VWAP Pullback

# Backfill market_data_daily (run once; nse_bhav_copy_task keeps it current after)
python scripts/backfill_daily_candles.py              # last 60 days, all F&O stocks
python scripts/backfill_daily_candles.py --days 90    # longer lookback
python scripts/backfill_daily_candles.py --symbols TCS,RELIANCE

# Strategy 5 signal backtest
python scripts/backtest_strategy5.py --confidence 60 --start 2026-05-05
python scripts/backtest_strategy5.py --sweep --start 2026-05-01 --end 2026-05-05
python scripts/backtest_strategy5.py --sweep --start 2026-05-01 --end 2026-05-05 --lots 1

# Thesis-invalidation exit study (baseline vs invalidation on the same trades)
python scripts/backtest_strategy5.py --confidence 70 --start 2026-04-29 --end 2026-06-02 --invalidation --inval-persist 3
python scripts/backtest_strategy5.py --inval-sweep --confidence 70 --start 2026-04-29 --end 2026-06-02
python scripts/backtest_strategy5.py --source shadow --inval-sweep --confidence 70 --start 2026-04-29 --end 2026-06-02  # replay REAL shadow trades + engine check
python scripts/backtest_strategy2.py --inval-sweep --confidence 70 --start 2026-04-29 --end 2026-06-02   # S2 (options)
# Backtest reads signals + index/underlying/futures candles from the configured DB. To replay
# prod days locally, stage prod's signals + market_data_1m (incl. %FUT for index VWAP volume) +
# global_market_snapshots into a local DB and set DATABASE_URL.

# Signal-ACCURACY measurement (engine-independent first-touch; faithful, unlike exit P&L)
# Stage prod signals + underlying 1m candles into a local DB and point DATABASE_URL at it.
DATABASE_URL=postgresql+asyncpg://trader:trader_dev_123@localhost:5433/stocktrading_bt \
  python scripts/analyze_strategy5_signal_accuracy.py --start 2026-04-29 --end 2026-06-02 --basis-adjust
python scripts/analyze_strategy5_signal_accuracy.py --start 2026-04-29 --end 2026-06-02 --min-confidence 70 --trace

# S2 (VWAP-pullback index options) signal accuracy — index first-touch vs index_sl/index_target
DATABASE_URL=postgresql+asyncpg://trader:trader_dev_123@localhost:5433/stocktrading_bt \
  python scripts/analyze_strategy2_signal_accuracy.py --start 2026-04-29 --end 2026-06-04
# S2 EDGE research on REAL option P&L (needs bt table s2_shadow staged from prod SHADOW trades)
DATABASE_URL=postgresql+asyncpg://trader:trader_dev_123@localhost:5433/stocktrading_bt \
  python scripts/research_strategy2_edge.py --min-n 18
# S2 target-distance re-sim on REAL option premium candles (needs bt tables s2_rsim + s2_opt)
DATABASE_URL=postgresql+asyncpg://trader:trader_dev_123@localhost:5433/stocktrading_bt \
  python scripts/research_strategy2_target_resim.py

# Strategy 6 (Breakout-Retest) replay + head-to-head accuracy vs S5 (same staged DB)
DATABASE_URL=postgresql+asyncpg://trader:trader_dev_123@localhost:5433/stocktrading_bt \
  python scripts/replay_strategy6.py --start 2026-04-29 --end 2026-06-02
python scripts/analyze_strategy5_signal_accuracy.py --strategy breakout_retest --start 2026-04-29 --end 2026-06-02 --basis-adjust

# Strategy 7 (VWAP Reclaim, index options) replay + head-to-head accuracy vs S2 (same staged DB)
DATABASE_URL=postgresql+asyncpg://trader:trader_dev_123@localhost:5433/stocktrading_bt \
  python scripts/replay_strategy2_reclaim.py --start 2026-04-29 --end 2026-06-04
DATABASE_URL=postgresql+asyncpg://trader:trader_dev_123@localhost:5433/stocktrading_bt \
  python scripts/analyze_strategy2_signal_accuracy.py --strategy vwap_reclaim --start 2026-04-29 --end 2026-06-04
```

## Deployment (GCP)

Single VM on Google Cloud Platform (asia-south1, Mumbai). Full architecture and infrastructure details in `docs/deployment-architecture.md`.

- **Live URL**: [http://8.231.84.44](http://8.231.84.44) (HTTP only — single user, paper trading)
- **Project**: `stock-trading-prod` / `kakwani-khayti-org` / `kakwani.khayti@gmail.com`
- **SSH**: `ssh -i ~/.ssh/st-deploy deploy@8.231.84.44` (key from Terraform state)
- **gcloud config**: `stock-trading` (isolated via `direnv` + `.envrc`)
- **Free trial**: Rs 28,365 credits, expires Aug 17 2026

### Key Commands

```bash
make release v=1.0.0         # Tag + deploy via GitHub Actions
make deploy-version v=0.9.5  # Rollback to specific version
make show-version            # Check deployed version (or: curl http://8.231.84.44/api/v1/health)
make ssh                     # SSH into VM
make prod-logs               # Tail production logs
make infra-plan              # Terraform preview
make infra-up                # Create/update infrastructure
```

### CI/CD

> **SAFE TO PUSH TO MASTER.** Master push only builds Docker images (CI validation) — does NOT deploy. Deploy: cut a semver tag.

- **Build** (master push): GitHub Actions builds images → pushes to ghcr.io with `:<sha>` + `:latest` tags (~2-4 min)
- **Deploy** (tag push): retags the `:<sha>` image as `:v1.0.0` (no rebuild, ~5s) → SSHes to VM → pulls + migrates + deploys
- **IMPORTANT**: Always wait for the Build workflow to complete before pushing a deploy tag. Deploy retags the commit's `:<sha>` image — if the build hasn't pushed it yet, the retag step fails.

### Versioning

Semver (`vMAJOR.MINOR.PATCH`). Run `git log v{last}..HEAD --oneline` before releasing:

- **Patch**: bug fixes, docs, config
- **Minor**: new features, endpoints, UI, strategy changes
- **Major**: always confirm with user — never auto-decide

## Test Coverage

- **Backend**: ~1115 pytest tests in `backend/tests/`. Run: `make test`. See `backend/CLAUDE.md` for per-module coverage details.
- **Frontend**: Vitest unit tests in `frontend/src/__tests__/`. Run: `cd frontend && npm test`. Tests pure logic functions copied verbatim from page files (not exported). Config: `frontend/vitest.config.ts`.

## Documentation Reference

### docs/strategies/

- `strategy-1-orb.md` through `strategy-6-breakout-retest.md` — full trading rules per strategy
- `strategy-5-phase1-reference.md`, `strategy-5-phase2-reference.md` — S5 phase implementation references
- `strategy-7-vwap-reclaim.md` — **Strategy 7 (VWAP Reclaim, index options)** — the S2 entry redesign, ACTIVE/ships dark. Arm on S2's VWAP-reversal trigger, fire only on a 1m reclaim of the reversal extreme (tight swing stop → R:R target), à la S6. Lean ungated confidence (the S2 composite is inverted). Full rules, params, the PASSED ship-or-kill validation table (target-first 15.6%→40.2%, +30 min forward 52.9%→62.1% robust across the split), and the live shadow real-P&L A/B plan
- `strategy-2-reclaim-entry.md` — **design rationale** for the S2 entry redesign (now BUILT as S7 — see above), motivated by `docs/backtest/s2-signal-accuracy-study.md`. The diagnosis (S2 fires on the exhaustion reversal candle at VWAP), the arm→reclaim→fire design, and the answered open questions (rr target + reversal-extreme reclaim won the replay A/B)
- `arjun-liquide-study.md` — reverse-engineering of "Arjun - Options by Liquide" Telegram channel (630 trades parsed, independently backtested, feature-importance analysis, implied strategy extraction)
- `intraday-hunter-study.md` — reverse-engineering of @IntradayHunter (30 videos analyzed, gap-down CE setup, position sizing confirmed)

### docs/ai/

- `shadow-agent.md` — shadow executor design, isolation guarantees
- `signal-confidence-agent.md` — LLM confidence overlay design

### docs/backtest/

- `harness.md` — backtest framework usage and modes
- `option-data.md` — option data sourcing for backtests
- `s5-invalidation-exit-study.md` — thesis-invalidation exit study (exit when the index regime flips against the trade), S5 + S2 over 20 days: **+38% for S5** (momentum, zero retracement cost at persist=3) but **net-negative for S2** (mean-reversion). Documents the index-VWAP futures-volume fidelity fix, why the same exit helps momentum yet hurts mean-reversion, and the **live per-YOLO-profile implementation** (`trade_monitor._check_invalidation`, `yolo_profiles.invalidation_*`) used to validate the magnitude on the paper book
- `s5-signal-accuracy-study.md` — **signal-accuracy** study (engine-independent first-touch + forward-direction over 25 days). Headline: S5 signals are **not directionally predictive** (forward-favorable 37–42%, target-first 18%) and **confidence is uninformative below ~80** (r≈0.04); only conf≥80 (7.6% of volume) shows edge. Bias-opposed (10.7% hit) and counter-trend (29.2%) cohorts are the cleanest negatives; re-fires hit *better*. Factor importance: oi_direction/screener_rank carry weak signal, rvol/nifty_bias/gap are noise. Surfaces a live "BSE"→`BSE:BANKEX..FUT` instrument-resolution bug. Tooling: `scripts/analyze_strategy5_signal_accuracy.py` (the same analyzer validated S6 Breakout-Retest, `--strategy breakout_retest`)
- `s2-signal-accuracy-study.md` — **S2 (VWAP-pullback index options) signal-accuracy** study, mirror of the S5 study (engine-independent index first-touch + forward-direction over 27 days, 94 signals). Headline: S2 signals are **~coin-flip and decaying** (forward-favorable 50/53/45%, EOD 43%, target-first 15.6% vs ~30% per-signal random) and **confidence is *counterproductive*** — r(conf,hit) = **−0.224** (strengthening; was −0.168 at 25 days), negative in both split halves; the `conf≥70` gate keeps the *worse* 34% cohort (25.0% vs 51.6%). Factor importance: the composite is **mis-signed with NO good factor** — at 94 signals not one factor is positively correlated with outcome, and the highest-weight `bias_alignment` (0.25) is flat (r −0.012, fell from +0.144 at 25 days); high-weight `reversal_quality`/`volume_quality` lean negative. Re-fires hit much better (60% vs 30%). NOT robust across the count-balanced split (flagged, not actioned): CE/PE (gap closed to 42.6/42.6), window-state, PDH-PDL, counter-trend. **EDGE found on REAL option P&L** (96 closed SHADOW vwap_pullback trades, prod `trades` joined to signal — the shadow book IS the YOLO>70 population, so no live A/B needed): the composite is **inverted** — `conf≥70` hits its target **4/62 (6%)** and loses (−8% avg); `conf<50` hits target as often as stop and returns **+7.8%** (47% win, robust across train/test). Mechanism: the score leans on `reversal_quality`+`rr_ratio_quality`, which select exhaustion reversals with far/unreachable targets (targets hit only 18%, at +46%; stops hit 51%, at −33%). Top proposals: (1) **stop gating execution on conf≥70** (selects the losing cohort — robust, immediate); (2) **redesign the ENTRY** — the root cause: 55% of stop-outs never go green (exhaustion reversals at VWAP), and BOTH nearer targets AND stop-management (breakeven/trail) tested negative on real premium paths → require the reversal to hold/reclaim before entry, à la S5→S6; (3) don't trust the composite to rank a new entry (no good factor). Tooling: `analyze_strategy2_signal_accuracy.py` (direction) + `research_strategy2_edge.py` (real-P&L edge mining) + `research_strategy2_target_resim.py` (target re-sim) + `research_strategy2_stopout_probe.py` (stop-out probe). Mean-reversion counterpart to the momentum S5/S6 work. **Proposal #2 (entry redesign) is BUILT** as Strategy 7 (`vwap_reclaim`, ships dark) — offline ship-or-kill gate PASSED (target-first 15.6%→40.2%, +30 min forward 52.9%→62.1% robust across the split); see `docs/strategies/strategy-7-vwap-reclaim.md`

### docs/

- `signal-to-trade-flow.md` — end-to-end pipeline: signal generation → instrument resolution → AI overlay → YOLO/shadow/manual execution → trade monitoring. Covers the `executable` flag, confidence ladder, price sourcing, grace period, and exit conditions
- `execution-concurrency.md` — concurrency model of the tick-to-trade pipeline: what's sequential vs fire-and-forget, why each await exists, per-symbol concurrency guard, trade monitor polling design
- `intraday-bias.md` — intraday bias composite design: 8-factor model, ADR-based normalizers, time decay, and how S2/S5 consume the bias (hard gates, confidence factors, lot sizing)
- `deployment-architecture.md` — full deployment architecture, infrastructure, secrets management, container health
- `permanent-watchlist.md` — permanent watchlist design: three stock categories (permanent-only, overlap, screener-only), "P" badge display rules, execution gates, screener bypass rules, data flow
- `BUGS-2026-04-29.md` — historical bug tracker
- `journal/strategy-6-journal.html` — **running daily diagnosis journal for Strategy 6** (live paper A/B). Agent-maintained: after ~2–3 weeks the accumulated patterns drive concrete, backtested changes to the strategy. **When you analyze an S6 trading day, append an entry** following `journal/how-to-update-strategy-6-journal.md` — work at the signal level (dedupe the ~3× profile fan-out), be honest about one-winner days, and compute "%-to-target before SL" on the **entry→exit window only** (not whole-window — that includes post-stop bounces). Open the HTML directly in a browser.

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

### Local Live Testing

Unit tests verify code correctness, but many bugs (volume spikes, VWAP disappearing, WS reconnect issues) only surface with real Fyers data. Always verify data-path changes against the live local stack before deploying.

**Prerequisites**: PostgreSQL + Redis running (`docker compose up -d`), Fyers token in Redis (auto-login runs daily at 7:45 AM via `fyers_login_task.py`; to trigger manually, call `auto_login_and_store()` from `fyers_auto_login.py`).

#### During Market Hours (9:15-15:30 IST)

```bash
# 1. Start the backend (or it may already be running)
make backend

# 2. Establish baselines — snapshot current state BEFORE the change
docker exec -i st-postgres psql -U trader -d stocktrading -c "
  SELECT symbol, ROUND(AVG(volume)) as avg_vol, MAX(volume) as max_vol
  FROM market_data_1m
  WHERE timestamp >= (NOW() AT TIME ZONE 'Asia/Kolkata')::date::timestamptz AT TIME ZONE 'Asia/Kolkata'
    AND symbol IN ('VEDL','SAIL','NIFTY','BANKNIFTY')
  GROUP BY symbol ORDER BY symbol;"

# 3. Test restart scenarios
kill -9 $(lsof -ti :8080)
make backend &
sleep 90  # wait for candle close

# 4. Compare post-restart candles against baseline
docker exec -i st-postgres psql -U trader -d stocktrading -c "
  SELECT symbol, timestamp AT TIME ZONE 'Asia/Kolkata' AS ts, volume
  FROM market_data_1m
  WHERE timestamp > NOW() - INTERVAL '3 minutes'
    AND symbol IN ('VEDL','SAIL','NIFTY','BANKNIFTY')
  ORDER BY timestamp DESC, symbol;"

# 5. Test WS reconnect
curl -s http://localhost:8080/api/v1/market/feed/stop -X POST
sleep 2
curl -s http://localhost:8080/api/v1/market/feed/start -X POST
sleep 90

# 6. Check strategy diagnostics
curl -s "http://localhost:8080/api/v1/options/agent-log?date=$(date +%F)&limit=5" | python3 -m json.tool
curl -s "http://localhost:8080/api/v1/intraday-futures/agent-log?date=$(date +%F)&limit=5" | python3 -m json.tool
```

**What to look for:** Normal candle volumes (not millions), VWAP in agent logs after reconnect, Redis prices updating within seconds, no `ERROR` lines in stdout.

#### Outside Market Hours

```bash
make test                      # Unit tests first
make backend                   # Startup tasks run but no live candles

# REST endpoints that work without live data
# Health check — returns data_feed_ready (bool) and startup_error (str|null)
# data_feed_ready=false means background startup (symbol master, backfill, WS) is still running
curl -s http://localhost:8080/api/v1/health | python3 -m json.tool
curl -s http://localhost:8080/api/v1/tasks | python3 -m json.tool
curl -s http://localhost:8080/api/v1/strategies | python3 -m json.tool
```

#### Simulated Mode (Full-Stack Testing Anytime)

Use the Market Simulator for end-to-end testing outside market hours — live ticks, candle aggregation, strategy evaluation, signal generation all work.

```bash
# 1. Start the Market Simulator (separate repo)
cd ../marketSimulator && make run     # Runs on port 8787

# 2. Set MARKET_MODE=simulated in .env (already defaults to live)
# MARKET_MODE=simulated

# 3. Start the backend — connects to simulator instead of Fyers
make backend

# 4. Start a simulator session (volume auto-inferred from symbol type)
curl -s -X POST http://localhost:8787/sim/session -H "Content-Type: application/json" -d '{
  "speed": 10.0,
  "symbols": {
    "NSE:NIFTY50-INDEX": {"base_price": 24800, "prev_close": 24750, "volatility": 0.001},
    "NSE:NIFTYBANK-INDEX": {"base_price": 55000, "prev_close": 54900, "volatility": 0.0012},
    "NSE:INDIAVIX-INDEX": {"base_price": 15.5, "prev_close": 15.3, "volatility": 0.002},
    "NSE:NIFTY26MAYFUT": {"base_price": 24850, "prev_close": 24800, "volatility": 0.001},
    "NSE:BANKNIFTY26MAYFUT": {"base_price": 55100, "prev_close": 55000, "volatility": 0.0012}
  }
}'

# 5. Verify: prices flowing, candles forming, strategies evaluating
# Health check includes data_feed_ready — wait for it to be true before testing strategies
curl -s http://localhost:8080/api/v1/health | python3 -m json.tool
curl -s http://localhost:8080/api/v1/market/prices | python3 -m json.tool
```

**Futures contract symbols** change monthly — update `NIFTY26MAYFUT` etc. to the current near-month contract. Check `NSE_FO.csv` symbol master or the backend startup logs for resolved futures symbols.

**What changes in simulated mode:** WS connects to simulator (not Fyers), REST data calls route to simulator, `is_market_open()` always returns True, `is_trading_day()` always returns True, `is_past_close_deadline()` always returns False, all trading window checks (`is_in_trading_window`, `is_in_custom_trading_window`, `get_window_state`, `get_custom_window_state`) always return in-window, `is_in_dead_zone()` always returns False, Fyers TOTP login is skipped entirely, symbol master CSVs fetched from simulator.

**What stays the same:** PostgreSQL, Redis, strategy evaluation, signal pipeline, agent runner, trade monitor — all real code paths exercised.

**Key rule:** Never test against production. Local has its own PostgreSQL (port 5433), Redis (port 6380), and Fyers token. `TELEGRAM_ENABLED=false` in local `.env` prevents accidental Telegram messages.

## Known Cleanup Tasks

- **Backend deps in `backend/pyproject.toml` are unpinned (`>=`), so Docker image rebuilds re-resolve latest and aren't reproducible.** This crash-looped prod once (fastapi 0.137.0's strict empty-path check vs our `@router.get("")` collection routes), so `fastapi` is pinned `==0.136.3` + `starlette==1.1.0`. Proper fix: add a lock file (`uv lock` / `pip-compile`) so the build installs an exact tested set; then `fastapi` can be unpinned once the empty-path routes are migrated to non-empty paths.
