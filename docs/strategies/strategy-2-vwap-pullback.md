# Strategy 2: VWAP Pullback

**Status:** PRIMARY / ACTIVE  
**File:** `backend/app/strategies/strategy_2_vwap_pullback.py`  
**Instruments:** NIFTY, BANKNIFTY, FINNIFTY, SENSEX, MIDCPNIFTY — index options (BUY CE / BUY PE)  
**Holding type:** INTRADAY

---

## Overview

Buys index options at VWAP pullback points using five layers of confirmation:

1. **Composite intraday bias** — live score from today's tape + global overnight cues (not just yesterday)
2. **VWAP proximity** — price must pull back to within 0.15% of VWAP
3. **Candle pattern** — bullish/bearish reversal at the pullback
4. **Volume quality** — pullback on below-average volume
5. **Confidence gate** — weighted 10-factor composite must clear 55/100 before the signal fires

A Gemini LLM overlay then reviews the full indicator snapshot and adjusts confidence ±15 with a human-readable rationale.

---

## Trading Windows

- **Window 1 (Primary):** 9:45 AM – 11:00 AM IST
- **Window 2 (Secondary):** 1:45 PM – 2:45 PM IST
- **Dead zone:** 11:30 AM – 1:30 PM IST

Signals generated outside the active windows are persisted as **informational** (non-executable) so the strategy's view is visible in the UI without triggering trades.

---

## Directional Bias — Composite Intraday Score

**File:** `backend/app/indicators/intraday_bias.py`

The strategy does not use a hard yesterday-only gate. Instead, a live composite score in `[-1, +1]` is computed each minute from six factors:

| Factor | Weight | Description |
|---|---|---|
| Yesterday's close_position | 0.25 | Where yesterday closed relative to its range (0=at low, 1=at high) |
| Gap vs PDC | 0.20 | Today's opening gap vs previous close |
| VWAP slope (last 10 candles) | 0.25 | Intraday trend direction |
| Price vs VWAP | 0.10 | Which side of VWAP price is on |
| Global overnight cues | 0.10 | Weighted Dow futures, S&P close, USD/INR, crude composite |
| Candle momentum (last 5 bars) | 0.10 | Net bullish/bearish body direction |

**Strength thresholds:**
- `|score| ≥ 0.50` → STRONG
- `|score| ≥ 0.20` → MODERATE
- `|score| < 0.20` → WEAK

**Direction gate:**
- Structural gate: pullback from above VWAP → CE candidate; from below VWAP → PE candidate
- **STRONG** opposing bias blocks the trade entirely
- **MODERATE** or **WEAK** opposing bias allows the trade but lowers the `bias_alignment` confidence factor (the signal may still fire if overall confidence clears 55)

This means a bearish gap on a bullish-yesterday day produces a MODERATE BEARISH bias — PE signals can fire, whereas a strong multi-day bull trend (STRONG BULLISH) will veto PE entries.

---

## Entry Conditions

### BUY CE — all must pass

1. Intraday bias is not STRONG BEARISH
2. Price above VWAP, pulling back to within 0.15% of VWAP
3. Bullish reversal candle (engulfing, pin bar, doji + next candle bullish) on the 5m chart
4. Current 5m volume ≤ 1.2× 20-period average (healthy pullback, not panic volume)
5. Composite confidence ≥ 55 (see Confidence Scoring below)

### BUY PE — all must pass

1. Intraday bias is not STRONG BULLISH
2. Price below VWAP, rallying to within 0.15% of VWAP
3. Bearish reversal candle (engulfing, shooting star) on the 5m chart
4. Current 5m volume ≤ 1.2× 20-period average
5. Composite confidence ≥ 55

---

## Confidence Scoring

**File:** `backend/app/indicators/confidence.py`

A 10-factor weighted composite produces a 0–100 score. The signal fires only when this score ≥ 55 (configurable via `settings.fire_confidence_threshold`).

| Factor | Weight | What it measures |
|---|---|---|
| `bias_alignment` | 0.20 | How strongly the intraday bias score aligns with the signal direction |
| `reversal_quality` | 0.15 | Engulfing body magnitude and wick cleanliness |
| `vwap_slope_alignment` | 0.10 | Slope of the last 4 × 5m candles matches signal direction |
| `volume_quality` | 0.10 | How far below average the pullback volume is |
| `rr_ratio_quality` | 0.10 | Index-level R:R from market structure SL/target |
| `oi_support` | 0.10 | OI walls confirm the trade direction |
| `global_alignment` | 0.10 | Global overnight cues (Dow, S&P, USD/INR) match direction |
| `cpr_narrow_trending` | 0.05 | Narrow CPR indicates a trending day |
| `vix_regime` | 0.05 | VIX 10–18: ideal; > 22: penalised |
| `time_of_day` | 0.05 | Full weight inside trade windows; half outside |

The full factor breakdown is persisted to `signal.indicators["confidence_factors"]` for every signal.

---

## LLM Confidence Overlay

**File:** `backend/app/research/agents/signal_confidence.py`

After the deterministic gates pass and the option contract is resolved, a Gemini call reviews the complete indicator snapshot and returns:

- `confidence_adjustment` ±15 — applied to the deterministic score
- `ai_summary` — one-sentence headline for the UI (e.g., "PE on NIFTY after -0.45% gap at VWAP 24358 with bearish engulfing 1.41× volume")
- `ai_rationale` — 3–5 sentences citing specific values for post-trade review
- `key_supports` / `key_risks` — list of 2–4 concrete factors with values
- `recommended_action` — PROCEED / PROCEED_WITH_CAUTION / RECONSIDER
- `suggested_lot_adjustment` — NONE / REDUCE_50_PCT / SKIP

