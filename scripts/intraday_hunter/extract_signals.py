"""Extract precise entry signals from Intraday Hunter transcripts via price anchoring.

For each live trading video:
  1. LLM reads transcript segments (with per-second timestamps) and identifies
     the entry moment: approximate video_secs, price level, index, direction.
  2. Fetches 1m candles for that index on the trading date from Fyers.
  3. Scans candles for the entry price (price-level anchoring) to find the
     actual market timestamp — no clock-time assumptions needed.
  4. Computes indicator context at that candle:
       gap_pct, VWAP position, PDH/PDL position, time bucket, candle pattern.
  5. Aggregates features across all resolved trades to surface patterns.

Requires:
  - transcript JSON from fetch_transcripts.py (must have 'segments' + 'trading_date')
  - Fyers access token cached in Redis (backend must be running, or token seeded)

Usage:
    python scripts/intraday_hunter/extract_signals.py data/transcripts_*.json
    python scripts/intraday_hunter/extract_signals.py data/transcripts_*.json --verbose
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "backend"))
load_dotenv(REPO_ROOT / ".env")

# Reuse candle fetching from the Arjun study — same Fyers backend
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "telegram"))
from verify_signals import fyers_history, get_candles_cached  # noqa: E402

from app.research.llm_client import create_llm_client  # noqa: E402

IST = ZoneInfo("Asia/Kolkata")
DATA_DIR = Path(__file__).resolve().parent / "data"
CANDLES_DIR = DATA_DIR / "candle_cache"

# Fyers symbols per index
INDEX_SYMBOLS = {
    "BANKNIFTY": "NSE:NIFTYBANK-INDEX",
    "NIFTY": "NSE:NIFTY50-INDEX",
    "SENSEX": "BSE:SENSEX-INDEX",
    "FINNIFTY": "NSE:FINNIFTY-INDEX",
}

# Price range → index identification (approximate)
PRICE_RANGES = [
    ("BANKNIFTY", 40_000, 65_000),
    ("NIFTY",     20_000, 28_000),
    ("SENSEX",    65_000, 90_000),
    ("FINNIFTY",  18_000, 25_000),
]

# Tolerance for price matching (points)
PRICE_TOLERANCE = {"BANKNIFTY": 100, "NIFTY": 50, "SENSEX": 200, "FINNIFTY": 50}

MARKET_OPEN_H, MARKET_OPEN_M = 9, 15
MARKET_CLOSE_H, MARKET_CLOSE_M = 15, 30


# ---------------------------------------------------------------------------
# LLM: find entry moment from transcript segments
# ---------------------------------------------------------------------------

ENTRY_EXTRACTION_SYSTEM = """You are analyzing a Hindi stock trading video transcript.

Find the moment when the trader announces entering a trade (entry lena, trade banana,
buy/sell karna, position lena, etc.) and the price level of the index at that moment.

Return JSON only:
{
  "found_entry": true or false,
  "entry_video_secs": <float or null, seconds into video when entry announced (only if segments provided)>,
  "entry_price": <float, index price mentioned near the entry (BankNifty/Nifty/Sensex level)>,
  "index": "BANKNIFTY" | "NIFTY" | "SENSEX" | "FINNIFTY",
  "direction": "CE" | "PE" | null,
  "confidence": "high" | "medium" | "low",
  "entry_context": "<exact Hindi phrase from transcript that indicates entry>"
}

