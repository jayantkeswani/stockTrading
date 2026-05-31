# Strategy 5: Intraday Stock Futures — Phase 1 Implementation Reference

This document describes everything implemented in Phase 1. Use it as context when starting Phase 2 implementation.

---

## What Phase 1 Delivers

A fully functional daily agent workflow with the ORB (Opening Range Breakout) sub-setup:

1. **Morning Briefing (8:00 AM)** — Agent reviews yesterday's trades and last 5 days' performance, feeds to LLM for synthesis. Produces "today's approach" (aggressive/normal/conservative), sector bias, flags. Stored in Redis, displayed on dedicated page.

2. **Global Cues Snapshot (8:00 AM)** — Packages existing `global_market_task` data (GIFT Nifty, US markets, crude, USD/INR, VIX) into a Strategy 5-specific Redis snapshot. VIX > 20 halts the agent.

3. **Morning Screener (8:30 AM)** — 3-stage pipeline:
   - Stage 1: Quantitative scoring of ~180 F&O stocks (8 weighted factors)
   - Stage 2: News & sentiment scan on top ~20 candidates via existing `NewsSentimentAgent` (Gemini)
   - Stage 3: LLM confidence check (single batched call) to filter final watchlist
   - Output: 15-20 stocks in Redis, subscribed on Fyers WebSocket

4. **Phase State Machine** — 7 time-based phases controlling which setups are active:
   - PRE_MARKET → ORB_FORMING → MORNING_ACTIVE → CAUTION_ZONE → AFTERNOON → CLOSING → DONE

5. **ORB Sub-Setup (9:30 AM – 11:00 AM)** — Breakout above/below 15-minute opening range with volume confirmation, VWAP filter, Nifty bias gate, chart-based SL/target, R:R >= 1.5 validation

6. **3-Stage Trailing SL** — Original → breakeven (0.5% gain) → progressive trail (0.3% below high water mark)

7. **Cross-Position Awareness** — Strategy-scoped soft enforcement: max 3 positions, 5 trades/day, 3% drawdown, sector dedup. Signals flagged, never suppressed.

8. **Dedicated Frontend Page** (`/intraday-futures`) — Day status bar, global cues + morning briefing, watchlist table, agent activity log, config panel

9. **Trades Page Filter** — Strategy filter dropdown on existing trades page

---

## Files Created

### Backend — New Files
| File | Purpose |
|------|---------|
| `backend/app/indicators/rvol.py` | RVOL: time-of-day normalized volume (20-day 5-min profile) |
| `backend/app/indicators/adr.py` | ADR: average daily range over 20 days |
| `backend/app/services/morning_screener.py` | Morning briefing + 3-stage screener (quant + news + LLM) |
| `backend/app/strategies/strategy_5_intraday_futures.py` | Strategy class: phase machine, ORB setup, cross-position checks, dynamic symbols |
| `backend/app/api/v1/intraday_futures.py` | API router: watchlist, agent log, global cues, briefing, screener, pause/resume |
| `backend/app/data/sector_classification.json` | Static F&O stocks → sectors mapping (~180 stocks, ~15-18 sectors) |
| `backend/alembic/versions/xxxx_add_high_since_entry_seed_strat5.py` | Migration: `high_since_entry` on positions + seed strategy_configs row |

### Backend — Tests
| File | Purpose |
|------|---------|
| `backend/tests/test_indicators/test_rvol.py` | RVOL profile building, computation, edge cases |
| `backend/tests/test_indicators/test_adr.py` | ADR computation, threshold checks |
| `backend/tests/test_services/test_morning_screener.py` | Screener scoring, news integration, LLM enrichment, briefing |
| `backend/tests/test_strategies/test_intraday_futures.py` | Phase machine, ORB detection, filters, cross-position checks, sizing |
| `backend/tests/test_agent/test_trade_monitor.py` | Extended: intraday trailing (breakeven, progressive, HWM, SL-never-moves-down) |

### Frontend — New Files
| File | Purpose |
|------|---------|
| `frontend/src/app/intraday-futures/page.tsx` | Dedicated Strategy 5 page |
| `frontend/src/components/intraday-futures/DayStatusBar.tsx` | Phase, bias, VIX, stats, controls |
| `frontend/src/components/intraday-futures/GlobalCues.tsx` | Overnight data + morning briefing |
| `frontend/src/components/intraday-futures/Watchlist.tsx` | Screener output table |
| `frontend/src/components/intraday-futures/AgentLog.tsx` | Chronological activity feed |
| `frontend/src/components/intraday-futures/ConfigPanel.tsx` | Strategy params editor |

---

## Files Modified

