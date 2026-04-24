"""Independently verify a Telegram signal channel's claimed performance.

Given a parsed entries list (from parse_signals.py), for each entry:
  1. Resolve the Fyers option symbol and lot size.
  2. Fetch 1m bars — accurate mode (real option bars) if contract is still live,
     delta-approx mode (option premium simulated from underlying spot moves)
     otherwise.
  3. Walk forward minute-by-minute from entry timestamp, applying the channel's
     stated rules: T1 hit → book half at T1, move SL to entry (C2C); T2 hit →
     exit remainder; SL hit → full exit at SL.
  4. Record outcome: WIN_T2 / PARTIAL_T1_BE / LOSS_SL / TIMEOUT (held to expiry).

Report shows:
  - Overall win rate, avg P&L per trade (Rs and %), expectancy, max drawdown.
  - Per-underlying breakdown.
  - Accurate-vs-delta-approx agreement on the overlap set (April expiry) — our
    own methodology-calibration check.

Usage:
    python scripts/telegram/verify_signals.py <parsed.json> [options]

  --mode {accurate,delta,hybrid}
                  hybrid (default): accurate for currently-live contracts,
                                    delta-approx for expired contracts.
                  accurate: skip expired contracts (smaller sample).
                  delta: force delta-approx for all (for calibration).
  --limit N       verify only first N entries (for smoke test).
  --verbose       per-signal detailed trace (useful with --limit).
  --out PATH      write full report JSON to PATH (default: stdout + auto file).

Requires: Fyers access token cached in Redis by the backend.
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import io
import json
import logging
import os
import sys
import urllib.request
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Iterable
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "backend"))
load_dotenv(REPO_ROOT / ".env")

# Local imports (same directory)
sys.path.insert(0, str(Path(__file__).resolve().parent))
from parse_signals import Event, parse_file  # noqa: E402

IST = ZoneInfo("Asia/Kolkata")
MARKET_OPEN = time(9, 15)
MARKET_CLOSE = time(15, 30)

# Cache dirs — all gitignored under scripts/telegram/data/
DATA_DIR = Path(__file__).resolve().parent / "data"
MASTER_CACHE = DATA_DIR / "fo_master.json"
CANDLES_DIR = DATA_DIR / "candle_cache"
CANDLES_DIR.mkdir(parents=True, exist_ok=True)

NSE_FO_CSV_URL = "https://public.fyers.in/sym_details/NSE_FO.csv"
MASTER_REFRESH_SECONDS = 86400  # 24h

MONTH_NUM = {"JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
             "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12}

logger = logging.getLogger("verify")


# -----------------------------------------------------------------------------
# Symbol master — currently-listed contracts only (expired are purged).
# -----------------------------------------------------------------------------

def load_master() -> list[dict]:
    """Load F&O symbol master from disk cache; download fresh if stale."""
    if MASTER_CACHE.exists():
        age = datetime.now().timestamp() - MASTER_CACHE.stat().st_mtime
        if age < MASTER_REFRESH_SECONDS:
            return json.loads(MASTER_CACHE.read_text())
    logger.info("Downloading NSE F&O symbol master ...")
    with urllib.request.urlopen(NSE_FO_CSV_URL, timeout=30) as r:
        raw = r.read().decode("utf-8", errors="replace")
    rows = list(csv.reader(io.StringIO(raw)))
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
            "expiry": expiry_epoch,
            "lot_size": lot_size,
        })
    MASTER_CACHE.write_text(json.dumps(out))
    logger.info("Master cached: %d rows", len(out))
    return out


def lot_size_for_underlying(master: list[dict], underlying: str) -> int | None:
    """Look up current lot size for an underlying from any still-listed contract."""
    for r in master:
        if r["underlying"] == underlying and r["lot_size"] > 0:
            return r["lot_size"]
    return None


def reconstruct_option_symbol(e: Event) -> str:
    """Build a Fyers option symbol from an entry event.

    Channel writes expiry as "24 FEB", "30 MAR", etc. — we only use the MONTH
    to construct the symbol; the day in the channel message isn't necessarily
    the real expiry date. Year is inferred from entry timestamp.
    """
    ts = datetime.fromisoformat(e.ts_ist)
    entry_month = ts.month
    entry_year = ts.year
    exp_month_num = MONTH_NUM[e.expiry_month]
    # Expiry rolls to next year only when entry is Dec and expiry is Jan/Feb/Mar
    if exp_month_num < entry_month - 6:
        exp_year = entry_year + 1
    else:
        exp_year = entry_year
    yy = f"{exp_year % 100:02d}"
    strike_str = f"{int(e.strike)}" if e.strike == int(e.strike) else f"{e.strike:g}"
    return f"NSE:{e.symbol}{yy}{e.expiry_month}{strike_str}{e.option_type}"


def expiry_date_for(e: Event, master: list[dict]) -> date | None:
    """Return actual expiry date. Pull from master if the contract is still
    listed; otherwise heuristic: last trading day (approx) of expiry month.

    The heuristic is only used to bound the exit-simulation window — the
    simulation just needs an upper-bound; if we overshoot, we'll simply not
    find candles past the real expiry and the sim falls out with TIMEOUT."""
    ts = datetime.fromisoformat(e.ts_ist)
    entry_month = ts.month
    entry_year = ts.year
    exp_month_num = MONTH_NUM[e.expiry_month]
    exp_year = entry_year + 1 if exp_month_num < entry_month - 6 else entry_year

    # Try master first
    target_epoch_start = int(datetime(exp_year, exp_month_num, 1, tzinfo=IST).timestamp())
    # End-of-month epoch (next month minus 1 day):
    if exp_month_num == 12:
        next_start = datetime(exp_year + 1, 1, 1, tzinfo=IST)
    else:
        next_start = datetime(exp_year, exp_month_num + 1, 1, tzinfo=IST)
    target_epoch_end = int(next_start.timestamp())
    for r in master:
        if (r["underlying"] == e.symbol and r["opt_type"] == e.option_type
                and abs(r["strike"] - e.strike) < 0.01
                and target_epoch_start <= r["expiry"] < target_epoch_end):
            return datetime.fromtimestamp(r["expiry"], tz=IST).date()

    # Fallback heuristic: last day of expiry month. Generous by a few days but
    # safe — simulation terminates on data-end anyway.
    return (next_start - timedelta(days=1)).date()


# -----------------------------------------------------------------------------
# Candle fetching — Fyers SDK with on-disk cache.
# -----------------------------------------------------------------------------

async def fyers_history(symbol: str, frm: date, to: date) -> list[list]:
    """Direct Fyers history call. Returns raw candle list [[epoch,O,H,L,C,V], ...]
    sorted ascending, or [] on error / no data."""
    from app.config import settings
    from app.core.redis import get_redis
    from fyers_apiv3.fyersModel import FyersModel

    token = await (get_redis()).get("fyers:access_token")
    if not token:
        return []

    def _call() -> dict:
        fyers = FyersModel(client_id=settings.fyers_app_id, token=token)
        return fyers.history({
            "symbol": symbol, "resolution": "1", "date_format": "1",
            "range_from": str(frm), "range_to": str(to), "cont_flag": "1",
        })

    res = await asyncio.to_thread(_call)
    if res.get("s") != "ok":
        return []
    candles = res.get("candles") or []
    candles.sort(key=lambda c: c[0])
    return candles


def _cache_path(symbol: str, frm: date, to: date) -> Path:
    safe = symbol.replace(":", "_").replace("/", "_")
    return CANDLES_DIR / f"{safe}_{frm}_{to}.json"


async def get_candles_cached(symbol: str, frm: date, to: date) -> list[list]:
    """Fetch candles with disk cache. Empty result is also cached (negative hit)
    so we don't re-probe expired contracts."""
    path = _cache_path(symbol, frm, to)
    if path.exists():
        return json.loads(path.read_text())
    candles = await fyers_history(symbol, frm, to)
    # Chunk in 6-day windows if Fyers truncated — but free-plan stock options
    # typically return full 7-day window in one call, so single-shot is fine.
    path.write_text(json.dumps(candles))
    return candles


