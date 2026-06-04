"""Stop-out probe for S2 — what do the ~47 stop-outs share, and is the fix entry
quality or stop management?

The target re-sim showed nearer targets don't help (they cap the fat-tail winners). The
remaining leak is the stop-outs at −33%. This probe walks each stop-out's REAL option 1m
premium path to ask the decisive question: **did the premium ever go green before
stopping?**
  - If stop-outs ran up first (high MFE) → it's a *stop-management* problem (a breakeven /
    trailing stop would save them WITHOUT capping winners — the cap re-sim's blind spot).
  - If stop-outs went straight against us (MFE ≈ 0) → it's an *entry-quality* problem (the
    entry sits at a local premium top) → an S6-style entry redesign is the lever.

Then: (B) which entry-time features separate stop-outs from target-hits, and (C) a
stop-management re-sim (breakeven / trailing on the real candles) to see if — unlike the
target cap — it beats the original baseline.

Data (bt, staged from prod): `s2_rsim` (trade + signal indicators + option symbol),
`s2_opt` (the contracts' 1m candles).

Usage:
    cd backend && source .venv/bin/activate
    DATABASE_URL=postgresql+asyncpg://trader:trader_dev_123@localhost:5433/stocktrading_bt \
        python scripts/research_strategy2_stopout_probe.py
"""

from __future__ import annotations

import asyncio
import statistics
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, time as dt_time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from app.core.constants import (  # noqa: E402
    DEAD_ZONE_END, DEAD_ZONE_START, IST,
    WINDOW_1_END, WINDOW_1_START, WINDOW_2_END, WINDOW_2_START,
)
from app.core.database import async_session_factory  # noqa: E402

CLOSE_DEADLINE = dt_time(15, 25)
DEFAULT_SL_PCT = 0.33
DEFAULT_TGT_PCT = 0.50


@dataclass
class RT:
    osym: str
    entry: float
    qty: int
    entry_ts: datetime
    exit_reason: str
    conf: float
    is_ce: bool
    symbol: str
    sl_pct: float
    tgt_pct: float
    ind: dict
    candles: list   # (ts,o,h,l,c) from entry -> 15:25

    @property
    def f(self) -> dict:
        return self.ind.get("confidence_factors", {}) or {}


def resolve(entry, target, sl, candles):
    """First-touch (ts_stop_index, outcome). Long premium."""
    for i, (ts, o, h, l, c) in enumerate(candles):
        if l <= sl:
            return i, "SL"
        if h >= target:
            return i, "TARGET"
    return len(candles) - 1, "TIME"


def mfe_before(idx, entry, candles) -> float:
    """Max favorable excursion% over candles[0..idx] (inclusive)."""
    if not candles:
        return 0.0
    hi = max(c[2] for c in candles[: idx + 1])
    return (hi / entry - 1) * 100


async def load():
    from sqlalchemy import text
    async with async_session_factory() as s:
        rows = (await s.execute(text(
            "SELECT osym, entry_prem, created_at, exit_reason, quantity, sig_entry, "
            "sig_sl, sig_target, symbol, signal_type, conf, indicators FROM s2_rsim"))).all()
        cand = (await s.execute(text(
            "SELECT symbol, ts, open, high, low, close FROM s2_opt ORDER BY ts"))).all()
    by_sym = defaultdict(list)
    for c in cand:
        by_sym[c.symbol].append((c.ts, float(c.open), float(c.high), float(c.low), float(c.close)))
    trades = []
    for r in rows:
        se, ssl, stg = float(r.sig_entry or 0), float(r.sig_sl or 0), float(r.sig_target or 0)
        sl_pct = (se - ssl) / se if se > 0 and 0 < (se - ssl) / se < 0.9 else DEFAULT_SL_PCT
        tgt_pct = (stg - se) / se if se > 0 and stg > se else DEFAULT_TGT_PCT
        day = r.created_at.astimezone(IST).date()
        deadline = datetime.combine(day, CLOSE_DEADLINE, tzinfo=IST)
        cs = [c for c in by_sym.get(r.osym, [])
              if c[0] >= r.created_at and c[0].astimezone(IST) <= deadline]
        trades.append(RT(
            osym=r.osym, entry=float(r.entry_prem), qty=int(r.quantity),
            entry_ts=r.created_at, exit_reason=r.exit_reason,
            conf=float(r.conf) if r.conf is not None else 0.0,
            is_ce=r.signal_type == "BUY_CE", symbol=r.symbol,
            sl_pct=sl_pct, tgt_pct=tgt_pct, ind=r.indicators or {}, candles=cs))
    return trades