| File | Change |
|------|--------|
| `backend/app/core/enums.py` | Added `INTRADAY_FUTURES = "intraday_futures"` to `StrategyName` |
| `backend/app/models/position.py` | Added `high_since_entry` nullable Numeric field |
| `backend/app/services/strategy_params.py` | Added `INTRADAY_FUTURES_DEFAULTS` dict + registered in `_STRATEGY_DEFAULTS` |
| `backend/app/strategies/base.py` | Added `async get_symbols() -> list[str] | None` method (default returns None) |
| `backend/app/strategies/registry.py` | Imported and registered `IntradayFuturesStrategy` |
| `backend/app/services/strategy_runner.py` | Modified `get_auto_strategies_for_symbol()` to check `strategy.get_symbols()` for dynamic symbol support |
| `backend/app/agent/trade_monitor.py` | Extended trailing SL to INTRADAY positions (when `trailing_sl_enabled=True`), added progressive trail + HWM tracking, SHORT position support (direction-aware SL/target/PnL/trailing), skip logging flush |
| `backend/app/tasks/morning_workflow_task.py` | Added ORB level logging (9:31 AM) + EOD summary (3:15 PM) scheduled jobs |
| `backend/app/api/router.py` | Added `include_router` for `intraday_futures` |
| `frontend/src/lib/constants.ts` | Added `intraday_futures: "Intraday Futures"` to `STRATEGY_LABELS` |
| `frontend/src/lib/types.ts` | Added `WatchlistItem`, `AgentLogEntry`, `DailyStats`, `Phase`, `GlobalCues`, `MorningBriefing` types |
| `frontend/src/lib/api.ts` | Added API functions for all Strategy 5 endpoints |
| `frontend/src/components/layout/Sidebar.tsx` | Added nav item for `/intraday-futures` |
| Trades page component | Added strategy filter dropdown |

---

## Key Architecture Decisions

### Dynamic Symbol Selection
Strategy 5 doesn't use static `strategy_configs.symbols`. Instead:
- Morning screener writes watchlist to Redis `strat5:watchlist:{date}`
- `IntradayFuturesStrategy.get_symbols()` reads from Redis (cached in-memory 60s)
- `get_auto_strategies_for_symbol()` in `strategy_runner.py` calls `get_symbols()` for strategies where the symbol isn't found in DB config
- Screener subscribes watchlist stocks on Fyers WebSocket after storing them

### Phase State Machine
Purely time-based, deterministic. Stored in Redis `strat5:phase:{date}`. Strategy's `evaluate()` checks phase before running any setup logic.

### Trailing SL Extension
`trade_monitor.py` now supports trailing for INTRADAY positions when `trailing_sl_enabled: true` in strategy_params. Uses fallback chain: `trailing_sl_breakeven_pct` → `trailing_sl_activation_pct` → constant. Progressive trail only activates when `trailing_sl_trail_pct` is in params. CAN SLIM POSITIONAL behavior is completely unchanged.

### SHORT Position Support
Direction detection uses `target_price` vs `entry_price` (target below entry = SHORT). This is robust because target is immutable during trailing — unlike `stop_loss` which moves during breakeven/progressive trail. Direction-aware logic covers: SL hit, target hit, unrealized PnL, HWM tracking (lowest price for shorts), breakeven (SL moves down to entry), progressive trail (SL = LWM × (1 + trail%)), and PnL calculation on close.

### Skip Logging
Strategy's sync `evaluate()` can't write to async Redis, so uses `_pending_logs` list + `drain_pending_logs()`. `strategy_runner._flush_strategy_logs()` drains after each evaluate call and writes to Redis `strat5:agent_log:{date}`.

### Cross-Position Count Injection
`strategy_runner._enrich_strategy5_params()` queries DB for active position count and daily trade count, injects as `_active_position_count` and `_daily_trade_count` into strategy params before evaluate.

### Morning Workflow Scheduler
`tasks/morning_workflow_task.py` runs 4 scheduled jobs: briefing (8:00), screener (8:30), ORB level logging (9:31), EOD summary (15:15). All are idempotent per day and skip non-trading days.

### Cross-Position Awareness
All risk checks scoped to `strategy_name = 'intraday_futures'`. Soft enforcement: signals get `risk_warnings` array but are never suppressed. Shadow trades always fire. YOLO auto-execution pauses when warnings present.

### Position Sizing
1-2 lots only (hard cap `max_lots = 2`). Phase 1 simplified rule: 2 lots if RVOL >= 3.0, else 1 lot, capped by morning briefing's `max_lots_recommendation`. Full spec requires additional checks (STRONG bias + score > 70 + enhanced ORB) — deferred to Phase 2.

