# Strategy 1: Opening Range Breakout (ORB) + VWAP Confirmation

**Status:** STUB (not yet implemented)
**File:** `backend/app/strategies/strategy_1_orb.py`

## Overview
Captures morning momentum by trading breakouts from the first 15-minute range, confirmed by VWAP direction. Best for the 9:30-11:00 AM window.

## Rules (to be implemented)
- Mark high/low of first 15-minute candle (9:15-9:30)
- Buy CALL on breakout above range high + price above VWAP + volume spike
- Buy PUT on breakdown below range low + price below VWAP + volume spike
- SL: 30-40% of premium
- Target: 1:1.5 risk-reward, trail remainder
- Max 2 trades per day
