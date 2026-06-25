#!/usr/bin/env python3
"""Independent validation of the Intraday Hunter (@IntradayHunter) setups.

This script does NOT trust his self-reported P&L. It extracts his three setups
as falsifiable, mechanical hypotheses about INDEX behavior and scores them
engine-independent against historical 1m index spot candles — the same
first-touch + forward-direction methodology used by
`scripts/analyze_strategy2_signal_accuracy.py`.

Setups (mechanical, no discretion), classified per (index, day) at 09:15 open:

  Setup 1 — Large Gap-Down Reversal (LONG / CE):
      gap_pct <= -gap1  (default -1.5%). Expect index to revert UP from open.

  Setup 2 — Continuation aligned with previous-day structure:
      prev-day bearish close (close in lower third) AND gap DOWN in [-gap1,-gap_min]
          -> SHORT (continuation down)
      prev-day bullish close (close in upper third) AND gap UP in [+gap_min,+gap1]
          -> LONG (continuation up)

  Setup 3 — Fade an unstructured Gap-Up (SHORT / PE) — his documented loser:
      gap UP in [+gap_min,+gap1] AND prev-day NOT bullish -> SHORT (fade)

Scoring (entry = the 09:15 open, no look-ahead; he enters ~09:18 "while the
candle forms" — the open is the faithful anchor):
  * Forward direction at +15/+30/+60 min and EOD: did the index move in the
    predicted direction? (his exits are discretionary, so direction is the
    honest primary read).
  * First-touch: from entry, which barrier is hit first — target (rr*stop) or
    stop (stop_pct)? Ambiguous same-candle touches count as the stop (conservative).
  * MFE / time-to-peak: max favorable excursion and minutes to reach it
    (captures the "the move is in the first minute" reality seen on Mar 19).

Every metric is compared to a BASE RATE (the unconditional favorable rate for
the same direction across all days for that index), so we can tell a real edge
from generic drift-from-open. Results are split chronologically (train/test) to
guard against the prior study's post-hoc overfitting.

CAVEAT (carried from the S2/S7 work): index direction != live option win rate.
Theta decay and tight-stop whipsaw can flip a directionally-correct index call
into an option loss (this is what killed Strategy 7). Treat a positive index
edge here as necessary-but-not-sufficient; the options layer is a separate test.

Usage:
    DATABASE_URL=postgresql://trader:trader_dev_123@localhost:5433/stocktrading \
      python scripts/intraday_hunter/validate_setups.py
    python scripts/intraday_hunter/validate_setups.py --gap1 1.5 --stop-pct 0.5 --rr 1.5
    python scripts/intraday_hunter/validate_setups.py --indices BANKNIFTY
"""
from __future__ import annotations

import argparse
import asyncio
import os
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, time

import asyncpg

IST = "Asia/Kolkata"
DEFAULT_DB = "postgresql://trader:trader_dev_123@localhost:5433/stocktrading"
HORIZONS = [15, 30, 60]  # minutes after the 09:15 open


@dataclass
class Candle:
    t: time
    open: float
    high: float
    low: float
    close: float


@dataclass
class Day:
    dt: date
    candles: list[Candle]

    @property
    def open_0915(self) -> float | None:
        for c in self.candles:
            if c.t >= time(9, 15):
                return c.open
        return None

    @property
    def high(self) -> float:
        return max(c.high for c in self.candles)

    @property
    def low(self) -> float:
        return min(c.low for c in self.candles)

    @property
    def close(self) -> float:
        return self.candles[-1].close

    @property
    def close_position(self) -> float:
        """0.0 = closed at the day low, 1.0 = closed at the day high."""
        rng = self.high - self.low
        if rng <= 0:
            return 0.5
        return (self.close - self.low) / rng


@dataclass
class Signal:
    index: str
    dt: date
    setup: int
    direction: int  # +1 long, -1 short
    gap_pct: float
    entry: float
    # filled by scorer:
    fwd: dict = field(default_factory=dict)  # horizon -> favorable bool
    eod_fav: bool = False
    target_first: bool | None = None  # None = neither barrier hit by EOD
    mfe_pct: float = 0.0
    mae_pct: float = 0.0
    min_to_peak: int | None = None


async def load_index(conn, index: str) -> list[Day]:
    rows = await conn.fetch(
        f"""
        SELECT (timestamp AT TIME ZONE '{IST}')::date AS dt,
               (timestamp AT TIME ZONE '{IST}')::time AS t,
               open, high, low, close
        FROM market_data_1m
        WHERE symbol = $1
        ORDER BY timestamp
        """,
        index,
    )
    by_day: dict[date, list[Candle]] = defaultdict(list)
    for r in rows:
        by_day[r["dt"]].append(
            Candle(r["t"], float(r["open"]), float(r["high"]), float(r["low"]), float(r["close"]))
        )
    days = [Day(dt, cs) for dt, cs in sorted(by_day.items())]
    # keep only regular-session candles with valid prices, sorted
    for d in days:
        d.candles = [
            c for c in d.candles
            if time(9, 15) <= c.t <= time(15, 30) and min(c.open, c.high, c.low, c.close) > 0
        ]
    return [d for d in days if d.candles]


