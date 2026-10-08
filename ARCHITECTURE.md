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
                    ├── _fyers_to_internal() converts Fyers symbols to short names
                    │   (e.g. "NSE:TCS-EQ" → "TCS") via _reverse_map
                    │   Option/futures contracts pass through as-is
                ──> feed_manager.py (receives internal short name + optional fyers_alias)
                    ├── Redis cache "price:{short_name}" (primary, for strategies)
                    ├── Redis cache "price:{fyers_alias}" (alias, for watchlist/positions)
                    ├── Aggregate into 1m candles (under short name only → DB consistency)
                    ├── Store completed candles in PostgreSQL (market_data_1m)
                    ├── WebSocket broadcast to frontend under both names (price:update)
                    └── On candle close: check auto_mode strategies for this symbol
                        └── If any match → trigger strategy_runner.on_candle_close()

Symbol naming convention:
  - Internal/DB/strategies: short names ("TCS", "NIFTY", "ADANIPORTS")
  - Fyers API calls: qualified names ("NSE:TCS-EQ", "NSE:NIFTY50-INDEX")
  - Redis price cache: dual — both "price:TCS" and "price:NSE:TCS-EQ"
  - strategy_configs.symbol_map stores the short→Fyers mapping

Subscribed symbols (all get live WebSocket ticks):
  - FYERS_SYMBOL_MAP (5 indices + India VIX) — always subscribed
  - Strategy-configured symbols — subscribed on startup with symbol_map for reverse lookup
  - Watchlist items from Redis — subscribed on startup and on add
  - Open positions + today's closed trades (Fyers option symbols) — for hold analysis

Frontend also polls GET /api/v1/market/prices every 10s as fallback.

Chart data: GET /api/v1/market/ohlcv/{symbol}?resolution=5&days=15
  → Backend proxies to Fyers history API (pre-aggregated candles)
  → Paginated for large ranges, deduped, sorted ascending
  → Falls back to PostgreSQL (MarketData1m) if Fyers unavailable
  → Resolutions: "1" (1m), "5" (5m), "15" (15m), "60" (1h), "D" (daily)
