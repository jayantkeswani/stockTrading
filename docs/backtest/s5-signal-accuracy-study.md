# Strategy 5 Signal-Accuracy Study

Measures the **"are the signals directionally right?"** half of S5 — separate from
the exit/invalidation P&L work in [`s5-invalidation-exit-study.md`](s5-invalidation-exit-study.md).

## Why this backtests faithfully (unlike the exit P&L study)

Signal accuracy = "was the signal directionally correct" = did the underlying reach
the signal's **TARGET before its STOP** within the session. That is a pure
**first-touch** read of 1m candles — no trailing stop, no tick polling, no exit
engine — so it does **not** suffer the ~3.6× engine-fidelity gap that made the exit
P&L untrustworthy (see [`s5-invalidation-exit-study.md`](s5-invalidation-exit-study.md)
"Engine fidelity"; memory `project_s5_invalidation_exit`). The hit-rate / calibration
numbers below can be trusted.

Tooling: `scripts/analyze_strategy5_signal_accuracy.py` (sibling of
`backtest_strategy5.py`, reuses its `fetch_candles_after`). Window **2026-04-29 →
2026-06-02** (25 trading days), against the local prod replica `stocktrading_bt`
(517 S5 `intraday_futures` signals, all confidences).

```bash
cd backend && source .venv/bin/activate
DATABASE_URL=postgresql+asyncpg://trader:trader_dev_123@localhost:5433/stocktrading_bt \
  python scripts/analyze_strategy5_signal_accuracy.py --start 2026-04-29 --end 2026-06-02 --basis-adjust
```

## Method

- For each signal, walk the **underlying stock** 1m candles `generated_at → 15:30`.
  S5 entry/SL/target are **futures** prices valued on the underlying spot candles
  (symbol = short name) — exactly like `backtest_strategy5.py` ("stock futures
  track spot intraday").
- **OUTCOME**: `TARGET` if the target wick is touched before the stop wick, `SL` if
  the reverse, `OPEN` if neither by 15:30 (then classified by EOD close direction).
  Same-candle both-touch → `SL` (conservative).
- **binary_hit** (the trustworthy label) = 1 for `TARGET`, 0 for `SL`, and for
  `OPEN` = 1 iff the EOD close is favorable to the signal direction.
- **target-first rate** (secondary) = `TARGET / (TARGET + SL)` — the cleanest
  "reached target before stop" read on *resolved* trades, excluding OPEN.
- **forward direction** (secondary, exit-free) = fraction of signals whose underlying
  close is favorable at +15 / +30 / +60 min.

### Data-fidelity fixes (material — read before trusting any number)

Two staging artifacts inject **fake** TARGET/SL resolutions; both are dropped:

1. **Wrong-instrument resolution (18 signals).** The signal's futures-priced band
   and the spot candle series must be on the same scale; real stock-futures basis
   is <1%. **12 "BSE" signals** carry `BSE:BANKEX26MAYFUT` prices (~60,000 — the
   *BANKEX index future*) while the "BSE" candle is **BSE Ltd stock (~4,000)**, a
   15× mismatch. A long there "hits SL" instantly (spot 4,000 is trivially below a
   ~58,000 stop). WIPRO (5) and NUVAMA (1) showed similar scale gaps. Dropped via
   `|entry basis| > 3%`. **This is a live instrument-resolution bug — see "Data
   bugs" below.**
2. **Inverted stop (1 signal).** One OFSS long had its stop *above* entry —
   un-executable; dropped.

Two checks confirm the remaining numbers are sound:
- **Basis** on the clean 498: median **+0.18%**, mean **+0.20%** — genuine
  monthly-futures premium, negligible. `--basis-adjust` anchors the spot path at
  the futures entry (compares spot against the premium-shifted barriers); it is the
  canonical mode here. Raw vs basis-adjust move the headline only ~2pp.
- **Tie-break** (same-candle both-touch → SL vs → target) changes **nothing** —
  simultaneous touches essentially never occur, so the conservative rule isn't
  biasing results.

## Headline — signals are not directionally predictive

**Clean set: 498 signals.** (`--basis-adjust`, after dropping 18 wrong-instrument +
1 inverted.)

| metric | value | read |
|---|---|---|
| TARGET / SL / OPEN | 36 / 163 / 299 | 60% never reach either barrier |
| Binary hit (TARGET, or OPEN-favorable-by-EOD) | **40.0%** | below a coin flip |
| Target-first rate (TARGET / resolved) | **18.1%** (199 resolved) | SL hit **4.5×** more than target |
| Forward favorable +15 / +30 / +60 min | **37.3% / 38.6% / 41.5%** | all < 50% |

Under a driftless random walk at the system's ~1:1.5 R:R (target 1.5× farther than
stop), target-first would be ≈ 40%. Observed **18%** is far *below* random: when an
S5 trade resolves to a structural level, it is the **stop ~82% of the time**. The
exit-free forward-direction read agrees — only ~37–42% of signals see price continue
in their favor over 15–60 min.