def window(t: RT) -> str:
    tm = t.entry_ts.astimezone(IST).time()
    if WINDOW_1_START <= tm <= WINDOW_1_END or WINDOW_2_START <= tm <= WINDOW_2_END:
        return "IN_WINDOW"
    if DEAD_ZONE_START <= tm <= DEAD_ZONE_END:
        return "DEAD_ZONE"
    return "OUT"


def bias_aligned(t: RT):
    ib = t.ind.get("intraday_bias")
    if not isinstance(ib, dict) or "score" not in ib:
        return None
    s = float(ib["score"])
    return None if abs(s) < 0.20 else ((s > 0) == t.is_ce)


# ── Stop-management re-sim policies (all keep the ORIGINAL far target) ──

def sim_policy(t: RT, be_trigger=None, trail=None):
    """Walk candles; return (outcome, ret_frac). be_trigger: move SL->breakeven after
    +be% high. trail: once green, SL = max_high*(1-trail). Target stays original (far)."""
    entry, sl0 = t.entry, t.entry * (1 - t.sl_pct)
    target = t.entry * (1 + t.tgt_pct)
    sl = sl0
    peak = entry
    for ts, o, h, l, c in t.candles:
        # update trailing SL using the prior peak (conservative: stop checked vs current sl)
        if l <= sl:
            return "SL", sl / entry - 1
        if h >= target:
            return "TARGET", target / entry - 1
        peak = max(peak, h)
        if be_trigger is not None and peak >= entry * (1 + be_trigger) and sl < entry:
            sl = entry  # breakeven
        if trail is not None and peak > entry:
            sl = max(sl, peak * (1 - trail))
    return "TIME", t.candles[-1][4] / entry - 1 if t.candles else 0.0


def summ(results):
    rets = [r for _, r in results]
    n = len(results)
    wins = sum(1 for _, r in results if r > 0)
    nets = [r for _, r in results]  # equal-weight return; net below uses notional in caller
    return {"n": n, "win": wins / n * 100 if n else 0,
            "avg": statistics.mean(rets) * 100 if rets else 0,
            "med": statistics.median(rets) * 100 if rets else 0,
            "sl": sum(1 for o, _ in results if o == "SL"),
            "tgt": sum(1 for o, _ in results if o == "TARGET")}


