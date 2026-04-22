# Strategy 5: Intraday Stock Futures — Momentum Breakout Scanner

## Overview

An intraday strategy for trading **stock futures** on NSE. Unlike CAN SLIM (positional, held for days/weeks), this strategy identifies same-day momentum opportunities in F&O stocks and squares off all positions before 3:00 PM IST.

**Key difference from other strategies:** Two-phase approach — a **morning screener** ranks the best candidates before market opens, then **intraday setups** generate signals on those candidates throughout the day.

**Instrument:** Stock Futures (BUY_FUT / SELL_FUT)
**Holding type:** INTRADAY (square off by 3:00 PM)
**Target move:** 1-2% intraday

---

## Research Findings

### How Professional Intraday Traders in India Select Stocks

Based on extensive research across prop trading firms (PropaTrade, FundedStock), Indian trading educators (Vivek Bajaj, P.R. Sundar), broker platforms (Zerodha Streak, Tradetron, Chartink), academic research (NSE block-based ORB optimization papers), and professional trading communities.

#### The Consensus Approach

1. **Pre-market preparation** (6:00–9:15 AM): Check global cues, rank stocks by setup quality
2. **Opening observation** (9:15–9:30 AM): Watch ORB formation, volume surge
3. **Active trading** (9:30 AM–2:45 PM): Execute 2-4 high-quality setups
4. **Exit all** by 3:00 PM to avoid broker auto-square-off penalties

Professional traders consistently recommend **quality over quantity** — 3 good trades outperform 10 random ones.

---

## Phase A: Morning Screener — Pre-Market Stock Ranking

### Why Screen Before Market Opens

The F&O universe has ~180 stocks. Without filtering, you'd drown in noise. The morning screener reduces this to 10-15 high-probability candidates ranked by a composite "trade-ability score."

### Screening Factors

| Factor | Weight | What It Measures | Data Source | Scoring |
|--------|--------|-----------------|------------|---------|
| **Relative Strength vs NIFTY** | 20% | Stock outperforming the index | 1Y price history → RS raw score | RS > 80 (100pts), RS 50-80 (linear), RS < 50 (0pts) |
| **Previous Day Range & Close** | 15% | Directional bias from yesterday | Yesterday's OHLC candle | Close in top 25% of range = bullish (100pts), bottom 25% = bearish, middle = neutral |
| **Volume Trend** | 15% | Is recent volume increasing? | Last 5 days volume vs 20-day avg | Ratio > 1.5 (100pts), 1.0-1.5 (linear), < 1.0 (low) |
| **OI Change (prev day)** | 15% | Rising OI = new money entering | Bhav copy / OI snapshots | OI up + price up = 100pts, OI up + price down = 50pts (shorts building), OI down = low score |
| **Proximity to 52-Week High** | 10% | Momentum stocks near highs | Stock info | Within 5% = 100pts, 5-15% = linear, > 15% = low |
| **Sector Momentum** | 10% | Is the stock's sector in favor? | Sector-level RS ranking | Top 3 sectors = 100pts, middle = 50pts, bottom 3 = 0pts |
| **Delivery Percentage** | 10% | Genuine buying vs speculation | NSE bhav copy | > 50% delivery in uptrend = strong, < 30% = speculative |
| **Beta** | 5% | Larger intraday range = more opportunity | Computed from price history | Beta > 1.5 = 100pts (bigger moves), Beta < 0.8 = low |

### Composite Score

```
Watchlist Score = (RS × 0.20) + (PrevDay × 0.15) + (Volume × 0.15) + (OI × 0.15)
                + (52WH × 0.10) + (Sector × 0.10) + (Delivery × 0.10) + (Beta × 0.05)
```

**Minimum score to include:** 50/100
**Top 10-15 stocks** form "Today's Watchlist"

### Data Sources for Screening