> **Honest caveat.** Target-first ≠ live win-rate: live exits trail and book partial
> winners well before the 1.5R target (the exit study's baseline showed ~53% "hits"
> under trailing). What is trustworthy and damning is the **exit-free direction**:
> from the signal moment, price more often moves *against* the trade. The pattern is
> consistent with **late breakout entries** (PDH_PDL / ORB / GAP buying strength at a
> local extreme that immediately pulls back) — forward-favorable is worst at +15 min
> (37.3%) and recovers slightly by +60 min (41.5%).

## 1. Confidence calibration — the model is uninformative

| confidence | n | hit% (EOD) | target-first% |
|---:|---:|---:|---:|
| 30–50 | 166 | 40.4% | 14.1% |
| 50–60 | 109 | 40.4% | 10.0% |
| 60–70 | 71 | **22.5%** | 4.0% |
| 70–80 | 96 | 44.8% | 24.3% |
| 80–90 | 28 | **57.1%** | 50.0% |
| 90+ | 10 | 50.0% | 100.0% |

**point-biserial r(confidence, hit) = +0.04 ≈ 0.** Calibration is flat and
non-monotonic below 80 (the 60–70 bucket is actually a *trough*). Only **conf ≥ 80**
(38 signals = 7.6% of volume) shows real edge — hit **55.3%**, target-first 50–100%.

This is the major finding: **the confidence score carries essentially no information
below ~80**, so the live **conf ≥ 70 execution gate sits in the flat region** — it is
not selecting better signals. The edge, such as it exists, lives only at the extreme
top of the range.

## 2. Per-setup accuracy

| setup | n | hit% (EOD) | target-first% |
|---|---:|---:|---:|
| ORB | 103 | **43.7%** | **31.9%** |
| PDH_PDL | 331 | 41.4% | 15.3% |
| GAP_CONTINUATION | 56 | **28.6%** | 8.8% |
| VWAP_BOUNCE | 8 | 12.5% | 14.3% |

`GAP_CONTINUATION` is the clear laggard (28.6% hit, 8.8% target-first) — the worst
setup with a meaningful sample. `ORB` is the strongest. `PDH_PDL` is the bulk (66% of
signals) and only mediocre. `VWAP_BOUNCE` is effectively unused (8 signals).

## 3. Factor importance — which of the 9 confidence sub-scores predict?

Point-biserial r of each `confidence_factors` sub-score vs the binary outcome, plus
the hit-rate of the bottom-third vs top-third of that factor's values:

| factor | n | r(hit) | lo⅓ hit% | hi⅓ hit% | lift |
|---|---:|---:|---:|---:|---:|
| oi_direction | 472 | **+0.108** | 40.1% | 47.1% | +7.0 |
| setup_quality | 498 | +0.067 | 39.2% | 39.2% | +0.0 |
| screener_rank | 498 | +0.054 | 34.9% | 44.6% | **+9.6** |
| stock_trend | 498 | −0.037 | 41.0% | 38.6% | −2.4 |
| volume | 498 | +0.034 | 39.2% | 38.0% | −1.2 |
| phase | 498 | −0.033 | 39.8% | 31.9% | −7.8 |
| gap_alignment | 498 | +0.018 | 38.0% | 41.0% | +3.0 |
| rvol | 498 | +0.012 | 41.0% | 39.2% | −1.8 |
| nifty_bias | 498 | +0.009 | 41.6% | 41.0% | −0.6 |

**No factor is a strong predictor** — the best (`oi_direction`) is only r ≈ 0.11. But
the ranking is informative: `oi_direction` and `screener_rank` carry the most signal,
while **`rvol`, `nifty_bias`, and `gap_alignment` — heavily used in the screener and
the confidence composite — are statistical noise** (r < 0.02). `phase` and
`stock_trend` even lean mildly *counter*productive (higher value → lower hit). This
explains the ≈0 composite correlation in §1: the composite leans on factors that
don't predict.

## 4. Regime / direction conditioning

**4a. vs NIFTY day-trend** (`nifty_day_change_pct` sign vs trade direction):

| group | n | hit% (EOD) |
|---|---:|---:|
| with-trend | 163 | **42.9%** |
| counter-trend | 113 | **29.2%** |
| flat tape (<0.10%) | 80 | 41.2% |

**4b. vs stock intraday_bias** (`intraday_bias.score` sign vs trade direction):

| group | n | hit% (EOD) |
|---|---:|---:|
| bias-aligned | 362 | 41.4% |
| bias-opposed | 28 | **10.7%** |
| bias-neutral | 59 | 33.9% |

Counter-trend signals (against the NIFTY day move) hit **13.7pp worse** than
with-trend. Signals where the **stock's own intraday_bias opposes the trade** hit a
dismal **10.7%** (n=28) — the cleanest negative cohort in the study. The live bias
gate is **STRONG-only**, so these moderate counter-bias signals slip through.