If no clear entry moment is found, return {"found_entry": false}.
If a price level is not mentioned near the entry, set entry_price to null.
"""


async def find_entry_in_transcript(llm, video: dict) -> dict:
    """Ask LLM to find the entry moment. Works with segments or plain text."""
    title = video["title"]
    segments = video.get("segments", [])

    if segments:
        # Rich mode: timestamped segments
        content = "\n".join(
            f"{s['start_secs']:.1f}s: {s['text']}"
            for s in segments[:200]
        )
        prompt = f"Video title: {title}\n\nTimestamped transcript segments:\n{content}"
    else:
        # Fallback mode: plain joined text (no timestamps)
        text = video.get("transcript", "")[:6000]
        prompt = f"Video title: {title}\n\nTranscript (no timestamps available):\n{text}"

    try:
        return await llm.generate_json(prompt=prompt, system=ENTRY_EXTRACTION_SYSTEM, max_tokens=1024)
    except Exception as e:
        return {"found_entry": False, "error": str(e)}


# ---------------------------------------------------------------------------
# Candle helpers
# ---------------------------------------------------------------------------

def _to_ist(epoch: int) -> datetime:
    return datetime.fromtimestamp(epoch, tz=IST)


def _filter_day(candles: list[list], day: date) -> list[list]:
    """Return only candles belonging to a specific IST date."""
    return [c for c in candles if _to_ist(c[0]).date() == day]


def _market_candles(candles: list[list], day: date) -> list[list]:
    """Return candles for a day within market hours (9:15–15:30 IST)."""
    result = []
    for c in candles:
        dt = _to_ist(c[0])
        if dt.date() != day:
            continue
        if (dt.hour, dt.minute) < (MARKET_OPEN_H, MARKET_OPEN_M):
            continue
        if (dt.hour, dt.minute) > (MARKET_CLOSE_H, MARKET_CLOSE_M):
            continue
        result.append(c)
    return result


def _detect_index_from_price(price: float) -> str | None:
    for name, lo, hi in PRICE_RANGES:
        if lo <= price <= hi:
            return name
    return None


def _find_matching_candles(candles: list[list], price: float, index: str) -> list[list]:
    """Find candles where the price range (low–high) includes the stated price."""
    tol = PRICE_TOLERANCE.get(index, 100)
    return [c for c in candles if (c[3] - tol) <= price <= (c[2] + tol)]


def _compute_vwap(candles_up_to: list[list]) -> float | None:
    """Cumulative VWAP from start up to (and including) the last candle."""
    total_tp_vol = 0.0
    total_vol = 0.0
    for c in candles_up_to:
        _, o, h, l, close, vol = c
        if vol <= 0:
            continue
        tp = (h + l + close) / 3
        total_tp_vol += tp * vol
        total_vol += vol
    if total_vol == 0:
        return None
    return total_tp_vol / total_vol


def _time_bucket(dt: datetime) -> str:
    hm = (dt.hour, dt.minute)
    if hm < (9, 30):
        return "9:15-9:30"
    if hm < (10, 0):
        return "9:30-10:00"
    if hm < (11, 0):
        return "10:00-11:00"
    if hm < (12, 0):
        return "11:00-12:00"
    return "12:00+"


def _candle_direction(c: list) -> str:
    _, o, h, l, close, _ = c
    body = abs(close - o)
    rng = h - l
    if rng == 0:
        return "doji"
    if body / rng < 0.1:
        return "doji"
    return "green" if close >= o else "red"


def _price_vs_level(price: float, level: float, name: str, pct_threshold: float = 0.15) -> str:
    pct = (price - level) / level * 100
    if abs(pct) <= pct_threshold:
        return f"at_{name}"
    return f"above_{name}" if pct > 0 else f"below_{name}"


# ---------------------------------------------------------------------------
# Per-trade context builder
# ---------------------------------------------------------------------------

async def build_trade_context(
    trading_date: date,
    entry_price: float,
    index: str,
    direction: str | None,
    verbose: bool = False,
) -> dict | None:
    """
    Fetch candles, find the entry candle, compute indicators.
    Returns None if candles unavailable or price anchor fails.
    """
    fyers_sym = INDEX_SYMBOLS.get(index)
    if not fyers_sym:
        return None

    # Fetch 2 days: previous trading day + trading day
    prev_day = trading_date - timedelta(days=1)
    # Skip weekend: if prev_day is Sunday, go to Friday
    while prev_day.weekday() >= 5:
        prev_day -= timedelta(days=1)

    CANDLES_DIR.mkdir(exist_ok=True)
    all_candles = await get_candles_cached(fyers_sym, prev_day, trading_date)
    if not all_candles:
        if verbose:
            print(f"    No candles for {fyers_sym} on {trading_date}")
        return None

    today_candles = _market_candles(all_candles, trading_date)
    prev_candles = _market_candles(all_candles, prev_day)

    if not today_candles:
        if verbose:
            print(f"    No today candles for {trading_date}")
        return None

    # PDH / PDL / PDC
    pdh = max(c[2] for c in prev_candles) if prev_candles else None
    pdl = min(c[3] for c in prev_candles) if prev_candles else None
    pdc = prev_candles[-1][4] if prev_candles else None
    today_open = today_candles[0][1]
    gap_pct = ((today_open - pdc) / pdc * 100) if pdc else None

    # Find entry candle
    matching = _find_matching_candles(today_candles, entry_price, index)
    if not matching:
        if verbose:
            print(f"    Price {entry_price} not found in {index} candles on {trading_date}")
        return None

    # Use the first match (earliest time price touched that level)
    entry_candle = matching[0]
    entry_dt = _to_ist(entry_candle[0])

    # VWAP up to entry
    idx = today_candles.index(entry_candle)
    vwap = _compute_vwap(today_candles[: idx + 1])

    # Assemble context
    ctx: dict = {
        "trading_date": str(trading_date),
        "index": index,
        "direction": direction,
        "entry_price": entry_price,
        "entry_time_ist": entry_dt.strftime("%H:%M"),
        "time_bucket": _time_bucket(entry_dt),
        "candle_direction": _candle_direction(entry_candle),
        "gap_pct": round(gap_pct, 2) if gap_pct is not None else None,
        "gap_direction": None,
        "vwap": round(vwap, 1) if vwap else None,
        "price_vs_vwap": None,
        "pdh": pdh,
        "pdl": pdl,
        "pdc": pdc,
        "price_vs_pdh": None,
        "price_vs_pdl": None,
        "price_position": None,
        "anchor_candles_found": len(matching),
    }

    if gap_pct is not None:
        ctx["gap_direction"] = "gap_down" if gap_pct < -0.3 else ("gap_up" if gap_pct > 0.3 else "flat")

    if vwap:
        ctx["price_vs_vwap"] = _price_vs_level(entry_price, vwap, "vwap")

    if pdh:
        ctx["price_vs_pdh"] = _price_vs_level(entry_price, pdh, "pdh")
    if pdl:
        ctx["price_vs_pdl"] = _price_vs_level(entry_price, pdl, "pdl")

    if pdh and pdl:
        if entry_price > pdh:
            ctx["price_position"] = "above_pdh"
        elif entry_price < pdl:
            ctx["price_position"] = "below_pdl"
        else:
            ctx["price_position"] = "inside_range"

    return ctx


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------

def aggregate_contexts(contexts: list[dict]) -> dict:
    def freq(field: str) -> dict:
        return dict(Counter(c[field] for c in contexts if c.get(field)).most_common())

    gaps = [c["gap_pct"] for c in contexts if c.get("gap_pct") is not None]
    return {
        "trade_count": len(contexts),
        "direction": freq("direction"),
        "time_bucket": freq("time_bucket"),
        "gap_direction": freq("gap_direction"),
        "price_position": freq("price_position"),
        "price_vs_vwap": freq("price_vs_vwap"),
        "candle_direction_at_entry": freq("candle_direction"),
        "avg_gap_pct": round(sum(gaps) / len(gaps), 2) if gaps else None,
        "gap_pct_range": [round(min(gaps), 2), round(max(gaps), 2)] if gaps else None,
        "index_distribution": freq("index"),
    }


def print_summary(agg: dict, contexts: list[dict]) -> None:
    print("\n" + "=" * 60)
    print("INTRADAY HUNTER — SIGNAL FEATURE ANALYSIS")
    print(f"Resolved trades: {agg['trade_count']}")
    print("=" * 60)

    print(f"\nDirection (CE/PE): {agg['direction']}")
    print(f"Index traded:      {agg['index_distribution']}")

    print(f"\nEntry timing:      {agg['time_bucket']}")
    print(f"Gap at open:       {agg['gap_direction']}")
    if agg["avg_gap_pct"] is not None:
        print(f"  avg gap %:       {agg['avg_gap_pct']}%  range: {agg['gap_pct_range']}")

    print(f"\nPrice position (vs PDH/PDL): {agg['price_position']}")
    print(f"Price vs VWAP:               {agg['price_vs_vwap']}")
    print(f"Entry candle direction:       {agg['candle_direction_at_entry']}")

    print("\nPer-trade detail:")
    for c in contexts:
        print(
            f"  {c['trading_date']} {c['entry_time_ist']}  {c['index']} {c['direction'] or '?'}"
            f"  price={c['entry_price']}  gap={c['gap_pct']}%  {c['price_position'] or ''}  "
            f"vwap={c['price_vs_vwap'] or ''}  bucket={c['time_bucket']}"
        )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

import re as _re

_MONTH_MAP = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}


def _infer_dates(videos: list[dict]) -> None:
    """Infer trading_date for videos that don't have one, using analysis title pairing."""
    for v in videos:
        if v.get("trading_date"):
            continue
        if v.get("video_type") == "analysis":
            m = _re.search(r"(\d{1,2})\s+([A-Za-z]{3})\s+(\d{4})", v["title"])
            if m:
                d, mon, y = int(m.group(1)), m.group(2).lower(), int(m.group(3))
                mn = _MONTH_MAP.get(mon)
                if mn:
                    v["trading_date"] = f"{y:04d}-{mn:02d}-{d:02d}"

    for i, v in enumerate(videos):
        if v.get("video_type") == "live_trading" and not v.get("trading_date"):
            for j in range(i + 1, min(i + 4, len(videos))):
                if videos[j].get("video_type") == "analysis" and videos[j].get("trading_date"):
                    v["trading_date"] = videos[j]["trading_date"]
                    break


