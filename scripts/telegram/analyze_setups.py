"""Reverse-engineer Arjun's implied trading setup from his entry timestamps.

For each parsed entry, fetch the underlying's 1m bars leading up to entry time,
compute the indicator-state at the entry minute using our existing indicator
modules, then aggregate distributional stats across CE vs PE entries. The goal
is NOT to predict their next trade — it's to answer "what setup are they
scanning for?" with evidence instead of reading their messages.

Features computed at each entry minute:
  Time:       entry_hhmm bucket
  VWAP:       above/below, % distance, pullback flag, slope over last 15m
  PrevDay:    PDH / PDL / PDC position + distance, gap % from PDC
  CPR:        above TC / inside / below BC, narrow vs wide
  Candle:     pattern at entry bar + 1 prior (bullish / bearish reversal / doji)
  Volume:     entry-bar volume ÷ today's avg 1m volume (20-bar)
  DayRange:   position in today's high-low range
  Momentum:   % move over last 15m
  Moneyness:  (spot - strike) / spot  — CE ITM when positive
  Premium:    entry premium Rs (bucket)

Usage:
    python scripts/telegram/analyze_setups.py <parsed.json>
    python scripts/telegram/analyze_setups.py <parsed.json> --limit 50
    python scripts/telegram/analyze_setups.py <parsed.json> --csv features.csv
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import json
import logging
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, time, timedelta
from pathlib import Path
from statistics import mean, median
from typing import Iterable
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "backend"))
load_dotenv(REPO_ROOT / ".env")
sys.path.insert(0, str(Path(__file__).resolve().parent))

from parse_signals import Event, parse_file  # noqa: E402
from verify_signals import (                  # noqa: E402
    _spot_symbol_for, get_candles_cached, load_master,
)

# Backend indicator imports — run under backend/.venv with PYTHONPATH set.
from app.indicators.candle_patterns import (  # noqa: E402
    Candle, is_bearish_engulfing, is_bearish_pin_bar, is_bearish_reversal,
    is_bullish_engulfing, is_bullish_pin_bar, is_bullish_reversal, is_doji,
)
from app.indicators.cpr import calculate_cpr  # noqa: E402
from app.indicators.previous_day import analyze_previous_day  # noqa: E402
from app.indicators.vwap import (  # noqa: E402
    calculate_vwap, is_pullback_to_vwap, price_distance_from_vwap,
)

IST = ZoneInfo("Asia/Kolkata")
MARKET_OPEN = time(9, 15)
MARKET_CLOSE = time(15, 30)

CONTEXT_DAYS = 5  # trading-day buffer before entry to get PDH/PDL
NEAR_THRESHOLD_PCT = 0.25  # "near VWAP/PDH" means within 0.25%

logger = logging.getLogger("analyze")


# -----------------------------------------------------------------------------
# Candle fetching with analysis-specific cache key (wider window).
# -----------------------------------------------------------------------------

async def get_ctx_candles(underlying: str, entry_day: date) -> list[list]:
    """Fetch a ~7-calendar-day window ending on entry_day for context.
    Returns raw [[epoch, O, H, L, C, V], ...] ascending."""
    frm = entry_day - timedelta(days=CONTEXT_DAYS + 2)  # extra pad for weekends
    to = entry_day
    return await get_candles_cached(_spot_symbol_for(underlying), frm, to)


def _split_by_trading_day(candles: list[list]) -> dict[date, list[list]]:
    """Group candles by trading date (IST)."""
    out: dict[date, list[list]] = defaultdict(list)
    for bar in candles:
        d = datetime.fromtimestamp(bar[0], tz=IST).date()
        out[d].append(bar)
    return out


def _to_candle(bar: list) -> Candle:
    # list format: [epoch, O, H, L, C, V]
    return Candle(open=bar[1], high=bar[2], low=bar[3], close=bar[4], volume=int(bar[5] or 0))


# -----------------------------------------------------------------------------
# Feature extraction for a single entry.
# -----------------------------------------------------------------------------

def compute_features(e: Event, all_ctx: list[list]) -> dict | None:
    entry_ts = datetime.fromisoformat(e.ts_ist)
    entry_day = entry_ts.date()
    by_day = _split_by_trading_day(all_ctx)

    # Previous trading day = the latest day in the ctx window that is strictly
    # before entry_day (handles weekends/holidays without a separate calendar).
    prev_days = sorted([d for d in by_day if d < entry_day])
    if not prev_days:
        return None
    prev_day = prev_days[-1]
    prev_bars = by_day[prev_day]
    if not prev_bars:
        return None

    prev_o = prev_bars[0][1]
    prev_h = max(b[2] for b in prev_bars)
    prev_l = min(b[3] for b in prev_bars)
    prev_c = prev_bars[-1][4]

    pdl = analyze_previous_day(prev_o, prev_h, prev_l, prev_c)
    cpr = calculate_cpr(prev_h, prev_l, prev_c)

    # Today's bars from 9:15 through the entry minute (inclusive).
    today_bars = by_day.get(entry_day, [])
    before_entry = [b for b in today_bars if datetime.fromtimestamp(b[0], tz=IST) <= entry_ts]
    if len(before_entry) < 2:
        return None

    day_open = before_entry[0][1]
    day_high_so_far = max(b[2] for b in before_entry)
    day_low_so_far = min(b[3] for b in before_entry)
    spot_entry = before_entry[-1][4]

    # VWAP from today's cumulative bars.
    highs = [b[2] for b in before_entry]
    lows = [b[3] for b in before_entry]
    closes = [b[4] for b in before_entry]
    vols = [int(b[5] or 0) for b in before_entry]
    vwap_res = calculate_vwap(highs, lows, closes, vols)
    if vwap_res is None:
        return None
    vwap_dist_pct = price_distance_from_vwap(spot_entry, vwap_res.vwap)
    above_vwap = spot_entry > vwap_res.vwap
    near_vwap = is_pullback_to_vwap(spot_entry, vwap_res.vwap, NEAR_THRESHOLD_PCT)

    # VWAP slope: vwap at entry minute vs 15 bars ago.
    if len(before_entry) >= 16:
        prior = before_entry[:-15]
        vw_prior = calculate_vwap(
            [b[2] for b in prior], [b[3] for b in prior],
            [b[4] for b in prior], [int(b[5] or 0) for b in prior],
        )
        vwap_slope_pct = (
            (vwap_res.vwap - vw_prior.vwap) / vw_prior.vwap * 100.0 if vw_prior else 0.0
        )
    else:
        vwap_slope_pct = 0.0

    # CPR position
    if spot_entry > cpr.tc:
        cpr_pos = "ABOVE_TC"
    elif spot_entry < cpr.bc:
        cpr_pos = "BELOW_BC"
    else:
        cpr_pos = "INSIDE_CPR"

    # Previous day position
    above_pdh = spot_entry > pdl.pdh
    below_pdl = spot_entry < pdl.pdl
    pdh_dist_pct = (spot_entry - pdl.pdh) / pdl.pdh * 100.0 if pdl.pdh else 0.0
    pdl_dist_pct = (spot_entry - pdl.pdl) / pdl.pdl * 100.0 if pdl.pdl else 0.0
    pdc_dist_pct = (spot_entry - pdl.pdc) / pdl.pdc * 100.0 if pdl.pdc else 0.0
    gap_pct = (day_open - pdl.pdc) / pdl.pdc * 100.0 if pdl.pdc else 0.0

    # Candle pattern at entry bar (look at last 2-3 bars).
    last2 = before_entry[-2:]
    last3 = before_entry[-3:] if len(before_entry) >= 3 else last2
    c_curr = _to_candle(before_entry[-1])
    c_prev = _to_candle(before_entry[-2])
    bull_reversal = is_bullish_reversal([_to_candle(b) for b in last3])
    bear_reversal = is_bearish_reversal([_to_candle(b) for b in last3])
    bull_engulf = is_bullish_engulfing(c_prev, c_curr)
    bear_engulf = is_bearish_engulfing(c_prev, c_curr)
    bull_pin = is_bullish_pin_bar(c_curr)
    bear_pin = is_bearish_pin_bar(c_curr)
    doji = is_doji(c_curr)

    # Volume: entry-bar volume vs avg of today's bars-so-far.
    avg_today_vol = mean(vols[:-1]) if len(vols) > 1 else 1
    vol_ratio = vols[-1] / avg_today_vol if avg_today_vol > 0 else 1.0

    # Position in today's range so far.
    day_range = day_high_so_far - day_low_so_far
    range_pos = ((spot_entry - day_low_so_far) / day_range) if day_range > 0 else 0.5

    # 15-min momentum (close now vs close 15 bars ago).
    if len(before_entry) >= 16:
        c15 = before_entry[-16][4]
        momentum_15m_pct = (spot_entry - c15) / c15 * 100.0 if c15 else 0.0
    else:
        momentum_15m_pct = 0.0

    # Move from day open.
    intraday_move_pct = (spot_entry - day_open) / day_open * 100.0 if day_open else 0.0

    # Moneyness
    moneyness_pct = (spot_entry - e.strike) / spot_entry * 100.0  # CE: >0 ITM, <0 OTM

    # Time-of-day bucket (30-min)
    mins = entry_ts.hour * 60 + entry_ts.minute
    bucket_start = (mins // 30) * 30
    tod_bucket = f"{bucket_start // 60:02d}:{bucket_start % 60:02d}"

    return {
        "msg_id": e.msg_id,
        "ts": e.ts_ist,
        "symbol": e.symbol,
        "opt_type": e.option_type,
        "strike": e.strike,
        "entry_price": e.entry_price,
        "tod_bucket": tod_bucket,
        "spot_entry": round(spot_entry, 2),
        "moneyness_pct": round(moneyness_pct, 2),
        # VWAP
        "above_vwap": above_vwap,
        "vwap_dist_pct": round(vwap_dist_pct, 3),
        "near_vwap": near_vwap,
        "vwap_slope_15m_pct": round(vwap_slope_pct, 3),
        # Prev day
        "above_pdh": above_pdh,
        "below_pdl": below_pdl,
        "pdh_dist_pct": round(pdh_dist_pct, 3),
        "pdl_dist_pct": round(pdl_dist_pct, 3),
        "pdc_dist_pct": round(pdc_dist_pct, 3),
        "prev_day_bias": pdl.bias.value if hasattr(pdl.bias, "value") else str(pdl.bias),
        "gap_pct": round(gap_pct, 3),
        # CPR
        "cpr_position": cpr_pos,
        "cpr_type": cpr.cpr_type.value if hasattr(cpr.cpr_type, "value") else str(cpr.cpr_type),
        # Candle
        "bull_reversal": bull_reversal,
        "bear_reversal": bear_reversal,
        "bull_engulf": bull_engulf,
        "bear_engulf": bear_engulf,
        "bull_pin": bull_pin,
        "bear_pin": bear_pin,
        "doji": doji,
        # Momentum / location
        "vol_ratio": round(vol_ratio, 2),
        "range_pos": round(range_pos, 3),
        "momentum_15m_pct": round(momentum_15m_pct, 3),
        "intraday_move_pct": round(intraday_move_pct, 3),
    }


# -----------------------------------------------------------------------------
# Aggregate reporting.
# -----------------------------------------------------------------------------

def _pct_true(feats: list[dict], key: str) -> float:
    if not feats:
        return 0.0
    return sum(1 for f in feats if f[key]) / len(feats) * 100.0


def _distribution(feats: list[dict], key: str) -> dict[str, int]:
    c = Counter(f[key] for f in feats)
    return dict(c.most_common())


def _stats(feats: list[dict], key: str) -> dict:
    vals = [f[key] for f in feats if f[key] is not None]
    if not vals:
        return {}
    vals_sorted = sorted(vals)
    return {
        "mean": round(mean(vals), 2),
        "median": round(median(vals), 2),
        "p10": round(vals_sorted[int(len(vals) * 0.1)], 2),
        "p90": round(vals_sorted[int(len(vals) * 0.9)], 2),
        "min": round(min(vals), 2),
        "max": round(max(vals), 2),
    }


def print_side_report(label: str, feats: list[dict]) -> None:
    n = len(feats)
    print()
    print(f"{'=' * 72}")
    print(f"{label}  (n = {n})")
    print(f"{'=' * 72}")
    if n == 0:
        return

    print("\n-- Time of day --")
    for tod, c in sorted(Counter(f["tod_bucket"] for f in feats).most_common()):
        bar = "█" * int(c / n * 50)
        print(f"   {tod}  {c:>4}  {c / n * 100:>5.1f}%  {bar}")

    print("\n-- VWAP --")
    print(f"   above VWAP:      {_pct_true(feats, 'above_vwap'):.1f}%")
    print(f"   pullback-to-VWAP (|dist|<0.25%): {_pct_true(feats, 'near_vwap'):.1f}%")
    print(f"   vwap_dist_pct   stats: {_stats(feats, 'vwap_dist_pct')}")
    print(f"   vwap_slope_15m  stats: {_stats(feats, 'vwap_slope_15m_pct')}")

    print("\n-- Previous day / Gap --")
    print(f"   above PDH:       {_pct_true(feats, 'above_pdh'):.1f}%")
    print(f"   below PDL:       {_pct_true(feats, 'below_pdl'):.1f}%")
    print(f"   prev_day_bias:   {_distribution(feats, 'prev_day_bias')}")
    print(f"   gap_pct stats:   {_stats(feats, 'gap_pct')}")
    print(f"   pdc_dist_pct:    {_stats(feats, 'pdc_dist_pct')}")

    print("\n-- CPR --")
    print(f"   cpr_position:    {_distribution(feats, 'cpr_position')}")
    print(f"   cpr_type:        {_distribution(feats, 'cpr_type')}")

    print("\n-- Candle pattern at entry bar --")
    for key in ("bull_reversal", "bear_reversal", "bull_engulf", "bear_engulf",
                "bull_pin", "bear_pin", "doji"):
        p = _pct_true(feats, key)
        flag = "  <-- strong" if p > 40 else ""
        print(f"   {key:<15} {p:>5.1f}%{flag}")

    print("\n-- Volume / Position / Momentum --")
    print(f"   vol_ratio (entry-bar vs today avg): {_stats(feats, 'vol_ratio')}")
    print(f"   vol_ratio > 1.5x:  {sum(1 for f in feats if f['vol_ratio'] > 1.5) / n * 100:.1f}%")
    print(f"   range_pos stats:   {_stats(feats, 'range_pos')}")
    print(f"   range_pos > 0.8 (near today's high): {sum(1 for f in feats if f['range_pos'] > 0.8) / n * 100:.1f}%")
    print(f"   range_pos < 0.2 (near today's low):  {sum(1 for f in feats if f['range_pos'] < 0.2) / n * 100:.1f}%")
    print(f"   momentum_15m_pct stats:   {_stats(feats, 'momentum_15m_pct')}")
    print(f"   intraday_move_pct stats:  {_stats(feats, 'intraday_move_pct')}")

    print("\n-- Moneyness / Premium --")
    print(f"   moneyness_pct stats: {_stats(feats, 'moneyness_pct')}")
    otm = sum(1 for f in feats if (f['opt_type'] == 'CE' and f['moneyness_pct'] < -0.25) or (f['opt_type'] == 'PE' and f['moneyness_pct'] > 0.25))
    atm = sum(1 for f in feats if abs(f['moneyness_pct']) <= 0.25)
    itm = n - otm - atm
    print(f"   OTM / ATM / ITM:  {otm}/{atm}/{itm}  ({otm / n * 100:.0f}% / {atm / n * 100:.0f}% / {itm / n * 100:.0f}%)")
    print(f"   entry_price stats: {_stats(feats, 'entry_price')}")


def derive_hypothesis(ce: list[dict], pe: list[dict]) -> None:
    """Print a compact, evidence-backed hypothesis string."""
    print("\n" + "=" * 72)
    print("IMPLIED STRATEGY HYPOTHESIS")
    print("=" * 72)

    all_f = ce + pe
    n = len(all_f)

    def frac(feats, cond):
        return sum(1 for f in feats if cond(f)) / len(feats) * 100 if feats else 0

    tod_counts = Counter(f["tod_bucket"] for f in all_f)
    top_tod = tod_counts.most_common(3)
    tod_coverage = sum(c for _, c in top_tod) / n * 100 if n else 0

    lines = [
        f"Sample: {n} entries — CE={len(ce)}, PE={len(pe)}  (bias = {len(ce) * 100 // max(1, n)}% long calls)",
        "",
        "Time of day:",
        f"  Top 3 buckets: {', '.join(f'{t}({c})' for t, c in top_tod)}  → {tod_coverage:.0f}% of trades",
        "",
        "CE setup:",
        f"  {frac(ce, lambda f: f['above_vwap']):.0f}% above VWAP",
        f"  {frac(ce, lambda f: f['above_pdh']):.0f}% above PDH (breakout)",
        f"  {frac(ce, lambda f: f['cpr_position'] == 'ABOVE_TC'):.0f}% above CPR TC",
        f"  {frac(ce, lambda f: f['momentum_15m_pct'] > 0.2):.0f}% had +0.2% momentum in prior 15m",
        f"  {frac(ce, lambda f: f['range_pos'] > 0.7):.0f}% near today's high (range_pos > 0.7)",
        f"  {frac(ce, lambda f: f['vol_ratio'] > 1.5):.0f}% had entry-bar volume > 1.5× today avg",
        f"  {frac(ce, lambda f: abs(f['moneyness_pct']) <= 0.5):.0f}% picked ATM/~ATM strike (|moneyness| <= 0.5%)",
        "",
        "PE setup:",
        f"  {frac(pe, lambda f: not f['above_vwap']):.0f}% below VWAP",
        f"  {frac(pe, lambda f: f['below_pdl']):.0f}% below PDL (breakdown)",
        f"  {frac(pe, lambda f: f['cpr_position'] == 'BELOW_BC'):.0f}% below CPR BC",
        f"  {frac(pe, lambda f: f['momentum_15m_pct'] < -0.2):.0f}% had -0.2% momentum in prior 15m",
        f"  {frac(pe, lambda f: f['range_pos'] < 0.3):.0f}% near today's low (range_pos < 0.3)",
        f"  {frac(pe, lambda f: f['vol_ratio'] > 1.5):.0f}% had entry-bar volume > 1.5× today avg",
    ]
    for line in lines:
        print(line)


# -----------------------------------------------------------------------------
# CLI orchestration.
# -----------------------------------------------------------------------------

async def _run(args: argparse.Namespace) -> int:
    events, meta = parse_file(args.path)
    entries = [e for e in events if e.kind == "ENTRY" and e.strike and e.option_type]
    if args.limit:
        entries = entries[: args.limit]
    logger.info("Analyzing %d entries from %s", len(entries), args.path.name)

    _ = load_master()  # warm master cache so the verifier cache key logic matches

    features: list[dict] = []
    skipped: list[str] = []
    for idx, e in enumerate(entries, 1):
        try:
            entry_day = datetime.fromisoformat(e.ts_ist).date()
            ctx = await get_ctx_candles(e.symbol, entry_day)
            if not ctx:
                skipped.append(f"{e.symbol} (no ctx)")
                continue
            feats = compute_features(e, ctx)
            if feats is None:
                skipped.append(f"{e.symbol} (insufficient data)")
                continue
            features.append(feats)
        except Exception:
            logger.exception("Failed on entry id=%s", e.msg_id)
            skipped.append(f"{e.symbol} (exception)")
        if idx % 40 == 0:
            print(f"  ... {idx}/{len(entries)}", flush=True)

    print(f"\nExtracted features for {len(features)} of {len(entries)} entries  ({len(skipped)} skipped)")

    ce_feats = [f for f in features if f["opt_type"] == "CE"]
    pe_feats = [f for f in features if f["opt_type"] == "PE"]
    print_side_report("CE entries (bullish / expects rise)", ce_feats)
    print_side_report("PE entries (bearish / expects drop)", pe_feats)
    derive_hypothesis(ce_feats, pe_feats)

    if args.csv:
        args.csv.parent.mkdir(parents=True, exist_ok=True)
        with args.csv.open("w", newline="") as fh:
            if features:
                w = csv.DictWriter(fh, fieldnames=list(features[0].keys()))
                w.writeheader()
                w.writerows(features)
        print(f"\nFeatures CSV written to: {args.csv}")

    out_json = args.out or (
        Path(__file__).resolve().parent / "data" /
        f"setups_{datetime.now(IST).strftime('%Y%m%d_%H%M%S')}.json"
    )
    out_json.write_text(json.dumps({
        "source_file": str(args.path),
        "chat_title": meta.get("chat_title"),
        "generated_at_ist": datetime.now(IST).isoformat(),
        "n_entries": len(entries),
        "n_features": len(features),
        "features": features,
    }, indent=2, default=str))
    print(f"Feature dump written to: {out_json}")
    return 0


def main(argv: Iterable[str]) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("path", type=Path, help="parsed Telegram JSON (from fetch_history.py)")
    p.add_argument("--limit", type=int, help="smoke-test on first N entries")
    p.add_argument("--csv", type=Path, help="write feature matrix to CSV")
    p.add_argument("--out", type=Path, help="write feature JSON here")
    args = p.parse_args(list(argv))

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    return asyncio.run(_run(args))


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