def score(sig: Signal, day: Day, args) -> None:
    """Fill forward-direction, first-touch, and MFE/MAE on the signal."""
    entry = sig.entry
    d = sig.direction
    stop_pct = args.stop_pct / 100.0
    tgt_pct = stop_pct * args.rr

    # barriers
    if d > 0:
        stop_lvl, tgt_lvl = entry * (1 - stop_pct), entry * (1 + tgt_pct)
    else:
        stop_lvl, tgt_lvl = entry * (1 + stop_pct), entry * (1 - tgt_pct)

    # walk candles from 09:15 onward
    best_fav = 0.0
    worst_fav = 0.0
    min_to_peak = None
    elapsed = 0
    for c in day.candles:
        if c.t < time(9, 15):
            continue
        # favorable excursion in direction d (use intrabar extreme in our favor)
        fav_extreme = (c.high - entry) / entry if d > 0 else (entry - c.low) / entry
        adv_extreme = (entry - c.low) / entry if d > 0 else (c.high - entry) / entry
        if fav_extreme > best_fav:
            best_fav = fav_extreme
            min_to_peak = elapsed
        worst_fav = max(worst_fav, adv_extreme)

        # first-touch (only if not already resolved)
        if sig.target_first is None:
            hit_tgt = c.high >= tgt_lvl if d > 0 else c.low <= tgt_lvl
            hit_stop = c.low <= stop_lvl if d > 0 else c.high >= stop_lvl
            if hit_stop:  # conservative: stop wins ties / same-candle
                sig.target_first = False
            elif hit_tgt:
                sig.target_first = True
        elapsed += 1

    sig.mfe_pct = best_fav * 100
    sig.mae_pct = worst_fav * 100
    sig.min_to_peak = min_to_peak

    # forward direction at horizons (price = close of candle nearest the mark)
    def price_at(minute: int) -> float | None:
        target_t = (9 * 60 + 15) + minute
        best = None
        for c in day.candles:
            ct = c.t.hour * 60 + c.t.minute
            if ct >= target_t:
                return c.close
            best = c.close
        return best  # past EOD -> last close

    for h in HORIZONS:
        p = price_at(h)
        if p is None:
            sig.fwd[h] = None
            continue
        sig.fwd[h] = (p > entry) if d > 0 else (p < entry)
    sig.eod_fav = (day.close > entry) if d > 0 else (day.close < entry)


def base_rates(days: list[Day]) -> dict:
    """Unconditional favorable rate for LONG (up-from-open) per horizon + EOD."""
    out = {h: [] for h in HORIZONS}
    out["eod"] = []
    for day in days:
        entry = day.open_0915
        if entry is None:
            continue

        def price_at(minute: int) -> float | None:
            target_t = (9 * 60 + 15) + minute
            best = None
            for c in day.candles:
                ct = c.t.hour * 60 + c.t.minute
                if ct >= target_t:
                    return c.close
                best = c.close
            return best

        for h in HORIZONS:
            p = price_at(h)
            if p is not None:
                out[h].append(p > entry)
        out["eod"].append(day.close > entry)
    return {k: (sum(v) / len(v) if v else 0.0, len(v)) for k, v in out.items()}


def pct(xs: list[bool]) -> str:
    xs = [x for x in xs if x is not None]
    if not xs:
        return "  n/a"
    return f"{100*sum(xs)/len(xs):4.0f}%"


