# Strategy 2 Signal-Accuracy Study

Measures the **"are the signals directionally right?"** half of S2 (VWAP Pullback,
index options) — the mirror of [`s5-signal-accuracy-study.md`](s5-signal-accuracy-study.md),
adapted from stock-futures to index options.

## Why this backtests faithfully (and where it does NOT)

S2 trades index **options**, but every signal carries **index-level** barriers
(`index_entry_price` / `index_sl` / `index_target` in `indicators` JSONB) next to the
premium entry/SL/target. Signal accuracy here = "did the traded index reach
`index_target` before `index_sl`" — a pure **first-touch** read of the index's own 1m
spot candles. No option premium, no theta, no trailing engine, and **no futures-spot
basis to adjust** (the index spot *is* the underlying). So unlike the S5 exit-P&L work
there is no ~3.6× engine gap.

> **Index direction is the *entry-quality* read; option money is the objective.** The
> index first-touch isolates "is the entry directionally right" with no theta/exit
> contamination. But the live trade is the option *premium*, so for the P&L question we
> read the **shadow trades directly** (real option fills + real exits) — see
> [Realized option P&L](#realized-option-pl-shadow-trades--the-objective-read) below. The
> two agree.

Tooling: `scripts/analyze_strategy2_signal_accuracy.py` (reuses `fetch_candles_after`
from `backtest_strategy5.py`). Window **2026-04-29 → 2026-06-04** (27 trading days),
against the local prod replica `stocktrading_bt` (**94 `vwap_pullback` signals**, all
confidences).

```bash
cd backend && source .venv/bin/activate
DATABASE_URL=postgresql+asyncpg://trader:trader_dev_123@localhost:5433/stocktrading_bt \
  python scripts/analyze_strategy2_signal_accuracy.py --start 2026-04-29 --end 2026-06-04
```

## Method

- For each signal, walk the **traded index's** 1m candles `generated_at → 15:30`.
- **First-touch** (only the 39/94 signals that carry both `index_sl` and `index_target`):
  `TARGET` if the index target wick is hit before the stop wick, `SL` if the reverse,
  `OPEN` if neither by 15:30. Same-candle both-touch → `SL` (conservative).
- **binary_hit** (barrier-independent, so calibration/factor analysis keeps all 94) =
  1 for `TARGET`; 0 for `SL`; for `OPEN` = 1 iff the EOD index close is favorable to the
  option direction (CE wants the index up, PE down).
- **target-first rate** = `TARGET / (TARGET + SL)` on the resolved subset.
- **per-signal random baseline** = `stop_dist / (stop_dist + tgt_dist)`. S2 takes
  *variable* market-structure R:R (≥ 1:1), so the random target-first is **not** a flat
  ~40% — we compare the observed rate against each signal's own geometry (mean ≈ 30%).
- **forward direction** (exit-free) = fraction of signals whose index close is favorable
  at +15 / +30 / +60 min.

### Data-shape facts (read before trusting any number)

1. **Only 39/94 signals carry index-level barriers.** The other 55 fell back to a
   premium `sl_pct`/`rr_multiplier` with no index levels (`market_levels.select_index_sl_target`
   returned `None` — no valid level or R:R < 1:1). First-touch / target-first are
   reported **only on the resolved subset (32 of those 39 resolve)**; the binary label is
   barrier-independent so the calibration and factor tables keep n = 94. *That ~59% of
   S2 signals lack a market-structure index level is itself worth a look (see Proposals).*
2. **Factor-set skew within the window.** Only **6** signals carry `global_alignment`
   (the early ones, before it was removed from `confidence.py` and re-weighted). It is
   reported but **uninterpretable at n = 6** — ignore it. The other 9 factors have full
   n = 94.
3. **Tie-break** (same-candle both-touch → SL vs target) changes nothing — simultaneous
   touches essentially never occur.

## Headline — signals are ~coin-flip and confidence is *counterproductive*

**94 signals** (47 CE / 47 PE; balanced across the 5 indices, 17–20 each).

| metric | value | read |
|---|---|---|
| Forward favorable +15 / +30 / +60 min | **50.5% / 52.9% / 45.0%** | coin-flip, fading by 60 min |
| EOD index direction favorable | **42.6%** | below a coin flip by close |
| Target-first (TARGET / resolved) | **15.6%** (32 resolved) | vs **~30%** per-signal random — SL hit ~5× more |
| TARGET / SL / OPEN | 5 / 27 / 62 | 66% never reach either index barrier |

The exit-free read is the trustworthy one: from the signal moment the index is a
**coin-flip at 15–30 min (51% / 53%) and drifts *against* the trade by 60 min and EOD
(45% / 43%)**. Where a trade resolves to a structural level, it's the **stop ~84% of the
time** (target-first 15.6% vs a random ~30%, because S2's market-structure targets sit
~2.4× the stop distance away). This is the same "not directionally predictive" verdict as
S5 — S2 is mean-reversion rather than S5's late-breakout-chasing, but the *accuracy* is no
better.

## Realized option P&L (shadow trades) — the objective read

The first-touch above answers "is the entry directionally right." The **actual objective
is option money**, and we already have it: the **shadow executor takes every signal above
the ~31% shadow floor as a 1-lot paper trade with real option fills and real
`trade_monitor` exits**. **96 closed shadow `vwap_pullback` trades** over the same window
(prod `trades`, `source=SHADOW`, joined to their signal). This is the decisive read — and
it confirms the direction study on real money, *more strongly*.

| metric | value |
|---|---|
| overall | 96 trades, **31% win-rate, −₹283 avg/trade — net-losing** |
| r(confidence, net_pnl) | **−0.286** |
| r(confidence, win) | **−0.273** |

By confidence (size-clean return% = `net_pnl / notional`, so different index lot sizes are
comparable):

| confidence | n | win% | avg return% | total net |
|---:|---:|---:|---:|---:|
| **< 50** | 34 | **47.1%** | **+7.8%** | **+₹18.0k** |
| 50–70 | 36 | 22.2% | −22.9% | −₹27.4k |
| ≥ 70 | 26 | 23.1% | −8.3% | −₹17.8k |

**Only the sub-50 cohort makes money.** The live `conf ≥ 70` execution gate selects a
23%-win, losing cohort; confidence is **inversely** related to realized P&L on both the
rupee and the size-normalized measure (r ≈ −0.28). CE vs PE is a wash on real money
(CE −8.3% / PE −7.7% return — the index study's vanished CE gap holds here too). Weakest
indices: BANKNIFTY (14% win) and MIDCPNIFTY (17%); best: NIFTY (45%) and SENSEX (41%) —
again matching the direction study.

> **Caveat:** shadow P&L conflates entry quality + exit logic + theta (many exits are
> `TIME_EXIT` at 3:25 — buying options and holding to close bleeds theta, which is part of
> *why* the book is net-negative). But the confidence **anti-correlation** is measured
> *within a constant exit logic*, so it's a clean read that the composite is harmful
> regardless of how exits are run.

## Edge research — the confidence composite is *inverted*

Mining the same 96 shadow trades for a profitable subset (`scripts/research_strategy2_edge.py`
— scans single filters + 2-way combos, scored on realized return% with a train/test date
split). The decisive structure is the **exit-reason mix** and how it splits by confidence:

| exit reason | n | win% | avg return% |
|---|---:|---:|---:|
| AGENT_SL (option stop) | 49 | 0% | **−33.3%** |
| TIME_EXIT (held to 3:25) | 30 | 43% | +2.4% |
| AGENT_PROFIT (target) | 17 | 100% | **+46.0%** |

The book loses because **stops outnumber targets ~3:1**, and that ratio is **entirely a
function of confidence**:

| cohort | target hits | SL hits | TIME_EXIT | result |
|---|---:|---:|---:|---|
| **conf < 50** | 13 (+40%) | 11 (−22%) | 10 (0%) | **+7.8% avg, 47% win, +₹18k** |
| **conf ≥ 50** | 4 (+67%) | 38 (−36%) | 20 (+4%) | net-losing |

**High-confidence signals reach their target 4 times in 62 (6%); low-confidence signals
reach it as often as they stop (13 vs 11).** This is not a penny-option artifact — the
conf<50 winners are real ₹100–570 options hitting genuine targets (NIFTY 220→319,
BANKNIFTY 319→570, SENSEX 371→698). And **`conf < 50` is the single most robust filter** —
profitable on the full set *and* positive in both the train (+0%) and test (+14%) halves;
no other single filter survives both.

**Mechanism.** The composite's weight sits on `reversal_quality` (0.20) and
`rr_ratio_quality` (0.10) — both shown *negatively* related to P&L (§3). A "high-confidence"
S2 signal is therefore, almost by construction, a **sharp reversal candle (exhaustion) with
a far target (high R:R)** — exactly the combination that stops out before reaching an
unreachable target. The score is not noisy; it is **backwards**.

> **Honesty check.** `conf < 50` survives this split but it is one window (n=34) and an
> *inverted* rule — the kind that overfits. The robust, low-regret action is to **remove
> confidence as an execution gate** (it demonstrably selects the losing cohort), not to
> literally "trade only < 50."

## Target-distance re-sim — nearer targets do NOT help (tested, negative result)

The natural fix for "targets get hit only 18%" is to cap the target nearer. **Tested it
and it fails.** `scripts/research_strategy2_target_resim.py` walks each trade's **real
option 1m premium candles** and re-sims a capped target vs the same SL (original target
re-sim'd on the same engine for an apples-to-apples baseline):

| target policy | win% | avg return% | targets hit |
|---|---:|---:|---:|
| original (far, re-sim) | 39.6% | **+3.3%** | 16/96 |
| cap +10% | 61.5% | −1.5% | 57/96 |
| cap +20% | 49.0% | −1.5% | 41/96 |
| cap +25% | 46.9% | −1.0% | 36/96 |
| cap +40% | 41.7% | −0.2% | 23/96 |

A nearer target **raises win-rate** (39.6% → 61.5% at +10%) and converts losers — at +25%,
**8 of 49 stop-outs and 11 of 30 time-exits become wins**. But **expectancy drops at every
cap**: the cap also chops the ~17 fat winners (which run to +46%+) down to +X%, and options
carry a long right tail — the few far targets that *do* print are what make the money.
Cutting winners short to raise hit-rate is a losing trade. The "targets are too far"
intuition is **wrong as a fix**.

It also does not rescue the inverted gate: even at +25%, `conf < 50` is still the only
profitable cohort (+5.6%) and `conf ≥ 70` still loses (−4.7%). The real leak is the **47
stop-outs at −33%** — addressed by *entry quality / stop management*, not target distance.

> **Engine note.** The 1m first-touch re-sim is optimistic vs live ticks (it flips the
> original target to +₹20.6k vs the live −₹27.1k — the known ~3.6× S5-style gap). Trust the
> *relative* cap-vs-original direction (unambiguous: nearer = worse), not the absolute net.

## Stop-out probe — bad entries, not bad exits

If targets and caps aren't the lever, what is? `scripts/research_strategy2_stopout_probe.py`
walks the **47 stop-outs' real premium paths** to ask the decisive question: *did the
premium ever go green before stopping?*

| max favorable excursion before the stop | count |
|---|---:|
| **< +5% (never meaningfully green)** | **26 / 47 (55%)** |
| +5–15% | 12 |
| +15–25% | 3 |
| +25%+ | 6 |

Median MFE **+3.9%**, median time-to-stop **21 min** (15 of 47 stop within 15 min). **The
majority of losing entries go straight against us** — they sit at a local premium top and
bleed immediately. That is an **entry-quality** problem, not a stop-placement one.

**What separates stop-outs from target-hits at signal time** (the same inversion, at trade
level): stop-outs carry *higher* confidence (60 vs 48), *higher* `reversal_quality`
(0.71 vs 0.64) and *higher* premium (₹257 vs ₹198). The "sharper reversal candle" the
score rewards is **exhaustion that fails**.

**Stop-management can't rescue it** (re-sim on the real candles, far target kept):

| policy | win% | avg return% | net (1-lot) |
|---|---:|---:|---:|
| original (no mgmt) | 39.6% | **+3.3%** | +₹20.6k |
| breakeven @ +15% | 21.9% | −1.2% | −₹34.3k |
| trail 33% from peak | 33.3% | +2.8% | +₹4.9k |

Breakeven *hurts* (it scratches winners that dip then recover — SL count 47→72); trailing is
neutral-to-worse. **You cannot manage your way out of a bad entry.**

**Conclusion — this is the S5→S6 parallel.** S5 chased late breakouts into local extremes;
**S2 buys exhaustion reversals at VWAP that immediately bleed.** Direction (coin-flip),
edge-mining (confidence inverted), the target re-sim (caps don't help) and this probe (55%
never green, exits can't fix) all triangulate to the same root cause: **the VWAP-pullback
reversal-candle entry**. The fix is an entry redesign — require the reversal to *hold /
reclaim* before entering (S6's retest+reclaim logic), not a parameter tweak.

## 1. Confidence calibration — negative, not just flat

| confidence | n | hit% (dir) | target-first% |
|---:|---:|---:|---:|
| 0–40 | 18 | 55.6% | 0.0% |
| 40–50 | 10 | 50.0% | 50.0% |
| 50–60 | 16 | **62.5%** | 16.7% |
| 60–70 | 18 | 38.9% | 12.5% |
| 70–80 | 26 | **23.1%** | 12.5% |
| 80+ | 6 | 33.3% | 0.0% |

**point-biserial r(confidence, hit) = −0.224** — and it is **negative in both halves of
a count-balanced split (−0.168 / −0.288)**, so this is robust, not a one-window fluke.
The composite is **inversely** related to being directionally right: the 50–60 bucket
hits **63%**, the 70–80 bucket only **23%**. The live `confidence ≥ 70` execution gate is
therefore selecting the *worse* signals on this window (see Filter lifts). This finding
**strengthened** when the window was extended from 25 to 27 days (r went from −0.168 to
−0.224).

## 2. CE vs PE and per-index

| cohort | n | hit% (dir) | target-first% | TGT / SL |
|---|---:|---:|---:|---:|
| CE (bullish) | 47 | 42.6% | 13.0% | 3 / 20 |
| PE (bearish) | 47 | 42.6% | 22.2% | 2 / 7 |

Binary hit is **identical** (42.6% each) — the "CE much worse" gap seen at 25 days did
**not** survive the extra two days, so treat it as noise. A residual asymmetry remains on
the *resolved* subset: CE resolves to its **stop far more often (20 vs 7)** and PE leads on
target-first (22% vs 13%) — directional, not robust.

| index | n | hit% (dir) |
|---|---:|---:|
| SENSEX | 18 | 55.6% |
| NIFTY | 20 | 45.0% |
| BANKNIFTY | 19 | 42.1% |
| FINNIFTY | 17 | 41.2% |
| MIDCPNIFTY | 20 | **30.0%** |

MIDCPNIFTY is consistently the weakest, SENSEX the strongest; NIFTY barely resolves
(18/20 OPEN — its index moves are small relative to the chosen barriers). Samples are
17–20 each — directional, not conclusive.

## 3. Factor importance — the composite is mis-signed, with *no* good factor

Point-biserial r of each stored `confidence_factors` sub-score vs the binary outcome
(full n = 94; `global_alignment` omitted — n = 6):

| factor | weight | r(hit) | lo⅓ hit% | hi⅓ hit% | lift |
|---|---:|---:|---:|---:|---:|
| volume_quality | 0.10 | **−0.218** | 51.6% | 41.9% | −9.7 |
| reversal_quality | 0.20 | **−0.193** | 58.1% | 35.5% | **−22.6** |
| time_of_day | 0.05 | −0.153 | 45.2% | 22.6% | −22.6 |
| rr_ratio_quality | 0.10 | −0.123 | 54.8% | 38.7% | −16.1 |
| vix_regime | 0.05 | −0.105 | 41.9% | 45.2% | +3.2 |
| oi_support | 0.10 | −0.088 | 51.6% | 45.2% | −6.5 |
| cpr_narrow_trending | 0.05 | −0.058 | 45.2% | 48.4% | +3.2 |
| vwap_slope_alignment | 0.15 | +0.022 | 38.7% | 38.7% | +0.0 |
| bias_alignment | 0.25 | **−0.012** | 38.7% | 41.9% | +3.2 |

**Not a single factor carries positive signal at full n.** `reversal_quality` (the
second-highest weight, 0.20) and `volume_quality` (0.10) lean meaningfully *negative*;
`bias_alignment` — the **highest-weighted factor (0.25)** — is flat (r −0.012). At 25 days
`bias_alignment` looked like the one good factor (r +0.144); the extra two days erased
that, so do **not** lean on it. This is why the composite's overall correlation is
negative: it is a weighted sum of factors that are flat-to-wrong. Plausible mechanics:
- **reversal_quality −0.193**: a *sharper* rejection candle at VWAP is more often
  exhaustion/a trap that fails than a clean continuation.
- **rr_ratio_quality −0.123**: a higher index-level R:R means the target is *farther*
  relative to the stop → mechanically lower probability of touching it first.
- **volume_quality −0.218**: rewarding the *lightest* pullback volume selects
  conviction-less drifts.

## 4. Regime / direction conditioning (directional, mostly NOT robust)

- **Own `intraday_bias.score`**: bias-aligned **43.6%** (n=39) vs bias-neutral 41.8%
  (n=55) — **no edge** (the apparent edge at 25 days vanished, consistent with
  `bias_alignment` falling to r≈0). There is no MODERATE/WEAK bias-opposed cohort — the
  live STRONG-opposed gate plus the neutral band absorbed them all.
- **NIFTY day-trend**: with-trend 40.9% vs counter-trend 43.5% — *no* edge, and the sign
  **flips across the split** (counter-trend 28.6% first half, 50.0% second). Not a usable
  broad-tape filter.
- **Window-state**: IN_WINDOW (tradeable) **32.6%** vs OUT_OF_WINDOW (informational)
  56.5%. The tradeable cohort is the worst in aggregate and **below its half-average in
  both split halves** (37.5% / 26.3%), though the magnitude is unstable. Directionally
  suggestive, not yet a reliable filter.
- **Time-of-day**: the 14:45–15:30 tail looks great (78%) but is n=9 with mostly OPEN
  trades classified by a near-EOD coin-flip — an artifact, not a signal.

## 5. S2-specific cuts (directional, treat as hypotheses)

| cut | better cohort | worse cohort |
|---|---|---|
| PDH/PDL proximity | away ≥0.30% **50.0%** (n=24) | near <0.30% 40.0% (n=70) |
| reversal_quality | weak-half **51.1%** | strong-half 34.0% |
| OI confirmation | oi_weak 54.5% (n=11) | oi_confirmed 41.0% |
| CPR type | WIDE 44.0% | NARROW 36.8% |
| India VIX | ≥18 50.0% (n=14) | 14–18 41.2% |
| VWAP distance | ≥0.10% 46.7% | <0.05% 23.1% (n=13) |

These reinforce §3: entries on **stronger reversal candles**, **with OI "confirmation"**,
and on **narrow CPR** — all rewarded by the current logic — under-perform. None survive
the count-balanced split cleanly given the small cohorts; they are leads, not filters.

## 6. Re-fire effect — re-fires are much *better* (as in S5)

| | n | hit% (dir) |
|---|---:|---:|
| first signal of index/day | 54 | 29.6% |
| 2nd+ (re-fire) | 40 | **60.0%** |

Same result as S5, and it **strengthened** with more data: subsequent same-index signals
hit ~30pp better. **Do not filter re-fires.**

## Filter lifts (re-measured on the binary hit-rate; base 42.6%)

| filter | kept n | kept hit% | lift | dropped hit% |
|---|---:|---:|---:|---:|
| **confidence ≥ 70** | 32 | **25.0%** | **−17.6** | 51.6% |
| COMBINED (¬bias-opposed ∧ ¬counter-trend ∧ in-window) | 36 | 30.6% | −12.0 | 50.0% |
| IN_WINDOW only | 43 | 32.6% | −10.0 | 51.0% |
| CPR NARROW only | 19 | 36.8% | −5.7 | 44.0% |
| OI-confirmed only | 83 | 41.0% | −1.6 | 54.5% |
| drop counter-trend | 71 | 42.3% | −0.3 | 43.5% |

Every "quality" gate the strategy currently leans on (high confidence, in-window,
narrow-CPR, OI-confirmed) **costs** hit-rate on this window. The single most striking
result: the **`conf ≥ 70` gate keeps the worst 34% and drops a 51.6% cohort**.

## Concrete, testable proposals

Ordered by evidence strength. None touch live signal generation yet — they are
*proposals to test*, and the lifts above are the predicted effect **on this 27-day
window only**. Correlations are weak (best |r| ≈ 0.22) and the sample is small (94);
**re-run the script on a fresh window before committing any code change.**

1. **Remove confidence as an execution gate — it is inverted, confirmed on real money.**
   r(conf, shadow net P&L) = −0.286; the live `conf ≥ 70` cohort hits its **target 4 times
   in 62 (6%)** and stops out 61%, while `conf < 50` hits target as often as stop and
   returns **+7.8%** (47% win, robust across train/test). The composite leans on
   `reversal_quality` + `rr_ratio_quality`, which select exhaustion reversals with
   unreachable targets. **First action: stop gating execution on `min_confidence_for_execution
   ≥ 70`** (it demonstrably selects the losing cohort). The shadow book *is* the A/B — no
   live experiment needed to know the current gate loses. *Do NOT naively invert to "trade
   only < 50": one window, n=34, an inverted rule — overfit risk. Removing the gate is the
   robust move; the target fix (#2) is the causal one.*
2. **Redesign the entry — it is the root cause (both target and stop fixes tested
   negative).** The stop-out probe shows **55% of losing entries never go green** (median
   time-to-stop 21 min): the VWAP-pullback *reversal candle* buys exhaustion at a local
   premium top that immediately bleeds. Capping targets cuts expectancy (kills the fat tail)
   and no stop-management policy beats the original — **you cannot fix a bad entry with
   exits**. The S5→S6 move applies directly: require the reversal to **hold / reclaim** the
   level before entering (a 1m confirmation after the reversal candle), rather than firing on
   the reversal candle itself. This is the real, structural lever — prototype it and re-sim
   on the shadow signals before shipping. Design spec:
   [`docs/strategies/strategy-2-reclaim-entry.md`](../strategies/strategy-2-reclaim-entry.md).
   *Bigger build; the evidence across all four analyses (direction, edge-mining, target
   re-sim, stop-out probe) points here.*
3. **The confidence composite cannot be rescued by reweighting — there is no good factor.**
   At 94 signals **no** factor is positively correlated with outcome, and the highest-weight
   (`bias_alignment` 0.25) is flat; the factors with weight (`reversal_quality`,
   `rr_ratio_quality`) are *negatively* signed. So removing the gate (#1) is right, and the
   score should not be trusted to rank a redesigned entry either — rebuild it from whatever
   the new entry's own win/loss data says, not from these factors.
4. **Surface why ~59% of signals lack an index-level SL/target.** 55/94 fell back to the
   premium %-based SL. Confirm whether `select_index_sl_target` is correctly discarding
   R:R < 1:1, or silently failing to find levels — the latter would mean most S2 signals
   trade on a coarse %-stop, not market structure.
5. **Leave re-fires alone** — they hit ~30pp better (as in S5).
6. **Do NOT act on CE/PE, window-state, PDH-PDL, or counter-trend cuts yet** — the
   CE-worse gap disappeared with more data, and the others flip or have unstable magnitude
   across the count-balanced split. Window-specific; revisit as the sample grows.

## Caveats

- Single 27-day window, **94 signals** (cohorts of 6–47); correlations are weak
  (best |r| ≈ 0.22). Treat every proposal as a hypothesis, not a fit. Re-run monthly.
- A **count-balanced** split (47/47) shows the calendar is lopsided: the first 47 signals
  span 04-29 → 05-26, the last 47 span just 05-26 → 06-04 — S2 signal volume surged in the
  final ~week. Both halves hit **42.6%**. The *robust* findings (negative & strengthening
  confidence r; coin-flip forward direction; target-first ≪ random; re-fires better) hold
  across both halves; the *fragile* ones (CE/PE, window-state magnitude, counter-trend) do
  not.
- Extending the window from 25 → 27 days **changed two findings**: `bias_alignment` fell
  from r +0.144 to −0.012 (no longer a "good" factor), and the CE-vs-PE binary gap closed
  (33%/47% → 42.6%/42.6%). A caution about how little it takes to move weak correlations.
- Target-first ≠ live option win-rate (theta + trailing exits). The trustworthy reads are
  index forward-direction and first-touch *direction*.
- This is the mean-reversion counterpart to the S5 momentum study. The thesis-invalidation
  exit work ([`s5-invalidation-exit-study.md`](s5-invalidation-exit-study.md)) already
  showed S2 is mean-reverting (regime-flip exits *hurt* it); this study shows its *entry*
  signal is, on this window, no more directional than a coin flip.
