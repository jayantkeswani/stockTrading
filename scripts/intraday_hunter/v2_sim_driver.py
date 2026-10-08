#!/usr/bin/env python
"""IH v2 simulator E2E driver — scripts a realistic index tape + option premiums into the
Market Simulator (MARKET_MODE=simulated) so the full v2 path can be exercised on wall-clock time
(09:10 → 11:35 IST): ATM±2 capture, 1-min OI, minute log, Call 2 from 09:16, signals → YOLO +
shadow, basket-level exit, grading.

The backend runs on wall-clock time (now_ist), so run this live during 09:05–11:40 IST.

Index tape (per index, relative to its previous close from the staged prev-day candles):
  09:15 opens `--gap-pct` (default −0.20%), then drifts in `--direction` (down = PE ride) through
  the PDL/PDH stop pool by ~09:18 and keeps riding until `--trend-end` (default 09:45), then
  chops sideways. Option premiums for every subscribed contract (the v2 ATM±2 capture + any
  signal contract) are priced from their index spot: intrinsic + a time value that decays with
  moneyness, so a ride in the basket's direction lifts the basket ~1:1 with delta.

    python scripts/intraday_hunter/v2_sim_driver.py --prev NIFTY=22231.8,BANKNIFTY=54515.05,SENSEX=71593.24 \
        --direction down --until 11:40

Needs: simulator on :8787, local Redis on :6380 (reads ih_v2:capture:{date}). v2 legs are\nATM / OTM-1, i.e. inside the ATM±2 capture, so every traded contract gets a model premium.
"""
from __future__ import annotations

import argparse
import json
import math
import random
import time
from datetime import datetime, timedelta, timezone

import httpx

IST = timezone(timedelta(hours=5, minutes=30))
SIM = "http://localhost:8787"
FYERS_INDEX = {"NIFTY": "NSE:NIFTY50-INDEX", "BANKNIFTY": "NSE:NIFTYBANK-INDEX",
               "SENSEX": "BSE:SENSEX-INDEX"}
VIX = "NSE:INDIAVIX-INDEX"


def index_path(prev: float, now: datetime, gap_pct: float, direction: int, trend_end: str,
               trend_pct: float) -> float:
    """Scripted index level at wall time `now` (IST)."""
    t = now.hour * 60 + now.minute + now.second / 60
    open_t = 9 * 60 + 15
    te_h, te_m = map(int, trend_end.split(":"))
    end_t = te_h * 60 + te_m
    open_px = prev * (1 + gap_pct / 100)
    if t < open_t:
        return prev
    if t <= end_t:
        frac = (t - open_t) / max(end_t - open_t, 1)
        drift = direction * trend_pct / 100 * frac
    else:
        drift = direction * trend_pct / 100 + 0.0004 * math.sin((t - end_t) / 3.0)
    noise = random.gauss(0, 0.00012)
    return round(open_px * (1 + drift + noise), 2)


def premium(spot: float, strike: float, opt: str) -> float:
    """Toy option price: intrinsic + ATM time value (~0.55% of spot) decaying with moneyness."""
    intrinsic = max(spot - strike, 0) if opt == "CE" else max(strike - spot, 0)
    tv = spot * 0.0055 * math.exp(-((strike - spot) / (spot * 0.01)) ** 2)
    return round(max(intrinsic + tv, 0.5), 2)


def option_contracts(r) -> dict[str, tuple[str, float, str]]:
    """{fyers_symbol: (index, strike, CE|PE)} for today's v2 ATM±2 capture (strike/type come from
    the capture record — Fyers weekly symbols can't be parsed reliably)."""
    today = datetime.now(IST).date().isoformat()
    raw = r.get(f"ih_v2:capture:{today}")
    out: dict[str, tuple[str, float, str]] = {}
    if raw:
        for idx, rows in (json.loads(raw).get("contracts") or {}).items():
            for row in rows:
                out[row["symbol"]] = (idx, float(row["strike"]), row["type"])
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--prev", required=True, help="NIFTY=..,BANKNIFTY=..,SENSEX=.. previous closes")
    ap.add_argument("--direction", choices=["up", "down"], default="down")
    ap.add_argument("--gap-pct", type=float, default=-0.20)
    ap.add_argument("--trend-pct", type=float, default=0.55)
    ap.add_argument("--trend-end", default="09:45")
    ap.add_argument("--until", default="11:40")
    ap.add_argument("--every", type=float, default=2.0)
    a = ap.parse_args()
    import redis  # type: ignore

    prev = {k: float(v) for k, v in (kv.split("=") for kv in a.prev.split(","))}
    direction = -1 if a.direction == "down" else 1
    r = redis.Redis(host="localhost", port=6380, decode_responses=True)
    http = httpx.Client(timeout=10)

    # Session with index + VIX generators in CUSTOM mode (move only by injection).
    cfg = {FYERS_INDEX[i]: {"base_price": p, "prev_close": p, "mode": "custom"} for i, p in prev.items()}
    cfg[VIX] = {"base_price": 15.2, "prev_close": 15.24, "mode": "custom"}
    state = http.get(f"{SIM}/sim/state").json().get("data", {})
    if not state.get("running"):
        print(http.post(f"{SIM}/sim/session", json={"speed": 1.0, "symbols": cfg}).json())
    else:
        print(http.post(f"{SIM}/sim/subscribe", json={"symbols": cfg}).json())

    customised: set[str] = set()
    uh, um = map(int, a.until.split(":"))
    while True:
        now = datetime.now(IST)
        if now.hour * 60 + now.minute >= uh * 60 + um:
            break
        spots = {i: index_path(p, now, a.gap_pct, direction, a.trend_end, a.trend_pct)
                 for i, p in prev.items()}
        ticks = [{"symbol": FYERS_INDEX[i], "ltp": s, "volume_delta": 0} for i, s in spots.items()]
        ticks.append({"symbol": VIX, "ltp": round(15.2 + random.gauss(0, 0.02), 2)})
        opts = option_contracts(r)
        new = [s for s in opts if s not in customised]
        if new:  # switch newly seen contracts to custom mode at their model price
            sub = {}
            for s in new:
                idx, k, ot = opts[s]
                px = premium(spots[idx], k, ot)
                sub[s] = {"base_price": px, "prev_close": px, "mode": "custom",
                          "base_daily_volume": 2_000_000}
            http.post(f"{SIM}/sim/subscribe", json={"symbols": sub})
            customised.update(new)
            print(f"{now:%H:%M:%S} customised {len(new)} option contracts (total {len(customised)})")
        for s, (idx, k, ot) in opts.items():
            ticks.append({"symbol": s, "ltp": premium(spots[idx], k, ot),
                          "volume_delta": random.randint(500, 3000)})
        http.post(f"{SIM}/sim/inject/batch", json={"ticks": ticks})
        if now.second < a.every:
            print(f"{now:%H:%M:%S} " + " ".join(f"{i}={s}" for i, s in spots.items())
                  + f" opts={len(opts)}", flush=True)
        time.sleep(a.every)


if __name__ == "__main__":
    main()