```

### 1b. WebSocket Subscription Lifecycle

Four processes add symbols to the Fyers WebSocket throughout the day. The `_symbols` list on `FyersWSClient` grows monotonically — symbols are never unsubscribed (post-exit candle data needed for hold analysis).

```
1. STARTUP (main.py → _start_data_feed_if_authenticated)
   Assembles the full initial symbol set from 4 sources:
     a. Strategy configs → _get_all_backfill_symbols()
        (strategy-configured symbols + S5 permanent watchlist + near-month index futures)
     b. Dashboard watchlist → _get_watchlist_symbols()
        (Redis hash "watchlist:items" keys)
     c. S5 screener watchlist → _get_strat5_watchlist_symbols()
        (Redis "strat5:watchlist:{today}" — populated by previous day's screener or today's 8:30 AM run)
     d. Open positions + today's closed trades → _get_position_symbols()
        (SQL union_all of Position.fyers_option_symbol + today's closed Trade.fyers_option_symbol)
   → Registers symbol maps for (a) and (c) so ticks resolve to short names
   → Calls fyers_ws_client.start(extra_symbols=union of all)
   Also runs candle backfill (previous day + today) before starting WS.

2. WS RECONNECT (fyers_ws_client._managed_reconnect)
   Triggered by: _on_close during market hours, watchdog stale-tick detection
   → 5s backoff
   → _collect_dynamic_symbols() reads 3 Redis keys:
       - strat5:watchlist:{today} (screener output)
       - strat5:watchlist:permanent
       - watchlist:items (dashboard watchlist)
   → Merges with existing self._symbols (preserves everything from startup + mid-session subscribes)
   → stop() + start() (full WS reconnection with merged symbol set)
   Note: position symbols from startup are retained in _symbols; not re-queried from DB.

3. REAUTH (fyers_login_task._start_data_feed_after_login, 7:45 AM IST daily)
   Runs after TOTP auto-login obtains a fresh token.
   → Fresh symbol assembly — same 4-source logic as startup (a-d above)
   → Does NOT rely on _symbols carryover from previous session
   → Only runs if fyers_ws_client.is_connected is False
     (skips if existing WS is still connected — e.g., token refreshed without disconnect)

4. SCREENER PROVISIONING (morning_screener._provision_watchlist_symbols, ~8:30 AM IST)
   Runs at the end of run_morning_screener() after the 3-stage pipeline:
   → REST quote fetch for all screened symbols (primes Redis price cache)
   → Backfills previous day + today's 1m candles per symbol
   → fyers_ws_client.subscribe_symbols(fyers_symbols, symbol_map=fyers_map)
     (extends _symbols + _reverse_map; deduplicates against already-subscribed)
   If WS is down at call time: logs warning, falls through —
     _collect_dynamic_symbols() on next reconnect picks up the watchlist from Redis.
```

Key invariant: `_symbols` only grows. Closed positions keep receiving ticks for the rest of the day. Today's closed trades are included at startup/reauth so hold analysis has candle data even after a mid-day restart.

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
      │   NOTE: Signals are BARE TRADING OPPORTUNITIES — no lot sizing.
      │         Lot computation happens at execution time via lot_sizing.py.
      └── If signal generated:
          ├── [OPTION signals only] option_resolver enriches:
          │   ├── Select ATM/ITM strike (STRIKE_GAPS per index)
          │   ├── Select nearest expiry (weekly NIFTY/SENSEX, monthly others)
          │   ├── Look up Fyers symbol via symbol master
          │   ├── Fetch option premium (Redis cache → Fyers REST fallback)
          │   └── Compute SL/target on premium (not index price)
          ├── [FUTURE signals] futures_resolver enriches:
          │   ├── Resolve nearest-month futures contract (symbol, expiry, lot size)
          │   ├── Fetch futures LTP
          │   └── Proportionally adjust SL/target from spot to futures price (direction-aware)
          ├── _check_regulatory_limits() — F&O ban list check ONLY
          │   (max trades/drawdown gates are NOT here — they're in auto_executor)
          ├── Save to signals table (with executable flag)
          ├── Broadcast via WebSocket (signal:new)
          ├── YOLO mode → auto_executor.execute()
          └── MANUAL/SEMI → Telegram alert, wait for user
```

### Strategy Configuration (strategy_configs table)
```
strategy_name      | is_active | auto_mode | symbols              | symbol_map                          | parameters | risk_params
-------------------+-----------+-----------+----------------------+-------------------------------------+------------+------------
vwap_pullback      | true      | true      | ["NIFTY","BANKNIFTY"]| {"NIFTY":"NSE:NIFTY50-INDEX",...}   | {...}      | {...}
orb                | false     | false     | ["NIFTY"]            | {...}                               | {...}      | {...}
can_slim           | true      | true      | ["TCS","RELIANCE"]   | {"TCS":"NSE:TCS-EQ","RELIANCE":...} | {...}      | {...}
intraday_futures   | false     | true      | []                   | {}                                  | {...}      | {...}
  (Strategy 5 uses dynamic symbols from Redis watchlist, not the symbols column)

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
    │
    ├── + every 1 minute 09:15–09:45 (fetch_oi_snapshots_hf, NIFTY/BANKNIFTY/SENSEX only) —
    │     same rows; feeds the IH v2 opening OI flow (09:16→09:19 ΔPE−ΔCE, nearest expiry)
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

**Three execution paths** — lot sizing and margin are computed at execution time by
`lot_sizing.py` and `margin_calculator.py`, not during signal generation:

```
Signal ──> Execution Path:
           │
           ├── SHADOW (shadow_executor.py):
           │     Lots: always 1 via compute_lots_for_shadow() — clean per-lot P&L
           │     Gates: confidence, F&O ban, close deadline, permanent watchlist
           │     No risk gates (no drawdown, no max trades, no VIX, no trade window)
           │     Margin: computed via compute_margin(), stored on Trade + Position
           │
           ├── YOLO (auto_executor.py):
           │     Lots: strategy-aware via compute_lots_for_yolo()
           │           (S5 delegates to strategy._compute_lots() for conviction sizing)
           │     Gates (in _final_risk_check): drawdown breach, max trades/day,
           │           confidence, F&O ban, permanent watchlist, duplicate position
           │     Margin: computed via compute_margin(), stored on Trade + Position
           │
           └── MANUAL (signals.py preview + execute):
                 Lots: recommended via compute_lots_for_manual() (same logic as YOLO)
                 No gates — warnings only (user decides)
                 Margin: computed via compute_margin(), shown in preview modal

       ──> Trade Created (OPEN) ──> Position Created
                                    ├── margin_required set on both Trade + Position
                                    ├── Agent monitors (500ms loop)
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

Risk gate split:
  - strategy_runner._check_regulatory_limits(): F&O ban list ONLY
  - auto_executor._final_risk_check(): drawdown + max trades (YOLO path only)
  - strategy_runner._check_strategy_risk_limits(): trading windows + VIX (per-strategy)
```

### 4. Agent Decision Flow
```
Every 500ms (agent_runner main loop):
  For each open position:
    1. Get current price from Redis (option premium via fyers_option_symbol, index fallback)
    2. Detect direction: target < entry = SHORT, else LONG
       (uses target_price, not stop_loss — SL can be trailed past entry)
    3. Check SL: LONG price <= SL, SHORT price >= SL → AUTO CLOSE
    4. Check Target: LONG price >= target, SHORT price <= target
       - YOLO: auto-book profit
       - SEMI: send Telegram confirmation, wait for approve/reject
       - MANUAL: send alert only
    5. Trailing SL (POSITIONAL always, INTRADAY when trailing_sl_enabled):
       - Breakeven: move SL to entry after gain >= breakeven_pct
       - Progressive: trail SL at HWM*(1-trail%) for longs, LWM*(1+trail%) for shorts
    6. Check Expiry: POSITIONAL positions roll 3 days before futures expiry
    7. Check Time: time >= 15:15 IST → AUTO CLOSE INTRADAY positions
    8. Check Drawdown: daily_loss >= 5% → CLOSE ALL, HALT TRADING for the day
    9. Update unrealized P&L (direction-aware) → broadcast via WebSocket (position:pnl)
```

### 4b. Strategy 5 Daily Workflow
```
APScheduler (morning_workflow_task.py):
  8:00 AM  — Morning briefing: gather yesterday's trades + 5-day stats → LLM synthesis
             → Redis strat5:morning_briefing:{date}
  8:00 AM  — Global cues snapshot: package global_market_task data → Redis strat5:global_cues:{date}
             VIX > 20 → HALT agent
  8:30 AM  — Morning screener (3-stage):
             Stage 1: Quant scoring ~180 F&O stocks (8 factors: RS, range, volume, OI, ADR, sector, delivery%, 52w high)
             Stage 2: News sentiment ~20 stocks (parallel Gemini calls)
             Stage 3: LLM confidence check (batched)
             → Final watchlist (15-20 stocks) → Redis strat5:watchlist:{date}
             → Subscribe watchlist stocks on Fyers WebSocket
             → Build RVOL baselines → Redis strat5:rvol_baseline:{symbol}
  9:15 AM  — ORB_FORMING phase: strategy tracks 15-min opening range (high/low)
  9:31 AM  — ORB level logging: logs ORB high/low/range for each watchlist stock
  9:30+    — Signal generation: ORB breakout with ADR/RVOL/VWAP/volume/bias filters
  15:15 PM — EOD summary: query DB trades, log stats to agent log

Strategy 5 uses dynamic symbols (Redis watchlist, not DB config).
get_auto_strategies_for_symbol() calls strategy.get_symbols() for dynamic matching.
```

### 4c. Intraday Hunter Agent (discretionary index-options SUGGESTER — MANUAL-alert only)
```
A human-like LLM agent that reasons like the @IntradayHunter trader (stop-loss-hunting /
trapped-trader framework) and SUGGESTS a CE/PE basket (or SKIP) near the open. It NEVER
auto-executes — it only writes intraday_hunter_runs rows and surfaces them on /intraday-hunter.

08:45 AM  — Call 1 (pre-open thesis): intraday_hunter_task.py → thesis.run_call1()
             Builds per-index prev-day structure + multi-day STRUCTURAL memory (no P&L) +
             India VIX + expiry calendar → renders mplfinance prev-day charts → calls Claude
             (variant C, claude CLI on CLAUDE_CODE_OAUTH_TOKEN, inline base64 charts) →
             persists call1_json (status THESIS_READY).
09:18–09:30 — Call 2 (decision): IntradayHunterWatcher hooks the NIFTY 1m candle-close in
             feed_manager._emit_candle → decision.run_call2(). Live open/gap/first-candles +
             prior decisions + opening charts → Claude → ENTER/WAIT/SKIP + single-direction
             basket. ENTER/SKIP finalizes the day; WAIT reschedules (recheck 1-2m) capped 09:30;
             09:30 backstop finalizes. Every Call 2 appended to call2_history (validation log).

LLM transport: `claude -p --input-format stream-json` single-turn with inline base64 chart
images (no API credits — subscription OAuth). Returns None → treated as SKIP (fail-safe).
Gated by settings.intraday_hunter_enabled. Fully exercisable in MARKET_MODE=simulated +
via POST /run-call1 / /run-call2. Validation (index direction ≠ option win-rate) deferred to
the live paper book — no capital until real-premium profit is confirmed. Spec: docs/ai/intraday-hunter-agent.md.
(v1 rows are intraday_hunter_runs.variant='v1'.)
```

### 4d. Intraday Hunter v2 (parallel PAPER strategy + learning loop — v1 untouched)
```
Kill switch: strategy_configs.is_active('intraday_hunter_v2') (≤15s, fails closed) · env INTRADAY_HUNTER_V2_ENABLED
00:00/06:00/08:00  teacher plan (yt-dlp → Hindi subs + keyframes → Claude) → ih_teacher_days
                   (08:30 alert if missing; Mac fallback: POST /intraday-hunter/teacher/ingest)
08:45   v2 Call 1 (Opus + prev-day charts + teacher plan + stop pools + graded lessons) → run(variant='v2')
09:10   ATM±2 CE/PE subscribe (re-centred 09:16:30) → feed_manager persists premium 1m candles
09:15–09:45  1-min option chain → oi_snapshots (opening OI flow)
NIFTY 1m candle close (feed_manager._emit_candle, wrapped, fire-and-forget):
   ├── ih_v2_watcher: Call 2 at candle+1 → 09:16 first, every minute, 09:25 deadline
   │     text-only facts (levels.py stop pools, OI flow, order flow, teacher side) → Sonnet-class
   │     ENTER → signals.py: BN ATM+OTM-1, NIFTY ATM, SENSEX ATM → strategy_runner._handle_signal
   │            → shadow (1 lot) + YOLO profile IH-v2 (2 lots/leg) — strike-aware, isolated from v1
   └── minute_log: ih_minute_log row per index (09:15–10:45 + while in position): features + arms
         (rule_a, plan_side, oi_flow_side, v1_state, v2_llm, gates, jev)
Ticks: fyers_ws_client keeps tot_buy_qty/tot_sell_qty/bid_size/ask_size → Redis + orderflow_tracker
trade_monitor (500ms): _check_ih_v2_baskets BEFORE the per-position loop — per (day, book):
   bid-valued basket MTM vs ±T (0.20×cost) → close ALL legs (BASKET_TARGET/STOP), round-number
   hold (0.9T, giveback 0.75T, 5 min), 11:30 BASKET_TIME; per-leg checks bypassed for v2
   (15:25 last-resort fallback); "Close v2 basket" → MANUAL_BASKET (shadow left as counterfactual)
09:30   alert if v1 or v2 has no decision · 09:20+ alert on missing/stale index candles
13:00/15:00  teacher live trade (frames → tesseract clock/positions → Claude) → ih_teacher_days
16:00   grade → ih_day_grades (market first-touch labels, teacher, v1/v2 real P&L, every arm's
        counterfactual basket replayed on the captured premium candles, gate what-ifs, lesson)
        + v1 outcome_played_out; lessons feed tomorrow's v2 Call 1; GET /v2/ledger
Sat 10:00  weekly review (Claude proposal → ih_weekly_reviews + Telegram; nothing auto-applied)
Spec: docs/ai/intraday-hunter-v2.md
```

### 5. Fyers Authentication Flow
```
Option A (Manual): User visits /api/v1/auth/fyers/login → Fyers OAuth → callback → token stored in Redis
Option B (Auto):   APScheduler job → fyers_auto_login.py → base64 credentials + TOTP → token stored in Redis
                   Runs on startup + periodic refresh
                   NOTE: Fyers v3 /api/v3/token returns a consent page (not an auth code) if the
                   app hasn't been browser-approved — requires one-time manual browser approval via
                   the generate-authcode URL. The error message includes the exact URL.
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
          (watchlist symbols use Fyers names; prices cached under Fyers alias)
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
- **Signal pipeline**: `candle close → build_market_context (incl. intraday_bias + global_cues) → strategy.evaluate → composite confidence (10-factor) → fire threshold gate → option/futures resolve → LLM overlay (±15 adj, ai_summary/rationale) → persist (with ai_* fields) → broadcast → [YOLO: auto_executor] + [shadow_executor fire-and-forget]`. Strategy 5 adds skip logging (flush `_pending_logs` after each evaluate) and cross-position count injection into strategy params. Signals are bare trading opportunities — **no lot sizing at signal time**. Sizing deferred to execution via `lot_sizing.py` (3 functions: shadow=1 lot, YOLO=strategy-aware, manual=same as YOLO). Margin estimated via `margin_calculator.py` (options=full premium, futures=contract value × tier %) and stored on Trade + Position at execution time.
- **Shadow agent** (`backend/app/agent/shadow_executor.py`): every signal → `Trade(source="SHADOW") + Position(is_shadow=True)`, no gating. All default queries exclude shadows (`WHERE source != 'SHADOW'` / `WHERE is_shadow = FALSE`). Dashboard Active Positions widget + P&L bar have a Real/Signal Test toggle. `trade_monitor` monitors shadow positions identically to real ones. See `docs/ai/shadow-agent.md`.
- **Phase 2 bias**: `intraday_bias` replaces the yesterday-only hard gate with a weighted live composite (6 factors). STRONG opposing bias still blocks; MODERATE/WEAK allows with confidence haircut.
- **Backtest harness** (`backend/app/backtest/`): common replay framework — historical 1m candles → `context_builder.build_historical_context` → `strategy.evaluate` → `exit_simulator` → `BacktestReport`. No DB writes, no WS events. Accurate mode uses live-traded option candles from `market_data_1m`; fast mode uses delta approximation.
- **Watchlist on WebSocket**: Watchlist symbols are subscribed on the Fyers WebSocket at startup and on add. Not just REST polling.
- **Market Simulator mode** (`MARKET_MODE=simulated`): enables full-stack testing outside market hours. Six touch points swap Fyers to a local simulator (port 8787): (1) `fyers_ws_client.py` singleton returns `SimulatedWSClient` instead of `FyersWSClient`, (2) `fyers_client.py` routes REST to simulator URL with no auth, (3) `symbol_master.py` fetches CSVs from simulator, (4) `candle_backfill.py` uses httpx to simulator instead of Fyers SDK, (5) `utils.py` bypasses `is_market_open`/`is_trading_day`/`is_past_close_deadline`, (6) `fyers_login_task.py` skips TOTP login. All 13+ import sites of `fyers_ws_client` work unchanged via module-level singleton swap.