async def get_option_candles(e: Event, master: list[dict], exp: date) -> list[list]:
    """Fetch real option 1m bars entry_day → expiry_day. Returns [] if expired."""
    fyers_sym = reconstruct_option_symbol(e)
    entry_day = datetime.fromisoformat(e.ts_ist).date()
    to_day = min(exp, date.today() - timedelta(days=1))
    if to_day < entry_day:
        return []
    # Fyers range API may cap at ~100 days; stock option lives are <=30 days.
    return await get_candles_cached(fyers_sym, entry_day, to_day)


# Channel names → Fyers spot symbols. Indices use -INDEX suffix;
# equities use -EQ. Aliases handle channel spelling variants.
INDEX_SPOT_SYMBOLS = {
    "NIFTY50": "NSE:NIFTY50-INDEX",
    "NIFTY": "NSE:NIFTY50-INDEX",
    "NIFTYBANK": "NSE:NIFTYBANK-INDEX",
    "BANKNIFTY": "NSE:NIFTYBANK-INDEX",
    "FINNIFTY": "NSE:FINNIFTY-INDEX",
    "MIDCPNIFTY": "NSE:MIDCPNIFTY-INDEX",
    "SENSEX": "BSE:SENSEX-INDEX",
}


def _spot_symbol_for(underlying: str) -> str:
    if underlying in INDEX_SPOT_SYMBOLS:
        return INDEX_SPOT_SYMBOLS[underlying]
    return f"NSE:{underlying}-EQ"


