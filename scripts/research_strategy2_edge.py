"""Mine the S2 (VWAP-pullback) SHADOW trades for an actual edge on REAL option P&L.

Shadow takes every signal above the ~31% shadow floor as a 1-lot paper trade with real
option fills + real trade_monitor exits — so "YOLO conf>70, no other filter" is just the
conf>70 slice of Shadow. That means we can read what ANY entry filter would have done to
realized money, offline, no live A/B needed (the point the signal-accuracy study made on
index *direction*, here on actual premium).

This scans candidate entry filters and 2-way combinations, scoring each by the realized
P&L of the KEPT subset (win-rate, mean/median return%, total net), and reports a
train/test date split so an "edge" has to survive out-of-sample before we believe it.

Data: bt table `s2_shadow` (id, symbol, signal_type, signal_confidence, entry_price,
exit_price, quantity, pnl, net_pnl, exit_reason, created_at, indicators) — staged from
prod `trades` JOIN `signals` (source=SHADOW, strategy=vwap_pullback, CLOSED).

Usage:
    cd backend && source .venv/bin/activate
    DATABASE_URL=postgresql+asyncpg://trader:trader_dev_123@localhost:5433/stocktrading_bt \
        python scripts/research_strategy2_edge.py
    ... --min-n 20      # minimum kept-subset size to qualify as a usable filter
"""

from __future__ import annotations

import argparse
import asyncio
import statistics
import sys
from dataclasses import dataclass
from datetime import time as dt_time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from app.core.constants import (  # noqa: E402
    DEAD_ZONE_END, DEAD_ZONE_START, IST,
    WINDOW_1_END, WINDOW_1_START, WINDOW_2_END, WINDOW_2_START,
)
from app.core.database import async_session_factory  # noqa: E402


@dataclass
class T:
    symbol: str
    is_ce: bool
    conf: float
    entry: float
    qty: int
    net_pnl: float
    exit_reason: str
    ist_dt: object
    ind: dict

    @property
    def ret_pct(self) -> float:
        notional = self.entry * self.qty
        return (self.net_pnl / notional * 100) if notional else 0.0

    @property
    def win(self) -> int:
        return 1 if self.net_pnl > 0 else 0

    @property
    def f(self) -> dict:
        return self.ind.get("confidence_factors", {}) or {}

    def vwap_dist(self) -> float | None:
        v = self.ind.get("vwap_distance_pct")
        return abs(float(v)) if v is not None else None

    def bias_aligned(self) -> bool | None:
        ib = self.ind.get("intraday_bias")
        if not isinstance(ib, dict) or "score" not in ib:
            return None
        s = float(ib["score"])
        if abs(s) < 0.20:
            return None
        return (s > 0) == self.is_ce

    def window(self) -> str:
        t = self.ist_dt.time()
        if WINDOW_1_START <= t <= WINDOW_1_END or WINDOW_2_START <= t <= WINDOW_2_END:
            return "IN_WINDOW"
        if DEAD_ZONE_START <= t <= DEAD_ZONE_END:
            return "DEAD_ZONE"
        return "OUT_OF_WINDOW"

    def pd_prox(self) -> float | None:
        e = self.entry
        pdh, pdl = self.ind.get("pdh"), self.ind.get("pdl")
        # NOTE: pdh/pdl are INDEX levels; entry here is option premium — proximity only
        # meaningful via index_entry_price.
        ie = self.ind.get("index_entry_price")
        if ie is None:
            return None
        ie = float(ie)
        cands = [abs(ie - float(x)) / ie * 100 for x in (pdh, pdl) if x is not None]
        return min(cands) if cands else None


def load_rows(rows) -> list[T]:
    out = []
    for r in rows:
        ind = r.indicators or {}
        out.append(T(
            symbol=r.symbol,
            is_ce=r.signal_type == "BUY_CE",
            conf=float(r.signal_confidence) if r.signal_confidence is not None else 0.0,
            entry=float(r.entry_price),
            qty=int(r.quantity),
            net_pnl=float(r.net_pnl),
            exit_reason=r.exit_reason or "",
            ist_dt=r.created_at.astimezone(IST),
            ind=ind,
        ))
    return out


def stat(ts: list[T]) -> dict:
    n = len(ts)
    if n == 0:
        return {"n": 0}
    rets = [t.ret_pct for t in ts]
    return {
        "n": n,
        "win": sum(t.win for t in ts) / n * 100,
        "avg_ret": statistics.mean(rets),
        "med_ret": statistics.median(rets),
        "net": sum(t.net_pnl for t in ts),
    }


