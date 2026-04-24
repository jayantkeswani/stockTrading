"""One-shot probe: does the Fyers free plan return 1m history for stock options?

Downloads the NSE F&O symbol master CSV, resolves a handful of known recent
contracts from Arjun's signal log, and calls `fyersModel.history()` for a small
date window after each signal's entry timestamp. Prints per-symbol results so
we can decide between accurate-mode (real option candles) vs fast-mode
(delta-approximation from underlying spot) for the full verifier.

Requires: Fyers access token already cached in Redis by the backend.
"""
from __future__ import annotations

import asyncio
import csv
import io
import os
import sys
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "backend"))
load_dotenv(REPO_ROOT / ".env")

NSE_FO_CSV_URL = "https://public.fyers.in/sym_details/NSE_FO.csv"

# (underlying, option_type, description)
# We pick the nearest-expiry ATM-ish contract for each from the live master,
# then probe the last ~10 trading days. This sidesteps the problem of expired
# contracts being purged from the master.
PROBE_UNDERLYINGS = [
    ("MCX",        "CE", "stock opt (high-liquidity)"),
    ("PERSISTENT", "CE", "stock opt (mid-liquidity)"),
    ("TCS",        "CE", "stock opt (very high liquidity)"),
    ("NIFTY",      "PE", "index opt (positive control)"),
]


def _download_nse_fo_master() -> list[dict]:
    """Download and parse the NSE F&O symbol master CSV. No Redis dependency."""
    print(f"# Downloading {NSE_FO_CSV_URL} ...", flush=True)
    with urllib.request.urlopen(NSE_FO_CSV_URL, timeout=30) as resp:
        raw = resp.read().decode("utf-8", errors="replace")
    rows = list(csv.reader(io.StringIO(raw)))
    print(f"# Parsed {len(rows)} F&O rows")
    # CSV columns per symbol_master.py docstring:
    #  0: Fytoken, 1: Display name, 2: Instrument type, 3: Lot size,
    #  8: Expiry epoch, 9: Fyers symbol, 13: Underlying short name,
    # 15: Strike price, 16: Option type (CE/PE/XX)
    out = []
    for r in rows:
        if len(r) < 17:
            continue
        try:
            strike = float(r[15]) if r[15] else -1.0
            expiry_epoch = int(r[8]) if r[8] else 0
            lot_size = int(r[3]) if r[3] else 0
        except ValueError:
            continue
        out.append({
            "fyers_symbol": r[9],
            "underlying": (r[13] or "").upper(),
            "strike": strike,
            "opt_type": r[16],
            "expiry": date.fromtimestamp(expiry_epoch) if expiry_epoch else None,
            "lot_size": lot_size,
            "display": r[1],
        })
    return out


def _pick_active_contract(master: list[dict], underlying: str, opt_type: str) -> dict | None:
    """Pick the median-strike contract for the nearest upcoming expiry.

    Median-strike is a cheap proxy for "liquid / near-the-money"; good enough
    for a history-availability probe.
    """
    today = date.today()
    future = [
        r for r in master
        if r["underlying"] == underlying
        and r["opt_type"] == opt_type
        and r["expiry"] is not None
        and r["expiry"] >= today
    ]
    if not future:
        return None
    # Nearest upcoming expiry
    nearest_expiry = min(r["expiry"] for r in future)
    same_expiry = [r for r in future if r["expiry"] == nearest_expiry]
    # Median strike of this expiry's chain
    same_expiry.sort(key=lambda r: r["strike"])
    return same_expiry[len(same_expiry) // 2]


async def _probe_one_range(token: str, app_id: str, fyers_symbol: str, frm: date, to: date) -> dict:
    """Call fyersModel.history() for a date range of 1m bars."""
    from fyers_apiv3.fyersModel import FyersModel

    def _call() -> dict:
        fyers = FyersModel(client_id=app_id, token=token)
        return fyers.history({
            "symbol": fyers_symbol,
            "resolution": "1",
            "date_format": "1",
            "range_from": str(frm),
            "range_to": str(to),
            "cont_flag": "1",
        })

    return await asyncio.to_thread(_call)


async def main() -> int:
    from app.core.redis import get_redis
    r = get_redis()
    token = await r.get("fyers:access_token")
    if not token:
        print("ERROR: no fyers:access_token in Redis. Start the backend so auto-login populates it.", file=sys.stderr)
        return 2
    app_id = os.getenv("FYERS_APP_ID") or ""
    if not app_id:
        # Backend stores it in settings; fall back via Pydantic settings loader.
        from app.config import settings  # noqa
        app_id = settings.fyers_app_id
    if not app_id:
        print("ERROR: FYERS_APP_ID missing", file=sys.stderr)
        return 2

    master = _download_nse_fo_master()

    # Probe window: the last ~10 calendar days (7 trading days).
    probe_to = date.today() - timedelta(days=1)
    probe_from = probe_to - timedelta(days=10)

    print()
    print(f"# Probe window: {probe_from} → {probe_to}")
    print(f"{'sym':<12} {'resolved fyers symbol':<32}  {'expiry':<12}  {'result':<10}  {'candles':>7}  {'first close'}  ({'desc'})")
    print("-" * 130)

    any_stock_ok = False
    for underlying, opt_type, desc in PROBE_UNDERLYINGS:
        hit = _pick_active_contract(master, underlying, opt_type)
        if not hit:
            print(f"{underlying:<12} <no active contract found in master>  ({desc})")
            continue
        resolved = hit["fyers_symbol"]
        try:
            result = await _probe_one_range(token, app_id, resolved, probe_from, probe_to)
        except Exception as e:
            print(f"{underlying:<12} {resolved:<32}  {hit['expiry']!s:<12}  EXC         n/a     {type(e).__name__}: {e}")
            continue
        status = result.get("s", "err")
        candles = result.get("candles") or []
        first_close = f"₹{candles[0][4]}" if candles else "—"
        print(f"{underlying:<12} {resolved:<32}  {hit['expiry']!s:<12}  {status:<10}  {len(candles):>7}  {first_close:>10}  ({desc})")
        if status == "ok" and candles and underlying not in ("NIFTY", "BANKNIFTY", "FINNIFTY", "SENSEX", "MIDCPNIFTY"):
            any_stock_ok = True

    print()
    if any_stock_ok:
        print("VERDICT: Free plan DOES return 1m history for at least one stock option.")
        print("→ accurate-mode verifier is viable.")
    else:
        print("VERDICT: Free plan does NOT return 1m history for stock options.")
        print("→ must fall back to delta-approx from underlying spot bars.")

    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
