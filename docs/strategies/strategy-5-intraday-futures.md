# Strategy 5: Intraday Stock Futures

**Status:** IN DEVELOPMENT  
**File:** `backend/app/strategies/strategy_5_intraday_futures.py`  
**Instruments:** NSE F&O stock futures — BUY_FUT / SELL_FUT  
**Holding type:** INTRADAY (square off by 3:15 PM)

---

## Overview

An AI-agent-driven intraday strategy for trading stock futures on NSE. Unlike other strategies where the user triggers scans or the strategy evaluates on candle close for a fixed symbol list, Strategy 5 operates as an autonomous daily workflow — from morning research through signal generation to end-of-day journaling — on its own dedicated frontend page (`/intraday-futures`).

Key characteristics:

1. **Agent-driven lifecycle** — A background AI agent runs autonomously through the trading day following a phase-based schedule
2. **Dynamic symbol selection** — Picks its own symbols each morning via a 3-stage screener (quantitative scoring → news sentiment → LLM confidence). Overrides `get_symbols()` to return dynamic watchlist from Redis instead of static DB config
3. **Intra-day state** — Carries daily state (ORB levels, RVOL baselines, trade count, drawdown) in Redis, resets each day
4. **Cross-position awareness** — Checks open Strategy 5 positions before generating signals (sector dedup, position limits). Scoped to this strategy only
5. **Dedicated frontend page** — Agent activity log with category filters, watchlist with ORB levels, global cues, setup performance, day status bar
6. **Day-over-day memory** — Reviews yesterday's performance and recent trends via LLM morning briefing, adjusting today's approach

Signals also appear on the main homepage scanner with strategy-aware rendering (setup type, phase, RVOL, risk warnings).

**Target move:** 1-2% intraday

---

## Phase State Machine

The strategy's `evaluate()` method checks the current phase before deciding which sub-setups to run. Phases are time-based and deterministic. The current phase is stored in Redis (`strat5:phase:{date}`) for frontend display.

| Phase | Time | Active Setups | Notes |
|---|---|---|---|
| `PRE_MARKET` | before 9:15 AM | None | Screener has already run |
| `ORB_FORMING` | 9:15 – 9:30 AM | None | Record high/low per stock, compute opening RVOL |
| `MORNING_ACTIVE` | 9:30 – 11:30 AM | ORB, Gap Continuation, PDH/PDL, VWAP Bounce | Prime trading window |
| `CAUTION_ZONE` | 11:30 AM – 1:00 PM | PDH/PDL, VWAP Bounce | RVOL >= 2.5, breakout confirmation required |
| `AFTERNOON` | 1:00 – 2:45 PM | PDH/PDL, VWAP Bounce | ORB + Gap expired |
| `CLOSING` | 2:45 – 3:15 PM | None | Manage existing positions, force-close at 3:15 PM |
| `DONE` | after 3:15 PM | None | Log day summary, halt |

**Caution Zone:** Instead of a hard block on signals, the CAUTION_ZONE raises the bar — RVOL threshold increases from 1.5 to 2.5, breakout confirmation required (the breakout candle must close beyond the level AND the next candle must hold), and confidence is reduced. Signals generated during caution zone are flagged with `caution_zone: true`.

---

## Agent Daily Workflow

### 8:00 AM — Morning Briefing

The agent reviews recent history to inform today's approach:

1. **Yesterday's recap** — trades, wins/losses, net P&L, which setups worked
2. **5-day performance trend** — cumulative P&L, win rate by setup type, best/worst sectors, drawdown streak detection
3. **Market regime** — VIX trend, Nifty trending vs range-bound, sector themes
4. **LLM synthesis** — approach recommendation (aggressive/normal/conservative), setup priority adjustments, sector bias, flags (e.g., "3-day losing streak — recommend 1 lot max today")

The briefing adjusts confidence scores and may suggest parameter tweaks, but hard rules (RVOL, R:R, risk limits) still govern signal generation. Stored in Redis (`strat5:morning_briefing:{date}`).

### 8:00 AM — Global Cues

