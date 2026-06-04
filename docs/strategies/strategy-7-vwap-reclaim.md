# Strategy 7: VWAP Reclaim (Index Options)

**Status:** ACTIVE, **ships dark** (shadow-only A/B vs live S2). Instrument: index options
(NIFTY, BANKNIFTY, FINNIFTY, SENSEX, MIDCPNIFTY). File:
`backend/app/strategies/strategy_7_vwap_reclaim.py`.

The entry redesign for Strategy 2. Design rationale + the four analyses that motivated it:
[`strategy-2-reclaim-entry.md`](strategy-2-reclaim-entry.md) and
[`../backtest/s2-signal-accuracy-study.md`](../backtest/s2-signal-accuracy-study.md). This
is the index-options analogue of the S5→S6 move.

## Why (the diagnosis, one line)

S2 fires on the reversal *candle* at VWAP, which is exhaustion — **55% of S2's losing
entries never go green** and stop within ~21 min. Targets and exits were both tested and
ruled out as fixes; the **entry** is the root cause. So require the reversal to *hold /
reclaim* before entering.

## The entry — arm → reclaim → fire (mirror S6)

State machine per `(symbol × side)`, in-memory and ephemeral (re-forms on restart),
evaluated on **every 1m candle close**:

1. **ARM** — S2's own trigger fires on the latest 1m close: price pulled back into the VWAP
   band (`vwap_min_distance_pct ≤ |dist| ≤ vwap_proximity_pct`, i.e. 0.05–0.15%), correct
   side of VWAP, a **5m reversal candle** (`is_bullish_reversal` / `is_bearish_reversal` on
   the completed-5m series), bias not STRONG-opposed (`is_blocked_by_bias`), and no 5m
   futures-volume spike. Record the **reversal candle's extreme** as the pullback swing
   (low for a CE bounce, high for a PE rejection) and its other end as the **reclaim
   trigger**. One live arm per side; never re-arms the same 5m reversal bar.
