"""PDH_PDL universe edge test — does the setup generalise beyond the screener's ~15 picks?

Replays the REAL S5 PDH_PDL entry geometry (strategy_5._check_pdh_pdl_breakout) across the
full F&O universe backfilled into market_data_1m, and scores ENGINE-INDEPENDENT first-touch
(did the stock hit target before stop) + forward direction, segmented by ADR / liquidity
decile. Reuses the real pure indicators (compute_atr/calculate_vwap/average_volume/
compute_stock_trend/compute_adr) so the trigger is faithful.

Deliberately UNGATED on confidence: the 9-factor composite needs screener context we don't
have for the universe, and we already found it inverted/uninformative — so this measures the
setup's intrinsic edge. First-touch + forward-direction are the engine-free measures the S5
accuracy study uses.

    DATABASE_URL=postgresql+asyncpg://trader:trader_dev_123@localhost:5433/stocktrading_bt \
        python scripts/pdhpdl_universe_eval.py
"""

import asyncio
import os
import sys
from collections import defaultdict, namedtuple
from datetime import time as dt_time
from statistics import mean, stdev

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

Candle = namedtuple("Candle", "timestamp open high low close volume")
T0930, T1400, T1530 = dt_time(9, 30), dt_time(14, 0), dt_time(15, 30)