Collects overnight data from Redis (populated by existing `global_market_task` via yfinance every 15 min):

| Data Point | Source | Impact |
|---|---|---|
| GIFT Nifty % change | yfinance (SGX Nifty futures) | Gap > 1% → flag "volatile open expected" |
| US markets (S&P, Nasdaq, Dow close) | yfinance | Risk-on / risk-off sentiment |
| Crude oil price + % change | yfinance (CL=F) | Impacts energy sector (ONGC, RELIANCE, IOC, BPCL) |
| USD/INR movement | yfinance (USDINR=X) | Impacts IT sector (TCS, INFY, WIPRO) |
| India VIX | Redis `indicator:global:india_vix` | VIX > 20 → halt all stock futures trades for the day |

If VIX > 20, agent sets status to HALTED. Stored in Redis (`strat5:global_cues:{date}`).

**Mid-day shift detection:** If crude moves ±2% or VIX shifts ±2 points from the morning snapshot, the agent logs the shift (debounced 60 min). The bias system automatically picks this up via the `global_overnight_score` component.

### 8:30 AM — Morning Screener

Three-stage pipeline (see Morning Screener section below).

### 9:08 AM — Pre-Open Reassessment

After NSE pre-open session ends (9:07 AM), the agent gap-adjusts the watchlist using live pre-open data:

- **Relative gap** = stock gap - Nifty gap. |relative_gap| > 1% → force override bias. 0.5-1% → nudge bias
- **Live VIX** — refreshed from pre-open (the 8:30 AM value was previous-close)
- **Nifty gap** — stored in global cues for strategy consumption
- **Re-ranking** — stocks whose gap aligns with their bias get a score bonus (up to +5 points); watchlist re-sorted

Pure math — no LLM calls. Implemented in `run_preopen_reassessment()` in `morning_screener.py`.

### 9:15 AM — ORB Formation

Records high and low of the first 15 minutes per watchlist stock. Computes opening RVOL from 20-day volume profile. No signals generated. ORB levels stored in Redis (`strat5:orb:{date}:{symbol}`).

### 9:30 AM – 2:45 PM — Active Trading

Phase-dependent evaluation on each 1-minute candle close: hard filters → sub-setup checks → chart-based SL/target → cross-position risk checks → signal persist. Manages open positions (trailing SL, target monitoring). Tracks daily P&L and drawdown (Strategy 5 scoped).

### 2:45 PM — Closing Phase

No new signals. Existing positions managed (trailing SL, target). Force-close at 3:15 PM via existing `is_past_close_deadline()`.

### 3:15 PM — Day Complete

End-of-day summary logged: trades taken, wins/losses, net P&L, best/worst trade, setup breakdown. Stored in Redis → feeds tomorrow's morning briefing.

---

## Morning Screener

**File:** `backend/app/services/morning_screener.py`

### Stage 1: Quantitative Scoring (~180 F&O stocks)

Scans all F&O stocks from `nse_client.get_fo_lot_sizes()`. For each stock, computes 8 screening factors:

| Factor | Weight | What It Measures | Scoring |
|---|---|---|---|
| Relative Strength vs NIFTY | 20% | 3-month stock outperformance vs index | RS > 80 → 100, RS 50-80 → linear, RS < 50 → 0 |
| Previous Day Range & Close | 15% | Directional bias from yesterday | Close in top/bottom 25% → 100, middle → 50 |
| Volume Trend | 15% | Recent volume increasing? | 5d/20d avg ratio: > 1.5 → 100, 1.0-1.5 → linear |
| OI Change (prev day) | 15% | New money entering the stock | Long buildup → 100, short buildup → 50, unwinding → low |
| ADR (Average Daily Range) | 10% | Does the stock move enough intraday? | > 2.5% → 100, 1.5-2.5% → linear, < 1.5% → excluded |
| Sector Momentum | 10% | Is the stock's sector in favor? | Top 3 RS sectors → 100, middle → 50, bottom 3 → 0 |
| Delivery Percentage | 10% | Genuine buying vs speculation | > 50% → 100, 30-50% → 50, < 30% → 0 |
| 52-Week High Proximity | 5% | Momentum near highs | Within 5% → 100, 5-15% → linear, > 15% → low |