### Briefing → Strategy Feedback Loop
Morning briefing output is not display-only — it feeds back into live strategy behavior:
- `strategy_runner._enrich_strategy5_params()` reads `strat5:morning_briefing:{date}` from Redis
- Injects `_briefing_approach`, `_briefing_max_lots`, `_briefing_sector_bias`, `_briefing_sector_avoid` into strategy params
- `IntradayFuturesStrategy._compute_lots()` uses `_briefing_max_lots` as a cap (e.g., briefing says "conservative, max 1 lot" → strategy never sizes at 2)

---

## AI/LLM Integration Details

Phase 1 has 3 AI integration points, all in `morning_screener.py`. Post-implementation, prompts were enriched with additional data sources and restructured with decision-framework rules.

### Morning Briefing (`_synthesize_briefing`)

**Data gathered** (`_gather_briefing_data`):
- Per-trade details from yesterday (symbol, direction, entry/exit prices, PnL, exit reason) — not just aggregate stats
- Per-sector P&L breakdown (via `get_sector()` helper)
- Per-setup win rates (ORB, VWAP_BOUNCE, etc.)
- Drawdown streak (consecutive losing trades at end of day)
- VIX 5-day trend direction (computed from `GlobalMarketSnapshot` table history)
- Today's global cues snapshot (GIFT Nifty, US markets, crude, VIX level)
- Yesterday's agent log summary (`_summarize_agent_log`): counts of SIGNAL/SKIP/TRADE/RISK categories + top skip reasons (RVOL, ADR, VWAP misalignment, etc.)

**System prompt**: Decision-framework style with explicit rules:
- 3+ consecutive losses → must recommend "conservative"
- VIX rising 3+ days in a row → must recommend "conservative"
- Sector-data-driven bias (avoid sectors that lost money, lean into winners)
- `sector_avoid` output field for sectors to skip today

**Fallback**: If LLM call fails, uses drawdown streak to determine approach (conservative if 3+ losses, else normal).

### News Sentiment — Screener Stage 2 (`_stage2_news_sentiment`)

Uses a screener-specific `_ScreenerNewsAgent` subclass that narrows the search window to **48 hours** (vs 30 days in the research module). This prevents irrelevant old news from polluting intraday screener decisions. Runs ~20 parallel Gemini Google Search grounded calls.

### LLM Confidence Check — Screener Stage 3 (`_stage3_llm_confidence`)

**4 parallel enrichment calls** via `asyncio.gather`:
1. **Global cues** — today's `strat5:global_cues:{date}` from Redis
2. **Morning briefing output** — today's approach, sector bias, flags
3. **Stock fundamentals** — from `stock_fundamentals` table (market cap, EPS growth, ROE, FII/MF holdings)
4. **Trade history** — last 10 Strategy 5 trades per candidate symbol (win/loss pattern, avg PnL)

**System prompt**: Calibration criteria defining what HIGH/MEDIUM/LOW confidence means:
- HIGH: strong fundamentals + favorable news + global alignment + no recent losses
- MEDIUM: mixed signals, some risk factors
- LOW: adverse news, poor fundamentals, or repeated losses on this stock

**Correlated sector dedup**: Response schema includes `correlated_groups` field. LLM identifies same-sector candidates that would create concentrated exposure. Handling code drops all but the highest-rated stock per correlated group.

---

## Redis Key Patterns (all with 90-day TTL)

