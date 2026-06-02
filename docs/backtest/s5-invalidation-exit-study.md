# Thesis-Invalidation Exit Study (S5 + S2)

Reverse-test of one question: **if we exit a trade early when the market regime flips against it (instead of waiting for SL / target / trailing / EOD), do we make more money — and how much do we lose to false alarms (retracements that recover)?**

Tested on both strategies. Headline: **it helps momentum (S5, +38%) and hurts mean-reversion (S2).**

Tooling: `scripts/backtest_strategy5.py --invalidation`/`--inval-sweep` (futures) and `scripts/backtest_strategy2.py` (index options). Run against a local replica of production (`stocktrading_bt`) seeded with prod's `signals` + underlying & futures `market_data_1m` + `global_market_snapshots`. Window **2026-04-29 → 2026-06-02** (20 signal days), confidence ≥ 70.

## Method

- **Trigger** = the relevant index intraday bias (`indicators/intraday_bias.py`) — the *same regime read the live entry gate uses* (S5: NIFTY's `_nifty_bias`; S2: the traded index's own bias). Recomputed minute-by-minute from index candles-up-to-now + VWAP + previous-day levels + global cues. **No look-ahead** — the exit at minute T uses only data available at T.
- **Critical data fidelity**: the index VWAP is weighted by **near-month futures volume** (`{index}_FUT`), because index *spot* volume in `market_data_1m` is ~always zero (373/375 candles on a sample day). Using the (degenerate) spot volume corrupts the `price-vs-VWAP` factor and **manufactures false flips** — an earlier run with spot volume overstated the retracement cost ~30× and invented a −23k whipsaw day (May 18) that doesn't exist with the correct VWAP. This mirrors live `strategy_runner._calculate_vwap_from_buffer` (futures volume for indices).
- **Rule**: exit (reason `INVALIDATION`) when the index bias opposes the trade (STRONG by default) for `inval_persist` consecutive candles. SL / target / time take precedence within a candle, so invalidation only ever exits *earlier* than baseline.
- **Guards**: `--inval-persist N`, `--inval-quorum` (also require the underlying to lose/reclaim its own VWAP), `--inval-moderate` (MODERATE+ instead of STRONG).
- **Measurement**: every trade is simulated twice (baseline vs with-invalidation); positive deltas = *reversal savings* (loser cut early), negative = *retracement cost* (premature exit that would have recovered).
- **S2 valuation**: option premium is delta-approximated from the index move (`prem ≈ entry ± δ·index_move`, δ=0.50) — needs only index candles, covers every signal incl. expired contracts. Ignores theta/vega; reported in **premium points** (size-independent). 1 lot/signal for S5.

## S5 (intraday stock futures) — invalidation WORKS

Baseline: **₹+156,310** / 142 trades, hit 53.5%.

| persist | quorum | changed | reversal savings | retracement cost | net Δ | inval net |
|--------:|:------:|--------:|-----------------:|-----------------:|------:|----------:|
| 1 | off | 17 | +64,532 | −28,447 | +36,085 | 192,394 |
| 2 | off | 12 | +56,847 | −1,125 | +55,722 | 212,031 |
| **3** | **off** | 11 | +59,793 | **0** | **+59,793** | **216,103** |
| 5 | off | 7 | +27,152 | −2,080 | +25,072 | 181,381 |
| 1 | ON | 8 | +25,432 | −2,990 | +22,442 | 178,751 |
| 3 | ON | 6 | +21,726 | −3,120 | +18,606 | 174,916 |

**Best: persist=3 / no-quorum → +₹59,793 (+38%)**, 11 trades changed and **all 11 were losers cut early — zero winners hit, zero retracement cost**, hit rate unchanged at 53.5%. Almost all the gain is cutting **losing longs** (LONG −60,010 → −10,150; SHORT already profitable, +216,320 → +226,252). With the faithful VWAP, persistence alone is enough — the quorum is barely needed (it just cuts fewer trades, leaving savings on the table).

Per-day (persist=3): **06-02 +26,219** (true U-turn — losing longs HINDALCO/NYKAA/MPHASIS/COALINDIA cut ~10:57 before SL/EOD), 05-29 +9,392, 05-14 +9,413, 05-12 +12,758. **05-18 = 0** — the bias never flagged that wobble as a STRONG reversal, so no false exits.

> Caveat: "zero retracement cost" is sample-specific on 20 days — it won't be exactly zero forward. The direction (net-positive, low cost) is the robust signal.

## S2 (index options, VWAP Pullback) — invalidation HURTS

Baseline: **−694.7 premium pts** / 26 trades (S2 was a net loser in this choppy window).

