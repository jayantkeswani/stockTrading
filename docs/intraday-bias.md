# Intraday Bias — Design & Strategy Integration

## What It Is

A composite directional score for each symbol, recomputed on every 1-minute candle close. Answers: **"Is today bullish or bearish for this symbol?"**

Output: `IntradayBias(bias, score, strength, components)`

- `score`: float in [-1, +1]. Positive = bullish, negative = bearish.
- `bias`: BULLISH (score > 0.20), BEARISH (score < -0.20), NEUTRAL (in between).
- `strength`: STRONG (|score| >= 0.50), MODERATE (>= 0.20), WEAK (< 0.20).

Source: `backend/app/indicators/intraday_bias.py`

## Factor Weights

| # | Factor | Base Weight | Decays? | Input | What It Measures |
|---|--------|------------|---------|-------|------------------|
| 1 | `yesterday_close_position` | 0.10 | Yes → 0.04 | `prev_day` | Where yesterday closed within its range (0=low, 1=high) |
| 2 | `gap_vs_pdc` | 0.15 | Yes → 0.06 | First candle open vs PDC | Opening gap direction and magnitude |
| 3 | `vwap_slope` | 0.30 | Gains freed weight | Last 30 1m candle closes | Intraday price slope (is it currently trending?) |
| 4 | `price_vs_vwap` | 0.10 | Gains freed weight | Current price vs VWAP | Binary: above (+1) or below (-1) VWAP |
| 5 | `global_overnight` | 0.10 | No | `GlobalCues.global_score` | Dow, S&P500, USD/INR overnight composite |
| 6 | `candle_momentum` | 0.10 | Gains freed weight | Last 5 1m candles | Net bullish vs bearish candle bodies |
| 7 | `move_from_pdc` | 0.15 | Gains freed weight | Current price vs PDC | Total move from yesterday's close (includes gap) |
| 8 | `nifty_bias_score` | 0.05 | No | NIFTY's own score | Benchmark index direction (non-NIFTY symbols only) |

**Total**: 1.05 (1.00 for NIFTY, which excludes factor 8). Score is `weighted_sum / total_weight`.

### ADR-Based Normalizer

Factors 2 (gap) and 7 (move_from_pdc) use an ADR-scaled normalizer instead of a fixed threshold:

```
normalizer = (prev_day.day_range / prev_day.pdc * 100) * 0.5
```

A move of half the previous day's range = full signal (±1.0). This means NIFTY (ADR ~1%) needs ~0.5% for full signal, while a volatile stock like VEDL (ADR ~3%) needs ~1.5%. Fallback: 0.5% when `prev_day` is unavailable.

### Time Decay

Static factors (yesterday, gap) decay linearly from 9:15 AM to 3:15 PM. Freed weight redistributes to dynamic factors:

- VWAP slope gets 40% of freed weight
- move_from_pdc gets 25%
- candle momentum gets 20%
- price_vs_vwap gets 15%

By close: yesterday 0.10 → 0.04, gap 0.15 → 0.06. Dynamic factors grow proportionally.

## Data Flow

```
1m candle close (feed_manager)
  → strategy_runner._build_market_context()
    → compute_intraday_bias(prev_day, candles_1m, vwap, current_price, global_cues, as_of, nifty_bias_score)
  → result stored in MarketContext.intraday_bias
  → if INDEX: cached in Redis (indicator:intraday_bias:{symbol}, 600s TTL)
             + broadcast via WS (market:bias_update)
  → if NIFTY: score cached as _last_nifty_bias_score for non-NIFTY symbols

Frontend: useWebSocket.ts listens for market:bias_update (NIFTY only)
        → Header.tsx displays "{bias} {strength}" (e.g. "BULLISH MODERATE")
```

### S5 Nifty Bias (single source of truth)

