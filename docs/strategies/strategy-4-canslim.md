# Strategy 4: CAN SLIM — Growth Stock Breakout (Stock Futures)

## Overview

CAN SLIM is a growth stock selection methodology by William O'Neil that combines fundamental screening with technical breakout entry timing. It targets **stock futures** on NSE for F&O eligible stocks (~208 stocks).

**Key difference from other strategies:** CAN SLIM is **positional** (holds for days/weeks), not intraday. Positions survive overnight and are managed by the agent with trailing stops and expiry rollover.

## How It Works

### Two-Layer Evaluation

1. **Fundamental Screening (Pre-computed):** The `fundamental_data_task` periodically fetches earnings, shareholding, and price data from yfinance + NSE. It computes individual CAN SLIM factor scores (C/A/N/S/L/I) and a composite score for each configured stock. This data is stored in the `stock_fundamentals` table.

2. **Real-Time Breakout Detection:** On each 1m candle close (auto mode) or manual scan, the strategy:
   - Reads pre-computed fundamental scores from DB
   - Checks minimum composite score (≥ 60)
   - Checks market direction (VIX < 20)
   - Detects chart base patterns from daily bars
   - Confirms price breakout above pattern resistance
   - Confirms volume surge (> 1.5x 20-day average)
   - If all pass → generates a BUY_FUT signal

### CAN SLIM Factors (India-Adapted)

| Letter | Factor | What We Check | Full Score (100) |
|--------|--------|---------------|-----------------|
| **C** | Current Earnings | Quarterly EPS growth YoY | ≥ 25% |
| **A** | Annual Earnings | 3-year EPS CAGR + ROE | ≥ 25% CAGR, ROE ≥ 20% |
| **N** | New Highs | Proximity to 52-week high | Within 5% |
| **S** | Supply/Demand | Free float, D/E, volume | Float < 40%, D/E < 0.5 |
| **L** | Leader | Relative Strength rating | RS ≥ 90 |
| **I** | Institutional | FII + MF holdings QoQ change | Both rising |
| **M** | Market Direction | NIFTY vs 50 DMA + India VIX | NIFTY > 50 DMA, VIX < 15 |

**Composite Score:** Weighted average (C=20%, A=20%, L=15%, M=15%, N=10%, S=10%, I=10%). Minimum 60/100 to qualify.

### Chart Base Patterns

Three patterns detected from daily OHLCV data:
- **Cup-with-Handle:** U-shaped correction (12-33% depth), recovery, small handle pullback
- **Flat Base:** Tight consolidation (< 15% range) after a prior 20%+ uptrend
- **Double Bottom:** W-shaped correction (15-35%), two lows within 3% of each other

**Breakout:** Price crosses above the pattern's resistance level on above-average volume (> 1.5x 20-day average).

## Entry Rules

- **Signal type:** `BUY_FUT` (buy stock futures)
- **Instrument:** Nearest-month stock futures contract (resolved by `futures_resolver`)
- **Entry price:** Current futures LTP at breakout
- **Confidence:** Composite CAN SLIM score (0-100)
- **Position type:** POSITIONAL (multi-day hold)

## SL/Target: Pattern-Based with Safety Caps

### How it works
SL and target are derived from the chart pattern's structure, not fixed percentages. The pattern's `base_low` (bottom of the cup/flat base/double bottom) and `breakout_price` drive the levels.

### Stop Loss
```
Pattern SL = base_low × 0.98    (2% below the pattern's lowest point)
Max SL     = entry × 0.92       (8% cap — never wider than this)
Final SL   = max(Pattern SL, Max SL)  → tighter of the two
```

**Example (Cup-with-Handle, ADANIPORTS):**
- Entry: ₹1,549.80, breakout: ₹1,545, base_low: ₹1,280 (17.2% depth)
- Pattern SL: ₹1,280 × 0.98 = ₹1,254 (too wide)
- Max SL (8%): ₹1,549.80 × 0.92 = ₹1,425.82 → **capped at ₹1,425.82**

