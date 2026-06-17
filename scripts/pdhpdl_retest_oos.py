"""Out-of-sample test: PDH_PDL CHASE entry (current S5) vs RETEST entry (S6-style).

Pre-registered, NOT fitted to this data: the retest geometry is S6's frozen
BREAKOUT_RETEST_DEFAULTS (designed/validated on an EARLIER dataset). Both entries
arm on the SAME PDH_PDL breakout (5m close break + 1.5x vol + VWAP + STRONG-trend gate);
the chase enters at the break (level-based stop, measured-move target); the retest waits
for a 1m retest+reclaim and enters with a tight swing stop + 1.8 R:R. Scored
engine-independent (first-touch target-vs-stop + forward direction at +30m), split
chronologically into TRAIN (first half of days) / TEST (second half).

    DATABASE_URL=postgresql+asyncpg://trader:trader_dev_123@localhost:5433/stocktrading_bt \
        python scripts/pdhpdl_retest_oos.py
"""

import asyncio
import os
import sys
from collections import defaultdict, namedtuple
from datetime import time as dt_time
from statistics import mean

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

Candle = namedtuple("Candle", "timestamp open high low close volume")
T0930, T1330 = dt_time(9, 30), dt_time(13, 30)

# Frozen S6 retest params (BREAKOUT_RETEST_DEFAULTS) — pre-registered, untuned here.
R = dict(retest_prox=0.15, slice_buf=0.20, max_wait=30, vol_mult=1.5, vol_lb=20,
         sl_swing_buf=0.15, min_risk=0.10, max_risk=1.5, rr=1.8)