Strategy 5's `_enrich_strategy5_params()` reuses the NIFTY bias cached on the NIFTY candle close (`self._last_nifty_bias`, the same value `nifty_bias_snapshot()` exposes to the trade monitor's invalidation exit) and injects it as `params["_nifty_bias"]` — so the signal-gen alignment gate and the live invalidation exit read the identical bias. It recomputes from the NIFTY candle buffer on demand only when the cache is cold (e.g. manual eval before any NIFTY candle has closed this session).

## Strategy Integration

### Strategy 2 — VWAP Pullback

**Direction**: comes from VWAP structure (price above VWAP → CE, below → PE). Bias does NOT determine direction.

**Hard gate** (`is_blocked_by_bias`): STRONG opposing bias blocks the signal. STRONG BEARISH blocks CE; STRONG BULLISH blocks PE. MODERATE/WEAK never blocks.

**Confidence factor**: `bias_alignment` at **0.25 weight** (heaviest factor in S2's 9-factor confidence model in `confidence.py`). Maps bias score to [0, 1]:

```python
alignment = score if CE else -score
factor = (alignment + 1.0) / 2.0   # -1 → 0.0, 0 → 0.5, +1 → 1.0
```

Impact: on a BULLISH MODERATE day (score ~+0.30), a CE signal gets bias_alignment = 0.65 → contributes 16.25 of 25 possible confidence points. On NEUTRAL WEAK (+0.10): 0.55 → 13.75 points. Delta: ~2.5 points.

### Strategy 5 — Intraday Futures

Uses the Nifty bias in **3 places**:

**1. Hard gate** (line 389): Same pattern as S2 — STRONG opposing blocks. STRONG BEARISH blocks LONG; STRONG BULLISH blocks SHORT.

**2. Confidence factor** (line 880): `bias_factor` at **0.12 weight** in S5's 9-factor confidence model. Same `(alignment + 1.0) / 2.0` mapping but direction-aware for LONG/SHORT instead of CE/PE.

**3. Lot sizing** (line 832): To qualify for 2 lots (vs default 1), ALL 6 conditions must be true:

```python
bias_ok = nifty_bias.strength == "STRONG"
# AND rvol >= 3.0 AND screener_score > 70 AND briefing == "aggressive"
#     AND enhanced_orb (for ORB) AND trend STRONG/MODERATE
```

`bias_ok` requires STRONG — MODERATE is not enough. This means lot sizing upgrade only happens when the Nifty bias score exceeds ±0.50.

### Stock Trend Filter (separate from bias)

Strategy 5 also uses `_stock_bias` / `_stock_trend_strength` from `stock_trend.py` — this is a per-stock daily trend indicator, NOT the intraday bias. STRONG opposing stock trend blocks the signal; MODERATE adds a risk warning.

## Design Decisions

### Why move_from_pdc instead of intraday_drift (from open)

Prior to this change, factor 7 measured `(current_price - today_open) / today_open`. On gap-up days where price opens high and consolidates, this read ~0 despite the day being clearly bullish. The entire gap — often the strongest directional signal — was invisible to the highest-weight dynamic factors.

`move_from_pdc` measures `(current_price - PDC) / PDC`, capturing the full move including the gap. It complements factor 2 (gap) rather than duplicating it:

| Scenario | gap_vs_pdc | move_from_pdc |
|----------|-----------|---------------|
| Gap up, holds | +1.0 (decaying) | +1.0 |
| Gap up, sells off to flat | +1.0 (decaying) | 0.0 |
| Flat open, rallies | 0.0 | +1.0 |
| Gap down, recovers | -1.0 (decaying) | 0.0 |

### Why yesterday_close_position is only 0.10

A gap-up IS the market repricing yesterday's close. Giving yesterday 0.20 weight (more than the gap's 0.15) allowed a bearish Friday close to cancel out a bullish Monday gap — incorrect behavior. At 0.10 weight, yesterday provides a regime hint without overwhelming current-day signals.

### Why ADR-based normalizer

A fixed 0.5% normalizer saturates too fast for volatile stocks (3%+ ADR like VEDL hit full signal on normal noise) and too slow for low-vol instruments. Scaling by `prev_day_range * 0.5` means "half a typical day's range = full directional signal" — proportional across all symbols.
