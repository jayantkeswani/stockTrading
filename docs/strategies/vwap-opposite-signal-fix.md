# VWAP Pullback: Opposite-Signal Problem & Evolution

## Problem

On sideways/range-bound days, Strategy 2 (VWAP Pullback) fires contradictory signals — a BUY_CE and a BUY_PE — on the same index within minutes. Both signals pass all gates independently, and both can get executed.

The strategy decides direction based solely on which side of VWAP the price sits:

- Price above VWAP (even by 0.01%) → bullish signal (Call)
- Price below VWAP (even by 0.01%) → bearish signal (Put)

On trending days this works — price stays on one side of VWAP. On sideways days, price oscillates in a tight band around VWAP and the direction flips every few candles:

```
Candle 1:  Price 24812 | VWAP 24800 | +0.05% → CE signal fires
Candle 2:  Price 24793 | VWAP 24800 | -0.03% → PE signal fires
```

The current proximity check only enforces a ceiling (price must be within 0.15% of VWAP). There is no floor — even a 0.01% distance qualifies. At current levels that's a ~36 point zone on NIFTY where a 5-point flicker across VWAP is enough to flip direction.

Dedup doesn't catch this because it matches on `signal_type` — CE and PE are treated as independent signal streams.

## Root Cause

The strategy assumes a pullback within a trend. On choppy days, there's no trend to pull back from — price is just oscillating around VWAP. The system needs to verify that a trend existed before the pullback, not just that price is on a particular side of VWAP.

> "Your strategy is explicitly a pullback strategy, not a breakout strategy. A breakout from consolidation is a different setup. Trying to catch both with the same signal is the root cause of the whipsaw problem. Accepting that this strategy sits out the first move and only enters on the first pullback after a trend is established is the cleanest solution."

## Current State

What the strategy checks today, in order:

1. VWAP, previous day data, CPR must exist
2. At least 5 five-minute candles
3. Price within 0.15% of VWAP (fixed threshold, configurable via `vwap_proximity_pct`)
4. Direction decided by sign of `(price - VWAP)` — **no minimum distance**
5. STRONG opposing intraday bias blocks the signal
6. Bullish/bearish reversal pattern on 5m candles
7. Volume spike filter (current vol > 1.2x average rejects)
8. 10-factor confidence score must exceed persistence threshold
9. OI confirmation (soft factor in confidence)

**What we compute but don't use for entry filtering:**
- VWAP standard deviation bands (upper/lower, ±1 sigma) — computed every candle, only used for SL/target placement
- Swing high/low detection — used for SL/target, not for trend confirmation
- ATR — used by Strategy 5, not by Strategy 2

## Proposed Direction

### Fixing the Opposite-Signal Problem

Three layers, each addressing a different failure mode.

**Layer 1: Prior Swing Requirement.** Before firing a bullish signal, require that price made a swing high at least 0.5x ATR above VWAP (or reached the upper VWAP sigma band) earlier in the session, and is now retracing toward VWAP. Mirror for bearish. This directly tests the strategy's premise — a pullback can only exist if there was a prior move away from VWAP. On flat days, price never gets far enough to create a qualifying swing, so no signals fire.

**Layer 2: VWAP Sigma Bands.** Replace the fixed 0.15% proximity threshold with VWAP standard deviation bands. Price inside ±1 sigma is "at VWAP" — too close to determine direction. Only when price is between 1 sigma and 2 sigma does it qualify as a meaningful pullback zone. The bands adapt to the day's volatility — tight choppy days produce narrow bands that naturally suppress signals. We already compute these bands on every candle.

**Layer 3: Conflict Suppression.** If a PENDING signal exists for the opposite direction on the same symbol, block the new signal. The system must resolve its current directional bet (executed, stopped out, or expired) before reversing. This is the safety net — even if Layers 1 and 2 have edge cases, two contradictory signals can never coexist.

### Evolving Toward a Multi-Setup Options Strategy

The opposite-signal fix addresses the immediate bug, but there's a larger architectural insight: the VWAP pullback is only one type of intraday setup. It catches re-entries within an established trend, but it has no way to catch the initial move that establishes the trend. On days where no strong trend develops, it either sits idle (good) or fires on noise (the bug).

Expert recommendation: think of intraday options trading as a two-mode system —