| Key | Content |
|-----|---------|
| `strat5:watchlist:{date}` | Morning screener output: ranked list with scores, bias, factors, news sentiment |
| `strat5:orb:{date}:{symbol}` | ORB high/low for a stock |
| `strat5:rvol_baseline:{symbol}` | 20-day avg volume by 5-min time bucket |
| `strat5:phase:{date}` | Current market phase enum |
| `strat5:daily_stats:{date}` | Trade count, P&L, drawdown, positions open |
| `strat5:agent_log:{date}` | Chronological agent activity entries |
| `strat5:global_cues:{date}` | Morning global cues snapshot |
| `strat5:morning_briefing:{date}` | AI morning briefing (yesterday recap + today's approach) |
| `strat5:agent_status:{date}` | RUNNING / PAUSED / HALTED / DONE |

---

## API Endpoints

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/v1/intraday-futures/watchlist?date=` | Screener output |
| GET | `/api/v1/intraday-futures/agent-log?date=` | Agent activity log |
| GET | `/api/v1/intraday-futures/global-cues?date=` | Global overnight data |
| GET | `/api/v1/intraday-futures/morning-briefing?date=` | AI morning briefing |
| GET | `/api/v1/intraday-futures/daily-stats?date=` | Daily P&L and stats |
| GET | `/api/v1/intraday-futures/phase` | Current phase |
| POST | `/api/v1/intraday-futures/screener/run` | Re-run screener manually |
| POST | `/api/v1/intraday-futures/briefing/run` | Re-run briefing manually |
| POST | `/api/v1/intraday-futures/agent/{action}` | Pause/resume agent |

---

## Strategy Parameters (`INTRADAY_FUTURES_DEFAULTS`)

```python
{
    "trailing_sl_enabled": True,
    "trailing_sl_breakeven_pct": 0.5,
    "trailing_sl_trail_pct": 0.3,
    "min_confidence_to_persist": 30.0,
    "min_confidence_for_shadow": 45.0,
    "min_confidence_for_execution": 60.0,
    "max_daily_drawdown_pct": 3.0,
    "max_simultaneous_positions": 3,
    "max_trades_per_day": 5,
    "rvol_threshold": 1.5,
    "rvol_caution_zone_threshold": 2.5,
    "enabled_setups": ["ORB"],
}
```

---

## Phase 1 Simplifications (Intentional)

These are deliberate simplifications in Phase 1 vs the full spec. Working code, but not yet at full spec fidelity:

| Area | Phase 1 Behavior | Full Spec |
|------|-------------------|-----------|
| Position sizing | 2 lots if RVOL >= 3.0, else 1 lot; capped by briefing `max_lots_recommendation` | Also requires STRONG bias + score > 70 + enhanced ORB for 2 lots |
| Confidence score | Hardcoded at 70.0 for all ORB signals | Should use multi-factor composite (volume + RVOL + bias strength + phase) |
| ORB levels | Stored in-memory on strategy instance | Full spec: persist to Redis `strat5:orb:{date}:{symbol}` (survives restarts during ORB_FORMING) |
| Phase persistence | Computed on-the-fly from IST clock | Full spec: cache in Redis `strat5:phase:{date}` with transition logging |
| OI change (screener) | Hardcoded at 50 in quant scoring | Phase 2: compute from `oi_snapshots` table (latest vs previous day) |
| Delivery % (screener) | Hardcoded at 50 in quant scoring | Phase 2: parse from NSE bhav copy download |

---

## What Phase 1 Does NOT Include (Deferred)

### Phase 2 — Additional Sub-Setups + Data Sources + Sizing
- VWAP Bounce sub-setup
- PDH/PDL Breakout sub-setup
- Gap Continuation sub-setup
- `indicators/gap_analysis.py` (gap detection + continuation)
- Daily OI change computation for screener scoring
- Delivery percentage data source (NSE bhav copy download)
- Global cues mid-day shift logging (agent logging when crude/VIX shift significantly mid-day)
- Full position sizing logic (bias + score + enhanced ORB checks for 2-lot conviction)
- Multi-factor confidence composite for ORB signals

### Phase 3 — Polish + Analytics
- Historical day review (date picker + Redis/DB data retrieval for past days) — **frontend scaffolding exists** (date picker in DayStatusBar, date prop flows to all components), but Redis data for past days depends on the system having run on those days
- Agent activity log filtering by category (SIGNAL, TRADE, SKIP, etc.)
- Per-setup performance tracking (win rate, P&L by setup type over time)
- Watchlist stock cards with live ORB levels and RVOL updates

---

## Existing Infrastructure Reused (Not Rebuilt)

| Component | Module | How Strategy 5 Uses It |
|-----------|--------|----------------------|
| Intraday bias | `indicators/intraday_bias.py` | Nifty alignment soft gate (6-factor composite) |
| Global market data | `tasks/global_market_task.py` | Morning global cues (already fetches every 15 min) |
| Futures resolver | `services/futures_resolver.py` | Stock → nearest futures contract mapping |
| News sentiment | `research/agents/news_sentiment.py` | Screener Stage 2 (Gemini Google Search grounding) |
| LLM client | `research/llm_client.py` | Morning briefing + screener Stage 3 |
| Shadow executor | `agent/shadow_executor.py` | Paper trades from signals (unchanged) |
| Trade monitor | `agent/trade_monitor.py` | SL/target/trailing/time exit (extended for progressive trail) |
| Confidence scoring | `indicators/confidence.py` | Pattern reused for Strategy 5 signal confidence |
| All indicators | `indicators/*.py` | VWAP, PDH/PDL, CPR, volume, candle patterns, RS, OI |
| F&O stock list | `data_sources/nse_client.py` | `get_fo_lot_sizes()` for screener universe |
| Position sizing | `services/position_sizing.py` | `vix_to_multiplier()` for VIX-based lot adjustment |
| Strategy params | `services/strategy_params.py` | Per-strategy defaults + DB override + caching pattern |
| WebSocket | `websocket/manager.py` | Signal broadcast (unchanged) |
| Time exit | `core/utils.py` | `is_past_close_deadline()` at 3:25 PM |