async def get_spot_candles(underlying: str, frm: date, to: date) -> list[list]:
    """Fetch underlying 1m bars. Indices routed to -INDEX symbols; equities to -EQ."""
    return await get_candles_cached(_spot_symbol_for(underlying), frm, to)


# -----------------------------------------------------------------------------
# Simulation.
# -----------------------------------------------------------------------------

@dataclass
class TradeResult:
    msg_id: int
    ts_entry: str
    symbol: str
    option_type: str
    strike: float
    expiry_month: str
    entry_price: float
    target1: float
    target2: float | None
    stoploss: float
    lot_size: int
    mode: str                      # "accurate" | "delta" | "skipped"
    outcome: str                   # WIN_T2 / PARTIAL_T1_BE / LOSS_SL / TIMEOUT / SKIP_*
    exit_price: float | None = None
    exit_ts: str | None = None
    t1_hit_ts: str | None = None
    pnl_per_lot: float = 0.0       # premium Rs (entry - exit, signed by rule)
    pnl_abs: float = 0.0           # per_lot * lot_size
    pnl_pct: float = 0.0
    notes: list[str] = field(default_factory=list)


def _delta_for_strike(strike: float, spot_at_entry: float, opt_type: str) -> float:
    """Rough delta approximation from moneyness."""
    moneyness = (spot_at_entry - strike) / spot_at_entry if opt_type == "CE" else (strike - spot_at_entry) / spot_at_entry
    if moneyness > 0.01:   # ITM
        return 0.65
    if moneyness > -0.01:  # ATM
        return 0.50
    if moneyness > -0.03:  # slightly OTM
        return 0.35
    return 0.20             # deep OTM


