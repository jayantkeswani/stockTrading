"""Measure Strategy 5 (and optionally Strategy 2) SIGNAL ACCURACY — the
"are the signals directionally right?" half, separate from the exit/invalidation
P&L work in backtest_strategy5.py.

WHY THIS IS FAITHFUL (unlike the exit P&L study): signal accuracy here = "did the
underlying reach the signal's TARGET before its STOP within the session." That is
a pure first-touch read of 1m candles — no trailing stop, no tick polling, no exit
engine — so it does NOT suffer the ~3.6x engine-fidelity gap that made the exit
P&L untrustworthy (see docs/backtest/s5-invalidation-exit-study.md "Engine fidelity"
and memory project_s5_invalidation_exit). The hit-rate / calibration numbers here
can be trusted.

METRIC (engine-independent), per signal, walking underlying 1m candles
generated_at -> 15:30 (S5 entry/SL/target are FUTURES prices valued on the
underlying stock candles; symbol = short name — exactly like backtest_strategy5.py,
"stock futures track spot intraday, basis negligible"):
  - OUTCOME = TARGET if high/low touches target_price before stop_loss,
              SL    if the reverse,
              OPEN  if neither by 15:30 (then classified by EOD direction).
  - Same-candle both-touch -> SL (conservative).
  - binary_hit = 1 for TARGET, 0 for SL, and for OPEN = 1 iff EOD close is
    favorable to the signal direction. Every signal gets a binary label.
  - "target-first rate" (secondary) = TARGET / (TARGET + SL), excludes OPEN —
    the cleanest "reached target before stop" read on resolved trades.
  - Forward direction at +15/30/60 min (secondary, exit-free): fraction of
    signals whose underlying close is favorable at that horizon.

ANALYSES:
  1. Confidence calibration (buckets 30-50, 50-60, 60-70, 70-80, 80-90, 90+)
  2. Per-setup accuracy (ORB / VWAP_BOUNCE / PDH_PDL / GAP_CONTINUATION / ...)
  3. Factor importance — 9 confidence_factors vs binary outcome (point-biserial
     Pearson r + bottom-third vs top-third hit-rate lift)
  4. Regime / direction conditioning (counter-trend vs with-trend, NEUTRAL/chop,
     morning vs rest, intraday_bias opposing direction)
  5. Re-fire effect (first same-symbol/day signal vs subsequent)
  Plus filter-lift re-runs for any clear filter.

Usage:
    cd backend && source .venv/bin/activate
    DATABASE_URL=postgresql+asyncpg://trader:trader_dev_123@localhost:5433/stocktrading_bt \
        python scripts/analyze_strategy5_signal_accuracy.py --start 2026-04-29 --end 2026-06-02
    # confidence floor, strategy, single-signal trace:
    ... --min-confidence 70
    ... --strategy vwap_pullback          # S2 directional read (index candles, EOD-direction only)
    ... --trace                           # print every signal's first-touch resolution
"""

from __future__ import annotations

import argparse
import asyncio
import math
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time as dt_time, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from app.core.constants import IST, MARKET_CLOSE
from app.core.database import async_session_factory

# Reuse the pure candle fetch from the exit backtester (identical query/convention).
from backtest_strategy5 import fetch_candles_after, parse_date

# 9 confidence sub-scores -> human label (task naming on the right).
FACTORS = {
    "rvol_factor": "rvol",
    "setup_factor": "setup_quality",
    "bias_factor": "nifty_bias",
    "phase_factor": "phase",
    "vol_factor": "volume",
    "gap_factor": "gap_alignment",
    "trend_factor": "stock_trend",
    "oi_factor": "oi_direction",
    "rank_factor": "screener_rank",
}

CONF_BUCKETS = [(30, 50), (50, 60), (60, 70), (70, 80), (80, 90), (90, 1000)]
HORIZONS_MIN = [15, 30, 60]


# ── Outcome classification ───────────────────────────────────────────────

