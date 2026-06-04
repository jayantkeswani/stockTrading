"""Faithful target-distance re-sim for S2 — walks each shadow trade's REAL option 1m
premium candles and asks: if the target were capped nearer (+X%), how many stop-outs /
time-exits convert to wins, and what happens to net P&L?

The signal-accuracy + edge studies showed S2's targets are unreachable (high-conf
signals hit target 4/62; targets pay +46% but only print 18% of the time, stops −33% at
51%). This sizes the fix on actual premium paths rather than a delta approximation.

Per trade: SL% is the signal's premium SL% applied to the live fill (`entry_prem`); the
original target is the signal's premium target%. For each cap X we set the effective
target = min(original, entry×(1+X)) and walk the option candles entry→15:25 for a
first-touch (high≥target before low≤SL; same-candle tie → SL, conservative). Outcome
return% = +X (target) / −SL% (stop) / last-close% (neither). Both the original target and
the caps are re-sim'd on the SAME 1m engine, so the cap-vs-original comparison is clean;
the live realized P&L is shown alongside for engine-fidelity context (1m first-touch runs
optimistic vs live ticks — trust the *relative* cap effect, not the absolute level).

Data (staged into bt from prod): `s2_rsim` (one row/shadow trade incl. signal premium
SL/target + fyers_option_symbol), `s2_opt` (the option contracts' 1m candles).

Usage:
    cd backend && source .venv/bin/activate
    DATABASE_URL=postgresql+asyncpg://trader:trader_dev_123@localhost:5433/stocktrading_bt \
        python scripts/research_strategy2_target_resim.py
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

from app.core.constants import IST  # noqa: E402
from app.core.database import async_session_factory  # noqa: E402

CLOSE_DEADLINE = dt_time(15, 25)
CAPS = [10, 15, 20, 25, 30, 40]      # % target caps to test
DEFAULT_SL_PCT = 0.33                  # fallback when signal SL is degenerate
DEFAULT_TGT_PCT = 0.50


@dataclass
class RTrade:
    osym: str
    entry: float
    qty: int
    entry_ts: datetime
    exit_reason: str
    live_net: float
    sl_pct: float
    tgt_pct: float
    conf: float


def first_touch(candles, entry, target, sl):
    """Long-premium first-touch return fraction: +target / −sl / last-close.
    candles: list of (ts, o, h, l, c). Returns (outcome, ret_frac)."""
    if not candles:
        return "NO_DATA", 0.0
    for ts, o, h, l, c in candles:
        sl_hit = l <= sl
        tgt_hit = h >= target
        if sl_hit and tgt_hit:           # conservative: stop prints first
            return "SL", sl / entry - 1
        if sl_hit:
            return "SL", sl / entry - 1
        if tgt_hit:
            return "TARGET", target / entry - 1
    return "TIME", candles[-1][4] / entry - 1


async def load():
    from sqlalchemy import text
    async with async_session_factory() as s:
        rows = (await s.execute(text(
            "SELECT osym, entry_prem, created_at, exit_reason, net_pnl, quantity, "
            "sig_entry, sig_sl, sig_target, conf FROM s2_rsim"))).all()
        trades = []
        for r in rows:
            se, ssl, stg = (float(r.sig_entry or 0), float(r.sig_sl or 0),
                            float(r.sig_target or 0))
            sl_pct = (se - ssl) / se if se > 0 and 0 < (se - ssl) / se < 0.9 else DEFAULT_SL_PCT
            tgt_pct = (stg - se) / se if se > 0 and stg > se else DEFAULT_TGT_PCT
            trades.append(RTrade(
                osym=r.osym, entry=float(r.entry_prem), qty=int(r.quantity),
                entry_ts=r.created_at, exit_reason=r.exit_reason,
                live_net=float(r.net_pnl), sl_pct=sl_pct, tgt_pct=tgt_pct,
                conf=float(r.conf) if r.conf is not None else 0.0))
        cand = (await s.execute(text(
            "SELECT symbol, ts, open, high, low, close FROM s2_opt ORDER BY ts"))).all()
    by_sym = defaultdict(list)
    for c in cand:
        by_sym[c.symbol].append((c.ts, float(c.open), float(c.high), float(c.low), float(c.close)))
    return trades, by_sym


def trade_candles(t: RTrade, by_sym) -> list:
    day = t.entry_ts.astimezone(IST).date()
    deadline = datetime.combine(day, CLOSE_DEADLINE, tzinfo=IST)
    return [c for c in by_sym.get(t.osym, [])
            if c[0] >= t.entry_ts and c[0].astimezone(IST) <= deadline]


def sim(trades, by_sym, target_level_fn):
    """Return list of (t, outcome, ret_frac) for a target-level policy."""
    out = []
    for t in trades:
        cs = trade_candles(t, by_sym)
        sl = t.entry * (1 - t.sl_pct)
        target = target_level_fn(t)
        outcome, ret = first_touch(cs, t.entry, target, sl)
        out.append((t, outcome, ret))
    return out


def summarize(res):
    rets = [r for _, _, r in res]
    nets = [r * t.entry * t.qty for t, _, r in res]
    wins = sum(1 for _, _, r in res if r > 0)
    n = len(res)
    return {
        "n": n, "win": wins / n * 100 if n else 0,
        "avg_ret": statistics.mean(rets) * 100 if rets else 0,
        "med_ret": statistics.median(rets) * 100 if rets else 0,
        "net": sum(nets),
        "tgt": sum(1 for _, o, _ in res if o == "TARGET"),
        "sl": sum(1 for _, o, _ in res if o == "SL"),
        "time": sum(1 for _, o, _ in res if o in ("TIME", "NO_DATA")),
    }


async def main():
    trades, by_sym = await load()
    n = len(trades)
    live_net = sum(t.live_net for t in trades)
    live_win = sum(1 for t in trades if t.live_net > 0) / n * 100

    print(f"\n{'='*94}")
    print(f"  S2 TARGET-DISTANCE RE-SIM on REAL option premium candles — {n} shadow trades")
    print(f"{'='*94}")
    print(f"  LIVE realized (tick engine, for reference): win {live_win:.1f}%   "
          f"net {live_net:+,.0f}")

    # Original target re-sim'd on the 1m engine (apples-to-apples baseline for the caps).
    orig = sim(trades, by_sym, lambda t: t.entry * (1 + t.tgt_pct))
    o = summarize(orig)
    print(f"\n  {'policy':<16} {'win%':>6} {'avgRet%':>8} {'medRet%':>8} {'net(1lot)':>11} "
          f"{'TGT':>4} {'SL':>4} {'TIME':>5}")
    print(f"  {'-'*16} {'-'*6} {'-'*8} {'-'*8} {'-'*11} {'-'*4} {'-'*4} {'-'*5}")
    print(f"  {'original (resim)':<16} {o['win']:>6.1f} {o['avg_ret']:>+8.1f} "
          f"{o['med_ret']:>+8.1f} {o['net']:>+11,.0f} {o['tgt']:>4} {o['sl']:>4} {o['time']:>5}")
    for cap in CAPS:
        res = sim(trades, by_sym, lambda t, cap=cap: min(t.entry * (1 + t.tgt_pct),
                                                         t.entry * (1 + cap / 100)))
        s = summarize(res)
        print(f"  {'cap +'+str(cap)+'%':<16} {s['win']:>6.1f} {s['avg_ret']:>+8.1f} "
              f"{s['med_ret']:>+8.1f} {s['net']:>+11,.0f} {s['tgt']:>4} {s['sl']:>4} {s['time']:>5}")

    # The user's exact question, at +25%: conversions among live SL / TIME exits.
    cap = 25
    res25 = sim(trades, by_sym, lambda t: min(t.entry * (1 + t.tgt_pct), t.entry * 1.25))
    print(f"\n  CONVERSIONS at +{cap}% (real premium reached the cap before SL):")
    for grp in ("AGENT_SL", "TIME_EXIT", "AGENT_PROFIT"):
        sub = [(t, o, r) for (t, o, r) in res25 if t.exit_reason == grp]
        if not sub:
            continue
        hit = sum(1 for _, o, _ in sub if o == "TARGET")
        print(f"    live {grp:<13} n={len(sub):>2}  ->  {hit:>2} now hit +{cap}% "
              f"({hit/len(sub)*100:.0f}%)")

    # Confidence interaction: does the nearer target rescue the high-conf cohort?
    print(f"\n  By confidence at +{cap}% target (does it fix the inverted gate?):")
    for lbl, pred in (("conf < 50", lambda t: t.conf < 50),
                      ("conf 50-70", lambda t: 50 <= t.conf < 70),
                      ("conf >= 70", lambda t: t.conf >= 70)):
        sub = [(t, o, r) for (t, o, r) in res25 if pred(t)]
        if not sub:
            continue
        s = summarize(sub)
        print(f"    {lbl:<11} n={s['n']:>2}  win {s['win']:>5.1f}%  "
              f"avgRet {s['avg_ret']:>+6.1f}%  net {s['net']:>+9,.0f}")
    print()


if __name__ == "__main__":
    asyncio.run(main())