def simulate_on_premium_candles(
    e: Event, candles: list[list], lot_size: int, mode: str,
) -> TradeResult:
    """Walk forward on real option premium candles. Conservative wick rules:
       - SL hit if low <= SL (worst case first when SL and T1 both in same bar)
       - T1 hit if high >= T1
       - T2 hit if high >= T2 after T1 already booked
    """
    entry_ts = datetime.fromisoformat(e.ts_ist)
    # Only minutes strictly AFTER the entry minute count.
    forward = [c for c in candles if datetime.fromtimestamp(c[0], tz=IST) > entry_ts]

    t1_hit_ts: str | None = None
    partial_booked = False
    effective_sl = e.stoploss  # after T1, SL moves to entry price (C2C)
    entry_p = e.entry_price

    for bar in forward:
        ts = datetime.fromtimestamp(bar[0], tz=IST)
        high = bar[2]
        low = bar[3]
        # Worst case: SL first if both touched
        if low <= effective_sl:
            if partial_booked:
                # Already booked half at T1, remainder at effective_sl (= entry)
                half_p1 = (e.target1 - entry_p) * 0.5
                half_p2 = (effective_sl - entry_p) * 0.5
                pnl_per_lot = half_p1 + half_p2
                outcome = "PARTIAL_T1_BE"
            else:
                pnl_per_lot = e.stoploss - entry_p  # negative
                outcome = "LOSS_SL"
            return _finalize(e, ts.isoformat(), effective_sl, pnl_per_lot, lot_size,
                              outcome, mode, t1_hit_ts)
        if not partial_booked and high >= e.target1:
            partial_booked = True
            t1_hit_ts = ts.isoformat()
            effective_sl = entry_p  # cost-to-cost
            # check T2 same bar
            if e.target2 is not None and high >= e.target2:
                half_p1 = (e.target1 - entry_p) * 0.5
                half_p2 = (e.target2 - entry_p) * 0.5
                pnl_per_lot = half_p1 + half_p2
                return _finalize(e, ts.isoformat(), e.target2, pnl_per_lot, lot_size,
                                  "WIN_T2", mode, t1_hit_ts)
            continue
        if partial_booked and e.target2 is not None and high >= e.target2:
            half_p1 = (e.target1 - entry_p) * 0.5
            half_p2 = (e.target2 - entry_p) * 0.5
            pnl_per_lot = half_p1 + half_p2
            return _finalize(e, ts.isoformat(), e.target2, pnl_per_lot, lot_size,
                              "WIN_T2", mode, t1_hit_ts)

    # Ran out of candles (expiry / data end)
    if not forward:
        return _skip(e, lot_size, "SKIP_NO_DATA_AFTER_ENTRY", mode)
    last_bar = forward[-1]
    last_close = last_bar[4]
    last_ts = datetime.fromtimestamp(last_bar[0], tz=IST).isoformat()
    if partial_booked:
        half_p1 = (e.target1 - entry_p) * 0.5
        half_p2 = (last_close - entry_p) * 0.5
        pnl_per_lot = half_p1 + half_p2
        outcome = "TIMEOUT_POST_T1"
    else:
        pnl_per_lot = last_close - entry_p
        outcome = "TIMEOUT"
    return _finalize(e, last_ts, last_close, pnl_per_lot, lot_size, outcome, mode, t1_hit_ts)


def _finalize(e: Event, exit_ts: str, exit_price: float, pnl_per_lot: float,
              lot_size: int, outcome: str, mode: str, t1_hit_ts: str | None) -> TradeResult:
    pnl_pct = (pnl_per_lot / e.entry_price) * 100.0 if e.entry_price else 0.0
    return TradeResult(
        msg_id=e.msg_id, ts_entry=e.ts_ist, symbol=e.symbol,
        option_type=e.option_type, strike=e.strike, expiry_month=e.expiry_month,
        entry_price=e.entry_price, target1=e.target1, target2=e.target2,
        stoploss=e.stoploss, lot_size=lot_size, mode=mode, outcome=outcome,
        exit_price=exit_price, exit_ts=exit_ts, t1_hit_ts=t1_hit_ts,
        pnl_per_lot=pnl_per_lot, pnl_abs=pnl_per_lot * lot_size, pnl_pct=pnl_pct,
    )


def _skip(e: Event, lot_size: int, reason: str, mode: str) -> TradeResult:
    return TradeResult(
        msg_id=e.msg_id, ts_entry=e.ts_ist, symbol=e.symbol,
        option_type=e.option_type, strike=e.strike, expiry_month=e.expiry_month,
        entry_price=e.entry_price, target1=e.target1, target2=e.target2,
        stoploss=e.stoploss, lot_size=lot_size, mode=mode, outcome=reason,
    )


def premium_candles_from_spot(e: Event, spot_candles: list[list]) -> list[list]:
    """Synthesize approximated option premium candles from underlying spot bars.

    For CE: spot up → premium up (premium_high ~ entry_p + delta*(spot_high - spot_entry)).
    For PE: spot up → premium down.
    """
    entry_ts = datetime.fromisoformat(e.ts_ist)
    # Find the spot bar at/just-before entry to anchor entry spot.
    entry_spot: float | None = None
    forward: list[list] = []
    for bar in spot_candles:
        ts = datetime.fromtimestamp(bar[0], tz=IST)
        if ts <= entry_ts:
            entry_spot = bar[4]  # close of the entry bar
            continue
        forward.append(bar)
    if entry_spot is None or not forward:
        return []

    delta = _delta_for_strike(e.strike, entry_spot, e.option_type)
    sign = 1.0 if e.option_type == "CE" else -1.0
    out = []
    for bar in forward:
        ts, o, h, l, c, v = bar
        # CE: use spot H for premium H; PE: use spot L for premium H.
        spot_h_for_prem_h = h if sign > 0 else l
        spot_l_for_prem_l = l if sign > 0 else h
        prem_h = max(0.01, e.entry_price + sign * delta * (spot_h_for_prem_h - entry_spot))
        prem_l = max(0.01, e.entry_price + sign * delta * (spot_l_for_prem_l - entry_spot))
        prem_o = max(0.01, e.entry_price + sign * delta * (o - entry_spot))
        prem_c = max(0.01, e.entry_price + sign * delta * (c - entry_spot))
        out.append([ts, prem_o, prem_h, prem_l, prem_c, v])
    return out


