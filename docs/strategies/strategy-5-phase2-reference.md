# Strategy 5: Intraday Stock Futures — Phase 2 Implementation Reference

This document describes everything implemented in Phase 2. Phase 1 reference: `strategy-5-phase1-reference.md`.

---

## What Phase 2 Delivers

Phase 2 transforms Strategy 5 from a single-setup ORB scanner into a multi-setup intraday system with real data sources and refined conviction logic:

1. **3 New Sub-Setups** — VWAP Bounce (10:00–14:45), PDH/PDL Breakout (9:30–14:00), Gap Continuation (9:30–11:00), each with independent time guards separate from the phase state machine.

2. **Phase-Based Dispatch** — `evaluate()` is now a dispatch hub. Each phase has a priority-ordered list of setups; the first signal wins per symbol.

3. **Caution Zone Confirmation** — Signals during CAUTION_ZONE (11:30 AM–1:00 PM) are stored as pending. They only fire if the price holds on the next 5-minute candle. Pending signals expire after 10 minutes or on phase change.

4. **ORB Redis Persistence** — ORB levels survive backend restarts via `strat5:orb:{date}:{symbol}` Redis keys. Restored by `_enrich_strategy5_params()` before each evaluation.

5. **Phase Transition Logging** — Phase changes are logged to the agent activity feed and written to Redis `strat5:phase:{date}`.

6. **Multi-Factor Confidence Composite** — Replaces hardcoded `confidence=70.0`. Six weighted factors: volume quality (0.20), RVOL strength (0.20), Nifty bias alignment (0.15), phase timing (0.15), setup quality (0.15), screener rank (0.15).

7. **Full Position Sizing** — 2 lots requires ALL 5 conditions: RVOL ≥ 3.0, STRONG Nifty bias, screener score > 70, enhanced ORB (ORB setup only), aggressive briefing. India VIX ≥ 18 caps at 1 lot.

8. **Stock Futures OI Data** — Daily EOD snapshot at 3:25 PM IST captures OI for all ~180 F&O stock futures. Screener scores OI change as long buildup / short buildup / short covering / long unwinding.

9. **NSE Bhav Copy Data** — Daily 7:30 AM IST download of previous day's delivery percentage. Screener uses delivery % as a scoring factor (high delivery = institutional accumulation).

10. **Global Cues Mid-Day Shift** — Detects when crude oil shifts ≥ 2% or VIX shifts ≥ 2 absolute from the morning snapshot. Logs to agent feed with 60-minute debounce per shift type.

11. **Briefing Per-Setup Win Rates** — Morning briefing now shows win rates by setup type (ORB, VWAP_BOUNCE, PDH_PDL, GAP_CONTINUATION) instead of a single aggregate.

12. **Frontend Config: Setup Checkboxes** — ConfigPanel now exposes checkboxes for enabling/disabling individual sub-setups (ORB, VWAP Bounce, PDH/PDL, Gap Continuation).

---

## Files Created

### Backend — New Files
| File | Purpose |
|------|---------|
| `backend/app/indicators/atr.py` | ATR (Average True Range) for dynamic SL sizing across sub-setups |
| `backend/app/indicators/gap_analysis.py` | Gap detection (`detect_gap`) + continuation pattern (`is_gap_continuation`) |
| `backend/app/tasks/nse_bhav_copy_task.py` | NSE CM bhav copy: download ZIP, parse CSV, store delivery % in Redis |

### Backend — Tests
| File | Tests | Purpose |
|------|-------|---------|
| `backend/tests/test_indicators/test_atr.py` | 6 | ATR computation, insufficient data, flat market |
| `backend/tests/test_indicators/test_gap_analysis.py` | 12 | Gap detection thresholds, direction, continuation, edge cases |
| `backend/tests/test_tasks/test_nse_bhav_copy.py` | 20 | CSV parsing, URL construction, holiday skip, retry, Redis storage |
| `backend/tests/test_tasks/test_stock_futures_oi.py` | 8 | Stock futures OI fetch, batch splitting, resolution failures |

---

## Files Modified

### Backend
| File | Change |
|------|--------|
| `app/strategies/base.py` | Added `atr_5m: float \| None` and `today_open: float \| None` to `MarketContext` dataclass |
| `app/strategies/strategy_5_intraday_futures.py` | Major rewrite: 3 new sub-setups, phase dispatch, caution zone confirmation, `_compute_confidence()`, full `_compute_lots()`, ORB Redis persistence, phase logging |
| `app/services/strategy_runner.py` | ATR + today_open in `_build_market_context()`. Screener score, India VIX, ORB levels, global cues shift detection in `_enrich_strategy5_params()`. ORB + phase persistence in `_flush_strategy_logs()` |
| `app/services/morning_screener.py` | Added `_fetch_stock_oi_changes()` helper. OI scoring from oi_snapshots. Delivery % scoring from bhav copy. Briefing per-setup win rates via Signal.indicators |
| `app/services/strategy_params.py` | `enabled_setups` expanded to all 4 setups. Added `min_adr: 1.5` |
| `app/tasks/oi_snapshot_task.py` | Added `fetch_stock_futures_oi()` daily at 3:25 PM IST. New CronTrigger alongside existing 3-min interval |
| `app/models/oi_snapshot.py` | `option_type` now supports `"FUT"` (strike_price=0) for stock futures |
| `app/main.py` | Added bhav copy scheduler to startup/shutdown lifecycle |

