# Strategy 6: Breakout-Retest Intraday Futures

**Status:** ACTIVE (ships dark — `is_active=false` by default) · **Instrument:** stock futures (BUY_FUT / SELL_FUT) · **File:** `backend/app/strategies/strategy_6_breakout_retest.py` · **Holding:** INTRADAY · **Universe:** the shared S5 screener watchlist (`strat5:watchlist:{today}`).

## Why this exists

S5's signal-accuracy study (`docs/backtest/s5-signal-accuracy-study.md`) showed S5's *entries* are directionally weak — target-first **18%** (vs ~40% under a random walk at 1:1.5 R:R, i.e. *worse than random*), forward-favorable 37/39/42% at +15/30/60 min, worst at +15 min. That signature is **late breakout-chasing into a local extreme that immediately pulls back**: S5 buys the break, the retrace stops it out before any move resumes.

Strategy 6 trades the same structural levels but enters on the **retest**, not the break — so the entry sits next to a tight, mechanical invalidation. It runs as a *separate* strategy (own `StrategyName`, `strategy_configs` row, agent-log prefix, YOLO profiles) so it A/B's cleanly against S5 on the same universe.

**The edge is entry geometry, not direction prediction.** You don't beat the ~coin-flip direction problem; you stop bleeding R:R by entering near the level (tight stop) instead of extended above it, and you subtract the structurally-doomed cohorts (counter-trend, bias-opposed) with machine discipline. The one weak-but-real informational input is FUT-OI on the reclaim.

## The retest state machine

