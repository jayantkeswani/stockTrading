"""Measure Strategy 2 (VWAP Pullback, index options) SIGNAL ACCURACY — the
"are the signals directionally right?" half, mirroring
analyze_strategy5_signal_accuracy.py but adapted for index options.

WHY THIS IS FAITHFUL (and where it is NOT): S2 trades index OPTIONS, but every
signal carries INDEX-LEVEL barriers (`index_entry_price` / `index_sl` /
`index_target` in indicators JSONB) alongside the premium entry/SL/target. The
engine-independent read here walks the traded INDEX's spot 1m candles and asks
"did the index reach index_target before index_sl" — a pure first-touch read of
the underlying, no option premium, no theta, no trailing engine. There is NO
futures-spot basis to adjust (the index spot IS the underlying).

  HONEST CAVEAT (state it loudly): index target-first / first-touch direction is
  NOT the live option win-rate. The live trade is the option premium, which bleeds
  theta and is exited by trailing stops — a slow grind to index_target can still
  LOSE on the option. The trustworthy reads are the exit-free INDEX
  forward-direction (+15/30/60) and first-touch DIRECTION, not a P&L proxy.

DATA SHAPE (verified on the bt replica, 2026-04-29 → 06-02, 77 signals):
  - Only ~half the signals carry index-level barriers (index_sl AND index_target):
    the rest fell back to a premium sl_pct/rr_multiplier with no index levels.
    => first-touch (TARGET/SL) + target-first are reported ONLY on that resolved
    subset; the binary hit label is barrier-INDEPENDENT (EOD/forward index
    direction) so calibration + factor importance keep the full n.
  - confidence_factors carries the OLD 10-factor set INCLUDING `global_alignment`.
    Live confidence.py has since removed global_alignment and re-weighted — so any
    finding about it is RETROSPECTIVE. We read whatever factor keys are present.

METRIC, per signal, walking the index 1m candles generated_at -> 15:30:
  - has_barriers (index_sl AND index_target present):
      OUTCOME = TARGET if the index target wick is hit before the stop wick,
                SL if the reverse, OPEN if neither by 15:30.
      Same-candle both-touch -> SL (conservative; --ties target to flip).
  - no barriers: OUTCOME = OPEN, classified by EOD index close direction only.
  - binary_hit = 1 for TARGET; 0 for SL; for OPEN = 1 iff EOD index close is
    favorable to the option direction (CE wants index up, PE wants index down).
  - target-first rate (resolved only) = TARGET / (TARGET + SL).
  - per-signal RANDOM baseline (resolved) = stop_dist / (stop_dist + tgt_dist):
    S2 takes variable market-structure R:R (>= 1:1), so the random target-first
    is NOT a flat ~40% — we compare observed against each signal's own geometry.
  - Forward direction at +15/30/60 min (exit-free): fraction of signals whose
    index close is favorable to the option direction at that horizon.

ANALYSES (mirror S5 + S2 angles):
  1. Confidence calibration + point-biserial r(confidence, hit)
  2. CE vs PE, and per-index
  3. Factor importance — the stored confidence_factors vs binary outcome
  4. Regime / direction conditioning: own intraday_bias.score, NIFTY day-trend,
     window-state (IN_WINDOW / DEAD_ZONE / OUT_OF_WINDOW), time-of-day
  5. S2-specific cuts: |VWAP distance| at entry, PDH/PDL proximity, OI-confirmed,
     CPR narrow/wide, VIX regime, reversal_quality
  6. Re-fire effect (same index, same day)
  7. Candidate filter lifts + a within-window date split (window-specific guard)

Usage:
    cd backend && source .venv/bin/activate
    DATABASE_URL=postgresql+asyncpg://trader:trader_dev_123@localhost:5433/stocktrading_bt \
        python scripts/analyze_strategy2_signal_accuracy.py --start 2026-04-29 --end 2026-06-02
    ... --min-confidence 70          # confidence floor
    ... --trace                       # print every signal's first-touch resolution
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

from app.core.constants import (
    DEAD_ZONE_END,
    DEAD_ZONE_START,
    IST,
    MARKET_CLOSE,
    WINDOW_1_END,
    WINDOW_1_START,
    WINDOW_2_END,
    WINDOW_2_START,
)
from app.core.database import async_session_factory

# Reuse the pure candle fetch from the exit backtester (identical query/convention).
from backtest_strategy5 import fetch_candles_after, parse_date

# Stored confidence sub-scores -> human label. The bt window carries the OLD
# 10-factor set (incl. global_alignment, since removed from live confidence.py).
# Keys absent from a given signal are simply skipped in the factor analysis.
FACTORS = {
    "bias_alignment": "bias_align",
    "reversal_quality": "reversal_qual",
    "vwap_slope_alignment": "vwap_slope",
    "volume_quality": "volume_qual",
    "rr_ratio_quality": "rr_ratio",
    "oi_support": "oi_support",
    "global_alignment": "global_align",
    "cpr_narrow_trending": "cpr_narrow",
    "vix_regime": "vix_regime",
    "time_of_day": "time_of_day",
}

CONF_BUCKETS = [(0, 40), (40, 50), (50, 60), (60, 70), (70, 80), (80, 1000)]
HORIZONS_MIN = [15, 30, 60]


# ── Outcome classification ───────────────────────────────────────────────

@dataclass
class SignalResult:
    symbol: str               # index (NIFTY, BANKNIFTY, ...)
    direction: str            # CE / PE
    confidence: float
    index_entry: float
    index_sl: float | None
    index_target: float | None
    generated_at: datetime
    indicators: dict
    has_barriers: bool = False
    outcome: str = ""          # TARGET / SL / OPEN / NO_DATA
    eod_favorable: bool = False
    binary_hit: int = 0        # 1 hit, 0 miss (barrier-independent label)
    resolved: bool = False     # TARGET or SL (excludes OPEN/NO_DATA)
    touch_time: datetime | None = None
    fwd_fav: dict | None = None  # {15: bool|None, 30: ..., 60: ...}
    rand_baseline: float | None = None  # per-signal random target-first (resolved only)

    @property
    def is_ce(self) -> bool:
        return self.direction == "CE"

    @property
    def factors(self) -> dict:
        return self.indicators.get("confidence_factors", {}) or {}


def window_state(t: dt_time) -> str:
    """S2 window state from the trade-window constants (NOT the live util, which
    short-circuits to IN_WINDOW under MARKET_MODE=simulated). IN_WINDOW = inside
    the two primary windows; DEAD_ZONE = 11:30-13:30; else OUT_OF_WINDOW."""
    if WINDOW_1_START <= t <= WINDOW_1_END or WINDOW_2_START <= t <= WINDOW_2_END:
        return "IN_WINDOW"
    if DEAD_ZONE_START <= t <= DEAD_ZONE_END:
        return "DEAD_ZONE"
    return "OUT_OF_WINDOW"


def classify_first_touch(
    entry: float,
    sl: float,
    target: float,
    is_long: bool,
    candles: list[tuple[datetime, float, float, float, float]],
    ties: str = "sl",
) -> tuple[str, bool, datetime | None]:
    """First-touch walk: returns (outcome, eod_favorable, touch_time).

    TARGET if the target wick is hit before the stop wick; SL if the reverse;
    OPEN if neither by the last candle. Same-candle both-touch broken by `ties`
    ("sl" conservative default / "target" optimistic)."""
    if not candles:
        return "NO_DATA", False, None

    for ts, o, h, l, c in candles:
        if is_long:
            sl_hit = l <= sl
            tgt_hit = h >= target
        else:
            sl_hit = h >= sl
            tgt_hit = l <= target
        if sl_hit and tgt_hit:
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


def eod_direction(
    entry: float, is_long: bool,
    candles: list[tuple[datetime, float, float, float, float]],
) -> bool:
    """EOD-favorable flag (barrier-free): last index close vs entry."""
    if not candles:
        return False
    last_close = candles[-1][4]
    return (last_close > entry) if is_long else (last_close < entry)


def forward_direction(
    entry: float, is_long: bool, generated_at: datetime,
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


# ── DB fetch (keeps the full indicators JSONB) ────────────────────────────

async def fetch_signals_full(
    session, start_date: date, end_date: date, min_conf: float,
    strategy_name: str = "vwap_pullback",
) -> list[dict]:
    """Load signals for one index-options strategy. `strategy_name='vwap_reclaim'`
    scores the reclaim-entry redesign on the SAME first-touch read (it carries the
    same index_sl/index_target barriers), for a head-to-head vs vwap_pullback."""
    from sqlalchemy import text

    start_ts = datetime.combine(start_date, dt_time(0, 0), tzinfo=IST)
    end_ts = datetime.combine(end_date + timedelta(days=1), dt_time(0, 0), tzinfo=IST)
    result = await session.execute(
        text("""
            SELECT id, symbol, signal_type, confidence, generated_at, indicators
            FROM signals
            WHERE strategy_name = :strategy_name AND confidence >= :min_conf
              AND generated_at >= :start_ts AND generated_at < :end_ts
            ORDER BY generated_at
        """),
        {"strategy_name": strategy_name, "min_conf": min_conf,
         "start_ts": start_ts, "end_ts": end_ts},
    )
    rows = []
    for r in result.all():
        rows.append({
            "symbol": r.symbol,
            "signal_type": r.signal_type,
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
        return {"n": 0, "label": label}
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
          f"{'hit%(dir)':>9} {'tgt-first%':>10}")
    print(f"  {'-'*22} {'-'*4} {'-'*4} {'-'*4} {'-'*4} {'-'*9} {'-'*10}")
    for s in summaries:
        if s["n"] == 0:
            continue
        tf = f"{s['target_first_rate']:8.1f}%" if s["resolved"] else "      — "
        print(f"  {s['label']:<22} {s['n']:>4} {s['target']:>4} {s['sl']:>4} "
              f"{s['open']:>4} {s['hit_rate']:>8.1f}% {tf:>10}")


# ── Analyses ────────────────────────────────────────────────────────────

def analyze_forward(results: list[SignalResult]) -> None:
    print("\nFORWARD / EOD INDEX DIRECTION (exit-free, fraction favorable)")
    print(f"  {'horizon':<12} {'n':>4} {'favorable%':>11}")
    print(f"  {'-'*12} {'-'*4} {'-'*11}")
    for h in HORIZONS_MIN:
        vals = [r.fwd_fav[h] for r in results if r.fwd_fav and r.fwd_fav[h] is not None]
        n = len(vals)
        fav = sum(1 for v in vals if v)
        print(f"  +{h:>3} min     {n:>4} {rate(fav, n):>11}")
    eod = [r.eod_favorable for r in results]
    print(f"  EOD close   {len(eod):>4} {rate(sum(1 for v in eod if v), len(eod)):>11}")


def analyze_calibration(results: list[SignalResult]) -> None:
    summaries = []
    for lo, hi in CONF_BUCKETS:
        sub = [r for r in results if lo <= r.confidence < hi]
        hilabel = "+" if hi == 1000 else f"-{hi}"
        summaries.append(summarize(sub, f"{lo}{hilabel}"))
    print_group_table("1. CONFIDENCE CALIBRATION", summaries)
    r = pearson([s.confidence for s in results], [float(s.binary_hit) for s in results])
    print(f"\n  point-biserial r(confidence, hit) = {r:+.3f}" if r is not None else "  r = n/a")


def analyze_option_type(results: list[SignalResult]) -> None:
    print_group_table("2a. CE vs PE", [
        summarize([r for r in results if r.is_ce], "CE (bullish)"),
        summarize([r for r in results if not r.is_ce], "PE (bearish)"),
    ])


def analyze_per_index(results: list[SignalResult]) -> None:
    by_idx: dict[str, list[SignalResult]] = defaultdict(list)
    for r in results:
        by_idx[r.symbol].append(r)
    print_group_table("2b. PER-INDEX",
                       [summarize(v, k) for k, v in sorted(by_idx.items())])


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
        lo3, hi3 = ordered[:k], ordered[-k:]
        lo_rate = sum(p[1] for p in lo3) / len(lo3) * 100
        hi_rate = sum(p[1] for p in hi3) / len(hi3) * 100
        hit_vals = [p[0] for p in pairs if p[1] == 1]
        miss_vals = [p[0] for p in pairs if p[1] == 0]
        mh = sum(hit_vals) / len(hit_vals) if hit_vals else float("nan")
        mm = sum(miss_vals) / len(miss_vals) if miss_vals else float("nan")
        rows.append((label, n, r_corr, lo_rate, hi_rate, hi_rate - lo_rate, mh, mm))
    rows.sort(key=lambda x: (abs(x[2]) if x[2] is not None else -1), reverse=True)
    for label, n, r_corr, lo_rate, hi_rate, lift, mh, mm in rows:
        if r_corr is None:
            print(f"  {label:<16} {n:>4} {'—':>8} {'—':>9} {'—':>9} {'—':>7} {'—':>9} {'—':>9}")
            continue
        print(f"  {label:<16} {n:>4} {r_corr:>+8.3f} {lo_rate:>8.1f}% {hi_rate:>8.1f}% "
              f"{lift:>+6.1f} {mh:>9.3f} {mm:>9.3f}")


def analyze_regime(results: list[SignalResult]) -> None:
    print("\n4. REGIME / DIRECTION CONDITIONING")

    # 4a. Own intraday_bias.score vs option direction. NOTE: STRONG-opposing is
    # already gated live, so the "bias-opposed" cohort here is MODERATE/WEAK.
    bias_with, bias_against, bias_neutral = [], [], []
    for r in results:
        ib = r.indicators.get("intraday_bias")
        score = None
        if isinstance(ib, dict) and "score" in ib:
            score = float(ib["score"])
        if score is None:
            continue
        if abs(score) < 0.20:
            bias_neutral.append(r)
        elif (score > 0) == r.is_ce:
            bias_with.append(r)
        else:
            bias_against.append(r)
    print_group_table("  4a. vs own intraday_bias.score (STRONG-opposed already gated)", [
        summarize(bias_with, "bias-aligned"),
        summarize(bias_against, "bias-opposed (mod/weak)"),
        summarize(bias_neutral, "bias-neutral <0.20"),
    ])

    # 4b. NIFTY day-trend (broad market). For non-NIFTY indices this is the NIFTY
    # move, not the traded index's own — a broad-tape filter.
    with_trend, counter, flat = [], [], []
    for r in results:
        ndc = r.indicators.get("nifty_day_change_pct")
        if ndc is None:
            continue
        ndc = float(ndc)
        if abs(ndc) < 0.10:
            flat.append(r)
        elif (ndc > 0) == r.is_ce:
            with_trend.append(r)
        else:
            counter.append(r)
    print_group_table("  4b. vs NIFTY day-trend (nifty_day_change_pct)", [
        summarize(with_trend, "with-trend"),
        summarize(counter, "counter-trend"),
        summarize(flat, "flat tape <0.10%"),
    ])

    # 4c. Window-state (only IN_WINDOW signals are tradeable).
    buckets = {"IN_WINDOW": [], "DEAD_ZONE": [], "OUT_OF_WINDOW": []}
    for r in results:
        buckets[window_state(r.generated_at.astimezone(IST).time())].append(r)
    print_group_table("  4c. by window-state",
                       [summarize(v, k) for k, v in buckets.items()])

    # 4d. Time-of-day.
    tod = {"09:15-11:00": [], "11:00-13:00": [], "13:00-14:45": [], "14:45-15:30": []}
    for r in results:
        t = r.generated_at.astimezone(IST).time()
        if t < dt_time(11, 0):
            tod["09:15-11:00"].append(r)
        elif t < dt_time(13, 0):
            tod["11:00-13:00"].append(r)
        elif t < dt_time(14, 45):
            tod["13:00-14:45"].append(r)
        else:
            tod["14:45-15:30"].append(r)
    print_group_table("  4d. by time-of-day (entry)",
                       [summarize(v, k) for k, v in tod.items()])


def analyze_s2_cuts(results: list[SignalResult]) -> None:
    print("\n5. S2-SPECIFIC CUTS")

    # 5a. |VWAP distance| at entry (the pullback depth; strategy band 0.05-0.15%).
    def vdist(r):
        v = r.indicators.get("vwap_distance_pct")
        return abs(float(v)) if v is not None else None
    near, mid, far = [], [], []
    for r in results:
        d = vdist(r)
        if d is None:
            continue
        (near if d < 0.05 else mid if d < 0.10 else far).append(r)
    print_group_table("  5a. by |VWAP distance| at entry", [
        summarize(near, "<0.05%"),
        summarize(mid, "0.05-0.10%"),
        summarize(far, ">=0.10%"),
    ])

    # 5b. PDH/PDL proximity (nearest of PDH/PDL as % of entry).
    def pdprox(r):
        e = r.index_entry
        pdh, pdl = r.indicators.get("pdh"), r.indicators.get("pdl")
        cands = [abs(e - float(x)) / e * 100 for x in (pdh, pdl) if x is not None]
        return min(cands) if cands else None
    pnear, pfar = [], []
    for r in results:
        d = pdprox(r)
        if d is None:
            continue
        (pnear if d < 0.30 else pfar).append(r)
    print_group_table("  5b. by PDH/PDL proximity", [
        summarize(pnear, "near PDH/PDL <0.30%"),
        summarize(pfar, "away >=0.30%"),
    ])

    # 5c. OI-confirmed flag.
    oi_yes = [r for r in results if r.indicators.get("oi_confirmed") is True]
    oi_no = [r for r in results if r.indicators.get("oi_confirmed") is False]
    print_group_table("  5c. by OI confirmation", [
        summarize(oi_yes, "oi_confirmed"),
        summarize(oi_no, "oi_weak"),
    ])

    # 5d. CPR narrow vs wide.
    cpr_n = [r for r in results if r.indicators.get("cpr_type") == "NARROW"]
    cpr_w = [r for r in results if r.indicators.get("cpr_type") == "WIDE"]
    print_group_table("  5d. by CPR type", [
        summarize(cpr_n, "NARROW (trending)"),
        summarize(cpr_w, "WIDE (rangey)"),
    ])

    # 5e. VIX regime.
    def vix(r):
        v = r.indicators.get("india_vix")
        return float(v) if v is not None else None
    vlow, vmid, vhigh = [], [], []
    for r in results:
        v = vix(r)
        if v is None:
            continue
        (vlow if v < 14 else vmid if v < 18 else vhigh).append(r)
    print_group_table("  5e. by India VIX", [
        summarize(vlow, "VIX <14"),
        summarize(vmid, "VIX 14-18"),
        summarize(vhigh, "VIX >=18"),
    ])

    # 5f. reversal_quality (entry trigger strength).
    def rq(r):
        return r.factors.get("reversal_quality")
    lo, hi = [], []
    rqs = sorted((r for r in results if rq(r) is not None), key=lambda r: rq(r))
    if rqs:
        k = max(1, len(rqs) // 2)
        lo, hi = rqs[:k], rqs[-k:]
    print_group_table("  5f. by reversal_quality (low vs high half)", [
        summarize(lo, "weak reversal"),
        summarize(hi, "strong reversal"),
    ])


def analyze_refire(results: list[SignalResult]) -> None:
    seen: dict[tuple[str, date], int] = defaultdict(int)
    first, refire = [], []
    for r in sorted(results, key=lambda x: x.generated_at):
        key = (r.symbol, r.generated_at.astimezone(IST).date())
        (first if seen[key] == 0 else refire).append(r)
        seen[key] += 1
    print_group_table("6. RE-FIRE EFFECT (same index, same day)", [
        summarize(first, "first signal"),
        summarize(refire, "2nd+ (re-fire)"),
    ])


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


# ── Filter predicates (shared by filter-lifts and the window split) ───────

def _bias_opposed(r: SignalResult) -> bool:
    ib = r.indicators.get("intraday_bias")
    if not isinstance(ib, dict) or "score" not in ib:
        return False
    score = float(ib["score"])
    if abs(score) < 0.20:
        return False
    return (score > 0) != r.is_ce


def _counter_trend(r: SignalResult) -> bool:
    ndc = r.indicators.get("nifty_day_change_pct")
    if ndc is None:
        return False
    ndc = float(ndc)
    if abs(ndc) < 0.10:
        return False
    return (ndc > 0) != r.is_ce


def _in_window(r: SignalResult) -> bool:
    return window_state(r.generated_at.astimezone(IST).time()) == "IN_WINDOW"


# ── Driver ────────────────────────────────────────────────────────────────

async def build_results(
    start_date: date, end_date: date, min_conf: float, trace: bool, ties: str = "sl",
    strategy_name: str = "vwap_pullback",
) -> list[SignalResult]:
    out: list[SignalResult] = []
    n_barrier = 0
    async with async_session_factory() as session:
        signals = await fetch_signals_full(session, start_date, end_date, min_conf, strategy_name)
        print(f"Loaded {len(signals)} {strategy_name} signals "
              f"(confidence >= {min_conf}, {start_date} -> {end_date})  [ties={ties}]")
        for sig in signals:
            ind = sig["indicators"]
            gen_at = sig["generated_at"]
            sig_date = gen_at.astimezone(IST).date()
            is_ce = sig["signal_type"] == "BUY_CE"
            day_end = datetime.combine(sig_date, MARKET_CLOSE, tzinfo=IST)

            idx_entry = ind.get("index_entry_price", ind.get("price"))
            if idx_entry is None:
                continue
            idx_entry = float(idx_entry)
            candles = await fetch_candles_after(session, sig["symbol"], gen_at, day_end)
            if not candles:
                continue

            idx_sl = ind.get("index_sl")
            idx_tgt = ind.get("index_target")
            has_barriers = idx_sl is not None and idx_tgt is not None
            idx_sl = float(idx_sl) if idx_sl is not None else None
            idx_tgt = float(idx_tgt) if idx_tgt is not None else None

            rand_baseline = None
            if has_barriers:
                n_barrier += 1
                outcome, eod_fav, touch = classify_first_touch(
                    idx_entry, idx_sl, idx_tgt, is_ce, candles, ties=ties)
                stop_dist = abs(idx_entry - idx_sl)
                tgt_dist = abs(idx_tgt - idx_entry)
                if stop_dist + tgt_dist > 0:
                    rand_baseline = stop_dist / (stop_dist + tgt_dist)
            else:
                outcome = "OPEN"
                eod_fav = eod_direction(idx_entry, is_ce, candles)
                touch = None

            binary = 1 if outcome == "TARGET" else (1 if outcome == "OPEN" and eod_fav else 0)
            out.append(SignalResult(
                symbol=sig["symbol"],
                direction="CE" if is_ce else "PE",
                confidence=sig["confidence"],
                index_entry=idx_entry,
                index_sl=idx_sl,
                index_target=idx_tgt,
                generated_at=gen_at,
                indicators=ind,
                has_barriers=has_barriers,
                outcome=outcome,
                eod_favorable=eod_fav,
                binary_hit=binary,
                resolved=outcome in ("TARGET", "SL"),
                touch_time=touch,
                fwd_fav=forward_direction(idx_entry, is_ce, gen_at, candles),
                rand_baseline=rand_baseline,
            ))
            if trace:
                tt = touch.astimezone(IST).strftime("%H:%M") if touch else "  -  "
                bar = "" if has_barriers else " [no-barrier:EOD]"
                print(f"  {sig_date} {gen_at.astimezone(IST).strftime('%H:%M')} "
                      f"{sig['symbol']:<10} {('CE' if is_ce else 'PE')} conf={sig['confidence']:5.1f} "
                      f"E={idx_entry:>10.2f} SL={(idx_sl or 0):>10.2f} T={(idx_tgt or 0):>10.2f} "
                      f"-> {outcome:<6} @{tt} hit={binary}{bar}")
    print(f"  with index barriers (first-touch eligible): {n_barrier} / {len(out)}  "
          f"({len(out) - n_barrier} EOD-direction only)")
    return out


def print_headline(results: list[SignalResult], title: str) -> None:
    overall = summarize(results, "ALL")
    resolved = [r for r in results if r.resolved]
    mean_rand = (sum(r.rand_baseline for r in resolved if r.rand_baseline is not None)
                 / len(resolved) * 100) if resolved else float("nan")
    print(f"\n{'='*74}\n  {title}\n{'='*74}")
    print(f"  Signals with data: {overall['n']}   "
          f"TARGET={overall['target']} SL={overall['sl']} OPEN={overall['open']}")
    print(f"  Binary hit-rate (TARGET, or OPEN-favorable-by-EOD direction): "
          f"{overall['hit_rate']:.1f}%")
    if resolved:
        print(f"  Target-first rate (TARGET / resolved): "
              f"{overall['target_first_rate']:.1f}%  ({overall['resolved']} resolved)  "
              f"vs per-signal random ~{mean_rand:.1f}%")


async def main_async(args) -> None:
    end_date = args.end or args.start
    results = await build_results(
        args.start, end_date, args.min_confidence, args.trace, ties=args.ties,
        strategy_name=args.strategy)
    if not results:
        print("No signals with candle data in range.")
        return

    print_headline(results, f"{args.strategy.upper()} SIGNAL ACCURACY   "
                            f"conf>={args.min_confidence:g}   {args.start} -> {end_date}")
    analyze_forward(results)
    analyze_calibration(results)
    analyze_option_type(results)
    analyze_per_index(results)
    analyze_factors(results)
    analyze_regime(results)
    analyze_s2_cuts(results)
    analyze_refire(results)

    # Candidate filters — lift measured by re-running the binary hit-rate.
    print(f"\n{'='*74}\n  CANDIDATE FILTER LIFTS\n{'='*74}")
    report_filter_lift(results, "drop bias-opposed (own intraday_bias mod/weak opposes)",
                       lambda r: not _bias_opposed(r))
    report_filter_lift(results, "drop counter-trend (nifty_day_change_pct opposes dir)",
                       lambda r: not _counter_trend(r))
    report_filter_lift(results, "IN_WINDOW only (9:45-11:00 / 13:45-14:45)", _in_window)
    report_filter_lift(results, "OI-confirmed only",
                       lambda r: r.indicators.get("oi_confirmed") is True)
    report_filter_lift(results, "CPR NARROW only",
                       lambda r: r.indicators.get("cpr_type") == "NARROW")
    report_filter_lift(results, "confidence >= 70", lambda r: r.confidence >= 70)
    report_filter_lift(
        results, "COMBINED: not bias-opposed AND not counter-trend AND IN_WINDOW",
        lambda r: not _bias_opposed(r) and not _counter_trend(r) and _in_window(r))

    # Within-window split — flag window-specific findings (the S5 blind spot was a
    # lopsided 80/418 calendar split). Split COUNT-balanced (chronological) so the
    # two halves carry comparable n; print the cut date so the calendar skew is
    # still visible (S2 signal volume rises over the window).
    print(f"\n{'='*74}\n  WITHIN-WINDOW SPLIT (count-balanced robustness check)\n{'='*74}")
    ordered = sorted(results, key=lambda r: r.generated_at)
    if len(ordered) >= 4:
        half_n = len(ordered) // 2
        first, second = ordered[:half_n], ordered[half_n:]
        cut = second[0].generated_at.astimezone(IST).date()
        print(f"  cut at signal #{half_n} (≈{cut}); first half ends "
              f"{first[-1].generated_at.astimezone(IST).date()}")
        for half, lbl in ((first, "first half"), (second, "second half")):
            s = summarize(half, lbl)
            r_conf = pearson([x.confidence for x in half], [float(x.binary_hit) for x in half])
            iw = summarize([x for x in half if _in_window(x)], "in-window")
            ct = summarize([x for x in half if _counter_trend(x)], "counter")
            rconf_s = f"{r_conf:+.3f}" if r_conf is not None else "n/a"
            print(f"\n  {lbl}: n={s['n']}  hit {s['hit_rate']:.1f}%  "
                  f"tgt-first {s['target_first_rate']:.1f}% ({s['resolved']} res)  r(conf)={rconf_s}")
            print(f"      in-window: n={iw['n']} hit {iw['hit_rate']:.1f}%   "
                  f"counter-trend: n={ct['n']} hit {ct['hit_rate']:.1f}%")
    print()


def main() -> None:
    p = argparse.ArgumentParser(description="Strategy 2 signal-accuracy measurement")
    p.add_argument("--start", type=parse_date, required=True)
    p.add_argument("--end", type=parse_date, default=None)
    p.add_argument("--strategy", choices=["vwap_pullback", "vwap_reclaim"],
                   default="vwap_pullback",
                   help="index-options strategy to score (vwap_reclaim = the entry redesign)")
    p.add_argument("--min-confidence", type=float, default=0.0)
    p.add_argument("--ties", choices=["sl", "target"], default="sl",
                   help="same-candle both-touch tie-break (default sl, conservative)")
    p.add_argument("--trace", action="store_true", help="print every signal's resolution")
    args = p.parse_args()
    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