@dataclass
class SignalResult:
    symbol: str
    setup_type: str
    direction: str            # LONG / SHORT
    confidence: float
    entry_price: float
    stop_loss: float
    target_price: float | None
    generated_at: datetime
    indicators: dict
    outcome: str = ""          # TARGET / SL / OPEN / NO_DATA
    eod_favorable: bool = False
    binary_hit: int = 0        # 1 hit, 0 miss (the trustworthy accuracy label)
    resolved: bool = False     # TARGET or SL (excludes OPEN/NO_DATA)
    touch_time: datetime | None = None
    fwd_fav: dict | None = None  # {15: bool|None, 30: ..., 60: ...}

    @property
    def factors(self) -> dict:
        return self.indicators.get("confidence_factors", {}) or {}


def classify_first_touch(
    entry: float,
    sl: float,
    target: float | None,
    is_long: bool,
    candles: list[tuple[datetime, float, float, float, float]],
    ties: str = "sl",
) -> tuple[str, bool, datetime | None]:
    """First-touch walk: returns (outcome, eod_favorable, touch_time).

    TARGET if the target wick is hit before the stop wick; SL if the reverse;
    OPEN if neither by the last candle. Same-candle both-touch is broken by
    `ties`: "sl" (conservative, default — assumes the adverse level prints first)
    or "target" (optimistic). The gap between the two bounds the tie-break bias.
    """
    if not candles:
        return "NO_DATA", False, None

    for ts, o, h, l, c in candles:
        if is_long:
            sl_hit = l <= sl
            tgt_hit = target is not None and h >= target
        else:
            sl_hit = h >= sl
            tgt_hit = target is not None and l <= target
        if sl_hit and tgt_hit:
            # Both levels printed in the same minute — resolve per `ties`.
            if ties == "target":
                return "TARGET", True, ts
            return "SL", False, ts
        if sl_hit:
            return "SL", False, ts
        if tgt_hit:
            return "TARGET", True, ts

    last_close = candles[-1][4]
    eod_fav = (last_close > entry) if is_long else (last_close < entry)
    return "OPEN", eod_fav, None


def forward_direction(
    entry: float,
    is_long: bool,
    generated_at: datetime,
    candles: list[tuple[datetime, float, float, float, float]],
) -> dict:
    """Favorable-direction flag at +15/30/60 min using the close of the first
    candle at-or-after the horizon. None if no candle reaches that horizon."""
    out: dict = {h: None for h in HORIZONS_MIN}
    for h in HORIZONS_MIN:
        target_ts = generated_at + timedelta(minutes=h)
        close_at = None
        for ts, o, hi, lo, c in candles:
            if ts >= target_ts:
                close_at = c
                break
        if close_at is not None:
            out[h] = (close_at > entry) if is_long else (close_at < entry)
    return out


# ── DB fetch (keeps the full indicators JSONB, unlike backtest_strategy5) ─

async def fetch_signals_full(
    session, strategy: str, start_date: date, end_date: date, min_conf: float
) -> list[dict]:
    from sqlalchemy import text

    start_ts = datetime.combine(start_date, dt_time(0, 0), tzinfo=IST)
    end_ts = datetime.combine(end_date + timedelta(days=1), dt_time(0, 0), tzinfo=IST)
    result = await session.execute(
        text("""
            SELECT id, symbol, signal_type, entry_price, stop_loss, target_price,
                   confidence, generated_at, indicators
            FROM signals
            WHERE strategy_name = :strat AND confidence >= :min_conf
              AND generated_at >= :start_ts AND generated_at < :end_ts
            ORDER BY generated_at
        """),
        {"strat": strategy, "min_conf": min_conf, "start_ts": start_ts, "end_ts": end_ts},
    )
    rows = []
    for r in result.all():
        rows.append({
            "symbol": r.symbol,
            "signal_type": r.signal_type,
            "entry_price": float(r.entry_price),
            "stop_loss": float(r.stop_loss),
            "target_price": float(r.target_price) if r.target_price is not None else None,
            "confidence": float(r.confidence),
            "generated_at": r.generated_at,
            "indicators": r.indicators or {},
        })
    return rows