def line(label: str, s: dict, base_net_avg: float | None = None) -> str:
    if s["n"] == 0:
        return f"  {label:<34} n=0"
    return (f"  {label:<34} n={s['n']:>3}  win {s['win']:>5.1f}%  "
            f"avgRet {s['avg_ret']:>+6.1f}%  medRet {s['med_ret']:>+6.1f}%  "
            f"net {s['net']:>+9,.0f}")


# ── Candidate filters: (name, predicate). None-valued features auto-excluded. ──

def build_filters() -> list[tuple[str, object]]:
    F: list[tuple[str, object]] = []
    # Premium level (the ₹2 penny-option junk vs the intended ₹150-400 band)
    for thr in (30, 50, 100, 150):
        F.append((f"premium >= {thr}", lambda t, thr=thr: t.entry >= thr))
    F.append(("premium 150-400 band", lambda t: 150 <= t.entry <= 400))
    # Confidence
    F.append(("conf < 50", lambda t: t.conf < 50))
    F.append(("conf < 55", lambda t: t.conf < 55))
    F.append(("conf 50-70", lambda t: 50 <= t.conf < 70))
    F.append(("conf >= 70", lambda t: t.conf >= 70))
    # VWAP distance (pullback depth)
    F.append(("|vwap_dist| >= 0.10", lambda t: (t.vwap_dist() or 0) >= 0.10))
    F.append(("|vwap_dist| < 0.10", lambda t: t.vwap_dist() is not None and t.vwap_dist() < 0.10))
    # Confidence sub-factors
    F.append(("reversal_qual < 0.5", lambda t: "reversal_quality" in t.f and t.f["reversal_quality"] < 0.5))
    F.append(("reversal_qual >= 0.5", lambda t: "reversal_quality" in t.f and t.f["reversal_quality"] >= 0.5))
    F.append(("volume_qual < 0.7", lambda t: "volume_quality" in t.f and t.f["volume_quality"] < 0.7))
    F.append(("rr_ratio >= 0.6", lambda t: "rr_ratio_quality" in t.f and t.f["rr_ratio_quality"] >= 0.6))
    # Regime
    F.append(("bias-aligned", lambda t: t.bias_aligned() is True))
    F.append(("VIX >= 16", lambda t: t.ind.get("india_vix") is not None and float(t.ind["india_vix"]) >= 16))
    F.append(("VIX < 16", lambda t: t.ind.get("india_vix") is not None and float(t.ind["india_vix"]) < 16))
    F.append(("CPR NARROW", lambda t: t.ind.get("cpr_type") == "NARROW"))
    F.append(("CPR WIDE", lambda t: t.ind.get("cpr_type") == "WIDE"))
    F.append(("oi_confirmed", lambda t: t.ind.get("oi_confirmed") is True))
    F.append(("PDH/PDL away >=0.30%", lambda t: (t.pd_prox() or 0) >= 0.30))
    # Window / time
    F.append(("IN_WINDOW", lambda t: t.window() == "IN_WINDOW"))
    F.append(("not IN_WINDOW", lambda t: t.window() != "IN_WINDOW"))
    F.append(("afternoon (>=13:00)", lambda t: t.ist_dt.time() >= dt_time(13, 0)))
    F.append(("morning (<11:00)", lambda t: t.ist_dt.time() < dt_time(11, 0)))
    # Direction / index
    F.append(("CE only", lambda t: t.is_ce))
    F.append(("PE only", lambda t: not t.is_ce))
    for idx in ("NIFTY", "BANKNIFTY", "FINNIFTY", "SENSEX", "MIDCPNIFTY"):
        F.append((f"drop {idx}", lambda t, idx=idx: t.symbol != idx))
    return F


def score(ts: list[T], pred, min_n: int) -> dict | None:
    kept = [t for t in ts if pred(t)]
    s = stat(kept)
    if s["n"] < min_n:
        return None
    return s


def split_train_test(ts: list[T]):
    ordered = sorted(ts, key=lambda t: t.ist_dt)
    h = len(ordered) // 2
    return ordered[:h], ordered[h:]