def five_min(day1m):
    b = defaultdict(list)
    for c in day1m:
        b[(c.timestamp.hour * 60 + c.timestamp.minute - 555) // 5].append(c)
    out = []
    for k in sorted(b):
        cs = b[k]
        out.append(Candle(cs[-1].timestamp, cs[0].open, max(x.high for x in cs),
                          min(x.low for x in cs), cs[-1].close, sum(x.volume for x in cs)))
    return out


def find_arm(day1m, pdh, pdl, trend, fns):
    """First qualifying PDH_PDL breakout in [09:30,13:30): arm + the chase entry."""
    compute_atr, calc_vwap, avg_vol = fns
    bars = five_min(day1m)
    mm = pdh - pdl
    if mm <= 0:
        return None
    for k, b in enumerate(bars):
        if not (T0930 <= b.timestamp.time() < T1330):
            continue
        price = b.close
        is_long, is_short = price > pdh, price < pdl
        if not (is_long or is_short):
            continue
        sofar = bars[:k + 1]
        av = avg_vol(sofar)
        if av > 0 and b.volume < av * 1.5:
            continue
        up = [c for c in day1m if c.timestamp <= b.timestamp]
        vw = calc_vwap([c.high for c in up], [c.low for c in up],
                       [c.close for c in up], [c.volume for c in up])
        if vw and ((is_long and price < vw.vwap) or (is_short and price > vw.vwap)):
            continue
        if trend and trend.strength == "STRONG" and (
            (is_long and trend.direction == "BEARISH") or (is_short and trend.direction == "BULLISH")):
            continue
        atr = compute_atr(sofar)
        slb = max(price * 0.005, atr * 0.5 if atr else price * 0.005)
        if is_long:
            sl, tgt, risk = pdh - slb, price + mm, price - (pdh - slb)
        else:
            sl, tgt, risk = pdl + slb, price - mm, (pdl + slb) - price
        if (is_long and sl >= price) or (is_short and sl <= price):
            continue
        if risk > 0 and abs(tgt - price) / risk < 1.5:
            tgt = price + risk * 1.5 if is_long else price - risk * 1.5
        if risk <= 0 or abs(tgt - price) / risk < 1.5:
            continue
        d = 1 if is_long else -1
        return {"dir": d, "level": pdh if is_long else pdl, "bar_ts": b.timestamp,
                "chase": {"t": b.timestamp, "entry": price, "sl": sl, "tgt": tgt,
                          "risk": risk, "reward": abs(tgt - price)}}
    return None


def retest_entry(arm, day1m):
    """S6-style retest+reclaim from the arm; None if it never fires."""
    d, level = arm["dir"], arm["level"]
    after = [c for c in day1m if c.timestamp > arm["bar_ts"]]
    prox = level * R["retest_prox"] / 100
    slice_thr = level * (1 - d * R["slice_buf"] / 100)
    vols = [c.volume for c in day1m if c.timestamp <= arm["bar_ts"]]
    retest, swing = False, None
    for i, c in enumerate(after):
        if i >= R["max_wait"]:
            break
        if (d == 1 and c.close < slice_thr) or (d == -1 and c.close > slice_thr):
            return None  # decisive wrong-side close = breakout failed
        if not retest:
            if (d == 1 and c.low <= level + prox) or (d == -1 and c.high >= level - prox):
                retest, swing = True, (c.low if d == 1 else c.high)
        else:
            swing = (min(swing, c.low) if d == 1 else max(swing, c.high))
            if (d == 1 and c.close > level) or (d == -1 and c.close < level):  # reclaim
                hist = vols[-R["vol_lb"]:]
                av = mean(hist) if hist else c.volume
                if av > 0 and c.volume < av * R["vol_mult"]:
                    vols.append(c.volume)
                    continue  # weak reclaim — keep waiting
                entry = c.close
                if d == 1:
                    sl = swing * (1 - R["sl_swing_buf"] / 100); risk = entry - sl
                else:
                    sl = swing * (1 + R["sl_swing_buf"] / 100); risk = sl - entry
                minr, maxr = entry * R["min_risk"] / 100, entry * R["max_risk"] / 100
                if risk < minr:
                    risk = minr; sl = entry - d * risk
                if risk <= 0 or risk > maxr:
                    return None
                tgt = entry + d * R["rr"] * risk
                return {"t": c.timestamp, "entry": entry, "sl": sl, "tgt": tgt,
                        "risk": risk, "reward": abs(tgt - entry)}
        vols.append(c.volume)
    return None


def score(sig, day1m, d):
    e, sl, tgt = sig["entry"], sig["sl"], sig["tgt"]
    after = [c for c in day1m if c.timestamp > sig["t"]]
    outcome = "EOD"
    for c in after:
        hs = (c.low <= sl) if d == 1 else (c.high >= sl)
        ht = (c.high >= tgt) if d == 1 else (c.low <= tgt)
        if hs:
            outcome = "STOP"; break
        if ht:
            outcome = "TARGET"; break
    cut = [c for c in after if (c.timestamp - sig["t"]).total_seconds() <= 1800]
    f30 = ((cut[-1].close - e) * d / e * 100) if cut else 0.0
    return {"outcome": outcome, "f30": f30, "rnd": sig["risk"] / (sig["risk"] + sig["reward"])}


async def main():
    from sqlalchemy import select
    from app.core.constants import IST
    from app.core.database import async_session_factory
    from app.indicators.atr import compute_atr
    from app.indicators.candle_patterns import average_volume
    from app.indicators.stock_trend import compute_stock_trend
    from app.indicators.vwap import calculate_vwap
    from app.models.market_data import MarketData1m

    fns = (compute_atr, calculate_vwap, average_volume)
    _IDX = {"NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "NIFTYNXT50", "SENSEX", "BANKEX", "NIFTYIT", "SENSEX50"}
    async with async_session_factory() as s:
        allsyms = [r[0] for r in (await s.execute(select(MarketData1m.symbol).distinct())).all()]
    syms = sorted(x for x in allsyms if ":" not in x and not x.endswith("_FUT") and x not in _IDX)
    print(f"Universe: {len(syms)} stocks | retest params = S6 frozen defaults (untuned)")

    recs = []
    all_days = set()
    for si, sym in enumerate(syms):
        async with async_session_factory() as s:
            rows = (await s.execute(
                select(MarketData1m.timestamp, MarketData1m.open, MarketData1m.high,
                       MarketData1m.low, MarketData1m.close, MarketData1m.volume)
                .where(MarketData1m.symbol == sym).order_by(MarketData1m.timestamp))).all()
        by = defaultdict(list)
        for ts, o, h, l, c, v in rows:
            t = ts.astimezone(IST)
            by[t.date()].append(Candle(t, float(o), float(h), float(l), float(c), int(v)))
        days = sorted(by)
        all_days.update(days)
        daily = [Candle(by[d][0].timestamp, by[d][0].open, max(x.high for x in by[d]),
                        min(x.low for x in by[d]), by[d][-1].close, sum(x.volume for x in by[d])) for d in days]
        for i in range(1, len(days)):
            trend = compute_stock_trend(daily[:i]) if i >= 10 else None
            arm = find_arm(by[days[i]], daily[i - 1].high, daily[i - 1].low, trend, fns)
            if not arm:
                continue
            ch = score(arm["chase"], by[days[i]], arm["dir"])
            ch.update(coh="chase", day=days[i]); recs.append(ch)
            rt = retest_entry(arm, by[days[i]])
            if rt:
                rs = score(rt, by[days[i]], arm["dir"])
                rs.update(coh="retest", day=days[i]); recs.append(rs)
        if (si + 1) % 50 == 0:
            print(f"  ...{si+1}/{len(syms)}")

    sdays = sorted(all_days)
    mid = sdays[len(sdays) // 2]
    print(f"Split: TRAIN {sdays[0]}..{sdays[len(sdays)//2 - 1]}  |  TEST {mid}..{sdays[-1]}")

    def summ(rs):
        if not rs:
            return None
        n = len(rs)
        return dict(n=n, tgt=100 * sum(r["outcome"] == "TARGET" for r in rs) / n,
                    stop=100 * sum(r["outcome"] == "STOP" for r in rs) / n,
                    rnd=100 * mean(r["rnd"] for r in rs),
                    f30=100 * sum(r["f30"] > 0 for r in rs) / n)

    print("\n" + "=" * 84)
    print("CHASE (current S5 entry) vs RETEST (S6-style) — engine-independent, OUT-OF-SAMPLE")
    print("=" * 84)
    hdr = f"{'split':6} {'cohort':8} {'n':>5} {'tgt%':>6} {'rand%':>6} {'lift':>6} {'stop%':>6} {'fwd30+%':>8}"
    print(hdr)
    for split in ("TRAIN", "TEST"):
        sel = [r for r in recs if (r["day"] < mid) == (split == "TRAIN")]
        chase = [r for r in sel if r["coh"] == "chase"]
        retest = [r for r in sel if r["coh"] == "retest"]
        for label, rs in (("chase", chase), ("retest", retest)):
            m = summ(rs)
            if not m:
                print(f"{split:6} {label:8} (none)")
                continue
            print(f"{split:6} {label:8} {m['n']:>5} {m['tgt']:>6.1f} {m['rnd']:>6.1f} "
                  f"{m['tgt']-m['rnd']:>+6.1f} {m['stop']:>6.1f} {m['f30']:>8.1f}")
        if chase:
            print(f"       retest fill-rate: {len(retest)}/{len(chase)} = {100*len(retest)/len(chase):.0f}% of breakouts retest+reclaim")
        print("-" * 84)


if __name__ == "__main__":
    asyncio.run(main())