Stocks scoring above 50/100 make the initial cut (typically 20-25).

### Stage 2: News & Sentiment (top ~20 candidates)

For each candidate, runs the existing `NewsSentimentAgent` (Gemini with Google Search grounding):
- Strong negative news (fraud, regulatory, downgrade) → exclude from watchlist
- Earnings today → flag but don't exclude
- Strong positive news → boost score
- Neutral → no adjustment

Calls run in parallel (~30-60 seconds total).

### Stage 3: LLM Confidence Check (top ~15 after news filter)

Single batched LLM call reviews all remaining candidates with quantitative scores and news context. Returns per-stock confidence (HIGH/MEDIUM/LOW) with reasoning. LOW candidates dropped.

**Output:** Final ranked watchlist of 15-20 stocks stored in Redis (`strat5:watchlist:{date}`). Each stock includes: composite score, directional bias (BULLISH/BEARISH/NEUTRAL), trend strength/score, top contributing factors, news sentiment, previous day levels (PDH/PDL/PDC), earnings flag.

### Directional Bias Derivation

Bias is derived from the stock's multi-day trend (computed by `indicators/stock_trend.py`): 5/20 DMA crossover, higher-highs/higher-lows pattern, ADR, close position within range, RS momentum. Output: direction (UP/DOWN/FLAT) + strength (STRONG/MODERATE/WEAK) + score (-1.0 to +1.0).

---

## Nifty Alignment — Directional Gate

Reuses the existing `compute_intraday_bias()` from `indicators/intraday_bias.py` — the same 6-factor weighted composite used by Strategy 2.

The strategy computes Nifty's own intraday bias (not just the stock's):
- **STRONG opposing** Nifty bias blocks the signal (STRONG BEARISH → no BUY_FUT, STRONG BULLISH → no SELL_FUT)
- **MODERATE or WEAK** allows both directions but adjusts `bias_factor` in confidence scoring

Same soft gate pattern as Strategy 2.

---

## Pre-Signal Hard Filters

Every sub-setup must pass all of these before generating a signal:

| Filter | Threshold | Rationale |
|---|---|---|
| RVOL (time-of-day normalized) | >= 1.5 (>= 2.5 in CAUTION_ZONE) | Volume must be elevated vs normal for this time of day |
| Nifty intraday bias alignment | STRONG opposing blocks | Counter-trend stock setups fail far more often |
| Stock price | > Rs 100 | Below this, tick sizes and slippage hurt |
| ADR | > 1.5% | Stock must move enough to be worth trading intraday |
| VWAP position | Must be on correct side for the signal direction | Ensures trend alignment |
| Stock trend direction | STRONG opposing blocks signal, MODERATE adds risk_warning | Multi-day trend from `stock_trend.py` |

---

## Entry Conditions — Sub-Setups

### Priority Order

| Time Window | Priority (highest first) |
|---|---|
| Morning (9:30 – 11:30 AM) | ORB > Gap Continuation > PDH/PDL Breakout > VWAP Bounce |
| Caution Zone (11:30 AM – 1:00 PM) | PDH/PDL Breakout > VWAP Bounce (RVOL >= 2.5 + breakout confirmation) |
| Afternoon (1:00 – 2:45 PM) | VWAP Bounce > PDH/PDL Breakout (ORB + Gap expired) |

If the same stock triggers multiple setups, take the highest-priority one only.

### Setup 1: ORB Breakout

**Time window:** 9:30 AM – 11:00 AM only

**Rules:**
- **Opening Range:** High and Low of first 15 minutes (9:15–9:30 AM), recorded per stock in Redis
- **Breakout:** 5-minute candle closes above ORB high → BUY_FUT. Closes below ORB low → SELL_FUT
- **Volume confirmation:** Breakout candle volume > 1.2x average 5-minute volume
- **VWAP filter:** For longs, price must be above VWAP. For shorts, below VWAP