def _five_min_bars(day1m):
    """Aggregate a day's 1m Candles into completed 5m bars, bucketed from 09:15."""
    buckets = defaultdict(list)
    for c in day1m:
        m = c.timestamp.hour * 60 + c.timestamp.minute
        buckets[(m - 555) // 5].append(c)  # 555 = 09:15 in minutes
    bars = []
    for k in sorted(buckets):
        cs = buckets[k]
        bars.append(Candle(cs[-1].timestamp, cs[0].open, max(c.high for c in cs),
                           min(c.low for c in cs), cs[-1].close, sum(c.volume for c in cs)))
    return bars


def _evaluate_day(symbol, day1m, pdh, pdl, trend, indicators_fns):
    """Return one signal dict (first qualifying break, ungated on confidence) or None."""
    compute_atr, calculate_vwap, average_volume = indicators_fns
    bars = _five_min_bars(day1m)
    mm = pdh - pdl
    if mm <= 0:
        return None
    for k in range(len(bars)):
        b = bars[k]
        bt = b.timestamp.time()
        if bt < T0930 or bt >= T1400:
            continue
        price = b.close
        is_long = price > pdh
        is_short = price < pdl
        if not (is_long or is_short):
            continue
        sofar = bars[: k + 1]
        avg_vol = average_volume(sofar)
        if avg_vol > 0 and b.volume < avg_vol * 1.5:
            continue
        # VWAP from the day's 1m up to this bar's close
        upto = [c for c in day1m if c.timestamp <= b.timestamp]
        vw = calculate_vwap([c.high for c in upto], [c.low for c in upto],
                            [c.close for c in upto], [c.volume for c in upto])
        if vw:
            if is_long and price < vw.vwap:
                continue
            if is_short and price > vw.vwap:
                continue
        if trend and trend.strength == "STRONG" and (
            (is_long and trend.direction == "BEARISH")
            or (is_short and trend.direction == "BULLISH")
        ):
            continue
        atr5 = compute_atr(sofar)
        sl_buf = max(price * 0.005, atr5 * 0.5 if atr5 else price * 0.005)
        if is_long:
            sl, tgt, risk = pdh - sl_buf, price + mm, price - (pdh - sl_buf)
        else:
            sl, tgt, risk = pdl + sl_buf, price - mm, (pdl + sl_buf) - price
        if (is_long and sl >= price) or (is_short and sl <= price):
            continue
        if risk > 0 and abs(tgt - price) / risk < 1.5:
            tgt = price + risk * 1.5 if is_long else price - risk * 1.5
        reward = abs(tgt - price)
        if risk <= 0 or reward / risk < 1.5:
            continue
        return {"symbol": symbol, "t": b.timestamp, "dir": 1 if is_long else -1,
                "entry": price, "sl": sl, "tgt": tgt, "risk": risk, "reward": reward}
    return None


def _trailing_exit(signal, after, be_pct=0.5, trail_pct=0.3, time_exit=dt_time(15, 25)):
    """Mirror trade_monitor trailing SL (breakeven at +0.5%, trail 0.3% below HWM, only
    ratchets favorably) + 3:25 time exit + measured-move target. Returns R (size-independent).
    Conservative: stop/target checked vs current SL before this candle trails it (no look-ahead)."""
    d, e, sl, tgt, risk = signal["dir"], signal["entry"], signal["sl"], signal["tgt"], signal["risk"]
    hwm, exitpx, cat = e, e, "EOD"
    for c in after:
        if c.timestamp.time() >= time_exit:
            exitpx, cat = c.open, "TIME"
            break
        hit_stop = (c.low <= sl) if d == 1 else (c.high >= sl)
        hit_tgt = (c.high >= tgt) if d == 1 else (c.low <= tgt)
        if hit_stop:  # conservative on ambiguous bars
            exitpx, cat = sl, ("TRAIL" if (d == 1 and sl >= e) or (d == -1 and sl <= e) else "STOP")
            break
        if hit_tgt:
            exitpx, cat = tgt, "TARGET"
            break
        fav = c.high if d == 1 else c.low
        hwm = max(hwm, fav) if d == 1 else min(hwm, fav)
        gain = (hwm - e) / e * 100 if d == 1 else (e - hwm) / e * 100
        if d == 1:
            if gain >= be_pct and sl < e:
                sl = e
            if sl >= e:
                sl = max(sl, hwm * (1 - trail_pct / 100))
        else:
            if gain >= be_pct and sl > e:
                sl = e
            if sl <= e:
                sl = min(sl, hwm * (1 + trail_pct / 100))
    else:
        exitpx = after[-1].close if after else e
    return (exitpx - e) * d / risk, cat


def _first_touch(signal, day1m):
    """Engine-independent first-touch (fixed target vs stop) + forward direction + the
    trailing-exit R (how the live book actually realises PDH_PDL)."""
    d, e, sl, tgt = signal["dir"], signal["entry"], signal["sl"], signal["tgt"]
    after = [c for c in day1m if c.timestamp > signal["t"]]
    outcome, exitpx = "EOD", (after[-1].close if after else e)
    for c in after:
        hit_stop = (c.low <= sl) if d == 1 else (c.high >= sl)
        hit_tgt = (c.high >= tgt) if d == 1 else (c.low <= tgt)
        if hit_stop:  # ambiguous bar → assume stop first (conservative)
            outcome, exitpx = "STOP", sl
            break
        if hit_tgt:
            outcome, exitpx = "TARGET", tgt
            break
    fwd = {}
    for mins in (15, 30, 60):
        cut = [c for c in after if (c.timestamp - signal["t"]).total_seconds() <= mins * 60]
        fwd[mins] = ((cut[-1].close if cut else e) - e) * d / e * 100
    Rt, cat_t = _trailing_exit(signal, after)
    R = (exitpx - e) * d / signal["risk"]
    rnd = signal["risk"] / (signal["risk"] + signal["reward"])
    return {**signal, "outcome": outcome, "R": R, "rnd": rnd, "Rt": Rt, "cat_t": cat_t,
            **{f"f{m}": fwd[m] for m in (15, 30, 60)}}


async def main():
    from sqlalchemy import select
    from app.core.constants import IST
    from app.core.database import async_session_factory
    from app.indicators.atr import compute_atr
    from app.indicators.adr import compute_adr
    from app.indicators.stock_trend import compute_stock_trend
    from app.indicators.vwap import calculate_vwap
    from app.indicators.candle_patterns import average_volume
    from app.models.market_data import MarketData1m

    ifns = (compute_atr, calculate_vwap, average_volume)

    _INDEX = {"NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "NIFTYNXT50",
              "SENSEX", "BANKEX", "NIFTYIT", "SENSEX50"}
    async with async_session_factory() as s:
        allsyms = [r[0] for r in (await s.execute(
            select(MarketData1m.symbol).distinct())).all()]
    syms = sorted(x for x in allsyms if ":" not in x and not x.endswith("_FUT") and x not in _INDEX)
    print(f"Universe: {len(syms)} stock symbols (of {len(allsyms)} total in DB)")

    per_stock = {}  # symbol -> {adr, turnover, signals:[...]}
    all_sigs = []
    for si, sym in enumerate(syms):
        async with async_session_factory() as s:
            rows = (await s.execute(
                select(MarketData1m.timestamp, MarketData1m.open, MarketData1m.high,
                       MarketData1m.low, MarketData1m.close, MarketData1m.volume)
                .where(MarketData1m.symbol == sym).order_by(MarketData1m.timestamp))).all()
        if not rows:
            continue
        by_day = defaultdict(list)
        for ts, o, h, l, c, v in rows:
            t = ts.astimezone(IST)
            by_day[t.date()].append(Candle(t, float(o), float(h), float(l), float(c), int(v)))
        days = sorted(by_day)
        daily = [Candle(by_day[d][0].timestamp, by_day[d][0].open, max(x.high for x in by_day[d]),
                        min(x.low for x in by_day[d]), by_day[d][-1].close,
                        sum(x.volume for x in by_day[d])) for d in days]
        adr = compute_adr(daily)
        turn = mean(sum(x.close * x.volume for x in by_day[d]) for d in days)  # avg daily turnover
        sigs = []
        for i in range(1, len(days)):
            pdh, pdl = daily[i - 1].high, daily[i - 1].low
            trend = compute_stock_trend(daily[:i]) if i >= 10 else None
            sig = _evaluate_day(sym, by_day[days[i]], pdh, pdl, trend, ifns)
            if sig:
                rec = _first_touch(sig, by_day[days[i]])
                sigs.append(rec)
                all_sigs.append(rec)
        per_stock[sym] = {"adr": adr, "turnover": turn, "n": len(sigs), "sigs": sigs}
        if (si + 1) % 40 == 0:
            print(f"  ...{si+1}/{len(syms)} processed, {len(all_sigs)} signals so far")

    # ---------- selection tagging (live shadow PDH_PDL picks) ----------
    subset = set()
    sub_path = os.environ.get("SUBSET_FILE", "/tmp/shadow_pdhpdl_subset.csv")
    if os.path.exists(sub_path):
        for ln in open(sub_path):
            ln = ln.strip()
            if "," in ln:
                sym, dy = ln.rsplit(",", 1)
                subset.add((sym, dy))
    for x in all_sigs:
        x["sel"] = (x["symbol"], str(x["t"].date())) in subset

    # ---------- summary helpers ----------
    def summ(sigs):
        if not sigs:
            return None
        n = len(sigs)
        return {
            "n": n,
            "tgt%": 100 * sum(x["outcome"] == "TARGET" for x in sigs) / n,
            "stop%": 100 * sum(x["outcome"] == "STOP" for x in sigs) / n,
            "R_ft": mean(x["R"] for x in sigs),     # fixed-target first-touch
            "R_trl": mean(x["Rt"] for x in sigs),   # trailing exit (how it's realised)
            "win%": 100 * sum(x["Rt"] > 0 for x in sigs) / n,
            "f30+": 100 * sum(x["f30"] > 0 for x in sigs) / n,
        }

    hdr = (f"{'segment':24} {'n':>5} {'tgt%':>6} {'stop%':>6} {'R_ft':>6} "
           f"{'R_trl':>6} {'win%':>6} {'f30+':>6}")

    def line(label, sg):
        m = summ(sg)
        if not m:
            print(f"{label:24} {'(no signals)':>12}")
            return
        print(f"{label:24} {m['n']:>5} {m['tgt%']:>6.1f} {m['stop%']:>6.1f} "
              f"{m['R_ft']:>+6.2f} {m['R_trl']:>+6.2f} {m['win%']:>6.1f} {m['f30+']:>6.1f}")

    print("\n" + "=" * 78)
    print("PDH_PDL UNIVERSE — first-touch + TRAILING exit (R = size-independent). Ungated.")
    print("R_ft = fixed measured-move target; R_trl = real trailing SL (be 0.5%, trail 0.3%)")
    print("=" * 78)
    print(hdr)
    line("ALL UNIVERSE", all_sigs)
    if subset:
        sel = [x for x in all_sigs if x["sel"]]
        line("SELECTED (live picks)", sel)
        line("UNSELECTED (rest)", [x for x in all_sigs if not x["sel"]])
        print(f"  [selection coverage: my eval fired on {len(sel)} of {len(subset)} live "
              f"shadow (symbol,day) picks]")

    fired = [sym for sym, d in per_stock.items() if d["n"] > 0]
    nstk = len(per_stock)
    print("\n-- by ADR decile (1=lowest, 10=highest) --")
    print(hdr)
    ranked = sorted(per_stock, key=lambda s: per_stock[s]["adr"])
    for dec in range(10):
        grp = ranked[dec * nstk // 10:(dec + 1) * nstk // 10]
        sg = [x for sym in grp for x in per_stock[sym]["sigs"]]
        adrs = [per_stock[sym]["adr"] for sym in grp]
        line(f"D{dec+1} ADR {min(adrs):.1f}-{max(adrs):.1f}%", sg)

    print("\n-- by LIQUIDITY decile (avg daily turnover; 1=thinnest, 10=most liquid) --")
    print(hdr)
    ranked = sorted(per_stock, key=lambda s: per_stock[s]["turnover"])
    for dec in range(10):
        grp = ranked[dec * nstk // 10:(dec + 1) * nstk // 10]
        sg = [x for sym in grp for x in per_stock[sym]["sigs"]]
        tns = [per_stock[sym]["turnover"] / 1e7 for sym in grp]
        line(f"D{dec+1} turn {min(tns):.0f}-{max(tns):.0f}cr", sg)

    # ---------- CUTOFF: where does PDH_PDL stop paying? (trailing R + t-stat) ----------
    def cut(label, sigs):
        if len(sigs) < 2:
            print(f"{label:26} (n<2)")
            return
        rt = [x["Rt"] for x in sigs]
        n, m, sd = len(rt), mean(rt), stdev(rt)
        t = m / (sd / n ** 0.5) if sd > 0 else 0.0
        w = 100 * sum(r > 0 for r in rt) / n
        f = 100 * sum(x["f30"] > 0 for x in sigs) / n
        print(f"{label:26} n={n:>5}  R_trl={m:>+6.3f}  t={t:>+5.2f}  win%={w:>5.1f}  f30+={f:>5.1f}")

    def by_adr(thr, lo=False):
        return [x for s in per_stock if (per_stock[s]["adr"] < thr) == lo
                for x in per_stock[s]["sigs"]]

    print("\n== ADR CUTOFF — pool stocks above/below threshold ==")
    for thr in (2.3, 2.5, 2.8, 3.1):
        cut(f"ADR >= {thr}%", by_adr(thr))
        cut(f"ADR <  {thr}%", by_adr(thr, lo=True))
    print("\n== does stacking filters help? ==")
    cut("ADR>=2.8 (all)", by_adr(2.8))
    cut("selected (any ADR)", [x for x in all_sigs if x["sel"]])
    cut("ADR>=2.8 AND selected", [x for x in by_adr(2.8) if x["sel"]])

    print(f"\nstocks firing: {len(fired)}/{nstk} | total signals: {len(all_sigs)} | "
          f"avg/firing-stock: {len(all_sigs)/max(len(fired),1):.1f}")


if __name__ == "__main__":
    asyncio.run(main())