### Frontend
| File | Change |
|------|--------|
| `frontend/src/components/intraday-futures/ConfigPanel.tsx` | Added `ALL_SETUPS` constant, `SETUP_LABELS`, checkbox rendering for enabled setups |

### Tests Modified
| File | Change |
|------|--------|
| `tests/test_strategies/test_intraday_futures.py` | +5 TestVWAPBounce, +5 TestPDHPDLBreakout, +6 TestGapContinuation, +6 TestPhaseDispatch, +5 TestCautionZoneConfirmation, +5 TestConfidence, rewrote TestPositionSizing (3→11 tests), added MagicMock import, extended `_make_ctx()` |
| `tests/test_services/test_morning_screener.py` | +5 TestOIScoring, +4 TestDeliveryScoring |

---

## Key Architecture Decisions

### Phase Dispatch

`evaluate()` is now a dispatch hub instead of calling ORB directly:

```
phase → _get_dispatch_order(phase) → priority-ordered setup list
                                   → filter by enabled_setups param
                                   → _dispatch_setup(name) → method call
                                   → first signal wins
```

Dispatch order per phase:
- MORNING_ACTIVE: `[ORB, GAP_CONTINUATION, PDH_PDL, VWAP_BOUNCE]`
- CAUTION_ZONE: `[PDH_PDL, VWAP_BOUNCE]`
- AFTERNOON: `[VWAP_BOUNCE, PDH_PDL]`

### Sub-Setup Time Guards (Independent of Phases)

Each sub-setup has its own time window enforced internally via `now_ist().time()`. These are separate from phase boundaries:

| Setup | Time Window | Reason |
|-------|------------|--------|
| ORB | 9:30–11:00 | ORB breakouts are morning-only patterns |
| Gap Continuation | 9:30–11:00 | Gaps are a morning phenomenon; fade after 11:00 |
| VWAP Bounce | 10:00–14:45 | Needs 30+ min of data to establish trend; active all day |
| PDH/PDL Breakout | 9:30–14:00 | Late-day PDH/PDL breakouts are unreliable |

This means a setup can be in the dispatch list for a phase but still return None because its own time guard says "too late." Example: PDH_PDL is dispatched during AFTERNOON (13:00–14:45) but its time guard stops it at 14:00.

### Caution Zone Confirmation

During CAUTION_ZONE (11:30 AM–1:00 PM), signals are not emitted immediately:

1. Signal is stored in `_pending_confirmations[symbol]` with timestamp and candle count
2. On next `evaluate()` call for the same symbol, `_check_pending_confirmations()` runs:
   - If > 10 minutes old → discard
   - If phase changed away from CAUTION_ZONE → discard
   - If a new 5m candle has formed AND price holds above/below breakout level → emit signal
   - If price doesn't hold → discard and log skip
3. Outside CAUTION_ZONE, signals fire immediately (no pending)

### Multi-Factor Confidence Composite

Replaces `confidence=70.0` across all 4 sub-setups:

| Factor | Weight | Scoring |
|--------|--------|---------|
| volume_quality | 0.20 | Breakout candle vol vs 2× avg: `min(1.0, vol / (avg * 2))` |
| rvol_strength | 0.20 | `max(0, min(1.0, (rvol - threshold) / threshold))` |
| nifty_bias_alignment | 0.15 | STRONG=1.0, MODERATE=0.7, WEAK=0.4, absent=0.0 |
| phase_timing | 0.15 | MORNING=1.0, AFTERNOON=0.7, CAUTION=0.4 |
| setup_quality | 0.15 | Enhanced ORB=1.0, ORB=0.8, PDH_PDL=0.7, VWAP_BOUNCE=0.7, GAP=0.6 |
| screener_rank | 0.15 | `min(1.0, score / 80)` |

Result: `sum(factor × weight) × 100`, clamped [0, 100]. Deterministic — same inputs always produce same output.

### Full Position Sizing

2 lots requires ALL 5 conditions simultaneously:
1. RVOL ≥ 3.0
2. Nifty bias `strength == "STRONG"`
3. Screener composite score > 70
4. Enhanced ORB (only for ORB setup — price also crosses PDH/PDL; other setups skip this)
5. Briefing approach == `"aggressive"`