async def main_async(min_n: int) -> None:
    from sqlalchemy import text
    async with async_session_factory() as session:
        res = await session.execute(text(
            "SELECT symbol, signal_type, signal_confidence, entry_price, quantity, "
            "net_pnl, exit_reason, created_at, indicators FROM s2_shadow "
            "WHERE net_pnl IS NOT NULL ORDER BY created_at"))
        ts = load_rows(res.all())

    base = stat(ts)
    print(f"\n{'='*92}")
    print(f"  S2 SHADOW EDGE RESEARCH — real option P&L, {base['n']} closed trades")
    print(f"{'='*92}")
    print(line("BASELINE (all)", base))
    print(f"  win-rate {base['win']:.1f}%  mean return {base['avg_ret']:+.1f}%  "
          f"total net {base['net']:+,.0f}  (1-lot paper)")

    # Exit-reason mix — how much is theta-bleed at 3:25?
    print(f"\n  Exit-reason mix (real money leaks here):")
    by_xr: dict[str, list[T]] = {}
    for t in ts:
        by_xr.setdefault(t.exit_reason, []).append(t)
    for xr, sub in sorted(by_xr.items(), key=lambda kv: -len(kv[1])):
        print(line(f"  {xr}", stat(sub)))

    train, test = split_train_test(ts)
    cut = test[0].ist_dt.date()
    print(f"\n  Train/test split at {cut}: train n={len(train)}, test n={len(test)}")

    # ── Single-filter scan, ranked by TEST avg return% (out-of-sample) ──
    print(f"\n{'-'*92}\n  SINGLE FILTERS (min kept n={min_n})  —  full / train / test\n{'-'*92}")
    rows = []
    for name, pred in build_filters():
        full = score(ts, pred, min_n)
        if full is None:
            continue
        tr = stat([t for t in train if pred(t)])
        te = stat([t for t in test if pred(t)])
        rows.append((name, full, tr, te))
    # Rank: profitable full-set first, then by test win-rate (robustness)
    rows.sort(key=lambda r: (r[1]["avg_ret"] > 0, r[3].get("win", -1) if r[3]["n"] else -1,
                             r[1]["avg_ret"]), reverse=True)
    for name, full, tr, te in rows:
        flag = "  <<< EDGE" if (full["avg_ret"] > 0 and te["n"] and te["avg_ret"] > 0
                                and tr["n"] and tr["avg_ret"] > 0) else ""
        print(line(name, full) + flag)
        trs = f"win {tr['win']:.0f}% ret {tr['avg_ret']:+.0f}%" if tr["n"] else "n<min"
        tes = f"win {te['win']:.0f}% ret {te['avg_ret']:+.0f}%" if te["n"] else "n<min"
        print(f"      train(n={tr['n']}): {trs:<22} test(n={te['n']}): {tes}")

    # ── 2-way combos of the most promising profitable single filters ──
    promising = [(n, p) for (n, p) in build_filters()
                 if (s := score(ts, p, max(8, min_n // 2))) and s["avg_ret"] > -5]
    print(f"\n{'-'*92}\n  BEST 2-WAY COMBOS (profitable full-set, min kept n={min_n})\n{'-'*92}")
    combos = []
    for i in range(len(promising)):
        for j in range(i + 1, len(promising)):
            n1, p1 = promising[i]
            n2, p2 = promising[j]
            pred = lambda t, p1=p1, p2=p2: p1(t) and p2(t)
            full = score(ts, pred, min_n)
            if full is None or full["avg_ret"] <= 0:
                continue
            te = stat([t for t in test if pred(t)])
            tr = stat([t for t in train if pred(t)])
            combos.append((f"{n1}  AND  {n2}", full, tr, te))
    combos.sort(key=lambda r: (r[3].get("win", -1) if r[3]["n"] else -1, r[1]["avg_ret"]), reverse=True)
    for name, full, tr, te in combos[:15]:
        flag = "  <<< robust" if (te["n"] >= max(6, min_n // 2) and te["avg_ret"] > 0
                                  and tr["n"] and tr["avg_ret"] > 0) else ""
        print(line(name, full) + flag)
        trs = f"win {tr['win']:.0f}% ret {tr['avg_ret']:+.0f}%" if tr["n"] else "n<min"
        tes = f"win {te['win']:.0f}% ret {te['avg_ret']:+.0f}%" if te["n"] else "n<min"
        print(f"      train(n={tr['n']}): {trs:<22} test(n={te['n']}): {tes}")
    print()


def main() -> None:
    p = argparse.ArgumentParser(description="S2 shadow-trade edge research (real P&L)")
    p.add_argument("--min-n", type=int, default=20)
    args = p.parse_args()
    asyncio.run(main_async(args.min_n))


if __name__ == "__main__":
    main()