**Policy:**
- 8-second hard timeout; the signal is never blocked by LLM failure
- `ai_confidence_enabled = False` in settings skips the call entirely
- `suggested_lot_adjustment = REDUCE_50_PCT` halves the computed lot count (floor 1)
- If post-LLM confidence drops below threshold, `executable = False` is set with `blocked_reason = "LLM downgrade"` but the signal is still persisted for study

All LLM fields are persisted as dedicated columns (`ai_summary`, `ai_rationale`, `ai_adjustment`, `ai_action`) on the `signals` table plus `ai_key_supports`/`ai_key_risks` in the `indicators` JSONB.

---

## Strike & Expiry Selection

**File:** `backend/app/services/option_resolver.py`

- **Strike:** ATM first, then 1-strike ITM if ATM unavailable
- **Delta:** ATM ≈ 0.50, ITM ≈ 0.60
- **Premium preference:** Rs 150–400 (soft; not a hard blocker)

**Expiry (post-SEBI Nov 2024):**
- NIFTY → nearest weekly Tuesday
- SENSEX → nearest weekly Thursday
- BANKNIFTY, FINNIFTY, MIDCPNIFTY → monthly only (last Tuesday of month)

---

## SL/Target — Index Level → Premium Conversion

The strategy emits **index-level** SL/target from market structure. The option resolver converts them to **premium-level** via delta approximation.

```
Example (BUY CE):
  Index entry: 24,050   index_sl: 23,980   index_target: 24,150
  Strike: 24,000 (ATM)  Premium: ₹320      Delta: 0.50

  premium_sl     = 320 - 0.50 × (24,050 - 23,980) = ₹285
  premium_target = 320 + 0.50 × (24,150 - 24,050) = ₹370
```

### Index SL selection (`market_levels.py`)

**BUY CE** — nearest support below entry (in priority order):
1. VWAP lower band
2. Recent 5m swing low (last 10 candles)
3. CPR BC (Bottom Central)
4. CPR S1
5. PDL (Previous Day Low)
6. Max PE OI strike

**BUY PE** — nearest resistance above entry (inverted order).

Level must be ≥ 0.10% from entry. R:R must be ≥ 1:1; if not, the signal is discarded.

**Fallback** (when no valid market-structure level exists):
- SL: 30% of premium (aligned bias) or 35% (opposing bias)
- Target: 1.5× risk-reward on premium

---

## Exit Rules

| Condition | Action |
|---|---|
| Premium ≤ `premium_sl` | SL hit → close |
| Premium ≥ `premium_target` | Target hit → close |
| Price crosses below VWAP × 0.998 (for calls) | Invalidation → close |
| 3:15 PM IST | Time exit → close all intraday positions |

---

## Position Sizing

**File:** `backend/app/services/position_sizing.py`

```
risk_amount  = capital × risk_pct            # e.g. 10L × 2% = ₹20,000
risk_per_lot = |entry − sl| × lot_size       # e.g. ₹80 × 75 = ₹6,000
lots         = floor(risk_amount / risk_per_lot) × vix_multiplier
lots         = max(1, min(lots, 5))           # hard cap: max_lots = 5
```

VIX multiplier: < 14 → 1.1×, 14–18 → 1.0×, 18–22 → 0.9×, > 22 → 0.8×

If the LLM overlay returns `suggested_lot_adjustment = REDUCE_50_PCT`, lots are halved (floor 1) before execution.

---

## Hard Risk Filters

The strategy runner blocks execution when:
- VIX ≥ 22 (`blocked_reason = "VIX extreme"`, signal still persisted)
- Daily drawdown limit breached (`blocked_reason = "Drawdown limit breached"`)
- Max trades per day reached (`blocked_reason = "Max trades reached (N/day)"`)
- Past 3:15 PM IST (signal not generated at all)
- Open position in same symbol + direction already exists (signal skipped)

---

## MarketContext Fields Used

| Field | Source | Purpose |
|---|---|---|
| `vwap` | Today's 1m candle buffer | VWAP proximity + slope |
| `previous_day` | DB (cached per day) | PDH/PDL/PDC for CPR + fallback bias |
| `cpr` | Computed from prev day HLC | CPR type (NARROW/WIDE), S1/R1 levels |
| `oi_analysis` | Latest `oi_snapshots` | OI walls for SL/target + oi_support factor |
| `india_vix` | Redis (`indicator:india_vix`) | VIX guard + vix_regime confidence factor |
| `intraday_bias` | Computed live from 1m buffer + global_cues | Directional gate + bias_alignment factor |
| `global_cues` | Redis (`indicator:global:*`) | Gap + overnight signal in intraday_bias + global_alignment factor |
| `candles_5m` | Aggregated from 1m buffer | Pattern detection + slope + volume |

---

## Key Files

| File | Role |
|---|---|
| `backend/app/strategies/strategy_2_vwap_pullback.py` | Entry/exit logic, gate sequence, signal construction |
| `backend/app/indicators/intraday_bias.py` | Composite live directional bias |
| `backend/app/indicators/confidence.py` | 10-factor confidence composite |
| `backend/app/indicators/market_levels.py` | Index-level SL/target from market structure |
| `backend/app/services/option_resolver.py` | Strike selection, premium fetch, delta SL/target |
| `backend/app/services/strategy_runner.py` | Orchestration: build context → evaluate → resolve → LLM → persist |
| `backend/app/research/agents/signal_confidence.py` | LLM overlay: rationale + confidence adjustment |
| `docs/ai/signal-confidence-agent.md` | Full LLM prompt, schema, tuning notes, cost model |
