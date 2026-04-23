# Strategy 2: VWAP Pullback + Previous Day Context + OI Confirmation

**Status:** PRIMARY / ACTIVE
**File:** `backend/app/strategies/strategy_2_vwap_pullback.py`

## Overview
Combines previous day analysis for directional bias, VWAP pullbacks for entries, and Open Interest data for institutional confirmation. This is the daily bread-and-butter strategy.

## Pre-Market Setup (Run at 9:00 AM IST)
1. Fetch previous day OHLCV for the index (NIFTY/BANKNIFTY)
2. Determine directional bias:
   - **Bullish:** Previous day close > open AND close in upper 30% of range
   - **Bearish:** Previous day close < open AND close in lower 30% of range
   - **Neutral:** Neither condition met → reduce position size by 50%
3. Calculate CPR (Central Pivot Range):
   - Pivot = (PDH + PDL + PDC) / 3
   - BC (Bottom Central) = (PDH + PDL) / 2
   - TC (Top Central) = (Pivot - BC) + Pivot
   - Narrow CPR (TC - BC < 0.1% of price) → trending day expected
   - Wide CPR → range-bound day expected
4. Check India VIX:
   - VIX < 14: Options cheap, favorable for buying (full position)
   - VIX 14-18: Normal (full position)
   - VIX > 18: Options expensive (reduce size by 30%)
5. Fetch PCR (Put-Call Ratio):
   - PCR < 0.7: Market oversold, expect bounce (bullish)
   - PCR > 1.5: Market overbought, expect drop (bearish)
   - PCR 0.7-1.5: Neutral

## Trading Windows
- **Window 1 (Primary):** 9:45 AM - 11:00 AM IST
- **Window 2 (Secondary):** 1:45 PM - 2:45 PM IST
- **AVOID:** 11:30 AM - 1:30 PM (dead zone)

## Entry Rules

### Buy CALL when ALL conditions met:
1. Previous day bias is BULLISH (or neutral with reduced size)
2. Price is above VWAP on 5-min chart
3. Price pulls back to within 0.15% of VWAP from above
4. Pullback candle shows bullish reversal (bullish engulfing, pin bar, or doji + next candle bullish)
5. RSI on 5-min is between 40-55 at pullback (not oversold crash)
6. Highest Put OI strike is below current price (OI support holds)
7. Volume on pullback < average volume (low volume pullback = healthy)

### Buy PUT when ALL conditions met:
1. Previous day bias is BEARISH (or neutral with reduced size)
2. Price is below VWAP on 5-min chart
3. Price rallies to within 0.15% of VWAP from below
4. Rally candle shows bearish reversal (bearish engulfing, shooting star)
5. RSI on 5-min is between 55-65 at rally
6. Highest Call OI strike is above current price (OI resistance holds)
7. Volume on rally < average volume

### Strike Selection (handled by `option_resolver.py`):
- ATM or 1-strike ITM (Delta 0.45-0.60)
- Premium should be Rs 150-400 range (soft preference, not a hard blocker)
- ATM tried first; if premium unavailable, falls back to ITM
- Strike gaps: NIFTY=50, BANKNIFTY=100, FINNIFTY=50, SENSEX=100, MIDCPNIFTY=25

### Expiry Selection (post-SEBI Nov 2024):
- **NIFTY**: Nearest weekly Tuesday expiry
- **SENSEX**: Nearest weekly Thursday expiry
- **BANKNIFTY, FINNIFTY, MIDCPNIFTY**: Monthly only — last Tuesday of month (no weekly contracts)
- If expiry day is a market holiday, shifts to previous trading day

## SL/Target: Index-Level Market Structure → Premium Conversion

### How it works
The strategy computes **index-level** SL and target from market structure, then the `option_resolver` converts them to **premium-level** prices using delta approximation.