async def main(transcript_file: Path, verbose: bool) -> None:
    data = json.loads(transcript_file.read_text(encoding="utf-8"))
    videos = data["videos"]

    # Infer missing trading dates from analysis video titles
    _infer_dates(videos)

    live_videos = [
        v for v in videos
        if v.get("video_type") == "live_trading"
        and v.get("trading_date")
        and v.get("transcript")
    ]

    no_date = [v for v in videos if v.get("video_type") == "live_trading" and not v.get("trading_date")]
    no_transcript = [v for v in videos if v.get("video_type") == "live_trading" and not v.get("transcript")]
    no_segments = [v for v in live_videos if not v.get("segments")]

    if no_date:
        print(f"⚠️  {len(no_date)} live video(s) have no trading_date — skipping")
    if no_transcript:
        print(f"⚠️  {len(no_transcript)} live video(s) have no transcript — skipping")
    if no_segments:
        print(f"ℹ️  {len(no_segments)} video(s) using plain text (no segment timestamps) — entry_video_secs will be null")

    if not live_videos:
        sys.exit("No live trading videos with transcript + trading_date. Run fetch_transcripts.py first.")

    print(f"Processing {len(live_videos)} live trading videos...")

    llm = create_llm_client()
    contexts = []
    skipped = []

    for video in live_videos:
        title = video["title"]
        trading_date = date.fromisoformat(video["trading_date"])
        mode = "segments" if video.get("segments") else "text"
        print(f"\n[{video['trading_date']}] [{mode}] {title[:50]}...")

        # Step 1: LLM finds entry moment
        extraction = await find_entry_in_transcript(llm, video)
        if verbose:
            print(f"  LLM: {extraction}")

        if not extraction.get("found_entry"):
            print(f"  → SKIP: LLM found no entry moment")
            skipped.append({"video_id": video["video_id"], "reason": "no_entry_found"})
            continue

        entry_price = extraction.get("entry_price")
        index = extraction.get("index")
        direction = extraction.get("direction")

        if not entry_price:
            # Try to infer index from direction + title heuristic
            print(f"  → SKIP: no price level extracted (confidence={extraction.get('confidence')})")
            skipped.append({"video_id": video["video_id"], "reason": "no_price"})
            continue

        # Step 2: auto-detect index if LLM didn't provide one
        if not index:
            index = _detect_index_from_price(float(entry_price))
        if not index:
            print(f"  → SKIP: can't determine index for price {entry_price}")
            skipped.append({"video_id": video["video_id"], "reason": "unknown_index"})
            continue

        print(f"  Entry: {index} {direction or '?'} @ {entry_price}  "
              f"(video t={extraction.get('entry_video_secs')}s, confidence={extraction.get('confidence')})")

        # Step 3: fetch candles + build context
        ctx = await build_trade_context(trading_date, float(entry_price), index, direction, verbose)
        if ctx is None:
            print(f"  → SKIP: candle anchor failed")
            skipped.append({"video_id": video["video_id"], "reason": "candle_anchor_failed"})
            continue

        ctx["video_id"] = video["video_id"]
        ctx["llm_confidence"] = extraction.get("confidence")
        ctx["entry_context"] = extraction.get("entry_context", "")
        print(f"  ✓ Anchored → {ctx['entry_time_ist']} IST  gap={ctx['gap_pct']}%  "
              f"pos={ctx['price_position']}  vwap={ctx['price_vs_vwap']}")
        contexts.append(ctx)

    if not contexts:
        print("\nNo trades resolved. Possible reasons:")
        print("  - Fyers token not in Redis (start backend or seed token)")
        print("  - Historical candle data not available for these dates")
        print("  - Price levels not mentioned clearly in transcripts")
        return

    agg = aggregate_contexts(contexts)
    print_summary(agg, contexts)

    from datetime import datetime as _dt
    ts = _dt.now(IST).strftime("%Y%m%d_%H%M%S")
    out = DATA_DIR / f"signals_{ts}.json"
    out.write_text(
        json.dumps({"resolved": contexts, "skipped": skipped, "aggregate": agg},
                   ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"\nSaved → {out}")


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("transcript_file", type=Path, help="JSON file from fetch_transcripts.py")
    p.add_argument("--verbose", action="store_true", help="Print per-candle debug info")
    args = p.parse_args()
    if not args.transcript_file.exists():
        sys.exit(f"File not found: {args.transcript_file}")
    asyncio.run(main(args.transcript_file, args.verbose))