# ── Stats helpers ─────────────────────────────────────────────────────────

def pearson(xs: list[float], ys: list[float]) -> float | None:
    """Point-biserial == Pearson when one variable is the 0/1 outcome."""
    n = len(xs)
    if n < 3:
        return None
    mx = sum(xs) / n
    my = sum(ys) / n
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    dx = math.sqrt(sum((x - mx) ** 2 for x in xs))
    dy = math.sqrt(sum((y - my) ** 2 for y in ys))
    if dx == 0 or dy == 0:
        return None
    return num / (dx * dy)


def rate(hits: int, n: int) -> str:
    return f"{hits / n * 100:5.1f}%" if n else "   — "


def summarize(results: list[SignalResult], label: str) -> dict:
    n = len(results)
    if n == 0:
        return {"n": 0}
    hits = sum(r.binary_hit for r in results)
    resolved = [r for r in results if r.resolved]
    tgt = sum(1 for r in results if r.outcome == "TARGET")
    sl = sum(1 for r in results if r.outcome == "SL")
    opn = sum(1 for r in results if r.outcome == "OPEN")
    tgt_first = (tgt / len(resolved) * 100) if resolved else float("nan")
    return {
        "label": label, "n": n, "hits": hits, "hit_rate": hits / n * 100,
        "target": tgt, "sl": sl, "open": opn,
        "target_first_rate": tgt_first, "resolved": len(resolved),
    }


def print_group_table(title: str, summaries: list[dict]) -> None:
    print(f"\n{title}")
    print(f"  {'group':<22} {'n':>4} {'TGT':>4} {'SL':>4} {'OPEN':>4} "
          f"{'hit%(EOD)':>9} {'tgt-first%':>10}")
    print(f"  {'-'*22} {'-'*4} {'-'*4} {'-'*4} {'-'*4} {'-'*9} {'-'*10}")
    for s in summaries:
        if s["n"] == 0:
            continue
        tf = f"{s['target_first_rate']:8.1f}%" if s["resolved"] else "      — "
        print(f"  {s['label']:<22} {s['n']:>4} {s['target']:>4} {s['sl']:>4} "
              f"{s['open']:>4} {s['hit_rate']:>8.1f}% {tf:>10}")


# ── Analyses ────────────────────────────────────────────────────────────

def analyze_calibration(results: list[SignalResult]) -> None:
    summaries = []
    for lo, hi in CONF_BUCKETS:
        sub = [r for r in results if lo <= r.confidence < hi]
        hilabel = "+" if hi == 1000 else f"-{hi}"
        summaries.append(summarize(sub, f"{lo}{hilabel}"))
    print_group_table("1. CONFIDENCE CALIBRATION", summaries)
    # Is confidence predictive at all? point-biserial conf vs binary hit.
    r = pearson([s.confidence for s in results], [float(s.binary_hit) for s in results])
    print(f"\n  point-biserial r(confidence, hit) = {r:+.3f}" if r is not None else "  r = n/a")


def analyze_setups(results: list[SignalResult]) -> None:
    by_setup: dict[str, list[SignalResult]] = defaultdict(list)
    for r in results:
        by_setup[r.setup_type].append(r)
    summaries = [summarize(v, k) for k, v in sorted(by_setup.items())]
    print_group_table("2. PER-SETUP ACCURACY", summaries)