1. **Breakout mode** (early in the day or when no trend exists) — catch the initial directional move
2. **Pullback mode** (after trend is confirmed) — catch re-entries on retracements to VWAP

The two modes cover different market phases rather than competing. The regime detection needed ("is the market trending or ranging?") is the same filter needed to fix the whipsaw problem — solving one gives you the architecture for both.

**Complementary setups that fit this framework:**

**Opening Range Breakout (ORB).** Define the opening range as the high/low of the first 15-30 minutes. A decisive break beyond that range (candle close, not just a wick) with above-average volume signals the initial move. Narrow opening ranges produce stronger breakouts. This pairs naturally with VWAP pullback — ORB catches the initial move, then VWAP pullback catches re-entries once the trend is established.

**Previous Day High/Low Breakout (PDH/PDL).** NIFTY and BANKNIFTY respect previous day's high and low as significant levels. A breakout above PDH (buy Calls) or below PDL (buy Puts) suggests new buying/selling interest beyond what was available the prior session. Requires a candle close beyond the level or 3-5 minutes of sustained hold. Works best on days that open within the previous day's range and expand out of it — less useful on gap days where price opens beyond PDH/PDL.

**Consolidation/Range Breakout.** When price chops sideways for 30-60+ minutes (3-4 touches on both upper and lower boundaries), mark the range and wait for a decisive exit on above-average volume. This directly turns the condition that causes VWAP whipsaws — sideways chop — into a setup. The system detects "chop mode," suppresses VWAP pullback signals, and arms the consolidation breakout instead.

**How breakouts fail and how to filter:**
- Retest confirmation: instead of entering on the break, wait for a pullback to retest the broken level as support/resistance, then bounce. Slower but dramatically reduces false entries. This retest entry is essentially a pullback trade — the existing VWAP pullback logic could be adapted for it.
- Volume profile: genuine breakouts show a volume surge. Price drifting past a level on average volume is more likely a fake.
- Time-of-day: breakouts in the first hour and last hour of the session tend to be more reliable than mid-day when volume thins out.

**Strategy 5 already implements this pattern** for stock futures — a phase state machine (ORB_FORMING → MORNING_ACTIVE → CAUTION_ZONE → AFTERNOON) with multiple sub-setups (ORB, VWAP Bounce, PDH/PDL, Gap Continuation) dispatched by phase and priority. The options strategy would follow the same architecture.

## Approaches Considered and Rejected

**Minimum distance floor (fixed %)** — Adding a floor (e.g., 0.05%) to the proximity check. Simple but doesn't adapt to volatility. A fixed floor that works on a low-vol day is too restrictive on a high-vol day. Superseded by Layer 2 (sigma bands) which achieves the same goal adaptively.

**Cooldown after firing** — Suppress opposite-direction signals for N minutes after a signal fires. Arbitrary time-based patch that doesn't address signal quality. Superseded by Layer 3 (conflict suppression) which is logic-based rather than time-based.

**Sideways market detection** — Count VWAP crosses or check if ADR is below a threshold to detect chop and suppress signals entirely. Reactive — by the time you've confirmed chop, losses are already taken. Also overly broad (suppresses all signals, not just bad ones). Superseded by Layer 1 (prior swing) which naturally suppresses on flat days without needing explicit chop detection.

**Consecutive candles arming** — Require 2+ consecutive candle closes on the same side of VWAP before firing. Adds latency on fast moves. Redundant if Layer 1 is in place — a prior swing above VWAP already proves directional commitment more reliably than 2 candle closes.

**VWAP slope filter** — Only fire if VWAP's slope confirms direction (rising VWAP for CE). VWAP moves too slowly — a fresh breakout above flat VWAP would be delayed until the slope catches up. Partially covered by Layer 1 which checks actual price action rather than the indicator's slope.

## Open Questions

- What's the right ATR multiplier for the prior swing requirement? 0.5x ATR is the starting suggestion but needs backtesting.
- Should Layer 3 (conflict suppression) expire the old signal when the opposite fires, or block the new one? Current thinking: block the new one — the strategy should resolve its current bet first.
- How do the sigma bands behave in the first 15-20 minutes when there are few candles? May need a minimum candle count before trusting the bands (we already gate on 5 candles).
- Should the multi-setup evolution be a rename of Strategy 2 or a new strategy that retires Strategies 1 (ORB stub), 2 (VWAP Pullback), and 3 (Gamma Scalping stub)?
