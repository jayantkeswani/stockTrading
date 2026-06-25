"""Pure context builders for the Intraday Hunter agent (no DB, no I/O).

Candle data in -> structured context dicts out, shared by the offline prototype
(asyncpg fetch) and the backend services (app async session). Keeps the
prev-day-structure and live-state shaping identical across both paths.

Candle format everywhere: list of {ts, open, high, low, close, volume}.
"""
from __future__ import annotations

from datetime import time

INDICES = ("NIFTY", "BANKNIFTY", "SENSEX")
# round-number step per index (his stops cluster on these)
ROUND_STEP = {"NIFTY": 100.0, "BANKNIFTY": 500.0, "SENSEX": 500.0}
MARKET_OPEN = time(9, 15)


def _f(x) -> float:
    return float(x)


def compute_levels(candles: list[dict], index_name: str) -> dict:
    """Key levels from a day's candles: prev close, PDH, PDL, two nearby round numbers."""
    closes = [_f(c["close"]) for c in candles]
    highs = [_f(c["high"]) for c in candles]
    lows = [_f(c["low"]) for c in candles]
    prev_close = closes[-1]
    step = ROUND_STEP.get(index_name, 100.0)
    base = round(prev_close / step) * step
    rounds = sorted({base - step, base, base + step})
    return {
        "prev_close": round(prev_close, 2),
        "pdh": round(max(highs), 2),
        "pdl": round(min(lows), 2),
        "round": [round(r, 2) for r in rounds],
    }


def prev_day_structure(candles: list[dict], index_name: str) -> dict:
    """Previous-day shape the trader reads: range, close position, wicks, a shape tag."""
    o = _f(candles[0]["open"])
    high = max(_f(c["high"]) for c in candles)
    low = min(_f(c["low"]) for c in candles)
    close = _f(candles[-1]["close"])
    rng = high - low
    close_pos = (close - low) / rng if rng > 0 else 0.5
    upper_wick = (high - max(o, close)) / rng if rng > 0 else 0.0
    lower_wick = (min(o, close) - low) / rng if rng > 0 else 0.0
    day_range_pct = (rng / o * 100.0) if o else 0.0

    if close_pos >= 0.6:
        close_tag = "closed strong (upper third)"
    elif close_pos <= 0.4:
        close_tag = "closed weak (lower third)"
    else:
        close_tag = "closed mid-range"
    rej = ""
    if upper_wick >= 0.4:
        rej = "; upper-wick rejection (sellers defended the high)"
    elif lower_wick >= 0.4:
        rej = "; lower-wick rejection (buyers defended the low)"
    shape = f"{close_tag}, day range {day_range_pct:.2f}%{rej}"

    levels = compute_levels(candles, index_name)
    return {
        "open": round(o, 2),
        "high": round(high, 2),
        "low": round(low, 2),
        "close": round(close, 2),
        "close_position_in_range": round(close_pos, 2),
        "upper_wick_frac": round(upper_wick, 2),
        "lower_wick_frac": round(lower_wick, 2),
        "day_range_pct": round(day_range_pct, 2),
        "shape": shape,
        **levels,
    }


def expected_daily_move_pct(vix: float | None) -> float | None:
    """1-day expected move ~= VIX / sqrt(252) ~= VIX / 15.87, in percent of spot."""
    if not vix or vix <= 0:
        return None
    return round(vix / (252 ** 0.5), 2)


def expected_range(spot: float, vix: float | None) -> list[float] | None:
    """[low, high] 1-sigma expected range for the day from VIX, around `spot`."""
    mv = expected_daily_move_pct(vix)
    if mv is None or not spot:
        return None
    band = spot * mv / 100.0
    return [round(spot - band, 2), round(spot + band, 2)]


def build_call1_context(
    per_index_prev: dict[str, dict],
    multi_day_memory: list[dict],
    calendar: dict,
    india_vix: float | None = None,
) -> dict:
    """Assemble the Call 1 (pre-open thesis) context object."""
    vol = {
        "india_vix": india_vix,
        "expected_daily_move_pct": expected_daily_move_pct(india_vix),
        "expected_range_from_prev_close": {
            idx: expected_range(s.get("prev_close"), india_vix)
            for idx, s in per_index_prev.items()
        },
    }
    return {
        "as_of": "pre-market",
        "indices": per_index_prev,  # {index: prev_day_structure(...)}
        "volatility": vol,  # India VIX -> expected daily move/range (BUY-vs-SELL input)
        "recent_days_structure": multi_day_memory,  # [{date, trapped_side, direction, thesis_played_out}]
        "calendar": calendar,  # {is_expiry, expiry_index, holiday_ahead}
    }


def gap_pct(today_open: float, prev_close: float) -> float:
    """Open gap % vs the previous close."""
    if not prev_close:
        return 0.0
    return round((today_open / prev_close - 1.0) * 100.0, 2)


def opening_candle_summary(candles: list[dict]) -> list[dict]:
    """Compact OHLC for the first few 1m candles from 09:15 (drops volume noise)."""
    out = []
    for c in candles:
        out.append(
            {
                "t": c["ts"][11:16] if isinstance(c["ts"], str) else c["ts"].strftime("%H:%M"),
                "o": round(_f(c["open"]), 2),
                "h": round(_f(c["high"]), 2),
                "l": round(_f(c["low"]), 2),
                "c": round(_f(c["close"]), 2),
            }
        )
    return out


def build_call2_live(
    per_index_open: dict[str, float],
    per_index_prev: dict[str, dict],
    per_index_opening_candles: dict[str, list[dict]],
    now_hhmm: str,
    minutes_since_open: int,
    india_vix: float | None = None,
) -> dict:
    """Assemble the Call 2 (decision) live-state object: gap, first candles, price-vs-levels."""
    live: dict = {
        "now": now_hhmm,
        "minutes_since_open": minutes_since_open,
        "india_vix": india_vix,
        "expected_daily_move_pct": expected_daily_move_pct(india_vix),
        "indices": {},
    }
    for idx in per_index_open:
        prev = per_index_prev.get(idx, {})
        open_px = per_index_open[idx]
        candles = per_index_opening_candles.get(idx, [])
        last = _f(candles[-1]["close"]) if candles else open_px
        pivot = prev.get("prev_close")
        live["indices"][idx] = {
            "open": round(open_px, 2),
            "gap_pct": gap_pct(open_px, pivot) if pivot else None,
            "last_price": round(last, 2),
            "prev_close_pivot": pivot,
            "pdh": prev.get("pdh"),
            "pdl": prev.get("pdl"),
            "above_pivot": (last > pivot) if pivot else None,
            "expected_range_from_open": expected_range(open_px, india_vix),
            "opening_candles": opening_candle_summary(candles),
        }
    return live