async def main():
    trades = await load()
    # 1m-engine resolution (consistent with the target re-sim).
    resolved = []
    for t in trades:
        target = t.entry * (1 + t.tgt_pct)
        idx, outcome = resolve(t.entry, target, t.entry * (1 - t.sl_pct), t.candles)
        resolved.append((t, idx, outcome))
    stops = [(t, idx) for t, idx, o in resolved if o == "SL"]
    tgts = [t for t, idx, o in resolved if o == "TARGET"]

    print(f"\n{'='*88}")
    print(f"  S2 STOP-OUT PROBE — {len(trades)} shadow trades, {len(stops)} stop-outs "
          f"(1m engine), {len(tgts)} target-hits")
    print(f"{'='*88}")

    # ── A. Did the stop-outs ever go green? (MFE before the stop) ──
    print(f"\n  A. MAX FAVORABLE EXCURSION of stop-outs before stopping (real premium path):")
    buckets = {"<+5% (never green)": 0, "+5-15%": 0, "+15-25%": 0, "+25%+": 0}
    mfes, tts = [], []
    for t, idx in stops:
        m = mfe_before(idx, t.entry, t.candles)
        mfes.append(m)
        mins = (t.candles[idx][0] - t.entry_ts).total_seconds() / 60 if t.candles else 0
        tts.append(mins)
        if m < 5:
            buckets["<+5% (never green)"] += 1
        elif m < 15:
            buckets["+5-15%"] += 1
        elif m < 25:
            buckets["+15-25%"] += 1
        else:
            buckets["+25%+"] += 1
    for k, v in buckets.items():
        bar = "#" * v
        print(f"    MFE {k:<20} {v:>3}  {bar}")
    if mfes:
        print(f"    median MFE {statistics.median(mfes):+.1f}%   "
              f"mean {statistics.mean(mfes):+.1f}%")
        print(f"    median time-to-stop {statistics.median(tts):.0f} min   "
              f"(<5min: {sum(1 for x in tts if x < 5)}, <15min: {sum(1 for x in tts if x < 15)})")

    # ── B. Entry-feature separation: stop-outs vs target-hits ──
    print(f"\n  B. ENTRY FEATURES — stop-outs vs target-hits (what's observable at signal):")
    so = [t for t, _ in stops]

    def mean_of(ts, fn):
        vals = [fn(t) for t in ts if fn(t) is not None]
        return statistics.mean(vals) if vals else float("nan")

    def rate_of(ts, fn):
        vals = [fn(t) for t in ts if fn(t) is not None]
        return 100 * sum(1 for v in vals if v) / len(vals) if vals else float("nan")

    feats_num = [
        ("confidence", lambda t: t.conf),
        ("reversal_quality", lambda t: t.f.get("reversal_quality")),
        ("rr_ratio_quality", lambda t: t.f.get("rr_ratio_quality")),
        ("|vwap_dist|%", lambda t: abs(float(t.ind["vwap_distance_pct"])) if t.ind.get("vwap_distance_pct") is not None else None),
        ("india_vix", lambda t: float(t.ind["india_vix"]) if t.ind.get("india_vix") is not None else None),
        ("entry premium", lambda t: t.entry),
    ]
    print(f"    {'feature':<18} {'stop-outs':>10} {'target-hits':>12}")
    for name, fn in feats_num:
        print(f"    {name:<18} {mean_of(so, fn):>10.2f} {mean_of(tgts, fn):>12.2f}")
    feats_rate = [
        ("CE %", lambda t: t.is_ce),
        ("bias-aligned %", bias_aligned),
        ("CPR NARROW %", lambda t: t.ind.get("cpr_type") == "NARROW"),
        ("oi_confirmed %", lambda t: t.ind.get("oi_confirmed") is True),
        ("IN_WINDOW %", lambda t: window(t) == "IN_WINDOW"),
    ]
    for name, fn in feats_rate:
        print(f"    {name:<18} {rate_of(so, fn):>9.0f}% {rate_of(tgts, fn):>11.0f}%")

    # ── C. Stop-management re-sim (keeps the far target — unlike the bad target cap) ──
    print(f"\n  C. STOP-MANAGEMENT re-sim (original far target kept; same 1m engine):")
    print(f"    {'policy':<22} {'win%':>6} {'avgRet%':>8} {'medRet%':>8} {'SL':>4} {'TGT':>4} {'net(1lot)':>11}")
    def net_of(policy_results):
        return sum(r * t.entry * t.qty for (t, (o, r)) in policy_results)
    def run(label, **kw):
        pr = [(t, sim_policy(t, **kw)) for t in trades]
        s = summ([res for _, res in pr])
        print(f"    {label:<22} {s['win']:>6.1f} {s['avg']:>+8.1f} {s['med']:>+8.1f} "
              f"{s['sl']:>4} {s['tgt']:>4} {net_of(pr):>+11,.0f}")
    run("original (no mgmt)")
    run("breakeven @ +10%", be_trigger=0.10)
    run("breakeven @ +15%", be_trigger=0.15)
    run("breakeven @ +20%", be_trigger=0.20)
    run("trail 25% from peak", trail=0.25)
    run("trail 33% from peak", trail=0.33)
    print()


if __name__ == "__main__":
    asyncio.run(main())