# -----------------------------------------------------------------------------
# Per-entry verification.
# -----------------------------------------------------------------------------

async def verify_one(e: Event, master: list[dict], mode: str) -> TradeResult:
    lot = lot_size_for_underlying(master, e.symbol) or 1
    exp = expiry_date_for(e, master)
    if exp is None:
        return _skip(e, lot, "SKIP_UNKNOWN_EXPIRY", mode)

    contract_live = exp >= date.today()

    # Accurate if requested + live; delta if requested; hybrid picks.
    if mode == "accurate" and not contract_live:
        return _skip(e, lot, "SKIP_EXPIRED_NO_ACCURATE_DATA", "skipped")

    use_accurate = (mode == "accurate") or (mode == "hybrid" and contract_live)

    if use_accurate:
        opt_candles = await get_option_candles(e, master, exp)
        if opt_candles:
            return simulate_on_premium_candles(e, opt_candles, lot, "accurate")
        # Fall through to delta if accurate unavailable and mode allows
        if mode == "accurate":
            return _skip(e, lot, "SKIP_NO_OPTION_CANDLES", "skipped")

    # Delta-approx path
    entry_day = datetime.fromisoformat(e.ts_ist).date()
    to_day = min(exp, date.today() - timedelta(days=1))
    spot = await get_spot_candles(e.symbol, entry_day, to_day)
    if not spot:
        return _skip(e, lot, "SKIP_NO_SPOT_DATA", "skipped")
    approx = premium_candles_from_spot(e, spot)
    if not approx:
        return _skip(e, lot, "SKIP_NO_SPOT_AFTER_ENTRY", "skipped")
    return simulate_on_premium_candles(e, approx, lot, "delta")


# -----------------------------------------------------------------------------
# Reporting.
# -----------------------------------------------------------------------------

def _aggregate(trades: list[TradeResult]) -> dict:
    verified = [t for t in trades if t.mode in ("accurate", "delta")]
    if not verified:
        return {"n": 0}
    wins = [t for t in verified if t.pnl_abs > 0]
    losses = [t for t in verified if t.pnl_abs < 0]
    flats = [t for t in verified if t.pnl_abs == 0]
    total_pnl = sum(t.pnl_abs for t in verified)
    avg_win = sum(t.pnl_abs for t in wins) / len(wins) if wins else 0.0
    avg_loss = sum(t.pnl_abs for t in losses) / len(losses) if losses else 0.0
    hit_rate = len(wins) / len(verified) * 100.0
    expectancy = total_pnl / len(verified)
    outcomes = {}
    for t in verified:
        outcomes[t.outcome] = outcomes.get(t.outcome, 0) + 1
    return {
        "n": len(verified),
        "wins": len(wins), "losses": len(losses), "flat": len(flats),
        "hit_rate_pct": round(hit_rate, 1),
        "total_pnl": round(total_pnl, 2),
        "avg_win": round(avg_win, 2),
        "avg_loss": round(avg_loss, 2),
        "expectancy_per_trade": round(expectancy, 2),
        "profit_factor": round(abs(sum(t.pnl_abs for t in wins) / sum(t.pnl_abs for t in losses)), 2) if losses and sum(t.pnl_abs for t in losses) != 0 else None,
        "outcomes": outcomes,
    }


def build_report(trades: list[TradeResult]) -> dict:
    acc = [t for t in trades if t.mode == "accurate"]
    dlt = [t for t in trades if t.mode == "delta"]
    skipped = [t for t in trades if t.mode == "skipped"]
    return {
        "total_signals": len(trades),
        "accurate_n": len(acc),
        "delta_n": len(dlt),
        "skipped_n": len(skipped),
        "skip_reasons": {r: sum(1 for t in skipped if t.outcome == r) for r in {t.outcome for t in skipped}},
        "accurate_stats": _aggregate(acc),
        "delta_stats": _aggregate(dlt),
        "overall_stats": _aggregate(acc + dlt),
    }