**Already available in the codebase:**
- **Relative Strength:** `indicators/relative_strength.py` → `compute_rs_raw_score()`, `percentile_rank_rs()`
- **Previous Day levels:** `indicators/previous_day.py` → `analyze_previous_day()` returns PDH, PDL, PDC, bias
- **Volume analysis:** `indicators/volume_analysis.py` → `compute_avg_volume()`, `volume_ratio()`
- **52-week high proximity:** `stock_fundamentals.pct_from_52w_high` (in DB)
- **F&O stock universe:** `nse_client.get_fo_lot_sizes()` returns all F&O eligible stocks

**Needs building or sourcing:**
- **OI change (daily):** Can compute from `oi_snapshots` table (latest vs previous day) for configured stocks, or parse NSE bhav copy for broader coverage
- **Delivery percentage:** Available from NSE daily bhav copy — needs a new data fetch
- **Sector classification:** Can use yfinance `stock.info["sector"]` or maintain a static mapping
- **Beta calculation:** Compute from 1Y daily returns vs NIFTY returns (covariance / variance)

---

## Phase B: Intraday Signal Generation

### Setup 1: Opening Range Breakout (ORB)

**Research basis:** ORB is the most backtested intraday strategy for Indian markets. Academic study by Chenxi Wang on NSE showed 400%+ annual returns with proper filters. Works particularly well for stock futures due to cleaner price action vs indices.

**Rules:**
- **Opening Range:** High and Low of first 15 minutes (9:15–9:30 AM)
- **Breakout:** 5-minute candle closes above ORB high → BUY_FUT. Closes below ORB low → SELL_FUT
- **Volume confirmation:** Breakout candle volume > 1.2x average 5-minute volume
- **VWAP filter:** For longs, price must be above VWAP. For shorts, below VWAP
- **Stop Loss:** Opposite side of ORB range (e.g., if long above ORB high, SL at ORB low)
- **Target:** 1.5x risk (R:R = 1:1.5)
- **Time window:** 9:30 AM – 11:00 AM only (ORB loses edge after mid-morning)

**Enhanced ORB (ORB + PRB):** If price also breaks Previous Day's Range (PDH for longs, PDL for shorts) in the same direction, probability increases significantly. This is a stronger signal variant.

### Setup 2: VWAP Bounce / Pullback

**Research basis:** VWAP is THE institutional benchmark. Professional fund managers execute relative to VWAP. Price rejections from VWAP in trending stocks are high-probability entries.

**Rules:**
- **Trend identification:** Price consistently above VWAP (30+ minutes) = uptrend. Below = downtrend
- **Pullback:** Price pulls back to touch VWAP (within 0.2%)
- **Rejection:** Bullish reversal candle at VWAP (pin bar, engulfing, or just a strong close away from VWAP)
- **Volume:** Rejection candle volume > average volume
- **Stop Loss:** 0.3% beyond VWAP on the wrong side
- **Target:** Previous swing high/low, or 1.5x risk
- **Time window:** 10:00 AM – 2:45 PM (needs trend to establish first)

### Setup 3: Previous Day Level Breakout (PDH/PDL)

**Research basis:** PDH and PDL are the most watched levels by all market participants. Breakouts with volume at these levels generate strong intraday moves.

**Rules:**
- **Setup:** Price approaches PDH (Previous Day High) or PDL (Previous Day Low)
- **Breakout:** 5-minute candle closes above PDH → BUY_FUT. Below PDL → SELL_FUT
- **Volume confirmation:** Breakout candle volume > 1.5x average 5-minute volume
- **VWAP alignment:** Price must be on the correct side of VWAP
- **Stop Loss:** 0.5% below breakout level (PDH for longs, PDL for shorts)
- **Target:** Measured move = (PDH - PDL) range added to breakout price
- **Time window:** 9:30 AM – 2:00 PM

### Setup 4: Gap Trading (Continuation)

**Research basis:** 60-70% of gaps with volume confirmation continue in gap direction. Gap-up with high volume = institutional buying, likely to extend.

**Rules:**
- **Gap identification:** Opening price > 0.5% above previous close (gap up) or < 0.5% below (gap down)
- **Entry trigger:** After first 15 minutes, if price holds above gap level → BUY_FUT (gap continuation)
- **Volume:** Opening 15-min volume > 2x average
- **Stop Loss:** Below gap fill level (previous close)
- **Target:** Gap size projected forward (e.g., if gapped up 1%, target 2% from previous close)
- **Time window:** 9:30 AM – 11:00 AM