Additional constraints:
- India VIX ≥ 18 → always 1 lot (checked first, short-circuits)
- Capped by `_briefing_max_lots` (morning briefing's recommendation)
- Hard max: `max_lots = 2` on the strategy class

### OI Scoring (from oi_snapshots)

`_fetch_stock_oi_changes()` queries the `oi_snapshots` table for `option_type="FUT"` rows, compares the two most recent daily snapshots to compute OI change. Combined with price direction:

| OI Direction | Price Direction | Interpretation | Score |
|-------------|----------------|----------------|-------|
| OI ↑ | Price ↑ | Long buildup | 100 |
| OI ↑ | Price ↓ | Short buildup | 50 |
| OI ↓ | Price ↑ | Short covering | 30 |
| OI ↓ | Price ↓ | Long unwinding | 20 |

Falls back to 50 when no OI data is available.

### Delivery % Scoring (from bhav copy)

NSE bhav copy provides per-stock delivery percentage (% of traded volume delivered). Higher delivery indicates institutional accumulation:

```python
delivery_score = min(100, max(0, (delivery_pct - 10) / 40 * 100))
```

- 10% delivery → score 0
- 30% delivery → score 50
- 50%+ delivery → score 100

Falls back to 50 when bhav copy is unavailable.

### Global Cues Mid-Day Shift

`_enrich_strategy5_params()` compares current crude oil and US VIX against the morning snapshot (`strat5:global_cues:{today}`):

- Crude shift ≥ 2%: logs via `_append_agent_log(today, "GLOBAL", message)`
- VIX shift ≥ 2 absolute: same
- Debounced: Redis key `strat5:global_shift_logged:{today}:{shift_key}` with 60-min TTL prevents duplicate logs

---

## New Redis Keys

| Key | Content | TTL |
|-----|---------|-----|
| `strat5:orb:{date}:{symbol}` | ORB high/low (JSON) — persisted for restart recovery | 90 days |
| `strat5:phase:{date}` | Current phase string — written on transition | 90 days |
| `strat5:global_shift_logged:{date}:{type}` | Debounce flag for mid-day shift logging | 60 min |
| `nse:bhav_copy:{date}` | Per-stock delivery %, close, prev_close (JSON) | 90 days |

---

## Strategy Parameters Updated

```python
INTRADAY_FUTURES_DEFAULTS = {
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
    "min_adr": 1.5,                                                  # NEW
    "enabled_setups": ["ORB", "VWAP_BOUNCE", "PDH_PDL", "GAP_CONTINUATION"],  # EXPANDED
}
```

---

## Enriched Strategy Params (injected by `_enrich_strategy5_params`)

| Param Key | Source | Used By |
|-----------|--------|---------|
| `_rvol_profile` | Redis `strat5:rvol_baseline:{symbol}` | `_get_current_rvol()` |
| `_active_position_count` | DB query (Position table) | `_check_cross_position_risks()` |
| `_daily_trade_count` | DB query (Trade table) | `_check_cross_position_risks()` |
| `_nifty_bias` | Computed from Nifty candle buffer | `_compute_lots()`, `_compute_confidence()` |
| `_briefing_approach` | Redis `strat5:morning_briefing:{date}` | `_compute_lots()` |
| `_briefing_max_lots` | Redis `strat5:morning_briefing:{date}` | `_compute_lots()` |
| `_briefing_sector_bias` | Redis `strat5:morning_briefing:{date}` | (future use) |
| `_briefing_sector_avoid` | Redis `strat5:morning_briefing:{date}` | (future use) |
| `_screener_score` | Redis `strat5:watchlist:{date}` | `_compute_lots()`, `_compute_confidence()` — **NEW** |
| `_india_vix` | Redis `price:INDIA VIX` | `_compute_lots()` VIX cap — **NEW** |
| ORB levels | Redis `strat5:orb:{date}:{symbol}` | `load_orb_from_redis()` — **NEW** |

---

## Sub-Setup Implementation Details

### VWAP Bounce (`_check_vwap_bounce`)

- **Time:** 10:00 AM – 2:45 PM
- **Phases:** MORNING_ACTIVE (after 10:00), CAUTION_ZONE, AFTERNOON
- **Conditions:**
  1. Last 6+ 5m candle closes all on same side of VWAP (trend established)
  2. Price within 0.2% of VWAP (pullback to VWAP)
  3. Bullish/bearish reversal candle (`is_bullish_reversal()` / `is_bearish_reversal()`)
  4. Reversal candle volume > average volume
  5. R:R ≥ 1.5
- **SL:** Below VWAP by `max(price × 0.003, 0.5 × ATR_5m)`; fallback 0.3% if no ATR
- **Target:** Swing high/low from `market_levels.py`; fallback 1.5× risk

### PDH/PDL Breakout (`_check_pdh_pdl_breakout`)

- **Time:** 9:30 AM – 2:00 PM
- **Phases:** MORNING_ACTIVE, CAUTION_ZONE, AFTERNOON (time-guarded to 2:00 PM)
- **Conditions:**
  1. Latest 5m candle close > PDH (BUY_FUT) or < PDL (SELL_FUT)
  2. Breakout candle volume > 1.5× average volume
  3. VWAP alignment (longs above VWAP, shorts below)
  4. R:R ≥ 1.5
- **SL:** Below breakout level by `max(price × 0.005, 0.5 × ATR_5m)`
- **Target:** Measured move = `PDH - PDL` projected from breakout point

### Gap Continuation (`_check_gap_continuation`)

- **Time:** 9:30 AM – 11:00 AM
- **Phases:** MORNING_ACTIVE only (time-guarded to 11:00)
- **Conditions:**
  1. Gap ≥ 0.5% detected via `detect_gap(today_open, prev_close)`
  2. Gap holds: all 5m closes after first 3 candles stay above/below gap level
  3. Opening 15-min volume (first 3 five-min candles) > 2× expected average
  4. R:R ≥ 1.5
- **SL:** Below gap fill level (PDC) with `max(price × 0.003, 0.5 × ATR_5m)` buffer
- **Target:** Gap size projected forward; fallback 1.5× risk

### ORB Breakout (Enhanced from Phase 1)

- **Time guard added:** 9:30 AM – 11:00 AM (new in Phase 2)
- **Enhanced ORB:** If breakout also crosses PDH (long) or PDL (short), sets `indicators["enhanced_orb"] = True` — counts toward 2-lot sizing
- **`indicators["setup_type"] = "ORB"`** added for per-setup tracking

---

## Scheduled Tasks Added/Modified

| Task | Schedule | Purpose |
|------|----------|---------|
| `fetch_stock_futures_oi` | Daily 3:25 PM IST (CronTrigger) | EOD OI snapshot for ~180 stock futures |
| `fetch_bhav_copy` | Daily 7:30 AM IST (CronTrigger) | Previous day's delivery % from NSE |
| `oi_snapshot_scheduler` | Existing + new daily job | Now includes stock futures OI alongside existing 3-min index option OI |

---

## Phase 1 Simplifications Resolved

| Area | Phase 1 | Phase 2 Resolution |
|------|---------|-------------------|
| Position sizing | 2 lots if RVOL ≥ 3.0 only | ALL 5 conditions + VIX cap |
| Confidence score | Hardcoded 70.0 | 6-factor weighted composite (0–100) |
| ORB levels | In-memory only | Persisted to Redis, restored on restart |
| Phase persistence | Computed on-the-fly | Written to Redis on transition |
| OI change (screener) | Hardcoded at 50 | Computed from oi_snapshots FUT rows |
| Delivery % (screener) | Hardcoded at 50 | Parsed from NSE bhav copy |

---

## What Phase 2 Does NOT Include (Deferred to Phase 3)

### Phase 3 — Polish + Analytics
- Historical day review (date picker + data retrieval from Redis/DB for past days)
- Agent activity log filtering by category
- Per-setup performance tracking (win rate, P&L by setup type over time)
- Watchlist stock cards with live ORB levels and RVOL updates
- Sector momentum scoring in screener (currently hardcoded at 50)

---

## Test Count

Total: 716 tests (up from 621 in Phase 1, net +95)

New test classes:
- `TestATR` (6) — ATR computation, edge cases
- `TestGapDetection` (6) + `TestGapContinuation` (6) — gap analysis
- `TestVWAPBounce` (5) — bounce signal generation, skip conditions
- `TestPDHPDLBreakout` (5) — breakout/breakdown, filters
- `TestGapContinuation` (6) — gap continuation signals
- `TestPhaseDispatch` (6) — dispatch order, disabled setups, time guards
- `TestCautionZoneConfirmation` (5) — pending storage, confirm, discard
- `TestConfidence` (5) — factor contributions, determinism
- `TestPositionSizing` (11, rewritten from 3) — all 5 conditions, VIX cap, briefing cap
- `TestOIScoring` (5) — long/short buildup, covering, unwinding
- `TestDeliveryScoring` (4) — high/mid/low delivery, fallback
- `TestParseBhavCsv` (7) + `TestBuildBhavUrl` (3) + `TestPreviousTradingDay` (4) + `TestFetchBhavCopy` (4) + `TestGetBhavCopy` (2) — bhav copy task
- `TestFetchStockFuturesOI` (8) — stock futures OI task