def report_setup(name: str, sigs: list[Signal], base_long: dict, base_short: dict) -> None:
    if not sigs:
        print(f"\n{name}: no signals")
        return
    d = sigs[0].direction
    base = base_long if d > 0 else base_short
    print(f"\n{'='*78}\n{name}   (n={len(sigs)}, direction={'LONG' if d>0 else 'SHORT'})\n{'='*78}")

    # forward direction vs base rate
    print("  Forward-favorable (index moved predicted way) vs unconditional base rate:")
    for h in HORIZONS:
        got = [s.fwd.get(h) for s in sigs]
        b, _ = base[h]
        lift = (sum(1 for x in got if x) / len([x for x in got if x is not None]) - b) * 100 if any(
            x is not None for x in got) else 0
        print(f"    +{h:>2}min: {pct(got)}   base {100*b:4.0f}%   lift {lift:+5.1f}pp")
    eod = [s.eod_fav for s in sigs]
    be, _ = base["eod"]
    print(f"    EOD   : {pct(eod)}   base {100*be:4.0f}%   lift {(sum(eod)/len(eod)-be)*100:+5.1f}pp")

    # first-touch
    resolved = [s for s in sigs if s.target_first is not None]
    tf = [s.target_first for s in resolved]
    print(f"  First-touch target-first: {pct(tf)}  (resolved {len(resolved)}/{len(sigs)}; "
          f"unresolved counted neither)")

    # MFE / timing
    mfe = sorted(s.mfe_pct for s in sigs)
    mae = sorted(s.mae_pct for s in sigs)
    peaks = [s.min_to_peak for s in sigs if s.min_to_peak is not None]
    med = lambda xs: xs[len(xs)//2] if xs else 0
    print(f"  Median MFE {med(mfe):.2f}%   median MAE {med(mae):.2f}%   "
          f"median min-to-peak {sorted(peaks)[len(peaks)//2] if peaks else 0}min")

    # per index
    byidx = defaultdict(list)
    for s in sigs:
        byidx[s.index].append(s)
    print("  Per index (+30min fwd-fav):  " +
          "   ".join(f"{k}: {pct([s.fwd.get(30) for s in v])} (n={len(v)})"
                     for k, v in sorted(byidx.items())))


def chrono_split(sigs: list[Signal]) -> tuple[list[Signal], list[Signal]]:
    sigs = sorted(sigs, key=lambda s: s.dt)
    cut = int(len(sigs) * 0.6)
    return sigs[:cut], sigs[cut:]


async def run(args) -> None:
    db = os.environ.get("DATABASE_URL", DEFAULT_DB).replace("+asyncpg", "")
    conn = await asyncpg.connect(db)
    try:
        indices = args.indices.split(",")
        all_sigs: dict[int, list[Signal]] = defaultdict(list)
        base_long_all, base_short_all = [], []
        per_index_days = {}

        for index in indices:
            days = await load_index(conn, index)
            per_index_days[index] = days
            print(f"[{index}] {len(days)} trading days "
                  f"({days[0].dt} -> {days[-1].dt})")

            g1, gmin = args.gap1 / 100.0, args.gap_min / 100.0
            for i in range(1, len(days)):
                day, prev = days[i], days[i - 1]
                entry = day.open_0915
                if entry is None or entry <= 0 or prev.close <= 0:
                    continue
                gap = entry / prev.close - 1.0
                cp = prev.close_position

                sig = None
                if gap <= -g1:  # Setup 1: large gap-down reversal LONG
                    sig = Signal(index, day.dt, 1, +1, gap, entry)
                elif cp < 0.40 and -g1 < gap <= -gmin:  # Setup 2 down: bearish struct + gap down
                    sig = Signal(index, day.dt, 2, -1, gap, entry)
                elif cp > 0.60 and gmin <= gap < g1:  # Setup 2 up: bullish struct + gap up
                    sig = Signal(index, day.dt, 2, +1, gap, entry)
                elif gmin <= gap < g1 and cp <= 0.60:  # Setup 3: unstructured gap-up fade SHORT
                    sig = Signal(index, day.dt, 3, -1, gap, entry)

                if sig is not None:
                    score(sig, day, args)
                    all_sigs[sig.setup].append(sig)

        # base rates pooled across all indices/days
        pooled_days = [d for ds in per_index_days.values() for d in ds]
        bl = base_rates(pooled_days)
        base_long = bl
        # short base = 1 - long base (up vs down from open are complementary at a horizon)
        base_short = {k: (1 - v[0], v[1]) for k, v in bl.items()}

        print(f"\nBASE RATES (pooled, n_days={bl['eod'][1]}): "
              f"up-from-open +30min {100*bl[30][0]:.0f}%, EOD {100*bl['eod'][0]:.0f}%")

        names = {
            1: "SETUP 1 — Large Gap-Down -> Reversal LONG (his '~90%' claim)",
            2: "SETUP 2 — Structure-aligned Continuation",
            3: "SETUP 3 — Unstructured Gap-Up Fade SHORT (his documented loser)",
        }
        for setup in (1, 2, 3):
            report_setup(names[setup], all_sigs[setup], base_long, base_short)
            tr, te = chrono_split(all_sigs[setup])
            if tr and te:
                tr_fav = pct([s.fwd.get(30) for s in tr])
                te_fav = pct([s.fwd.get(30) for s in te])
                print(f"  TRAIN/TEST (+30min fwd-fav):  train {tr_fav} (n={len(tr)})   "
                      f"test {te_fav} (n={len(te)})")
    finally:
        await conn.close()


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--indices", default="NIFTY,BANKNIFTY,SENSEX")
    p.add_argument("--gap1", type=float, default=1.5,
                   help="Large-gap threshold %% for Setup 1 / upper bound for 2&3 (default 1.5)")
    p.add_argument("--gap-min", type=float, default=0.5,
                   help="Minimum gap %% to qualify Setups 2&3 (default 0.5)")
    p.add_argument("--stop-pct", type=float, default=0.5,
                   help="Index stop distance %% from entry for first-touch (default 0.5)")
    p.add_argument("--rr", type=float, default=1.5, help="Target = rr * stop (default 1.5)")
    args = p.parse_args()
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
