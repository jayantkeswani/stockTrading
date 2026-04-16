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

## Exit Rules
- **Stop Loss:** 30-35% of option premium (e.g., buy at Rs 250, SL at Rs 175). Tighter SL (30%) when bias is strong, wider (35%) otherwise. Computed by `option_resolver.py` on actual option premium, not index price.
- **Target 1:** Book 60% at 1:1.5 risk-reward (e.g., SL 30% → target 45% above premium)
- **Target 2:** Trail remaining 40% with 9 EMA on 5-min as trailing stop
- **Time Exit:** Close all positions by 3:15 PM IST
- **Invalidation:** Close if underlying index price closes below VWAP (for calls) or above VWAP (for puts) on 5-min

## Position Sizing
- Base: 2% of capital per trade = Rs 20,000 risk
- If premium = Rs 250, SL at Rs 170, risk per lot = Rs 80 × lot_size
- NIFTY: Rs 80 × 75 = Rs 6,000 per lot → 3 lots max
- Adjust for VIX: if VIX > 18, reduce to 2 lots

## Risk Filters (Do NOT trade when):
- Daily drawdown already at 3%+ → reduce to 1 lot max
- Daily drawdown at 5% → HALT ALL TRADING
- Market is in first 15 minutes (9:15-9:30) → too volatile
- VIX > 22 → extreme conditions, sit out
- 3 consecutive losses today → stop for the day