**Example (Flat Base, shallow 5% depth):**
- Entry: ₹1,005, base_low: ₹950
- Pattern SL: ₹950 × 0.98 = ₹931 (tighter than 8% cap of ₹924.60) → **₹931 used**

### Target
```
Measured move   = breakout_price − base_low
Pattern Target  = breakout_price + measured_move
Min Target      = entry × 1.20   (20% floor — never less than this)
Final Target    = max(Pattern Target, Min Target)
```

**Example (Deep cup, 30% depth):**
- Entry: ₹510, breakout: ₹500, base_low: ₹350
- Measured move: ₹150, Pattern target: ₹650 (27.5% above entry)
- Min target (20%): ₹612 → **measured move wins at ₹650**

### Futures LTP Adjustment
When the futures resolver maps spot to futures LTP, SL/target percentage distances from spot entry are preserved proportionally against the futures LTP. Direction-aware: for longs, SL stays below and target above; for shorts, SL stays above and target below.

## Exit Rules

| Condition | Action | Exit Type |
|-----------|--------|-----------|
| Price hits SL (pattern-based or 8% cap) | Auto-close (all modes) | `SL_HIT` |
| Price reaches target (measured move or 20% floor) | Auto-close (YOLO) or confirm (SEMI) | `TARGET_HIT` |
| Price rose 10%+ then SL moved to breakeven | If retraces to breakeven, close | `TRAILING_SL` |
| Futures expiry within 3 days | Auto-close (YOLO) or alert (SEMI) | `EXPIRY_ROLL` |
| **No EOD exit** | Positional positions survive overnight | — |

### Trailing Stop Logic

When a position gains ≥ 10% from entry, the trade monitor automatically moves the stop loss to the entry price (breakeven). This protects profits while allowing further upside.

## Position Sizing

- **Method:** Risk-based: `risk_amount / (SL_distance × lot_size)`
- **Risk per trade:** 1.5-2% of capital (same as other strategies)
- **Max lots:** Capped at 2 (to manage futures margin)
- **Margin estimate:** ~18% of contract value (SPAN + exposure)

## Data Sources

| Data | Source | Refresh |
|------|--------|---------|
| Quarterly EPS, revenue | yfinance (.NS suffix) | Every 6 hours |
| Annual financials, ROE | yfinance | Every 6 hours |
| FII/DII/MF shareholding | NSE India API | Every 6 hours |
| Relative Strength | Computed from Fyers OHLCV | Every 6 hours |
| Daily candles (patterns) | MarketData1m (aggregated) | Real-time |
| Volume | Fyers WebSocket | Real-time |
| India VIX | Fyers (already integrated) | Real-time |

## Configuration

Strategy is configured via the Settings page, same as VWAP Pullback:

```
strategy_configs table:
  strategy_name: "can_slim"
  is_active: true
  auto_mode: true/false
  symbols: ["TCS", "RELIANCE", "INFY", ...]
  parameters: {}
  risk_params: {}
```

When symbols are added, the system auto-provisions:
1. REST quote → Redis price cache
2. Candle backfill → PostgreSQL
3. WebSocket subscription → live ticks
4. Fundamental data fetch → stock_fundamentals table

## Files

- `backend/app/strategies/strategy_4_canslim.py` — Strategy class
- `backend/app/strategies/canslim/scoring.py` — CAN SLIM factor scoring
- `backend/app/strategies/canslim/base_patterns.py` — Chart pattern detection
- `backend/app/services/futures_resolver.py` — Futures contract resolution
- `backend/app/data_sources/yfinance_client.py` — Yahoo Finance data
- `backend/app/data_sources/nse_client.py` — NSE shareholding data
- `backend/app/tasks/fundamental_data_task.py` — Periodic data fetcher
- `backend/app/models/fundamental_data.py` — StockFundamental + FundamentalHistory tables
- `backend/app/indicators/relative_strength.py` — RS rating
- `backend/app/indicators/volume_analysis.py` — Volume breakout detection