2. **RECLAIM** — a subsequent **1m** candle closes back *through* the trigger in the trade
   direction (CE: close above the reversal candle's high; PE: below its low), in the trade
   direction (green for CE / red for PE). This is the "did the bounce hold" gate S2 skips.
   `reclaim_ref="vwap"` switches the reference to a VWAP reclaim (tested worse — see below).
3. **FIRE** — entry at the reclaim close; **stop a hair past the pullback swing extreme**
   (`swing_buffer_pct`, with a `min_risk_pct` whipsaw floor and a `max_risk_pct` sanity cap)
   — the tight, mechanical stop is the R:R lever; target by R:R (`rr_multiplier`, default
   1.5). `index_sl` / `index_target` (index levels) are carried so `option_resolver`
   delta-converts them to a **tight premium stop** (a far tighter premium SL than S2's
   30–35%).
4. **ABORT** — no reclaim within `reclaim_timeout` 1m candles, or a 1m **slices through the
   swing extreme** before reclaiming (the pullback failed).

The 5m series is computed from `ctx.candles_1m` (`_completed_5m`, S6-style — no dependency
on how `candles_5m` was built; blocks of 5 align to the 09:15 boundary). The volume-spike
arm filter reads `ctx.candles_5m_futures_volume` for indices (spot volume is ~zero).

## Confidence — lean, structural, NOT gated

The S2 composite is **inverted** (study §3, r −0.224 vs outcome), so this strategy does
**not** reuse `compute_confidence`. It records a lean 3-factor structural score
(pullback-depth 0.40, bias-alignment 0.30, R:R 0.30) for calibration only and runs the
entry **effectively ungated by confidence** — the score is to be re-weighted from the
strategy's own win/loss data once the shadow A/B has outcomes. The LLM overlay is disabled
by default (`ai_overlay_enabled=False` in the defaults) — its ~25s latency erodes a
tight-entry fill while the structural stop stays pinned (same lesson as S6).

## Parameters (`VWAP_RECLAIM_DEFAULTS` in `strategy_params.py`)

`vwap_proximity_pct` 0.15 / `vwap_min_distance_pct` 0.05 (the arm band, reused from S2),
`vol_spike_mult` 1.2, `block_strong_opposing_bias` true, `reclaim_ref` `reversal_extreme`,
`reclaim_timeout` 5, `require_reclaim_green` true, `swing_buffer_pct` 0.03, `min_risk_pct`
0.03, `max_risk_pct` 1.0, `rr_multiplier` 1.5, `target_mode` `rr`, `trading_windows` +
`dead_zone` (same as S2, for executability-gate parity — like S2, signals are GENERATED
all session and shadowed; the window only gates executability), `ai_overlay_enabled` false.

## Offline validation (the ship-or-kill gate — PASSED)

`scripts/replay_strategy2_reclaim.py` drives the **live** `VWAPReclaimStrategy` minute-by-
minute over the index spot candles staged in `stocktrading_bt` (universe = the index-days
the live `vwap_pullback` fired on, for a clean head-to-head), then
`analyze_strategy2_signal_accuracy.py --strategy vwap_reclaim` scores first-touch. Window
2026-04-29 → 06-04, **98 vwap_reclaim signals** (54 CE / 44 PE) vs S2's 94.

| metric | S2 (vwap_pullback) | **vwap_reclaim** | read |
|---|---:|---:|---|
| Target-first (TARGET / resolved) | 15.6% (vs ~29.5% random) | **40.2%** (vs ~40.0% random) | S2's pathological stop-bias **removed** (was −14pp vs random → ~0) |
| Forward favorable +15 / +30 / +60 | 50.5 / 52.9 / 45.0% | **54.2 / 62.1 / 48.8%** | better at all 3 horizons |
| **+30 min forward, split halves** | — | **61.4% (H1) / 62.7% (H2)** | **robust** — the un-gameable, apples-to-apples win |
| r(confidence, hit) | −0.224 (inverted) | **+0.009** (flat) | the inversion is **broken** |
| resolved / total | 32 / 94 | 92 / 98 | the tight stop makes trades decisive |

**Verdict — PASS**, matching/exceeding the S6 precedent (18→36% / 37→49%). The honest
caveats: the target-first jump is partly a near-target geometry effect (the per-signal
random baseline rose 29.5%→40% with the R:R target), and target-first/binary-hit are *not*
robustly above random across the split — so the decisive evidence is the **+30 min forward
direction (~62% vs ~53%, robust in both halves)**, and the real test is the live shadow
real-P&L A/B (index direction ≠ option win-rate). Design A/B (folded in): `reclaim_ref=vwap`
floods to 157 weaker signals (target-first 39.6% ≈ random); `target_mode=structure` is no
better (36.4% ≈ random) — the `rr` + `reversal_extreme` default is the principled choice.

## Live dark-ship + real-P&L A/B (the decisive test)

Seed a `strategy_configs` row (`strategy_name='vwap_reclaim'`, `is_active=true`,
`auto_mode=true`, `shadow_enabled=true`, `yolo_enabled=false`, `symbols` = the 5 indices,
`parameters.ai_overlay_enabled=false`) so it generates signals and the shadow executor
mirrors them beside live S2 — **no YOLO, no change to the live S2 entry**. After a few
weeks, compare `vwap_reclaim` vs `vwap_pullback` shadow trades on realized option P&L (the
`scripts/research_strategy2_edge.py` method). The reclaim entry should lift S2's 31% win
rate and break the confidence inversion. **Watch-item:** the tight index swing stop
delta-converts to a tight *premium* stop (~1–2% on liquid options) — if the shadow book
shows premium whipsaw, add a premium-SL floor (this is exactly what the A/B is for).

## Tests

`backend/tests/test_strategies/test_vwap_reclaim.py` — drives the machine candle-by-candle:
happy path (tight swing stop + R:R target), no-reclaim, slice-through abort, reclaim
timeout, STRONG-opposing-bias gate, and the 5m aggregation helper.
