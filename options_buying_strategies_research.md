# Indian Index Options BUYING Strategies - Comprehensive Research Report
## Capital: 10 Lakhs INR | Target: 2-3 Trades/Day | Max 5% Daily Drawdown

---

## TABLE OF CONTENTS
1. [Current Market Context (2026)](#1-current-market-context-2026)
2. [Opening Range Breakout (ORB)](#2-opening-range-breakout-orb)
3. [VWAP-Based Strategies](#3-vwap-based-strategies)
4. [Gap Up/Gap Down Momentum](#4-gap-upgap-down-momentum)
5. [Expiry Day Strategies](#5-expiry-day-strategies)
6. [Previous Day High/Low Breakout](#6-previous-day-highlow-breakout)
7. [CPR (Central Pivot Range)](#7-cpr-central-pivot-range)
8. [Supertrend + Indicator Combos](#8-supertrend--indicator-combos)
9. [Price Action / Supply-Demand](#9-price-action--supply-demand-zones)
10. [Multi-Timeframe Analysis](#10-multi-timeframe-analysis)
11. [AI/Quantitative Approaches](#11-aiquantitative-approaches)
12. [Risk Management Framework for 10 Lakhs](#12-risk-management-framework-for-10-lakhs)
13. [Data Sources & Tools](#13-data-sources--tools)
14. [Strategy Comparison Matrix](#14-strategy-comparison-matrix)
15. [Recommended Implementation Plan](#15-recommended-implementation-plan)

---

## 1. CURRENT MARKET CONTEXT (2026)

### SEBI F&O Regulatory Changes (Nov 2024 - Jan 2026)
These changes SIGNIFICANTLY impact your strategy design:

- **Weekly expiry discontinued** for Bank Nifty, FinNifty, Midcap Nifty (only NIFTY 50 and SENSEX retain weekly expiry)
- **Lot sizes revised (Jan 2026)**: NIFTY = 75 (previously 50), Bank Nifty = 30 (previously 15), FinNifty = 60, SENSEX = 20
- **Contract values**: SEBI mandated Rs 15-20 lakh notional value per contract
- **Additional 2% ELM** (Extreme Loss Margin) on short options on expiry day
- **No calendar spread margin benefit** on expiry day (effective Feb 2025)

### Impact on Your Capital (10 Lakhs)
- **NIFTY ATM option (1 lot)**: Premium ~Rs 150-300 x 75 = Rs 11,250 - Rs 22,500 per lot
- **Bank Nifty ATM option (1 lot)**: Premium ~Rs 200-400 x 30 = Rs 6,000 - Rs 12,000 per lot
- **With 10 lakhs**: You can comfortably trade 3-5 lots of NIFTY or 5-8 lots of Bank Nifty options
- **5% daily drawdown limit** = Rs 50,000 max daily loss

### Current Index Levels (April 2026)
- Bank Nifty: ~51,000-52,000 range
- India VIX: Monitor closely -- above 18 favors option buying, below 13 favors selling

---

## 2. OPENING RANGE BREAKOUT (ORB)

### Overview
The ORB strategy is one of the most researched and backtested strategies for Indian index options. It involves marking the high/low of the first N minutes after market open and trading the breakout.

### Backtested Results (Zerodha "In The Money" Research, Jan 2022 - Feb 2026)

**Option Buying Variant:**
- **Win Rate**: ~48%
- **Max Drawdown**: ~45% (significant!)
- **Capital per lot**: Rs 1.5L margin; 5 lots = Rs 7.5L
- **Equity Curve**: Smooth 2022-2023, deteriorated in 2024-2025, recovering in 2026
- **Year-by-year**: 2022-2024 profitable, 2025 was rough, 2026 started well

**Entry Rules:**
1. Reference window: 9:15 AM to 11:15 AM (this is wider than the typical 15-min ORB)
2. Select strikes with premium closest to Rs 200 at 9:16 AM (separately for CE and PE)
3. Track premium high and low through 11:15 AM close
4. Enter when premium breaks above the established range high
5. Use ATM or slightly ITM options (higher delta, lower theta decay)

**Exit Rules:**
- Stop-loss: 20% below entry premium (e.g., entry at Rs 250, SL at Rs 200)
- Time-based exit: Close by 3:15 PM

**Alternate 15-Minute ORB (More Common):**
1. Mark high and low of 9:15-9:30 candle
2. Buy CE if price breaks above high with volume
3. Buy PE if price breaks below low with volume
4. Confirm with VWAP (price above VWAP for longs, below for shorts)
5. SL: Rs 10-15 per lot or low of breakout candle
6. Target: Rs 10-20 per lot or 15-25 NIFTY points

**5-Minute Range Variant:**
- Research suggests the 5-minute range nearly **doubled returns** compared to the 15-minute range while reducing max drawdown
- Entry: 5-minute candle close outside the 9:15-9:20 range

**0DTE (Zero Days to Expiry) ORB Results (US market reference):**
- Win rate: 41.25%
- Average winner: $417.66, Average loser: $209.82 (nearly 2:1 payoff)
- Profit factor: 1.40

### Recommended ORB Parameters for Your Setup
- **Capital allocation**: 2-3 lots per trade (Rs 30,000-60,000 premium risk)
- **Best timeframe**: 5-minute or 15-minute opening range
- **Risk per trade**: 1.5-2% of capital (Rs 15,000-20,000)
- **Expected win rate**: 42-48%
- **Risk-reward**: 1:1.5 to 1:2
- **Trades per day**: 1-2 (CE or PE breakout, not both)

### Strengths
- Well-backtested for Indian markets
- Clear, rule-based entry/exit
- Works well on trending days
- Automatable

### Weaknesses
- 45% max drawdown is severe for option buying
- Poor performance in sideways/choppy markets
- 2025 was a difficult year across the board
- ~48% win rate means long losing streaks are common

---

## 3. VWAP-BASED STRATEGIES

### Strategy A: VWAP Pullback (Primary Strategy)

**Concept:** Trade pullbacks to VWAP in the direction of the prevailing trend.

**Entry Rules - Long (Buy CE):**
1. Price trending above VWAP (bullish bias)
2. Price pulls back to touch or come near VWAP
3. Bullish candle confirmation at VWAP (engulfing, pin bar)
4. Optional: Volume spike at VWAP touch
5. Buy ATM or slightly ITM Call (Delta 0.40-0.60)

**Entry Rules - Short (Buy PE):**
1. Price trending below VWAP (bearish bias)
2. Price retraces up to VWAP
3. Bearish candle confirmation at VWAP
4. Buy ATM or slightly ITM Put (Delta 0.40-0.60)

**Exit Rules:**
- Stop Loss: Rs 10-15 below entry premium OR below VWAP level
- Target: Rs 10-20 per lot or 15-25 NIFTY points
- Book 70% profits at target, trail remaining with breakeven stop

**Timeframe:** 1-3 minute charts for scalping, 5-15 minute for swing intraday

**Practical Example (from research):**
- May 26, 2025: NIFTY Futures crossed above VWAP
- Trade: 24850 CE at Rs 417
- Exit: Rs 441
- Gain: 20+ points per lot

### Strategy B: VWAP Breakout

**Entry Rules:**
1. Market opens, observe price relative to VWAP
2. If price crosses above VWAP with strong volume after consolidation -> Buy CE
3. If price crosses below VWAP with strong volume -> Buy PE
4. Confirm with RSI (>60 for longs, <40 for shorts)

**Exit Rules:**
- SL: Opposite side of VWAP
- Target: Previous day's high/low or R1/S1 pivot levels

### Key Rules
- **Avoid 12:30 PM - 2:00 PM** (choppy mid-day, low directional moves)
- Use weekly ATM or 1-strike ITM options for tight spreads
- Higher volume near VWAP = stronger reversal signals
- Best on trending days; avoid range-bound days

### Performance Expectations
- **Win rate**: 50-55% (better than ORB due to trend alignment)
- **Risk-reward**: 1:1.5 to 1:2
- **Capital per trade**: Rs 15,000-25,000 (1-2 lots)
- **Max drawdown**: Lower than ORB due to trend-following nature

---

## 4. GAP UP/GAP DOWN MOMENTUM

### Strategy Rules

**Setup:**
1. Identify gap of >50-60 points in NIFTY (or >100 points in Bank Nifty)
2. Observe first 15-minute candle after market opens (9:15-9:30)
3. Plot 20 EMA on 15-minute chart

**Entry - Gap Up Play (Buy CE):**
1. Market gaps up >50 NIFTY points
2. First 15-minute candle is bullish (closes near high)
3. Price breaks above the high of the first candle
4. Volume confirms the breakout
5. Buy ATM CE

**Entry - Gap Down Play (Buy PE):**
1. Market gaps down >50 NIFTY points
2. First 15-minute candle is bearish
3. Price breaks below the low of the first candle
4. Buy ATM PE

**Entry - Gap Fill Play (Counter-trend):**
1. Market gaps up but first candle is bearish/doji
2. Wait for price to fall below 20 EMA
3. Buy PE targeting gap fill to previous close

**Exit Rules:**
- SL: 30-40% of premium paid
- Target: 50-100% of premium (gap fills or trend continuation)
- Time exit: Close by 1 PM if target not hit

**Success Rate:** Claimed ~65-70% when gap is significant and first candle confirms direction

### Performance Expectations
- **Win rate**: 55-65% on significant gaps (>60 NIFTY points)
- **Risk-reward**: 1:1.5 to 1:2.5
- **Capital per trade**: Rs 15,000-30,000
- **Frequency**: 2-4 times per week (not every day has a significant gap)

### Caveats
- Not available every day
- Works best on news-driven gaps (budget, RBI, global events)
- Gap fill trades are riskier than gap continuation
- Theta decay can erode profits if the move is slow

---

## 5. EXPIRY DAY STRATEGIES

### IMPORTANT UPDATE: Post-SEBI Changes
- Only **NIFTY** (Thursday) and **SENSEX** (Friday) have weekly expiry
- Bank Nifty has only **monthly expiry** (last Monday)
- This significantly changes expiry day dynamics

### Strategy A: Directional Breakout Buying (Expiry Day)

**Backtested Results (52 weeks 2025-2026):**
- **Win rate**: 62%
- **Avg winning trade**: +68% return on premium
- **Avg losing trade**: -30% return on premium

**Entry Rules:**
1. **Window 1 (9:20-9:35 AM)**: Wait for first 5-min candle to close, enter in breakout direction
2. **Window 2 (11:30 AM-12:00 PM)**: Mid-day momentum after morning range is established
3. Buy ATM or 1-strike OTM (never more than 2 strikes OTM)
4. Confirm direction with volume and OI data

**Exit Rules:**
- Stop Loss: 30% of premium (set immediately at entry)
- Profit target: 50-80% gain on premium
- Exit ALL positions by 12:30 PM if not profitable
- Never hold past 3:15 PM

**Real P&L Examples:**
- Winning trade (Mar 12, 2026): 52,900 CE at Rs 125, exit Rs 295 = +Rs 4,250 (+68%)
- Losing trade (Mar 19, 2026): 53,200 PE at Rs 150, SL at Rs 105 = -Rs 1,125 (-30%)

### Strategy B: Gamma Blast (Post 1:45 PM Expiry Play)

**Entry Rules:**
1. Wait until after 1:45 PM on expiry day
2. Look for: Volume spike + IV jump + Price breaks consolidation
3. PCR (Put-Call Ratio) under 0.7 for bullish, above 1.3 for bearish
4. Buy ATM or OTM with gamma above 0.002, delta 0.3-0.5
5. A 100-point Bank Nifty move can cause 60-80 point swing in premium

**Exit Rules:**
- Stop Loss: 50 points or 30% premium
- Exit by 3:15 PM always
- Target: 2-3x premium paid (possible due to gamma acceleration)

**Risk:**
- Extremely high risk - premium can halve in minutes
- Theta loss reaches 70-80% by afternoon
- Only for experienced traders

### Strategy C: Expiry Day Option Buying After 1 PM

**Entry Rules:**
1. After 1 PM, check VWAP direction
2. Buy ITM or ATM options aligned with daily trend
3. If Bank Nifty breaks day's high -> Buy CE
4. If Bank Nifty breaks day's low -> Buy PE

**Exit:**
- SL: Cost of premium (essentially risk full premium)
- Target: 2-3x premium paid
- Example: Buy 48000 CE at Rs 40, if Bank Nifty breaks high -> option rallies to Rs 100 by 2:45 PM

### Theta Decay Profile on Expiry Day
- By 11:00 AM: ATM options lose ~35% of morning value
- By 2:30 PM: Only 10-15% value retained
- After 2:30 PM: Only sellers and scalpers profit (extremely risky for buyers)

### Key Rules for Expiry Day
- Risk no more than 2% of capital per trade
- Maximum 2 trades per expiry
- Never average down on expiry day
- Never buy more than 2 strikes OTM (probability of profit <15%)

---

## 6. PREVIOUS DAY HIGH/LOW BREAKOUT

### Strategy Rules

**Setup (Night Before):**
1. Mark previous day's high and low on NIFTY/Bank Nifty chart
2. Calculate the range (PDH - PDL)
3. Note: Works best when previous day range was narrow (indicating consolidation)

**Entry Rules - Long (Buy CE):**
1. Price breaks above Previous Day High (PDH)
2. Confirmation: Strong candle close above PDH on 5 or 15-min chart
3. Volume spike accompanies the breakout
4. VWAP is below current price (confirming bullish bias)
5. Buy ATM or 1-strike ITM CE (Delta 0.40-0.60)

**Entry Rules - Short (Buy PE):**
1. Price breaks below Previous Day Low (PDL)
2. Strong candle close below PDL
3. Volume confirms breakdown
4. VWAP is above current price
5. Buy ATM or 1-strike ITM PE

**Exit Rules:**
- SL: Back inside the previous day's range (PDH for longs, PDL for shorts) or Rs 10-15 premium
- Target 1: 1x the previous day's range from breakout point
- Target 2: Next support/resistance level
- Book 50% at Target 1, trail rest

**Best Timeframe:** 15-minute chart for confirmation, 5-minute for entry

### Performance Expectations
- **Win rate**: 50-55%
- **Risk-reward**: 1:2 to 1:3 (when trend continues)
- **Capital per trade**: Rs 15,000-25,000
- **Frequency**: 3-4 times per week

### Enhancement: Combine with CPR
- If PDH/PDL breakout aligns with narrow CPR (trending day expected), probability improves significantly

---

## 7. CPR (CENTRAL PIVOT RANGE)

### Calculation (Use Previous Day's Data)
- **Pivot (P)** = (High + Low + Close) / 3
- **Bottom Central (BC)** = (High + Low) / 2
- **Top Central (TC)** = (P - BC) + P = 2P - BC

### Strategy A: Narrow CPR Breakout (High Probability)

**Concept:** Narrow CPR (TC and BC very close together) signals a likely trending day.

**Entry Rules:**
1. Calculate CPR before market opens
2. If CPR is narrow (TC-BC < 20 points for NIFTY), expect a trending day
3. After first 5-minute candle at market open:
   - If closes above R1 -> Bullish bias -> Buy CE with target R2, R3
   - If closes below S1 -> Bearish bias -> Buy PE with target S2, S3
4. Buy ATM options

**Exit Rules:**
- SL: Just below CPR for longs, above CPR for shorts
- Target: R2/S2 initially, trail to R3/S3

**Backtested Performance (CPR Credit Spread variation):**
- Profitable in 69.5% of backtested weeks
- Average weekly return: 1.25%
- Average weekly loss: 3%

### Strategy B: Wide CPR Reversal

**Concept:** Wide CPR signals sideways/range-bound day. Trade reversals at extremes.

**Entry Rules:**
1. Wide CPR identified (TC-BC > 50 points for NIFTY)
2. Price reaches TC -> Buy PE (expecting reversal to BC)
3. Price reaches BC -> Buy CE (expecting reversal to TC)
4. Confirm with reversal candle pattern (engulfing, pin bar)

**Exit:**
- SL: Beyond TC/BC by 10-15 points
- Target: Opposite end of CPR

### Strategy C: Virgin CPR (Untested from Previous Day)

**Concept:** If previous day's CPR was never touched by price, it becomes "virgin" CPR and acts as very strong support/resistance the next day.

**Entry Rules:**
1. Identify virgin CPR from previous session
2. When price approaches virgin CPR level, look for reversal
3. Enter with options in reversal direction

### Performance Expectations
- **Win rate**: 55-65% (narrow CPR breakouts)
- **Risk-reward**: 1:1.5 to 1:2
- **Capital per trade**: Rs 15,000-25,000
- **Best for**: Pre-market analysis to determine day type (trending vs range-bound)

### Key Advantage
CPR can be calculated before market opens, giving you a clear game plan for the day. It does not change throughout the day, providing structural context.

---

## 8. SUPERTREND + INDICATOR COMBOS

### Strategy A: Supertrend + EMA Crossover

**Settings:**
- Supertrend: ATR Period = 10, Multiplier = 3 (default)
- For faster signals: ATR Period = 5, Multiplier = 1.5
- EMA: 9 EMA (fast) and 21 EMA (slow)

**Entry Rules - Long (Buy CE):**
1. Supertrend turns green (price above Supertrend line)
2. 9 EMA crosses above 21 EMA
3. Price is above both EMAs
4. Strong bullish candle with volume spike
5. Buy ATM Call (Delta 0.40-0.60)

**Entry Rules - Short (Buy PE):**
1. Supertrend turns red (price below Supertrend line)
2. 9 EMA crosses below 21 EMA
3. Price below both EMAs
4. Buy ATM Put

**Exit:**
- SL: Rs 10-15 per lot or when Supertrend flips color
- Target: Rs 10-20 per lot
- Book 70% at target, trail remaining with breakeven stop

**Timeframe:** 5-minute or 15-minute charts

### Strategy B: Supertrend + RSI

**Settings:**
- Supertrend: ATR = 5, Multiplier = 1.5 (faster)
- RSI: Period 7 (faster) or 14 (standard)

**Entry Rules - Long:**
1. Supertrend green
2. RSI > 50 and rising
3. Price crosses above 20-EMA
4. Volume increasing

**Entry Rules - Short:**
1. Supertrend red
2. RSI < 50 and falling

**Key Points:**
- Supertrend has ~40-45% hit ratio on 5-minute timeframe
- When target hits, profits are big; when SL hits, losses are small
- Works best in trending markets, fails in sideways
- 15-minute chart recommended as minimum timeframe

### Performance (General Supertrend Backtest)
- **Hit ratio**: 40-45% on 5-min charts
- **Profit factor**: Positive due to favorable risk-reward
- **NIFTY 1-hour Supertrend**: Showed outstanding results over 2009-2024 backtest
- **NIFTY 50 stocks**: Mixed results, slight overall loss (-1.16%)

### Performance Expectations for Options Buying
- **Win rate**: 40-48%
- **Risk-reward**: 1:2 to 1:3 (compensates for lower win rate)
- **Capital per trade**: Rs 15,000-25,000
- **Best in**: Trending markets (check VIX > 15 for trend likelihood)

---

## 9. PRICE ACTION / SUPPLY-DEMAND ZONES

### Strategy: Supply-Demand Zone Options Buying

**Concept:** Identify institutional buying/selling zones (supply = resistance, demand = support) and trade reversals/breakouts at these zones.

**Identifying Zones:**
1. Fresh demand zone: Strong bullish move originating from a consolidation base
2. Fresh supply zone: Strong bearish move originating from a consolidation top
3. Zones are most effective when fresh and untested
4. Repeated testing weakens zone reliability

**Entry Rules - At Demand Zone (Buy CE):**
1. Price reaches identified demand zone (previous base of a rally)
2. Look for engulfing candle on 15-min chart
3. Confirm with volume spike
4. Buy ATM CE

**Entry Rules - At Supply Zone (Buy PE):**
1. Price reaches supply zone (previous top of a decline)
2. Bearish engulfing or rejection candle
3. Buy ATM PE

**Confirmation:**
- OI (Open Interest) confirmation: OI unwinding at zone = stronger signal
- Volume must support the reversal
- Multi-timeframe alignment (zone visible on hourly, entry on 15-min)

**Exit Rules:**
- SL: Beyond the zone (if zone breaks, thesis is invalid)
- Target: Nearest VWAP, opposing zone, or R1/S1

### Performance Expectations
- **Win rate**: 55-65% (when using fresh zones only)
- **Risk-reward**: 1:2 to 1:3
- **Capital per trade**: Rs 15,000-25,000
- **Skill level**: Intermediate to advanced (requires discretionary judgment)

---

## 10. MULTI-TIMEFRAME ANALYSIS

### Framework: Top-Down Stack

**Daily Chart (Macro View):**
- Determine overall trend direction
- Identify major support/resistance levels
- Check EMA 20/50 alignment

**Hourly Chart (Medium View):**
- Identify immediate trend
- Mark key intraday S/R levels
- VWAP position

**15-Minute Chart (Trigger):**
- Entry signals using ORB, VWAP pullback, or breakout
- Candle pattern confirmation

**5-Minute Chart (Fine-Tuning):**
- Precise entry timing
- Scalp management

### MTF Rules for Options Buying
1. Only buy CE if daily and hourly trends are bullish
2. Only buy PE if daily and hourly trends are bearish
3. Use 15-minute for signal generation
4. Use 5-minute for entry execution
5. If higher timeframe conflicts with lower -> NO TRADE

### Options-Specific MTF Implementation
- Use underlying's MTF signals for direction
- Execute via options with Delta 0.30-0.40 for directional plays
- ATM options for higher delta exposure
- Use spreads to manage theta/IV when holding for more than 1 hour

### Performance Expectations
- **Win rate**: 55-65% (higher due to trend alignment)
- **Risk-reward**: 1:1.5 to 1:2
- **Reduced whipsaws** compared to single-timeframe approaches
- Best for: Avoiding false breaks around opening volatility

---

## 11. AI/QUANTITATIVE APPROACHES

### Available Tools & Platforms

**Backtesting Platforms:**
1. **AlgoTest** (algotest.in) - 25 free backtests/week, supports NIFTY/BANKNIFTY options
2. **StockMock** (stockmock.in) - Free, 1-minute historical data, strategy builder
3. **OptionBacktesting.in** - Free unlimited backtests for NIFTY, Bank Nifty, Midcap Nifty, FinNifty
4. **FreeBacktesting.in** - Free option strategy backtester
5. **Quantsapp** - Professional-grade options backtesting

**Open-Source Algo Trading:**
1. **OpenAlgo** (GitHub: marketcalls/openalgo) - Open-source algo trading framework
2. **NIFTY-OPTIONS-TRADING-AI** (GitHub) - AI-powered NIFTY options trading application
3. **pykiteconnect** (GitHub: zerodha/pykiteconnect) - Official Zerodha Python client

### Common Algorithmic Approaches

**Supertrend + ADX:**
- Backtested on NIFTY 5-minute charts
- ADX filter eliminates many false Supertrend signals
- Buy when Supertrend green AND ADX > 25

**Mean Reversion:**
- Assumes prices revert to historical averages
- Effective during consolidation and overbought/oversold conditions
- Buy CE when RSI < 30 and price at support, Buy PE when RSI > 70 and price at resistance

**Machine Learning:**
- ML algorithms used to predict NIFTY direction
- Typically combine: OI data, VIX, price action, volume, time features
- Best used as a filter on top of rule-based strategies
- Not a standalone solution - requires significant data engineering

### Implementation Path
1. Start with rule-based strategies on AlgoTest/StockMock
2. Paper trade with Sensibull
3. Backtest with 1-minute option data
4. Graduate to API-based execution (Fyers/Dhan/Zerodha)

---

## 12. RISK MANAGEMENT FRAMEWORK FOR 10 LAKHS

### Capital Allocation

| Allocation | Amount | Purpose |
|-----------|--------|---------|
| Active Trading Capital | Rs 6,00,000 (60%) | Day-to-day options buying |
| Reserve Capital | Rs 2,00,000 (20%) | Drawdown buffer |
| Strategy Development | Rs 1,00,000 (10%) | Testing new strategies |
| Emergency Buffer | Rs 1,00,000 (10%) | Never touch |

### Per-Trade Risk Rules

| Parameter | Rule |
|-----------|------|
| Max risk per trade | 1.5-2% of active capital = Rs 9,000-12,000 |
| Max trades per day | 2-3 |
| Max daily loss | 5% of total capital = Rs 50,000 |
| Max weekly loss | 10% of total capital = Rs 1,00,000 |
| Stop-loss per trade | 30-40% of premium paid |
| Position size | 1-3 lots per trade |

### Position Sizing Formula
```
Number of lots = (Capital x Risk%) / (SL amount per lot)

Example:
Capital = Rs 6,00,000
Risk = 1.5% = Rs 9,000
SL = Rs 15 per unit, lot size = 75 (NIFTY)
SL per lot = Rs 15 x 75 = Rs 1,125

Lots = 9,000 / 1,125 = 8 lots maximum

But premium cost also matters:
If ATM premium = Rs 200, cost per lot = Rs 200 x 75 = Rs 15,000
8 lots = Rs 1,20,000 (20% of active capital -- acceptable for 1 trade)
```

### VIX-Based Position Sizing
| India VIX | Position Size Adjustment |
|-----------|------------------------|
| < 12 | Normal size (1x) - options cheap, good for buying |
| 12-18 | Normal size (1x) |
| 18-25 | Reduce to 0.75x - options expensive but volatile |
| > 25 | Reduce to 0.5x - very expensive, but big moves possible |

### Daily Drawdown Protocol
1. **After -2% daily loss** (Rs 20,000): Reduce position size by 50%
2. **After -3.5% daily loss** (Rs 35,000): Take only 1 more trade max
3. **After -5% daily loss** (Rs 50,000): **STOP TRADING FOR THE DAY**
4. **After -10% weekly loss**: Take 1 day off, review all trades

### Time-Based Rules
- **Best trading hours**: 9:15-10:30 AM and 2:30-3:15 PM
- **Avoid**: 12:30 PM - 2:00 PM (choppy, low directional moves)
- **Thursday expiry** (NIFTY): Use Strategy 5 (expiry day strategies)
- **Monday**: Often volatile due to weekend news -- good for ORB

### Option Selection Rules
1. **Always ATM or 1-strike ITM** for buying (Delta 0.40-0.60)
2. **Never buy more than 2 strikes OTM** (win probability < 15%)
3. **Use weekly options** for intraday (higher gamma, tighter spreads)
4. **Prefer NIFTY** for weekly plays (only weekly expiry available)
5. **Use Bank Nifty** for monthly expiry plays (larger moves)

---

## 13. DATA SOURCES & TOOLS

### Free Historical Data Sources

| Source | Data Type | Period | Link |
|--------|----------|--------|------|
| GitHub: aeron7/nifty-banknifty-intraday-data | 1-min OHLCV | Various years | github.com |
| GitHub: ShabbirHasan1/NSE-Data | Live tick + minute data | Updated daily | github.com |
| GitHub: sandeepkapri/BankNifty-Data | 1-min to daily BankNifty | Various | github.com |
| Kaggle: NSE Nifty 50 Minute Data | 1-min OHLC | 2015-2026 | kaggle.com |
| Kaggle: Indian Stock Index 1-min Data | 1-min OHLC | 2008-2020 | kaggle.com |
| Trading Tuitions | 1-min Options OHLC | 2021 onwards | tradingtuitions.com |
| NSE India | Daily OHLC | Historical | niftyindices.com |

**Note:** Comprehensive free historical OPTIONS tick data (with all strikes and expiries) is HARD to find. Most free sources provide index/futures data only. For options data, consider paid sources or backtesting platforms.

### Backtesting Platforms (Free)

| Platform | Features | Best For |
|----------|----------|---------|
| **AlgoTest** | 25 free backtests/week, NIFTY/BANKNIFTY, strategy builder | Quick strategy validation |
| **StockMock** | Free, 1-min data, multi-leg strategies, detailed stats | Comprehensive backtesting |
| **OptionBacktesting.in** | Unlimited free backtests | Simple strategy testing |
| **FreeBacktesting.in** | Free options backtest | Basic testing |
| **Quantsapp** | Professional-grade, detailed Greeks | Advanced analysis |

### Paper Trading Platforms

| Platform | Options Support | Cost | Key Feature |
|----------|----------------|------|-------------|
| **Sensibull** | Full F&O | Free (basic) | India's #1 options platform, virtual trading |
| **MoneyBhai** (Moneycontrol) | Stocks, F&O | Free | Rs 10L virtual capital |
| **Trinkerr** | Indices, strategies | Free | Rs 10L virtual capital, 5 indices |
| **Zerodha Streak** | Futures only (no options) | Free | Strategy automation |
| **NSE Paathshala** | Basic stocks | Free | NSE official |

### Broker APIs (For Algo Trading)

| Broker | API Cost | Data | Best For |
|--------|----------|------|---------|
| **Zerodha Kite Connect** | Free (personal use) | No market data included | Established ecosystem |
| **Fyers** | Free | Free historical + live data | Best free data package |
| **Dhan (DhanHQ)** | Free | Real-time + historical | Modern API-first platform |
| **Shoonya (Finvasia)** | Free | Basic data | Zero brokerage model |
| **Angel One SmartAPI** | Free | Market data included | Retail-friendly |
| **Upstox** | Free | WebSocket real-time | Order execution focus |

**Recommendation:** Start with **Fyers** for free API + data, or **Dhan** for modern API experience. Use **Sensibull** for paper trading. Backtest on **StockMock** or **AlgoTest**.

---

## 14. STRATEGY COMPARISON MATRIX

| Strategy | Win Rate | Risk-Reward | Complexity | Automation | Daily Frequency | Best Market |
|----------|----------|-------------|------------|------------|-----------------|-------------|
| **ORB (5-min)** | 42-48% | 1:1.5-2 | Low | High | 1-2 | Trending |
| **ORB (15-min)** | 45-50% | 1:1.5 | Low | High | 1-2 | Trending |
| **VWAP Pullback** | 50-55% | 1:1.5-2 | Medium | Medium | 2-3 | Trending |
| **VWAP Breakout** | 48-52% | 1:1.5 | Medium | Medium | 1-2 | Trending |
| **Gap Momentum** | 55-65% | 1:1.5-2.5 | Medium | Medium | 0-1 | Gap days |
| **Expiry Breakout** | 60-62% | 1:2 | Medium | Low | 1-2 | Expiry days |
| **Gamma Blast** | 35-45% | 1:2-3 | High | Low | 1 | Expiry PM |
| **PDH/PDL Breakout** | 50-55% | 1:2-3 | Low | High | 1-2 | Trending |
| **CPR Narrow** | 55-65% | 1:1.5-2 | Medium | High | 1-2 | Trending |
| **CPR Wide** | 50-55% | 1:1.5 | Medium | Medium | 2-3 | Range-bound |
| **Supertrend+EMA** | 40-48% | 1:2-3 | Low | High | 2-3 | Strong trends |
| **Supply-Demand** | 55-65% | 1:2-3 | High | Low | 1-2 | All markets |
| **MTF Analysis** | 55-65% | 1:1.5-2 | High | Medium | 1-2 | Trending |

### Top 3 Recommended Strategies for Your Profile

**1. VWAP Pullback + CPR Context (Primary Strategy)**
- Use CPR to determine day type (trending vs range-bound)
- On narrow CPR days: Trade VWAP pullbacks in trend direction
- On wide CPR days: Trade reversals at CPR extremes
- Expected: 2-3 trades/day, 50-60% win rate, 1:1.5-2 RR

**2. Opening Range Breakout - 5 Minute Variant (Secondary Strategy)**
- Quick entry after 9:20 AM
- Clear rules, easily automated
- Use only on days with narrow CPR (trending expected)
- Expected: 1 trade/day, 45-50% win rate, 1:1.5-2 RR

**3. Expiry Day Directional Breakout (Thursday NIFTY / Monthly Bank Nifty)**
- Higher win rate (62%)
- Excellent risk-reward due to gamma
- Limited to expiry days (4-5 per month)
- Expected: 1-2 trades on expiry day, 60%+ win rate

---

## 15. RECOMMENDED IMPLEMENTATION PLAN

### Phase 1: Paper Trading & Backtesting (Weeks 1-4)
1. **Set up accounts**: Sensibull (paper trading), StockMock (backtesting), Fyers (API)
2. **Backtest** the top 3 strategies on StockMock with 2+ years of data
3. **Paper trade** daily on Sensibull with Rs 10L virtual capital
4. **Track**: Win rate, average win/loss, max drawdown, profit factor
5. **Target**: 100+ paper trades before going live

### Phase 2: Live Trading - Small Size (Weeks 5-8)
1. Start with **1 lot only** per trade (NIFTY or Bank Nifty)
2. Trade only the **primary strategy** (VWAP Pullback + CPR)
3. Max **1 trade per day** initially
4. Daily journaling: entry reason, exit reason, emotions, mistakes
5. **Target**: Positive P&L after transaction costs

### Phase 3: Scale Up (Weeks 9-12)
1. Add **second strategy** (ORB)
2. Increase to **2 lots** if Phase 2 was profitable
3. Allow **2 trades per day**
4. Start tracking strategy-level metrics separately

### Phase 4: Full Deployment (Month 4+)
1. Trade all 3 strategies as appropriate (based on day type)
2. Scale to **3-5 lots** per trade based on performance
3. Implement **2-3 trades per day** as planned
4. Consider API-based semi-automation for ORB entries
5. Monthly review and strategy refinement

### Daily Trading Routine

| Time | Activity |
|------|----------|
| 8:30 PM (night before) | Calculate CPR, mark PDH/PDL, check global cues |
| 9:00 AM | Check India VIX, SGX Nifty, pre-market data |
| 9:10 AM | Determine day type (narrow/wide CPR), plan strategy |
| 9:15 AM | Market opens - observe, mark opening range |
| 9:20-9:30 AM | ORB signal window (if applicable) |
| 9:30-10:30 AM | Primary trading window (VWAP pullback, ORB trades) |
| 10:30-11:30 AM | Monitor positions, trail stops |
| 11:30 AM-12:30 PM | Second entry window (if first trade was stopped out) |
| 12:30-2:00 PM | **NO NEW TRADES** (choppy period) |
| 2:00-2:30 PM | Assess final trading window |
| 2:30-3:15 PM | Final trades (only on strong signal days) |
| 3:15-3:30 PM | Close all positions, daily P&L review |
| Evening | Journal trades, review charts, prepare next day |

---

## CRITICAL WARNINGS

1. **SEBI data shows 9 out of 10 retail F&O traders lose money.** Option buying is the hardest way to make money in markets.

2. **A 48% win rate means long losing streaks are normal.** You may have 5-7 consecutive losses. Your risk management must survive this.

3. **Option buying is a negative expectancy game** unless you have a genuine edge. Theta decay works against you every second.

4. **The 45% max drawdown** seen in the ORB backtest means with Rs 10L, you could draw down to Rs 5.5L before recovering. Can you psychologically handle that?

5. **Transaction costs matter significantly** for option buying. At Rs 20/order + STT + GST, each round trip costs ~Rs 100-200 per lot. With 2-3 trades/day at 2-3 lots each, you spend Rs 600-1800/day on costs alone.

6. **Paper trading results will be better than live results** due to slippage, emotional decisions, and execution delays.

7. **Backtest results are not future guarantees.** Markets change. What worked in 2022-2024 may not work in 2026.

8. **Start small, validate, then scale.** Never deploy full capital without at least 100 live trades of validation.

---

## SOURCES

### ORB Strategy
- [Zerodha In The Money - ORB with NIFTY Options (Part 2)](https://inthemoneybyzerodha.substack.com/p/how-to-trade-opening-range-breakout)
- [ORB Strategy 400% Returns](https://tradethatswing.com/opening-range-breakout-strategy-up-400-this-year/)
- [0DTE ORB Backtested Results](https://options.cafe/blog/0dte-opening-range-breakout-strategy-spy-backtested-results/)
- [AlgoTest Range Breakout Strategy](https://docs.algotest.in/sample-algo-trading-strategies/range-breakout-strategy/)
- [OptionBotics - ORB Backtest and Automate](https://www.optionbotics.com/options-articles-guides-and-strategies/2026/03/07/opening-range-breakout-options-strategies-how-to-backtest-and-automate-orb-setup/)

### VWAP Strategies
- [Tradejini - Scalping NIFTY Options with VWAP, EMA, Box Breakout](https://www.tradejini.com/blogs/introduction-to-scalping-in-nifty-options)
- [Rupeezy - VWAP Trading Strategy Intraday Options](https://rupeezy.in/blog/vwap-trading-strategy-intraday-options)
- [OneTradeJournal - VWAP Trading Strategy with Nifty Examples](https://onetradejournal.com/strategies/vwap-trading-strategy)

### Gap Strategies
- [Groww - Gap and Go Strategy](https://groww.in/blog/gap-and-go-strategy)
- [TradingView - Gap Up/Down BANKNIFTY Strategy](https://in.tradingview.com/chart/BANKNIFTY/pJkb3vkv-Gap-up-gap-down-intraday-strategy-with-simple-entry-exit/)
- [TrueData - Gap Trading Strategy](https://www.truedata.in/blog/Gap-up-and-gap-down-intraday-trading-strategy)
- [Definedge - Backtested Gap Trends](https://www.definedgesecurities.com/blog/products/have-you-ever-backtested-gap-up-gap-down-trends/)

### Expiry Day Strategies
- [Bank Nifty Weekly Expiry Strategy 2026](https://bankniftyoptions.com/artigos/banknifty-weekly-expiry-strategy)
- [Enrich Money - Gamma Blast Strategy](https://enrichmoney.in/blog-article/gamma-blast-strategy-nse-options-expiry)
- [ICFM India - 5 Proven Intraday Options Strategies](https://www.icfmindia.com/blog/master-intraday-options-trading-5-winning-strategies-for-consistent-profits)
- [PL Capital - Bank Nifty Expiry Day Trading](https://www.plindia.com/blogs/bank-nifty-monthly-expiry-day-trading-options-strategy/)
- [StockManiacs - Expiry Day Option Strategy](https://www.stockmaniacs.net/expiry-day-nifty-option-strategy/)

### CPR Strategy
- [Zerodha Varsity - Central Pivot Range](https://zerodha.com/varsity/chapter/the-central-pivot-range/)
- [5Paisa - CPR Trading Strategy](https://www.5paisa.com/blog/cpr-central-pivot-range-trading-strategy-understanding-market-structure)
- [Stockfyre - CPR Explained for Intraday](https://stockfyre.com/what-is-cpr-in-trading-the-central-pivot-range-strategy-explained-for-intraday-traders/)
- [Groww - Central Pivot Range Guide](https://groww.in/blog/central-pivot-range)
- [ELearn Markets - Options Trading Using CPR](https://blog.elearnmarkets.com/options-trading-using-cpr-indicator/)

### Supertrend Strategies
- [MarketCalls - Supertrend Nifty Strategy](https://www.marketcalls.in/trading-lessons/game-changer-trading-strategy-nifty-based-supertrend.html)
- [ELearn Markets - Supertrend Best Settings](https://blog.elearnmarkets.com/supertrend-indicator-strategy-trading/)
- [Quantified Strategies - Supertrend Backtest](https://www.quantifiedstrategies.com/supertrend-indicator/)
- [GWC India - Top 5 Indicator Setups for Bank Nifty](https://www.gwcindia.in/blog/top-5-indicator-setups-for-bank-nifty-traders-in-india/)

### Price Action & Supply-Demand
- [Unofficed - Price Action Trading for Options](https://unofficed.com/price-action-trading/)
- [MAK Trading School - Price Action with Supply and Demand](https://maktradingschool.com/best-price-action-trading-strategy-using-supply-and-demand-zones/)
- [Ventura - Price Action Trading in India](https://www.venturasecurities.com/blog/all-about-price-action-trading-strategies/)

### Multi-Timeframe Analysis
- [Endovia Wealth - Multi-Timeframe Signal Generation](https://www.endoviawealth.com/multi-timeframe-signal-generation-aligning-intraday-positional-strategies/)
- [Bajaj Broking - NIFTY Intraday Options](https://www.bajajbroking.in/knowledge-center/how-to-do-nifty-intraday-option-trading)

### AI/Algorithmic Trading
- [GitHub - NIFTY-OPTIONS-TRADING-AI](https://github.com/Aditya0049/NIFTY-OPTIONS-TRADING-AI)
- [GitHub - Automated Nifty Options Trading](https://github.com/srikar-kodakandla/fully-automated-nifty-options-trading)
- [AlgoTest Platform](https://algotest.in/)
- [GitHub - OpenAlgo](https://github.com/marketcalls/openalgo)

### Risk Management & Position Sizing
- [Zerodha In The Money - Position Sizing Options](https://inthemoneybyzerodha.substack.com/p/position-sizing-part-3-sizing-option)
- [PL Capital - NIFTY Options Complete Guide](https://www.plindia.com/blogs/nifty-options-trading-india-2025-complete-guide/)
- [Univest - Bank Nifty Option Tips 2026](https://univest.in/blogs/bank-nifty-option-tips)

### Data & Tools
- [GitHub - NIFTY/BANKNIFTY Intraday Data](https://github.com/aeron7/nifty-banknifty-intraday-data)
- [Kaggle - Nifty 50 Minute Data](https://www.kaggle.com/datasets/debashis74017/nifty-50-minute-data)
- [StockMock Platform](https://www.stockmock.in/)
- [OptionBacktesting.in](https://optionbacktesting.in/)
- [Sensibull Platform](https://sensibull.com/)

### SEBI Regulations & Lot Sizes
- [Zerodha - SEBI New Rules for Index Derivatives](https://zerodha.com/z-connect/business-updates/sebis-new-rules-for-index-derivatives-heres-whats-changing)
- [Ventura - Lot Size Changes Jan 2026](https://www.venturasecurities.com/blog/nifty-bank-nifty-lot-size-changes-january-2026-know-how-it-impacts-traders/)
- [Zerroday - Nifty Lot Size 2026](https://zerroday.com/blog/nifty-bank-nifty-lot-size-2026)

### Broker APIs
- [Zerodha - Free Personal APIs](https://zerodha.com/z-connect/updates/free-personal-apis-from-kite-connect)
- [Fyers Trading API](https://fyers.in/products/api/)
- [DhanHQ Platform](https://dhanhq.co/)
- [Pocketful - Best Free Trading APIs](https://www.pocketful.in/blog/trading/best-brokers-offering-free-trading-api/)
- [AlgoTest - Best Brokers for Algo Trading 2026](https://algotest.in/blog/best-brokers-for-algo-trading-in-india/)

### Trader Interviews
- [Zerodha - From IIT to Intraday Options on Nifty](https://zerodha.com/z-connect/zerodha-60-day-challenge/winners/from-iit-to-intraday-options-on-nifty)

### India VIX
- [Zerodha - Trading India VIX Simplified](https://zerodha.com/z-connect/general/trading-india-vix-simplified)
- [Stack Wealth - India VIX for Trading](https://stackwealth.in/blog/stocks/how-to-use-india-vix-for-trading)
