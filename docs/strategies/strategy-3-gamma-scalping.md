# Strategy 3: Expiry Day Gamma Scalping

**Status:** STUB (not yet implemented)
**File:** `backend/app/strategies/strategy_3_gamma_scalping.py`

## Overview
Exploits maximum gamma on expiry days. ATM options have the highest gamma on expiry, meaning small index moves create disproportionately large premium moves.

## Applicable Days
- NIFTY: Thursday weekly expiry
- SENSEX: Friday weekly expiry
- BANKNIFTY: Monthly expiry (last Thursday)

## Rules (to be implemented)
- Identify 20-minute opening range (9:15-9:35)
- Trade breakouts with volume > 2x average
- ATM or 1-strike OTM (premium Rs 80-200)
- SL: 50% of premium
- Target: 100-200% of premium
- Exit by 2:45 PM regardless