| persist | quorum | changed | savings | cost | net Δ | inval net |
|--------:|:------:|--------:|--------:|-----:|------:|----------:|
| 2 | off | 7 | +81.4 | −404.6 | −323.3 | −1,018.0 |
| 3 | off | 5 | +63.1 | −412.5 | −349.4 | −1,044.1 |
| 5 | off | 3 | +29.4 | 0 | +29.4 | −665.3 |

Net-**negative** at every meaningful setting. By type: CE −390.5 (one Jun-2 trade cut midday on a bearish wobble, then NIFTY rallied), PE +41.1.

**Why the inversion:** S2 is **mean-reversion** — it *fades* short-term moves (VWAP pullback), so an opposing regime reading is frequently the exact condition it's betting against; cutting on it exits right before the pullback works. S5 is **momentum** — it wants the regime *with* it, so an opposing flip means the setup failed and cutting helps.

> Caveats: only 26 S2 trades (very thin), baseline already negative, delta-approx ignores theta. Directionally clear, not conclusive.

## Engine fidelity — checked against the real shadow book (important)

`backtest_strategy5.py --source shadow` replays the **actual** shadow trades (real fill price + entry timestamp + structural SL/target from the signal snapshot — the deduped execution population the dashboard shows), instead of raw signals. This removes the population/entry mismatch. It surfaces a deeper limit:

On the identical 125 shadow trades (conf≥70): **candle re-sim baseline = +198,650, but the actual realized shadow P&L = +54,417** — the offline engine is **~3.6× too optimistic**, and switching the stop trigger to wick barely changes it (+200,482). The gap is structural: a **1-minute candle engine + snapshot SL/target cannot reproduce the live tick-based `trade_monitor` with recomputed SL/target** (we only persist 1m candles).

**Therefore the absolute P&L from this harness is not trustworthy** — neither signal-replay nor shadow-replay. The invalidation *delta* is measured entirely inside the optimistic engine (both arms use it), so its **magnitude (+23–38%) cannot be trusted against reality.** What *is* robust is the **direction**, consistent across every configuration (signals & real trades, close & wick, persist 1–5): net-positive at persist 2–3, changed trades are mostly losing LONGs cut early, low retracement cost at persist≥2.

## Conclusion & recommendation

The "cut when regime flips" exit is **strategy-specific, not universal**:

- **S5 (momentum): promising — validate live, don't trust the offline magnitude.** The direction is robust but the harness can't size the gain (see Engine fidelity). This is now **implemented live** as a per-YOLO-profile policy (see "Live implementation" below): run an invalidation-enabled profile beside an identical control over ~2–3 weeks of paper trading and compare on *real* exits — only the live book can size the gain.
- **S2 (mean-reversion): do not.** A regime-flip cut works against the strategy. If S2 needs a "reason-died" exit, base it on S2's *own* invalidation (price crossing back through VWAP — already written in `Strategy2.should_exit()`, unused live), not the index bias.
- Re-run both monthly as the sample grows.

## Live implementation (per-YOLO-profile policy)

The exit ships as a **per-`YoloProfile` setting**, so it can run as a normal active profile beside a no-invalidation control for live A/B on the paper book — the only way to size the gain (offline is ~3.6× optimistic).

- **Settings** on `yolo_profiles`: `invalidation_persist` (INT, NULL/0 = disabled, default operating point 3), `invalidation_quorum` (BOOL, default false), `invalidation_strong_only` (BOOL, default true). Edited in Settings → YOLO Profiles → the "Inval" toggle + persist input + "Q" quorum toggle. (The PATCH API drops `null`, so the client sends `invalidation_persist=0` to disable.)
- **Trigger** (`trade_monitor._check_invalidation`): for non-shadow INTRADAY `intraday_futures` positions whose profile enables it, the monitor reads the live NIFTY `IntradayBias` via `strategy_runner.nifty_bias_snapshot()` and advances a **per-position, candle-aligned** opposing counter — incremented at most once per NIFTY 1m candle (keyed on the bias candle timestamp, so the 500ms poll can't inflate it). The first candle observed per position is a baseline only (the pre-entry candle is never counted). A long is opposed by STRONG BEARISH bias, a short by STRONG BULLISH; `invalidation_strong_only=false` widens to MODERATE+. At `count >= invalidation_persist` the position closes with `ExitReason.INVALIDATION` (`AgentActionType.INVALIDATION_CLOSE` → `notify_invalidation_exit`).
- **Precedence**: checked after SL/target inside `_check_position`, so structural stops always win within a candle — invalidation only ever exits *earlier* than baseline (matches the backtest rule).
- **Scope**: S5 only (momentum). The same trigger is deliberately NOT wired for S2 — see above.
- **Validation plan**: enable on one profile (persist=3, quorum off), leave an identical-cap profile with it off, and compare realized exits over 2–3 weeks. Each signal already fans out one Trade+Position per active profile, so the two arms see the same signals.