def analyze_factors(results: list[SignalResult]) -> None:
    print("\n3. FACTOR IMPORTANCE (point-biserial r + bottom-third vs top-third hit-rate)")
    print(f"  {'factor':<16} {'n':>4} {'r(hit)':>8} {'lo3 hit%':>9} {'hi3 hit%':>9} "
          f"{'lift':>7} {'mean@hit':>9} {'mean@miss':>9}")
    print(f"  {'-'*16} {'-'*4} {'-'*8} {'-'*9} {'-'*9} {'-'*7} {'-'*9} {'-'*9}")
    rows = []
    for key, label in FACTORS.items():
        pairs = [(float(r.factors[key]), r.binary_hit) for r in results if key in r.factors]
        n = len(pairs)
        if n < 5:
            rows.append((label, n, None, None, None, None, None, None))
            continue
        xs = [p[0] for p in pairs]
        ys = [float(p[1]) for p in pairs]
        r_corr = pearson(xs, ys)
        ordered = sorted(pairs, key=lambda p: p[0])
        k = max(1, n // 3)
        lo3 = ordered[:k]
        hi3 = ordered[-k:]
        lo_rate = sum(p[1] for p in lo3) / len(lo3) * 100
        hi_rate = sum(p[1] for p in hi3) / len(hi3) * 100
        hit_vals = [p[0] for p in pairs if p[1] == 1]
        miss_vals = [p[0] for p in pairs if p[1] == 0]
        mh = sum(hit_vals) / len(hit_vals) if hit_vals else float("nan")
        mm = sum(miss_vals) / len(miss_vals) if miss_vals else float("nan")
        rows.append((label, n, r_corr, lo_rate, hi_rate, hi_rate - lo_rate, mh, mm))
    # Sort by |r| descending so the strongest predictors surface first.
    rows.sort(key=lambda x: (abs(x[2]) if x[2] is not None else -1), reverse=True)
    for label, n, r_corr, lo_rate, hi_rate, lift, mh, mm in rows:
        if r_corr is None:
            print(f"  {label:<16} {n:>4} {'—':>8} {'—':>9} {'—':>9} {'—':>7} {'—':>9} {'—':>9}")
            continue
        print(f"  {label:<16} {n:>4} {r_corr:>+8.3f} {lo_rate:>8.1f}% {hi_rate:>8.1f}% "
              f"{lift:>+6.1f} {mh:>9.3f} {mm:>9.3f}")


def _signal_dir_sign(r: SignalResult) -> int:
    return 1 if r.direction == "LONG" else -1


def analyze_regime(results: list[SignalResult]) -> None:
    print("\n4. REGIME / DIRECTION CONDITIONING")

    # 4a. Counter-trend vs with-trend by NIFTY day change.
    with_trend, counter, flat = [], [], []
    for r in results:
        ndc = r.indicators.get("nifty_day_change_pct")
        if ndc is None:
            continue
        ndc = float(ndc)
        if abs(ndc) < 0.10:           # < 0.10% NIFTY move = chop / flat tape
            flat.append(r)
        elif (ndc > 0) == (r.direction == "LONG"):
            with_trend.append(r)
        else:
            counter.append(r)
    print_group_table("  4a. vs NIFTY day-trend (nifty_day_change_pct)", [
        summarize(with_trend, "with-trend"),
        summarize(counter, "counter-trend"),
        summarize(flat, "flat tape <0.10%"),
    ])

    # 4b. intraday_bias (stock composite) opposing the trade direction.
    bias_with, bias_against, bias_neutral = [], [], []
    for r in results:
        ib = r.indicators.get("intraday_bias")
        if not isinstance(ib, dict) or "score" not in ib:
            continue
        score = float(ib["score"])
        if abs(score) < 0.15:
            bias_neutral.append(r)
        elif (score > 0) == (r.direction == "LONG"):
            bias_with.append(r)
        else:
            bias_against.append(r)
    print_group_table("  4b. vs stock intraday_bias score", [
        summarize(bias_with, "bias-aligned"),
        summarize(bias_against, "bias-opposed"),
        summarize(bias_neutral, "bias-neutral"),
    ])

    # 4c. Time-of-day.
    buckets = {"09:15-10:30": [], "10:30-12:00": [], "12:00-13:30": [], "13:30-15:30": []}
    for r in results:
        t = r.generated_at.astimezone(IST).time()
        if t < dt_time(10, 30):
            buckets["09:15-10:30"].append(r)
        elif t < dt_time(12, 0):
            buckets["10:30-12:00"].append(r)
        elif t < dt_time(13, 30):
            buckets["12:00-13:30"].append(r)
        else:
            buckets["13:30-15:30"].append(r)
    print_group_table("  4c. by time-of-day (entry)",
                       [summarize(v, k) for k, v in buckets.items()])

    # 4d. Direction.
    print_group_table("  4d. by direction", [
        summarize([r for r in results if r.direction == "LONG"], "LONG"),
        summarize([r for r in results if r.direction == "SHORT"], "SHORT"),
    ])


def analyze_refire(results: list[SignalResult]) -> None:
    seen: dict[tuple[str, date], int] = defaultdict(int)
    first, refire = [], []
    for r in sorted(results, key=lambda x: x.generated_at):
        key = (r.symbol, r.generated_at.astimezone(IST).date())
        if seen[key] == 0:
            first.append(r)
        else:
            refire.append(r)
        seen[key] += 1
    print_group_table("5. RE-FIRE EFFECT (same symbol, same day)", [
        summarize(first, "first signal"),
        summarize(refire, "2nd+ (re-fire)"),
    ])


def analyze_forward(results: list[SignalResult]) -> None:
    print("\nFORWARD DIRECTION (exit-free, fraction favorable at horizon)")
    print(f"  {'horizon':<10} {'n':>4} {'favorable%':>11}")
    print(f"  {'-'*10} {'-'*4} {'-'*11}")
    for h in HORIZONS_MIN:
        vals = [r.fwd_fav[h] for r in results if r.fwd_fav and r.fwd_fav[h] is not None]
        n = len(vals)
        fav = sum(1 for v in vals if v)
        print(f"  +{h:>3} min   {n:>4} {rate(fav, n):>11}")


def report_filter_lift(results: list[SignalResult], name: str, keep_pred) -> None:
    kept = [r for r in results if keep_pred(r)]
    dropped = [r for r in results if not keep_pred(r)]
    base = summarize(results, "all")
    kept_s = summarize(kept, "kept")
    drop_s = summarize(dropped, "dropped")
    print(f"\n  FILTER: {name}")
    print(f"    all      n={base['n']:>4}  hit {base['hit_rate']:5.1f}%")
    if kept_s["n"]:
        print(f"    kept     n={kept_s['n']:>4}  hit {kept_s['hit_rate']:5.1f}%  "
              f"(lift {kept_s['hit_rate'] - base['hit_rate']:+.1f})")
    if drop_s["n"]:
        print(f"    dropped  n={drop_s['n']:>4}  hit {drop_s['hit_rate']:5.1f}%")


# ── Driver ────────────────────────────────────────────────────────────────

async def build_results(
    strategy: str, start_date: date, end_date: date, min_conf: float, trace: bool,
    ties: str = "sl", basis_adjust: bool = False, max_basis_pct: float = 3.0,
) -> list[SignalResult]:
    out: list[SignalResult] = []
    basis_pcts: list[float] = []
    dropped_basis: list[tuple[str, float]] = []
    inverted = 0
    async with async_session_factory() as session:
        signals = await fetch_signals_full(session, strategy, start_date, end_date, min_conf)
        print(f"Loaded {len(signals)} {strategy} signals "
              f"(confidence >= {min_conf}, {start_date} -> {end_date})  "
              f"[ties={ties}, basis_adjust={basis_adjust}]")
        for sig in signals:
            gen_at = sig["generated_at"]
            sig_date = gen_at.astimezone(IST).date()
            is_long = sig["signal_type"] in ("BUY_FUT", "BUY_CE")
            day_end = datetime.combine(sig_date, MARKET_CLOSE, tzinfo=IST)
            candles = await fetch_candles_after(session, sig["symbol"], gen_at, day_end)
            if not candles:
                continue
            # Skip malformed signals whose stop is on the wrong side of entry
            # (e.g. a long with SL above entry) — they "hit SL" instantly and
            # are not executable; they'd be rejected live. Rare (data hygiene).
            sl, tgt = sig["stop_loss"], sig["target_price"]
            if (is_long and sl >= sig["entry_price"]) or (not is_long and sl <= sig["entry_price"]):
                inverted += 1
                continue
            # entry/SL/target are FUTURES prices; candles are SPOT. Measure the
            # entry basis = futures_entry - spot_at_entry. With --basis-adjust,
            # shift the spot series up by that (assumed-constant) basis so the
            # band is anchored at the futures entry — neutralizes the offset that
            # would otherwise make one barrier systematically closer.
            spot_entry = candles[0][4]
            basis = sig["entry_price"] - spot_entry
            basis_pct = basis / spot_entry * 100 if spot_entry else 0.0
            # An implausible basis (real stock-futures basis is <1%) means the
            # signal's futures-priced band and the spot candle series are on
            # different scales — i.e. a wrong-instrument resolution (e.g. "BSE"
            # the stock resolving to BSE:BANKEX..FUT, ~15x the price). Such a
            # signal can't be first-touch-evaluated against these candles; drop it.
            if abs(basis_pct) > max_basis_pct:
                dropped_basis.append((sig["symbol"], basis_pct))
                continue
            if spot_entry:
                basis_pcts.append(basis_pct)
            if basis_adjust and basis:
                candles = [(ts, o + basis, h + basis, l + basis, c + basis)
                           for ts, o, h, l, c in candles]
            outcome, eod_fav, touch = classify_first_touch(
                sig["entry_price"], sig["stop_loss"], sig["target_price"], is_long, candles,
                ties=ties,
            )
            binary = 1 if outcome == "TARGET" else (1 if outcome == "OPEN" and eod_fav else 0)
            res = SignalResult(
                symbol=sig["symbol"],
                setup_type=sig["indicators"].get("setup_type", "UNKNOWN"),
                direction="LONG" if is_long else "SHORT",
                confidence=sig["confidence"],
                entry_price=sig["entry_price"],
                stop_loss=sig["stop_loss"],
                target_price=sig["target_price"],
                generated_at=gen_at,
                indicators=sig["indicators"],
                outcome=outcome,
                eod_favorable=eod_fav,
                binary_hit=binary,
                resolved=outcome in ("TARGET", "SL"),
                touch_time=touch,
                fwd_fav=forward_direction(sig["entry_price"], is_long, gen_at, candles),
            )
            out.append(res)
            if trace:
                tt = touch.astimezone(IST).strftime("%H:%M") if touch else "  -  "
                print(f"  {sig_date} {gen_at.astimezone(IST).strftime('%H:%M')} "
                      f"{res.symbol:<10} {res.direction:<5} conf={res.confidence:5.1f} "
                      f"E={res.entry_price:>9.2f} SL={res.stop_loss:>9.2f} "
                      f"T={(res.target_price or 0):>9.2f} -> {outcome:<7} @{tt} "
                      f"hit={binary}")
    if inverted:
        print(f"  DROPPED {inverted} malformed signal(s) with stop on the wrong side of entry")
    if dropped_basis:
        from collections import Counter
        syms = Counter(s for s, _ in dropped_basis)
        print(f"  DROPPED {len(dropped_basis)} signals with |basis|>{max_basis_pct}% "
              f"(wrong-instrument resolution): {dict(syms)}")
    if basis_pcts:
        sp = sorted(basis_pcts)
        med = sp[len(sp) // 2]
        mean = sum(sp) / len(sp)
        print(f"  entry basis (futures - spot), clean set: median {med:+.3f}%  "
              f"mean {mean:+.3f}%  |>0.3%|: {sum(1 for b in sp if abs(b) > 0.3)}/{len(sp)}")
    return out


async def main_async(args) -> None:
    end_date = args.end or args.start
    results = await build_results(
        args.strategy, args.start, end_date, args.min_confidence, args.trace,
        ties=args.ties, basis_adjust=args.basis_adjust, max_basis_pct=args.max_basis_pct,
    )
    if not results:
        print("No signals with candle data in range.")
        return

    overall = summarize(results, "ALL")
    print(f"\n{'='*72}")
    print(f"  SIGNAL ACCURACY — {args.strategy}   conf>={args.min_confidence:g}   "
          f"{args.start} -> {end_date}")
    print(f"{'='*72}")
    print(f"  Signals with data: {overall['n']}   "
          f"TARGET={overall['target']} SL={overall['sl']} OPEN={overall['open']}")
    print(f"  Binary hit-rate (TARGET, or OPEN-favorable-by-EOD): {overall['hit_rate']:.1f}%")
    print(f"  Target-first rate (TARGET / resolved): "
          f"{overall['target_first_rate']:.1f}%  ({overall['resolved']} resolved)")

    analyze_forward(results)
    analyze_calibration(results)
    analyze_setups(results)
    analyze_factors(results)
    analyze_regime(results)
    analyze_refire(results)

    # Candidate filters — lift is measured by re-running the binary hit-rate.
    print(f"\n{'='*72}\n  CANDIDATE FILTER LIFTS\n{'='*72}")

    def not_counter_trend(r: SignalResult) -> bool:
        ndc = r.indicators.get("nifty_day_change_pct")
        if ndc is None:
            return True
        ndc = float(ndc)
        if abs(ndc) < 0.10:
            return True
        return (ndc > 0) == (r.direction == "LONG")

    def not_bias_opposed(r: SignalResult) -> bool:
        ib = r.indicators.get("intraday_bias")
        if not isinstance(ib, dict) or "score" not in ib:
            return True
        score = float(ib["score"])
        if abs(score) < 0.15:
            return True
        return (score > 0) == (r.direction == "LONG")

    def good_setup(r: SignalResult) -> bool:
        return r.setup_type not in ("GAP_CONTINUATION", "VWAP_BOUNCE")

    report_filter_lift(results, "drop counter-trend (nifty_day_change_pct opposes dir)",
                       not_counter_trend)
    report_filter_lift(results, "drop bias-opposed (intraday_bias.score opposes dir)",
                       not_bias_opposed)
    report_filter_lift(results, "drop GAP_CONTINUATION + VWAP_BOUNCE setups", good_setup)
    report_filter_lift(results, "confidence >= 80", lambda r: r.confidence >= 80)
    report_filter_lift(
        results,
        "COMBINED: not bias-opposed AND not counter-trend AND good setup",
        lambda r: not_bias_opposed(r) and not_counter_trend(r) and good_setup(r),
    )
    report_filter_lift(results, "first signal per symbol/day only", _is_first_factory(results))
    print()


def _is_first_factory(results: list[SignalResult]):
    seen: dict[tuple[str, date], int] = defaultdict(int)
    firsts = set()
    for r in sorted(results, key=lambda x: x.generated_at):
        key = (r.symbol, r.generated_at.astimezone(IST).date(), id(r))
        skey = (r.symbol, r.generated_at.astimezone(IST).date())
        if seen[skey] == 0:
            firsts.add(id(r))
        seen[skey] += 1
    return lambda r: id(r) in firsts


def main() -> None:
    p = argparse.ArgumentParser(description="Strategy 5/2 signal-accuracy measurement")
    p.add_argument("--strategy", default="intraday_futures",
                   choices=["intraday_futures", "vwap_pullback", "breakout_retest"])
    p.add_argument("--start", type=parse_date, required=True)
    p.add_argument("--end", type=parse_date, default=None)
    p.add_argument("--min-confidence", type=float, default=0.0)
    p.add_argument("--ties", choices=["sl", "target"], default="sl",
                   help="same-candle both-touch tie-break (default sl, conservative)")
    p.add_argument("--basis-adjust", action="store_true",
                   help="anchor spot candles to the futures entry (neutralize futures-spot basis)")
    p.add_argument("--max-basis-pct", type=float, default=3.0,
                   help="drop signals whose |futures-spot basis| exceeds this %% "
                        "(wrong-instrument resolution; default 3.0)")
    p.add_argument("--trace", action="store_true", help="print every signal's resolution")
    args = p.parse_args()
    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