```
Strategy:        "BUY CE NIFTY, index_sl=23980, index_target=24150"
                          ↓
Option Resolver:
  1. Strike = 24000 (ATM)                    ← unchanged
  2. Premium = ₹320 (LTP from Fyers)         ← unchanged
  3. Delta = 0.50 (ATM) or 0.60 (ITM)
  4. SL = ₹320 - 0.50 × (24050 - 23980) = ₹285
  5. Target = ₹320 + 0.50 × (24150 - 24050) = ₹370
```

### Index-Level SL Selection (via `market_levels.py`)
For **BUY CE** — picks the nearest valid **support** level below entry:
1. VWAP lower band
2. Recent 5m swing low (last 10 candles)
3. CPR BC (Bottom Central)
4. CPR S1
5. PDL (Previous Day Low)
6. Max PE OI strike (institutional support wall)

For **BUY PE** — picks the nearest valid **resistance** level above entry (inverted).

Filter: level must be at least 0.10% from entry (prevents SL that is too tight).
Selection: picks the **nearest** valid level — tighter SL = better R:R.

### Index-Level Target Selection
For **BUY CE** — picks the nearest valid **resistance** level above entry:
1. VWAP upper band
2. CPR TC (Top Central)
3. CPR R1
4. PDH (Previous Day High)
5. Max CE OI strike (institutional resistance wall)

For **BUY PE** — picks the nearest valid **support** level below entry (inverted).

Selection: picks the **nearest** valid level — most achievable target.

### R:R Filter
If reward/risk < 1:1 after computing index-level SL/target, the signal is **skipped entirely**. This prevents entering trades with poor risk-reward setups.

### Fallback
If no valid index-level SL/target can be found (e.g., insufficient market data), falls back to fixed-percentage computation:
- SL: 30% of premium (bullish bias) or 35% (neutral bias)
- Target: 1.5× risk-reward multiplier on premium

## Exit Rules
- **Stop Loss:** Premium-based SL derived from index-level support/resistance (see above). If market structure unavailable, falls back to 30-35% of premium.
- **Target 1:** Book 60% at premium-based target derived from index-level resistance/support.
- **Target 2:** Trail remaining 40% with 9 EMA on 5-min as trailing stop
- **Time Exit:** Close all positions by 3:15 PM IST
- **Invalidation:** Close if underlying index price closes below VWAP (for calls) or above VWAP (for puts) on 5-min

## Position Sizing
Computed by `calculate_lots()` in `backend/app/services/position_sizing.py`:
```
risk_amount = capital × risk_pct          # e.g. 10L × 2% = ₹20,000
risk_per_lot = |entry − SL| × lot_size   # e.g. ₹80 × 75 = ₹6,000
lots = int(risk_amount / risk_per_lot) × vix_multiplier
lots = max(1, min(lots, 5))               # hard cap: max_lots = 5
```

VIX multiplier (from `vix_to_multiplier()`):
- VIX < 14 → 1.1× (cheap options, slightly more exposure)
- VIX 14–18 → 1.0× (normal)
- VIX 18–22 → 0.9× (elevated)
- VIX > 22 → 0.8× (high volatility, reduce exposure)

Sizing is **snapshotted onto the signal** at resolution time so the preview modal and actual execution always use the same lot count. Users can override lots in the confirm modal before submitting.

## Risk Filters (Do NOT trade when):
- Daily drawdown already at 3%+ → reduce to 1 lot max
- Daily drawdown at 5% → HALT ALL TRADING
- Market is in first 15 minutes (9:15-9:30) → too volatile
- VIX > 22 → extreme conditions, sit out
- 3 consecutive losses today → stop for the day

## Key Files
- `backend/app/strategies/strategy_2_vwap_pullback.py` — strategy logic, entry/exit rules
- `backend/app/indicators/market_levels.py` — index-level SL/target selection from market structure
- `backend/app/services/option_resolver.py` — strike selection, premium fetch, delta-based SL/target conversion
- `backend/app/services/strategy_runner.py` — orchestrates strategy → option_resolver → persist/broadcast