**SL/Target:**
- **SL:** Opposite side of ORB range (ORB low for longs, ORB high for shorts). If ORB range < 1x ATR(14) on 5-min, widen SL to 1x ATR from entry
- **Target:** Nearest resistance above entry (PDH, swing high, VWAP upper band) that gives R:R >= 1.5. Fallback: 1.5x risk
- **R:R validation:** Must be >= 1.5; skip signal if not achievable

**Enhanced ORB:** If price also breaks PDH (for longs) or PDL (for shorts), this is a stronger variant logged as "ORB + PDH Breakout" with higher confidence.

### Setup 2: VWAP Bounce / Pullback

**Time window:** 10:00 AM – 2:45 PM

**Rules:**
- **Trend identification:** Price consistently above VWAP for 30+ minutes = uptrend, below = downtrend
- **Pullback:** Price pulls back to touch VWAP (within 0.2%)
- **Rejection:** Bullish reversal candle at VWAP (pin bar, engulfing, or strong close away). Uses existing `candle_patterns.py`
- **Volume:** Rejection candle volume > average volume

**SL/Target:**
- **SL:** Below VWAP by `max(0.3%, 0.5 x ATR(14))`. If price breaks VWAP convincingly, the bounce thesis is dead
- **Target:** Previous swing high/low from today's candles. Fallback: 1.5x risk
- **R:R validation:** Must be >= 1.5

### Setup 3: PDH/PDL Breakout

**Time window:** 9:30 AM – 2:00 PM

**Rules:**
- **Setup:** Price approaches PDH (Previous Day High) or PDL (Previous Day Low)
- **Breakout:** 5-minute candle closes above PDH → BUY_FUT. Below PDL → SELL_FUT
- **Volume confirmation:** Breakout candle volume > 1.5x average 5-minute volume
- **VWAP alignment:** Price must be on the correct side of VWAP

**SL/Target:**
- **SL:** Below breakout level by `max(0.5%, 0.5 x ATR(14))` buffer
- **Target:** Measured move = (PDH - PDL) range projected from breakout point. If R:R < 1.5, look for next structure level. Skip if no valid target
- **R:R validation:** Must be >= 1.5

### Setup 4: Gap Continuation

**Time window:** 9:30 AM – 11:00 AM

**Rules:**
- **Gap identification:** Opening price > 0.5% above previous close (gap up) or < 0.5% below (gap down). Uses `detect_gap()` from `indicators/gap_analysis.py`
- **Entry trigger:** After first 15 minutes, if price holds above gap level → BUY_FUT (continuation)
- **Volume:** Opening 15-min volume > 2x average

**SL/Target:**
- **SL:** Below gap fill level (previous close) with small buffer. If price fills the gap, the continuation thesis failed
- **Target:** Gap size projected forward. Validate R:R >= 1.5
- **R:R validation:** Must be >= 1.5

### SL/Target Summary

All sub-setups use **chart-based levels** — not fixed percentages. Each setup has a natural invalidation level (ORB low, VWAP, PDH/PDL, gap fill) that becomes the SL, and scans for structure-based targets.

**ATR(14) on 5-minute candles** serves as a sanity check — SL never closer than 0.5x ATR from entry (avoids whipsaw), target at least 1.5x risk distance.

