# S2 Reclaim-Entry Redesign (design spec)

**Status:** BUILT as **Strategy 7 (`vwap_reclaim`)**, ships dark — offline ship-or-kill
gate PASSED. This file is the design rationale; the canonical strategy doc (rules, params,
validation table, live A/B plan) is [`strategy-7-vwap-reclaim.md`](strategy-7-vwap-reclaim.md).
Design target for the entry redesign motivated by
[`docs/backtest/s2-signal-accuracy-study.md`](../backtest/s2-signal-accuracy-study.md).
This is the S2 analogue of the S5→S6 move.

> **Validation result (98 signals, 2026-04-29→06-04, `replay_strategy2_reclaim.py` +
> `analyze_strategy2_signal_accuracy.py --strategy vwap_reclaim`).** Target-first
> 15.6%→**40.2%** (S2's strongly-sub-random stop bias removed → ≈ random), +30 min
> forward-direction 52.9%→**62.1%** (robust in both split halves — the un-gameable win),
> confidence inversion broken (r −0.224 → +0.009). Cleared the bar; the decisive test is
> the live shadow real-P&L A/B. Open questions below were answered in replay: `rr` target +
> `reversal_extreme` reclaim is the principled default (`vwap` reclaim floods weaker
> signals; `structure` target is no better).

## Why (the diagnosis, in one line)

Four analyses on the live shadow book triangulate to one root cause: **S2 fires on the
reversal *candle* at VWAP, which is exhaustion — 55% of losing entries never go green and
stop within ~21 min.** Targets aren't the issue (capping cuts the fat tail), exits aren't
the issue (no stop-management policy beats baseline). The **entry** is. See the study's
"Stop-out probe" section.

## The change (mirror S6's arm → retest → reclaim)

Today S2 fires the instant a bullish/bearish reversal candle prints near VWAP. Instead,
treat that as an **ARM**, and only **fire on a 1m confirmation that the bounce/rejection
actually followed through** — with the stop at the tight pullback-swing extreme (the R:R
lever, exactly like S6).

State machine, per `(symbol × side)`, ephemeral (resets on restart), evaluated each 1m close:

1. **ARM** — the current S2 trigger fires: price pulled back to within `vwap_proximity_pct`
   of VWAP (and ≥ `vwap_min_distance_pct` away), correct side of VWAP, a reversal candle
   on the 5m, bias not STRONG-opposed. Record the **reversal candle's extreme** as the
   pullback swing (low for a CE bounce, high for a PE rejection) and its other end as the
   **trigger level**.
2. **CONFIRM/RECLAIM** — wait for a subsequent **1m** candle that closes back *through* the
   trigger in the trade direction (CE: 1m close above the reversal candle's high / VWAP
   reclaimed; PE: 1m close below the reversal candle's low), optionally volume-confirmed.
   This is the "did the bounce hold" gate that the current entry skips.
3. **FIRE** — entry at the reclaim close; **stop at the pullback swing extreme** (tight,
   mechanical) instead of the current 30–35% premium SL; target from `select_index_sl_target`
   as today (so the analyzer's index_sl/index_target read still works).
4. **ABORT** — no reclaim within `reclaim_timeout` 1m candles, or price slices through the
   swing extreme before reclaiming (the pullback failed). Cooldown before re-arming.

The single hypothesis: **requiring a green 1m follow-through before entry filters out the
"never-green" exhaustion entries** that are 55% of the current stop-outs.

## Build placement — new strategy `vwap_reclaim`, ships DARK (S6 precedent)

- New `StrategyName.VWAP_RECLAIM` + `strategy_7_vwap_reclaim.py`, **reusing S2's helpers**
  (the VWAP/bias/OI gates and `_build_signal` / `select_index_sl_target` / `compute_confidence`
  in `strategy_2_vwap_pullback.py` — refactor the shared bits into importable functions
  rather than copy-pasting). Only the entry *trigger* differs (arm→reclaim vs immediate).
- `instrument_type=OPTION` (same index options, strikes, expiry, option resolver).
- Ships **dark** (`is_active` config off / shadow-only) for a live paper A/B against live S2,
  exactly like S6 vs S5. No change to the live S2 entry until validated.

## Validation gates (do them IN ORDER; do not build live until ② passes)

The baseline to beat is the **current `vwap_pullback`** on the same window
(`analyze_strategy2_signal_accuracy.py`, 2026-04-29→06-04): target-first **15.6%**, forward
**50/53/45%**, and the shadow book (31% win, confidence inverted).

1. **Prototype offline first.** Write `scripts/replay_strategy2_reclaim.py` mirroring
   `replay_strategy6.py` (stateful, driven 1m-by-1m over the staged **index** candles in
   `stocktrading_bt`; universe = the days/indices S2 fired on). Persist signals as
   `strategy_name='vwap_reclaim'`.
2. **Accuracy gate.** Run `analyze_strategy2_signal_accuracy.py` (it reads index_sl/index_target,
   strategy-agnostic — add `vwap_reclaim` to its query). **Ship-or-kill:** the reclaim entry
   must beat the current entry's forward-direction and target-first on this window (the S6
   bar was 18%→36% / 37→49%). If it doesn't clear the baseline, stop — the idea failed.
3. **Real-P&L A/B (after live dark-ship).** Once shadowing live, compare `vwap_reclaim` vs
   `vwap_pullback` shadow trades on realized option P&L (the `research_strategy2_edge.py`
   method) over a few weeks. The reclaim entry should lift the 31% win-rate and break the
   confidence inversion.

## Suggested parameters (seed; tune in replay)

`reclaim_timeout` (1m candles to wait, ~3–5), `min_reclaim_vol_ratio` (reclaim volume vs
pullback, ~1.0 using futures volume for indices), `swing_buffer_pct` (stop a hair beyond the
swing), reuse `vwap_proximity_pct` / `vwap_min_distance_pct` from S2. Keep the same trading
windows + dead zone.

## Open questions for the builder

- Reclaim reference: the reversal candle's high/low vs a VWAP reclaim vs the pullback swing —
  test in replay.
- Whether to also **drop the confidence gate** in the dark-ship (the study's #1 finding) so
  the A/B isolates the *entry* change, or keep it — recommend running the reclaim entry
  **ungated** so the two changes aren't confounded.
- Confidence: do **not** reuse S2's composite to rank the new entry (no factor predicts —
  study §3). Rebuild from the new entry's own win/loss data, S6-style (lean factors).