**4c. Time-of-day** is *not* a clean filter: morning (09:15–10:30) target-first is
23.7%, actually among the best; the midday 12:00–13:30 block is worst on target-first
(7.1%) but its high `hit%` is an artifact of more OPEN trades (less time to reach a
barrier → coin-flip EOD classification). No action.

**4d. Direction** is balanced and uniformly poor (LONG 39.4%, SHORT 40.6%) — the weak
accuracy is not a directional-market artifact.

## 5. Re-fire effect — re-fires are *better*, not worse

| | n | hit% (EOD) | target-first% |
|---|---:|---:|---:|
| first signal of symbol/day | 410 | 38.0% | 17.0% |
| 2nd+ (re-fire) | 88 | **48.9%** | 25.0% |

The hypothesis (re-fires are noise) is **rejected**: subsequent same-symbol signals
hit ~11pp *better* — consistent with confirmation/persistence. **Do not filter
re-fires.**

## Filter lifts (re-measured on the binary hit-rate)

| filter | kept n | kept hit% | lift | dropped hit% |
|---|---:|---:|---:|---:|
| drop counter-trend | 385 | 43.1% | **+3.2** | 29.2% |
| drop bias-opposed | 470 | 41.7% | +1.7 | 10.7% |
| drop GAP_CONTINUATION + VWAP_BOUNCE | 434 | 41.9% | +2.0 | 26.6% |
| **confidence ≥ 80** | 38 | **55.3%** | **+15.3** | 38.7% |
| **COMBINED** (not bias-opposed ∧ not counter-trend ∧ good setup) | 338 | **44.7%** | **+4.7** | 30.0% |
| first-signal-only | 410 | 38.0% | −1.9 | 48.9% |

`conf ≥ 80` is by far the highest-quality cohort but keeps only 7.6% of volume. The
**combined behavioral filter** lifts the hit-rate from 40.0% → **44.7% while keeping
68%** of signals — a more practical trade-off.

## Concrete, testable proposals

Ordered by evidence strength. None touch live signal generation yet — they are
*proposals to test*, and the per-filter lifts above are the predicted effect on this
window. Re-run the script on the next window before committing any code change.

1. **Hard-gate bias-opposed signals** (`strategy_5_intraday_futures` bias gate).
   Block signals where the stock's own `intraday_bias.score` opposes the trade
   direction, not just the current STRONG-only check. Evidence: bias-opposed hit
   **10.7%** (n=28); removing them lifts +1.7pp at trivial volume cost. *Cleanest,
   lowest-risk change.*
2. **Penalize (or gate) counter-trend signals** (bias gate / `_compute_confidence`).
   Signals opposing the NIFTY day-trend (|`nifty_day_change_pct`| > 0.10%) hit
   **29.2%** (n=113); removing lifts +3.2pp. Volume cost is larger (23%), so prefer a
   **confidence penalty** over a hard block, then re-test.
3. **Demote `GAP_CONTINUATION`** (28.6% hit, worst meaningful setup): drop it, or
   require conf ≥ 80 **and** with-trend. `VWAP_BOUNCE` is effectively unused (8
   signals) — leave or retire.
4. **Recalibrate confidence** (`_compute_confidence`). The composite is ~uninformative
   below 80 (r = +0.04). Two options: **(a)** treat conf < 80 as undifferentiated and
   only escalate execution priority at conf ≥ 80; **(b)** reweight — *down-weight*
   `rvol`, `nifty_bias`, `gap_alignment` (noise, r < 0.02), *up-weight* `oi_direction`
   and `screener_rank` (the strongest, though still weak), and investigate why `phase`
   and `stock_trend` carry mildly *negative* signs. Validate on a fresh window before
   shipping — these correlations are weak (best ≈ 0.11) and could be window-specific.
5. **Leave re-fires alone** — the data says they are slightly *better*.

## Data bugs surfaced (fix regardless of strategy tuning)

- **"BSE" stock futures resolve to `BSE:BANKEX26MAYFUT`** (the BANKEX index future,
  ~15× the BSE-Ltd price) — 12 signals over the window. A symbol-collision in the
  futures resolver (the `BSE:` exchange prefix matching the BANKEX index instead of
  BSE Ltd stock futures). These would size/execute on the wrong instrument live.
  Worth a dedicated fix + test in the futures/option resolver. WIPRO (5) and NUVAMA
  (1) also showed scale mismatches — verify their futures resolution too.
- **1 inverted-stop signal** (OFSS long, stop above entry) — un-executable;
  indicates a missing SL-orientation validation somewhere in signal construction.

## Caveats

- Single 25-trading-day window; correlations are weak (best |r| ≈ 0.11) — treat the
  factor reweighting as a hypothesis, not a fit. Re-run monthly as the sample grows.
- Target-first ≠ live realized win-rate (live trailing exits book partial winners
  before the 1.5R target). The trustworthy, exit-free reads are forward-direction and
  EOD-direction; both point the same way.
- Conf ≥ 80 (38 signals) and bias-opposed (28) cohorts are small — directionally
  clear, not conclusive.