SL and target are computed directly on the stock futures price. No delta conversion or premium math (unlike Strategy 2's index options).

**Price sourcing convention (important):** PDH/PDL Breakout and Gap Continuation use `last_candle.close` — not the live tick (`ctx.current_price`) — as the reference price for entry, SL, and target computation. The candle close is the *confirmed* breakout price. Using the live tick instead can cause the SL to land on the wrong side of the entry when the tick has moved significantly from the candle close (e.g. BUY at 9533, SL at 9576 — seen in OFSS). PDH/PDL also includes a post-computation SL sanity guard that hard-rejects any signal where the SL lands on the wrong side of the entry price. **ORB** and **VWAP Bounce** use `ctx.current_price` intentionally: ORB's SL is anchored to structural `orb_low`/`orb_high` (always correct regardless of tick); VWAP Bounce's SL is anchored to VWAP (always below/above entry since the live price must be near VWAP to trigger the proximity check).

---

## Confidence Scoring

**File:** `backend/app/strategies/strategy_5_intraday_futures.py` — `_compute_confidence()`

An 8-factor weighted composite produces a 0–100 score. The full factor breakdown is persisted to `signal.indicators["confidence_factors"]` for every signal.

| Factor | Weight | What It Measures |
|---|---|---|
| `vol_factor` | 0.15 | Breakout candle volume relative to average; default 0.2 if no data |
| `rvol_factor` | 0.15 | Time-of-day normalized volume (RVOL threshold) |
| `bias_factor` | 0.12 | Nifty intraday bias alignment with signal direction |
| `phase_factor` | 0.12 | Current market phase quality (MORNING_ACTIVE > AFTERNOON > CAUTION_ZONE) |
| `setup_factor` | 0.14 | Setup-specific quality (enhanced ORB, reversal candle strength, breakout magnitude) |
| `rank_factor` | 0.12 | Stock's screener rank (`score/100`); default 0.0 if no screener data |
| `gap_factor` | 0.10 | Gap alignment with trade direction; default 0.2 if no gap data |
| `trend_factor` | 0.10 | Stock's multi-day trend alignment from `stock_trend.py`; default 0.2 if no data |

Weights sum to **1.0**. Missing-data defaults are **0.2** (not 0.5) to penalise signals where context is absent, reducing score inflation.

**Confidence thresholds** (configurable via strategy_params):
- `min_confidence_to_persist`: 30.0 — below this, signal not saved
- `min_confidence_for_shadow`: 45.0 — below this, no shadow trade
- `min_confidence_for_execution`: 60.0 — below this, signal saved but `executable = False`

---

## Exit Rules & Trailing Stop Loss

### Exit Priority (checked on each trade_monitor cycle)

| Priority | Rule | Trigger | Action |
|---|---|---|---|
| 1 | Hard Stop Loss | Price hits current SL (original or trailed) | Close immediately |
| 2 | Target hit | Price reaches target | Close full position |
| 3 | Time exit | 3:15 PM IST (`is_past_close_deadline()`) | Close ALL Strategy 5 positions |

### Trailing Stop Loss — Three Stages

The trailing SL progresses through three stages as the position moves in favor. Tracked via the `high_since_entry` field on the Position model.

**Stage 1: Original SL (entry → breakeven activation)**
- Position has its chart-based SL from the setup
- `trade_monitor` tracks the high water mark (highest price since entry for longs, lowest for shorts)
- No SL movement yet

**Stage 2: Breakeven (position gains >= `trailing_sl_breakeven_pct`)**
- Default: 0.5% gain from entry
- SL moves to entry price — trade is now risk-free

**Stage 3: Progressive Trail (beyond breakeven)**
- As price makes new highs, SL trails `trailing_sl_trail_pct` below the high water mark
- Default: 0.3% below highest price since entry
- SL only moves UP (for longs), NEVER back down

```
if current_price > position.high_since_entry:
    position.high_since_entry = current_price

trail_sl = high_since_entry * (1 - trail_pct / 100)
if trail_sl > position.stop_loss:
    position.stop_loss = trail_sl
```

**Example walkthrough:**

```
09:42  BUY_FUT ADANIPORTS @ 1,583. SL = 1,562 (ORB low). Target = 1,614.
       HWM = 1,583.

09:48  Price: 1,591 (+0.51%). HWM -> 1,591. Breakeven activated -> SL moves to 1,583.

09:55  Price: 1,600 (+1.07%). HWM -> 1,600. Trail: 1,600 x 0.997 = 1,595.
       SL moves 1,583 -> 1,595.

10:02  Price: 1,610 (+1.71%). HWM -> 1,610. Trail: 1,610 x 0.997 = 1,605.
       SL moves 1,595 -> 1,605.

10:05  Price: 1,607. HWM still 1,610. Trail still 1,605. SL stays at 1,605.

10:08  Price: 1,604. Hits trailed SL at 1,605 -> EXIT. Profit: +1.39%.
```

### Exit Parameters (in `strategy_params`)

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
    "enabled_setups": ["ORB", "VWAP_BOUNCE", "PDH_PDL", "GAP_CONTINUATION"],
}
```

---

## Position Sizing

**Default: 1 lot per trade. Maximum: 2 lots (hard cap).**

Standard risk-based sizing doesn't work for intraday stock futures — contract values are large (e.g., RELIANCE lot=250 x Rs 2800 = Rs 7L contract, Rs 1.4L margin). With 3 positions at 2 lots each, margin alone would be Rs 8.4L.

### 2-Lot Conviction Conditions (all 6 must be met)

| Factor | 2 lots (high conviction) | 1 lot (standard) |
|---|---|---|
| RVOL | >= 3.0 (extreme volume) | >= 1.5 (normal threshold) |
| Nifty bias | STRONG alignment with trade direction | MODERATE or WEAK |
| Screener score | Top 3 in watchlist (score > 70) | Score 50-70 |
| Setup type | Enhanced ORB (ORB + PDH break) | Single setup |
| Morning briefing | Agent recommended "aggressive" today | "Normal" or "conservative" |
| Stock trend | STRONG or MODERATE aligned | WEAK or opposing |

### VIX Adjustment

Reuses existing `vix_to_multiplier()` from `services/position_sizing.py`:
- VIX < 14: 1.1x
- VIX 14-18: 1.0x
- VIX 18-22: 0.9x → effectively caps at 1 lot (2 x 0.9 rounds down)
- VIX >= 22: strategy halted entirely

---

## Risk Management

### Cross-Position Awareness — Strategy-Scoped, Soft Enforcement

All risk limits are scoped to Strategy 5 only. Other strategies' positions don't count toward Strategy 5 limits, and vice versa.

Before emitting a signal, the strategy:
1. Queries open positions where `strategy_name = 'INTRADAY_FUTURES'`
2. Checks sector deduplication (no two Strategy 5 trades from the same sector)
3. Checks position count against max simultaneous (3)
4. Checks daily trade count against max trades (5)
5. Checks daily drawdown against 3% limit

**Soft enforcement:** Signals are never suppressed, only flagged with `risk_warnings` array (e.g., `["daily_drawdown_3pct_reached"]`, `["sector_duplicate: IT"]`). In YOLO mode, auto-execution pauses when warnings are present. Shadow executor still creates shadow trades for tracking.

### Market Condition Filters

| Condition | Action |
|---|---|
| India VIX > 20 | Skip ALL stock futures trades for the day (agent status → HALTED) |
| India VIX < 12 | Reduce target expectations (low volatility, smaller moves) |
| GIFT Nifty gap > 1% | Flag "volatile open" — agent may recommend conservative approach |
| First 15 minutes (9:15-9:30) | No trades — ORB forming |
| Caution zone (11:30-1:00) | Elevated RVOL >= 2.5, breakout confirmation required |
| Last 30 minutes (2:45-3:15) | No new trades — manage existing, force-close at 3:15 |
| Daily drawdown >= 3% | Flag all new signals with risk_warning |

### Margin Management

- Intraday stock futures margin: ~20% of contract value (SPAN + Exposure)
- Example: RELIANCE futures (lot=250, price=Rs 2800) → Contract Rs 7,00,000 → Margin ~Rs 1,40,000
- With Rs 10L capital and 1-2 lots per trade: max 3 positions fits within margin

---

## Redis State Management

All intra-day state lives in Redis with 90-day TTL:

| Key Pattern | Content |
|---|---|
| `strat5:watchlist:{date}` | Morning screener output: ranked list with scores, bias, factors, news sentiment |
| `strat5:orb:{date}:{symbol}` | ORB high/low for a stock |
| `strat5:rvol_baseline:{symbol}` | 20-day avg volume by 5-min time bucket (rebuilt daily) |
| `strat5:phase:{date}` | Current market phase enum |
| `strat5:daily_stats:{date}` | Trade count, P&L, drawdown, positions open (Strategy 5 only) |
| `strat5:agent_log:{date}` | Chronological agent activity entries (append-only) |
| `strat5:global_cues:{date}` | Morning global cues snapshot |
| `strat5:morning_briefing:{date}` | AI morning briefing (yesterday recap + approach for today) |
| `strat5:agent_status:{date}` | Agent status: RUNNING / PAUSED / HALTED / DONE |

---

## Key Indicators

### RVOL — Relative Volume (time-of-day normalized)

**File:** `backend/app/indicators/rvol.py`

Current 5-min candle volume / avg volume for this 5-min bucket over past 20 trading days. ~75 buckets per stock x 180 stocks. Stored in Redis (`strat5:rvol_baseline:{symbol}`), rebuilt each morning.

Thresholds: >= 1.5 minimum, >= 2.0 high confidence, >= 2.5 for caution zone.

### ADR — Average Daily Range

**File:** `backend/app/indicators/adr.py`

`mean((high - low) / close * 100)` over last 20 trading days. Threshold: > 1.5% to include in watchlist.

### ATR — Average True Range

**File:** `backend/app/indicators/atr.py`

Wilder's smoothed ATR on 5-minute candles. Used as SL sanity check (minimum distance) across all setups.

### Gap Analysis

**File:** `backend/app/indicators/gap_analysis.py`

`detect_gap(today_open, prev_close)` → gap direction, gap %. `is_gap_continuation()` confirms holding after 15 minutes.

### Stock Trend

**File:** `backend/app/indicators/stock_trend.py`

6-factor composite: 5/20 DMA crossover, HH/HL pattern, ADR, close position, RS momentum, V-reversal detection. Output: direction (UP/DOWN/FLAT) + strength (STRONG/MODERATE/WEAK) + score.

### OI Interpretation

| OI Change | Price Change | Interpretation | Screener Score |
|---|---|---|---|
| OI UP | Price UP | Long buildup | 100 |
| OI UP | Price DOWN | Short buildup | 50 |
| OI DOWN | Price UP | Short covering | 30 |
| OI DOWN | Price DOWN | Long unwinding | 20 |

---

## Frontend Components

### Dedicated Page: `/intraday-futures`

| Section | Component | Description |
|---|---|---|
| Day Status Bar | `DayStatusBar.tsx` | Phase, Nifty bias, VIX, agent status, daily stats, date picker |
| Morning Watchlist | `Watchlist.tsx` | Scored stocks with bias, factors, ORB levels, RVOL. 30s polling in live mode |
| Agent Activity Log | `AgentLog.tsx` | Chronological feed with category filter pills. Categories: BRIEFING, SCREENER, ORB, SIGNAL, TRADE, EXIT, SKIP, PHASE, RISK, GLOBAL, SYSTEM |
| Setup Performance | `SetupPerformance.tsx` | Per-setup win rate bars, W/L counts, P&L over configurable period |
| Global Cues | `GlobalCues.tsx` | Overnight data, VIX, morning briefing summary, flags |
| Config Panel | `ConfigPanel.tsx` | Setup checkboxes, RVOL thresholds, risk parameters |

### Signal Cards on Dashboard

Strategy 5 signals render with strategy-aware context in `ScannerPanel.tsx`:
- Setup type badge + enhanced ORB indicator
- Phase, RVOL (color-coded by threshold), gap %, VWAP/ORB/PDH context
- Risk warnings as amber warning pills
- 8-factor confidence breakdown with Strategy 5-specific labels

---

## Key Files

| File | Role |
|---|---|
| `backend/app/strategies/strategy_5_intraday_futures.py` | Strategy class: phase machine, 4 sub-setups, chart-based SL/target, cross-position checks, confidence scoring |
| `backend/app/services/morning_screener.py` | 3-stage screener (quant + news + LLM), global cues, morning briefing, watchlist, pre-open reassessment, setup performance |
| `backend/app/indicators/rvol.py` | RVOL: time-of-day normalized volume |
| `backend/app/indicators/adr.py` | ADR: average daily range |
| `backend/app/indicators/atr.py` | ATR: average true range (Wilder's smoothing) |
| `backend/app/indicators/gap_analysis.py` | Gap detection + continuation |
| `backend/app/indicators/stock_trend.py` | Multi-day trend direction + strength |
| `backend/app/api/v1/intraday_futures.py` | API router: watchlist, agent log, global cues, setup performance, phase, screener/briefing triggers, agent control |
| `backend/app/tasks/morning_workflow_task.py` | Scheduled task: orchestrates screener → briefing → pre-open reassessment |
| `backend/app/tasks/nse_bhav_copy_task.py` | Daily NSE bhav copy download (7:30 AM IST) — writes full OHLCV to `market_data_daily` table + slim `{delivery_pct, close, prev_close}` to Redis for delivery % scoring |
| `backend/app/models/market_data_daily.py` | `MarketDataDaily` table: one OHLCV row per symbol per date, primary source for all 8 quantitative scoring factors in Stage 1 |
| `backend/app/tasks/oi_snapshot_task.py` | Stock futures OI snapshot for screener OI scoring |
| `backend/app/data/sector_classification.json` | Static F&O stocks → sectors mapping |
| `backend/app/services/strategy_params.py` | `INTRADAY_FUTURES_DEFAULTS` dict |
| `backend/app/agent/trade_monitor.py` | Trailing SL (breakeven + progressive trail + HWM), time exit |
| `backend/app/models/position.py` | `high_since_entry` field for trailing SL tracking |
| `frontend/src/app/intraday-futures/page.tsx` | Dedicated Strategy 5 page |
| `frontend/src/components/intraday-futures/` | 7 components: DayStatusBar, Watchlist, AgentLog, GlobalCues, SetupPerformance, ConfigPanel, DailyStats |
| `frontend/src/components/dashboard/ScannerPanel.tsx` | Strategy-aware signal card rendering |
| `docs/strategies/strategy-5-intraday-futures.md` | This file |

---

## Implementation Notes

### Dynamic symbol registration
The strategy overrides `get_symbols()` to return today's watchlist symbols from Redis (`strat5:watchlist:{date}`) instead of the static `strategy_configs.symbols` column. The strategy runner calls `await strategy.get_symbols()` for auto-mode symbol resolution.

### No auto_mode toggle
Unlike other strategies, Strategy 5 has no separate `auto_mode` toggle. When the strategy is enabled, the agent runs autonomously. The user controls the agent via Pause/Resume on the dedicated page.

### Futures contract resolution
Uses existing `futures_resolver.py` for stock → nearest futures contract mapping. No option resolver — SL and target are set on the stock futures price. `strategy_runner._resolve_futures()` proportionally adjusts SL/target from spot entry to futures LTP (direction-aware for long/short).

### LLM usage is front-loaded
~22 LLM calls per day, all before 9:00 AM (1 briefing + ~20 news sentiment + 1 screener confidence). Zero LLM calls during market hours — all signal generation, filtering, and exit management is deterministic computation.

### Confidence factors storage
`_compute_confidence()` accepts an optional `indicators: dict` parameter. When provided, it injects a `confidence_factors` dict with the 8 factor values into the signal's indicators JSONB. This enables the frontend to render per-factor confidence bars in the AI panel.

### Parameter experiments — disable strategy first
Before changing thresholds for testing, go to Settings → Strategies and disable Strategy 5. Both auto-mode evaluations AND manual evals trigger `shadow_executor`, creating shadow trades. Same caveat as Strategy 2 (see Strategy 2 doc).

### Single target for V1
No T1/T2 partial booking. Close full position on target hit. Partial exits may be added in a future version after collecting real trade data.

### Time stop deferred
A time-based stop (e.g., "exit after 30 min if position hasn't moved 0.5%") is not yet implemented. Needs real trade data to calibrate the right threshold.

### Force-close time
3:15 PM via existing `is_past_close_deadline()`. No custom exit time in V1.