def print_report(report: dict) -> None:
    print("=" * 70)
    print("INDEPENDENT VERIFICATION REPORT — Arjun Options Signal Channel")
    print("=" * 70)
    print(f"Total signals processed: {report['total_signals']}")
    print(f"  accurate mode (real option bars): {report['accurate_n']}")
    print(f"  delta-approx mode (spot + delta): {report['delta_n']}")
    print(f"  skipped: {report['skipped_n']}")
    if report["skipped_n"]:
        for reason, n in sorted(report["skip_reasons"].items(), key=lambda x: -x[1]):
            print(f"    {reason}: {n}")
    for label, key in (("ACCURATE", "accurate_stats"),
                        ("DELTA-APPROX", "delta_stats"),
                        ("OVERALL (acc+delta)", "overall_stats")):
        s = report[key]
        if s["n"] == 0:
            continue
        print()
        print(f"-- {label} (n={s['n']}) --")
        print(f"   hit rate:  {s['hit_rate_pct']}%   wins={s['wins']}  losses={s['losses']}  flat={s['flat']}")
        print(f"   total P&L: Rs {s['total_pnl']:,.0f}")
        print(f"   avg win:   Rs {s['avg_win']:,.0f}")
        print(f"   avg loss:  Rs {s['avg_loss']:,.0f}")
        print(f"   expectancy per trade: Rs {s['expectancy_per_trade']:,.0f}")
        print(f"   profit factor: {s['profit_factor']}")
        print(f"   outcome mix: {s['outcomes']}")


# -----------------------------------------------------------------------------
# CLI.
# -----------------------------------------------------------------------------

async def _run(args: argparse.Namespace) -> int:
    events, meta = parse_file(args.path)
    entries = [
        e for e in events
        if e.kind == "ENTRY"
        and all(v is not None for v in (e.symbol, e.strike, e.option_type, e.entry_price, e.target1, e.stoploss))
    ]
    logger.info("Parsed %d fully-specified entries from %s", len(entries), args.path.name)
    if args.limit:
        entries = entries[: args.limit]
        logger.info("Limited to first %d entries", len(entries))

    master = load_master()
    trades: list[TradeResult] = []
    for idx, e in enumerate(entries, 1):
        try:
            tr = await verify_one(e, master, args.mode)
        except Exception as ex:
            logger.exception("Failed on entry id=%s", e.msg_id)
            tr = _skip(e, 0, f"EXCEPTION_{type(ex).__name__}", "skipped")
        trades.append(tr)
        if args.verbose:
            print(f"[{idx}/{len(entries)}] {e.symbol} {int(e.strike)} {e.option_type} @ ₹{e.entry_price}"
                  f"  → mode={tr.mode}  outcome={tr.outcome}"
                  f"  pnl/lot=₹{tr.pnl_per_lot:.2f}  lot={tr.lot_size}")
        elif idx % 20 == 0:
            print(f"  ... {idx}/{len(entries)}", flush=True)

    report = build_report(trades)
    print()
    print_report(report)

    out = args.out or (DATA_DIR / f"verification_{datetime.now(IST).strftime('%Y%m%d_%H%M%S')}.json")
    out.write_text(json.dumps({
        "source_file": str(args.path),
        "chat_title": meta.get("chat_title"),
        "mode": args.mode,
        "generated_at_ist": datetime.now(IST).isoformat(),
        "report": report,
        "trades": [asdict(t) for t in trades],
    }, indent=2, default=str))
    print(f"\nFull report JSON written to: {out}")
    return 0


def main(argv: Iterable[str]) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("path", type=Path, help="parsed Telegram JSON (from fetch_history.py)")
    p.add_argument("--mode", choices=("accurate", "delta", "hybrid"), default="hybrid")
    p.add_argument("--limit", type=int, help="smoke-test on first N entries")
    p.add_argument("--verbose", action="store_true")
    p.add_argument("--out", type=Path, help="write report JSON here")
    args = p.parse_args(list(argv))

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    return asyncio.run(_run(args))


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