---

## Exit Rules (All Setups)

| Rule | When | Action |
|------|------|--------|
| **Hard Stop Loss** | Always set at entry | Close immediately |
| **Target 1 hit** | Price reaches 1.5x risk | Book 50% position |
| **Trailing stop** | After 1% gain | Move SL to breakeven |
| **Extended trail** | After 1.5% gain | Trail SL by 0.5% below current price |
| **Time exit** | 3:00 PM IST | Close ALL remaining positions |
| **Dead zone** | 11:30 AM – 1:00 PM | No new entries (low volume, choppy) |

---

## Risk Management

### Position Sizing

```
Position Size = (Capital × Risk%) / (Entry - StopLoss)
```

- **Risk per trade:** 1-1.5% of capital
- **Max daily drawdown:** 3% → stop trading for the day
- **Max simultaneous positions:** 3
- **Max trades per day:** 5 (including closed ones)

### Correlation Filter

- **No two trades from the same sector** simultaneously (e.g., don't long both TCS and INFY futures)
- Reduces portfolio correlation risk
- If a sector is trending, pick the strongest RS stock from that sector only

### Market Condition Filters

| Condition | Action |
|-----------|--------|
| India VIX > 20 | Skip all stock futures trades (too volatile, whipsaws) |
| India VIX < 12 | Reduce target expectations (low volatility, smaller moves) |
| NIFTY gap > 1% | Reduce position size by 50% (gap-day uncertainty) |
| First 15 minutes | No trades — observation only (ORB range forming) |
| Last 30 minutes | No new trades — only manage existing positions |

### Margin Management

- **Intraday stock futures margin:** ~20% of contract value (SPAN + Exposure)
- **Example:** RELIANCE futures (lot=250, price=₹2800) → Contract value = ₹7,00,000 → Margin ≈ ₹1,40,000
- **With ₹10L capital:** Max 3-4 positions simultaneously depending on stock price and lot size
- **Use Zerodha margin calculator** or broker API to verify before entry

---

## Key Indicators for Intraday Stock Futures

### Most Effective (Research-Backed)

| Indicator | Use Case | How |
|-----------|----------|-----|
| **VWAP** | Trend bias + entry level | Above = bullish bias. Pullback to VWAP = entry |
| **RSI (14)** | Overbought/oversold on 5m chart | > 70 = caution on longs, < 30 = caution on shorts |
| **Volume Ratio** | Confirm breakouts | Breakout with > 1.5x avg volume = valid |
| **ATR (14)** | Dynamic SL sizing | SL = 1.5-2x ATR from entry |
| **Previous Day H/L** | Key support/resistance | PDH = resistance for longs, PDL = support for shorts |
| **CPR** | Range identification | Narrow CPR = trending day expected. Wide CPR = range day |
| **OI Analysis** | Institutional positioning | Rising OI + rising price = bullish conviction |

### OI Interpretation for Stock Futures

| OI Change | Price Change | Interpretation | Trading Bias |
|-----------|-------------|----------------|-------------|
| OI UP | Price UP | Long buildup (new money entering bullish) | BUY |
| OI UP | Price DOWN | Short buildup (new money entering bearish) | SELL |
| OI DOWN | Price UP | Short covering (bears exiting) | Weak BUY (may fade) |
| OI DOWN | Price DOWN | Long unwinding (bulls exiting) | Weak SELL (may bounce) |

### Delivery Percentage Guide

| Delivery % | During Uptrend | During Downtrend | Trading Signal |
|-----------|---------------|-----------------|----------------|
| > 50% | Strong institutional buying | Heavy distribution | HIGH conviction |
| 30-50% | Mixed activity | Mixed activity | MODERATE conviction |
| < 30% | Mostly speculative | Panic selling | LOW conviction, avoid |

---

## Pre-Market Routine (Daily Workflow)

### 6:00 AM – 8:00 AM: Global Cues

- GIFT Nifty movement (overnight) — if > 0.5% gap expected, adjust strategy
- US market close (S&P 500, Nasdaq) — risk-on or risk-off
- Crude oil price — impacts energy sector stocks (ONGC, Reliance, etc.)
- Any major news (RBI policy, earnings announcements today)

### 8:00 AM – 9:00 AM: Run Morning Screener

1. Fetch previous day bhav copy (OI changes, delivery %, volume)
2. Compute RS rankings across F&O universe
3. Score each stock on the 8 screening factors
4. Rank and select top 10-15 candidates
5. Note PDH/PDL/PDC for each candidate
6. Identify which candidates have earnings/events today (avoid or special handling)

### 9:00 AM – 9:15 AM: Pre-Open Session

- Check pre-open auction prices for watchlist stocks
- Note expected gaps (gap-up / gap-down stocks)
- Finalize which 5-8 stocks to actively monitor
- Set alerts on PDH/PDL levels

### 9:15 AM – 9:30 AM: Opening Range Formation

- DO NOT TRADE in first 15 minutes
- Record ORB high/low for each watchlist stock
- Note opening volume vs average
- Identify strongest/weakest stocks relative to NIFTY

### 9:30 AM onwards: Execute

- Trade only the setups defined above
- Max 2-3 trades in the morning session
- Review during dead zone (11:30 AM – 1:00 PM)
- Possibly 1-2 trades in afternoon session

---

## Real-World Approaches

### How Prop Trading Firms Screen

PropaTrade (India's largest prop firm, 9,847+ funded traders) and FundedStock teach:
- **Strict risk management** comes before any strategy
- **Consistency** over big wins — funded traders must show controlled drawdowns
- **3-5 max positions** simultaneously, diversified across sectors
- **High-quality setups only** — ORB, gap continuation, VWAP bounce

### Educator Approaches

**Vivek Bajaj (StockEdge co-founder, 20+ years):**
- Relative Strength (RS55 model) for stock selection
- Multi-timeframe analysis: 2-hour chart for direction, 15-minute for entry
- Price action over indicators — support/resistance + volume

**Common themes across all educators:**
1. Risk management first (stop-loss, position sizing) before strategy
2. Multiple timeframe confirmation
3. Volume confirmation is essential
4. Emotional discipline > complex systems

### Backtesting Statistics

| Strategy | Win Rate | Profit Factor | Best Timeframe | Notes |
|----------|----------|--------------|----------------|-------|
| ORB (15-min) | 55-65% | 1.8-2.2 | 9:30-11:00 AM | Requires volume filter |
| VWAP Bounce | 60-70% | 1.5-2.0 | 10:00 AM-2:45 PM | Only in trending stocks |
| PDH/PDL Breakout | 50-60% | 1.6-2.0 | 9:30 AM-2:00 PM | Needs strong volume |
| Gap Continuation | 60-70% | 1.4-1.8 | 9:30-11:00 AM | Gap size > 0.5% |
| MACD + RSI combo | 73%+ | 2.0+ | 15-30 min candles | Optimized params outperform defaults |

---

## Data Sources Needed

### Already Available in Codebase

| Data | Source | Module |
|------|--------|--------|
| 1Y daily price history | yfinance | `data_sources/yfinance_client.py` |
| RS rating (percentile) | Computed | `indicators/relative_strength.py` |
| VWAP + bands | Computed from candles | `indicators/vwap.py` |
| Previous Day H/L/C | Computed | `indicators/previous_day.py` |
| CPR levels | Computed | `indicators/cpr.py` |
| Volume analysis | Computed | `indicators/volume_analysis.py` |
| Candlestick patterns | Computed | `indicators/candle_patterns.py` |
| Market levels / SL | Computed | `indicators/market_levels.py` |
| OI analysis | Fyers option chain | `indicators/open_interest.py` |
| 1-min OHLCV candles | Fyers WebSocket | `data_feed/feed_manager.py` |
| Historical candles | Fyers REST | `data_feed/fyers_client.py` |
| F&O lot sizes | NSE CSV | `data_sources/nse_client.py` |
| Stock fundamentals | yfinance + NSE | `tasks/fundamental_data_task.py` |
| Futures symbol resolution | Fyers symbol master | `services/futures_resolver.py` |

### Needs Building / Sourcing

| Data | How to Get | Priority |
|------|-----------|----------|
| **Daily OI change** | Compute from `oi_snapshots` table (latest vs prev day) or NSE bhav copy | HIGH |
| **Delivery percentage** | NSE daily bhav copy CSV download | MEDIUM |
| **Sector classification** | yfinance `info["sector"]` or static mapping table | MEDIUM |
| **Beta vs NIFTY** | Compute from 1Y daily returns: `cov(stock, nifty) / var(nifty)` | LOW (can derive from RS) |
| **Gap detection** | Compare today's open vs yesterday's close | LOW (simple calc, build as indicator) |
| **Pre-open data** | NSE pre-open session API (9:00-9:15 AM) | NICE-TO-HAVE |

---

## Implementation Notes (for when we build this)

### Architecture

- **New strategy class:** `strategy_5_intraday_futures.py` extending `BaseStrategy`
  - `instrument_type = InstrumentType.FUTURE`
  - `holding_type = "INTRADAY"`
  - Multiple sub-setups (ORB, VWAP, PDH/PDL, Gap) evaluated in sequence

- **Morning screener service:** `services/morning_screener.py`
  - Runs at 9:00 AM via APScheduler
  - Scores all F&O stocks → ranks top 15
  - Stores watchlist in Redis for fast access during trading
  - Exposes via API for dashboard display

- **New indicator:** `indicators/gap_analysis.py`
  - `detect_gap(open, prev_close)` → gap type, gap %, gap fill probability
  - `is_gap_continuation(candles, gap_direction)` → bool

### Reusable Code

Almost everything is already built:
- `BaseStrategy` pattern for strategy class
- `strategy_runner.py` for auto-mode + manual evaluation
- `futures_resolver.py` for stock → nearest futures contract
- All indicators (VWAP, PDH/PDL, CPR, volume, patterns, RS, OI)
- `candle_backfill.py` for historical data
- Signal → Trade → Position pipeline
- WebSocket broadcasting for real-time UI updates

### Configuration

- Add `INTRADAY_FUTURES` to `StrategyName` enum
- Add intraday-specific constants (ORB window, dead zone, exit time, etc.)
- Seed `strategy_configs` row with F&O stock universe or curated watchlist
- Frontend: add strategy label in `STRATEGY_LABELS`

---

## Market Timing Reference (IST)

| Time | Phase | Action |
|------|-------|--------|
| 9:00-9:15 AM | Pre-open auction | Read pre-open prices, finalize watchlist |
| 9:15-9:30 AM | ORB formation | Observe only — record high/low of first 15 min |
| 9:30-11:00 AM | Morning session | Execute ORB breakouts, gap continuations |
| 10:00-11:30 AM | Stable phase | VWAP bounce entries, PDH/PDL breakouts |
| 11:30 AM-1:00 PM | Dead zone | No new entries — low volume, choppy |
| 1:00-2:45 PM | Afternoon session | VWAP bounce, PDH/PDL breakouts only |
| 2:45-3:00 PM | Exit window | Close all remaining positions |
| 3:00-3:30 PM | Avoid | Broker auto square-off zone |

---

## Key Principles (from research)

1. **Screen broadly, trade narrowly** — scan 180 stocks, track 15, trade 3-5
2. **Volume is the truth** — never trade a breakout without volume confirmation
3. **VWAP is the institutional anchor** — respect it, trade around it
4. **Risk management is non-negotiable** — 1.5% per trade, 3% daily max
5. **Time is a filter** — avoid first 15 min, dead zone, last 30 min
6. **Sector diversification** — max 1 position per sector
7. **ORB has the strongest statistical edge** — but only with proper filters
8. **Delivery % separates real moves from noise** — high delivery in uptrend = conviction
9. **OI buildup confirms direction** — rising OI + rising price = strong long
10. **Exit discipline > entry skill** — have a plan before you enter