State is tracked **per (symbol × level)**, in-memory and ephemeral (re-forms on restart — arms are short-lived; we deliberately skip breakouts we didn't witness rather than chase). `evaluate()` runs on every **1m** candle close.

| State | Enter when | Tracks |
|---|---|---|
| `ARMED` | a *completed* 5m candle closes beyond level L by `min_breakout_ext_pct` | L, direction, breakout extreme, arm time |
| (waiting) | armed; watching 1m for the pullback | running pullback swing extreme |
| **FIRE** | after a valid pullback, a 1m candle closes *reclaiming* L in-direction (close>open), with reclaim volume | entry = reclaim close; SL = retest swing ∓ buffer |
| `ABORT` | 1m slice-through of L, timeout, or no retest in `max_wait_minutes` | level can re-arm later |

- **Completed 5m bars** are computed inside the strategy from the 1m series (`candles_1m`, which starts at 09:15 so blocks of 5 align to 09:15–09:20, …) — true 5m-close confirmation, stable transition detection, and ORB = the first three blocks. No timestamp dependency.
- **Arming** is transition-guarded: arm once when a level is first broken; after a fire/timeout, re-arm is blocked until a 5m close back inside L (a per-level cooldown). A slice-through needs a fresh breakout to re-arm.
- **Retest** requires the breakout to have *extended* away from L first, then price (1m low for a long) to return within `retest_proximity_pct`. The retest swing (pullback extreme) is the SL anchor.
- **One fire attempt per retest:** the arm is consumed on a reclaim candle whether or not it fires (gates may reject), preventing per-candle re-fires.

## Levels armed (`enabled_levels`, default all three)

1. **ORB** — opening-range high/low (09:15–09:30, the first three completed 5m blocks). S5's strongest setup.
2. **PDH_PDL** — previous-day high/low (`ctx.previous_day`).
3. **SWING** — confirmed intraday 5m swing pivots (`find_pivot_high`/`find_pivot_low`); a swing within `retest_proximity_pct` of an ORB/PDH-PDL level is dropped to avoid double-arming the same price.

## Entry / SL / target (the tight-SL lever)

- **Entry:** the reclaim 1m candle close (spot). `_resolve_futures` later rescales entry/SL/target to the futures LTP, preserving the structural % distances.
- **SL:** `retest_swing ∓ max(entry·sl_swing_buffer_pct, sl_atr_mult·ATR_5m)` — just past the pullback swing, *not* the breakout level. This is the biggest R:R lever.
- **Whipsaw floor:** if the resulting risk is tighter than `min_risk_pct`, the SL is widened to that floor (a too-tight swing whipsaws); if wider than `max_risk_pct`, the signal is skipped. `max_lots` bounds the leverage a tight stop implies.
- **Target:** `entry ± rr_multiplier·risk` (default R:R 1.8; min 1.5 or skip).

## Hard gates (all must pass to fire)

1. **Time-of-day:** arm/fire only `arm_start`–`arm_cutoff` (09:30–13:30) — no fresh retests late. Existing positions run to the 3:25 exit via the trade monitor. **`size_down_after`/`size_down_mult`** halve size in the late window.
2. **With-NIFTY-trend:** block fires opposing the *sign* of `nifty_day_change_pct` (NIFTY % vs today's open), with a `nifty_flat_deadband_pct` deadband. Matches the study's measured "with-trend" cohort (counter-trend hit 29% vs 43%). Wired into params by `strategy_runner._enrich_strategy5_params`.
3. **Never bias-opposed:** block fires opposing the *sign* of the stock's `intraday_bias.score` (any opposing, with a `stock_bias_deadband` deadband — tighter than S5's STRONG-only gate). The study's bias-opposed cohort hit a dismal 10.7%.
4. **Reclaim volume:** the reclaim candle's volume ≥ `reclaim_vol_mult` × the recent 1m average.

Plus base filters: `min_price`, `min_adr`, R:R ≥ `min_rr`, and the runner-level F&O ban check.

## Confidence (lean, 4-factor)

The S5 study proved indicator-soup is uninformative, so S6 uses a deliberately lean composite (a `confidence_factors` dict is injected for the UI / AI overlay):

| factor | weight | meaning |
|---|---|---|
| `setup_factor` | 0.30 | level quality — ORB 0.8 > PDH/PDL 0.7 > SWING 0.6 |
| `reclaim_vol_factor` | 0.30 | reclaim candle volume vs recent 1m average |
| `oi_factor` | 0.20 | FUT-OI alignment (the one weakly-real informational factor) |
| `rr_factor` | 0.20 | R:R quality from the tight stop (1.0 at R:R ≥ 2.0) |

Gated by the global `min_confidence_to_persist`, like S2/S5. The **AI confidence overlay** has a dedicated `breakout_retest` prompt in `signal_confidence.py` (retest-specific context: level, retest swing, breakout extreme, reclaim volume ratio, R:R).

## Exits

Standard rails via `trade_monitor`: SL / target / trailing / 3:25 PM time-exit. The **thesis-invalidation exit** is also enabled for S6 (momentum, like S5) — `trade_monitor._check_invalidation` gates on `intraday_futures` + `breakout_retest`, per-YOLO-profile (`invalidation_persist > 0`). `should_exit()` returns None (exits are monitor-driven).

## Validation

Tooling: `scripts/replay_strategy6.py` drives the stateful strategy minute-by-minute over a historical window (universe = the symbols S5 fired on each day, for a clean head-to-head), persisting `breakout_retest` signals so `scripts/analyze_strategy5_signal_accuracy.py --strategy breakout_retest` can measure the engine-independent first-touch accuracy.

```bash
cd backend && source .venv/bin/activate
DATABASE_URL=postgresql+asyncpg://trader:trader_dev_123@localhost:5433/stocktrading_bt \
  python ../scripts/replay_strategy6.py --start 2026-04-29 --end 2026-06-02
# then run analyze_strategy5_signal_accuracy.py --strategy breakout_retest over the same window
```

**Result (2026-04-29 → 2026-06-02, same universe as S5):**

| metric | S5 baseline | **S6 Breakout-Retest** |
|---|---|---|
| signals | 498 | 258 (selective) |
| **target-first** (TARGET / resolved) | 18.1% | **36.5%** |
| forward-favorable +15 / 30 / 60 min | 37 / 39 / 42% | **49 / 49 / 52%** |
| binary hit (EOD) | 40.0% | 38.8% |
| entry basis (fidelity check) | +0.18% | +0.000% |

The two trustworthy, engine-independent reads both moved decisively: **target-first doubled** (the R:R lever) and **forward-direction crossed the coin-flip line** (the "entered at the extreme" pathology is gone). Binary-hit is flat only because it credits S5's 299 OPEN trades by EOD coin-flip; S6's tight band resolves fast (17 OPEN of 258). ORB_RETEST is the strongest setup (39% target-first).

**Caveats / next step.** One concentrated 25-day window. Replay fidelity is approximate (intraday_bias computed with `global_cues=None`/`nifty_bias_score=None`; FUT-OI/screener enrichment omitted; NIFTY day-change index-aligned), and the engine-fidelity ceiling means absolute P&L doesn't transfer — only the *direction* does. **Ship dark, then run an S6 shadow + a YOLO profile beside S5 for a live paper A/B (2–3 weeks) before trusting it.**

## Parameters (`strategy_configs.parameters`)

Defaults in `BREAKOUT_RETEST_DEFAULTS` (`backend/app/services/strategy_params.py`): `enabled_levels`, `min_breakout_ext_pct` (0.05), `retest_proximity_pct` (0.15), `reclaim_buffer_pct` (0.0), `slice_buffer_pct` (0.20), `max_wait_minutes` (30), `reclaim_vol_mult` (1.5), `reclaim_vol_lookback` (20), `swing_pivot_left/right` (2/2), `sl_swing_buffer_pct` (0.15), `sl_atr_mult` (0.3), `min_risk_pct` (0.10), `max_risk_pct` (1.5), `rr_multiplier` (1.8), `min_rr` (1.5), `arm_start` (09:30), `arm_cutoff` (13:30), `size_down_after` (12:30), `size_down_mult` (0.5), `require_with_nifty_trend` (true), `nifty_flat_deadband_pct` (0.10), `block_opposing_stock_bias` (true), `stock_bias_deadband` (0.15), `min_adr` (1.5), `min_price` (100), `max_lots` (2).

## Known limitations

- **Arm state is ephemeral** — a mid-day backend restart loses in-flight arms (they re-form on the next breakout). ORB/PDH/PDL/swing levels themselves are recomputed each evaluation, so only pending arms are lost. Acceptable: arms are short-lived and we never chase a breakout we didn't witness.
- Agent-log prefix is `strat6` (no dedicated API endpoint yet — readable via `agent_log.get_agent_log("strat6", …)`).
