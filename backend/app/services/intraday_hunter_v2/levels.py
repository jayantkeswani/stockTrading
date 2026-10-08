"""Stop-level facts — pure functions (no DB, no I/O), reused by the prompts, the minute log
and grading.

For each index at any minute: distance (points + %) and broken / not-broken status since the
open for the stop pools other traders' stops cluster at — previous close, PDH, PDL, the
nearest round numbers above/below, the opening range (09:15-09:19), the session high/low so far,
and the teacher's drawn levels. Plus: the opening type (basket-average gap), which pools have
been taken, and the nearest pool ahead / behind for each side.

Candle format: {ts: IST ISO str | datetime, open, high, low, close}; candles are today's
in-session 1m bars up to and including the minute being evaluated, oldest first.

Semantics (documented in docs/ai/intraday-hunter-v2.md §Level facts):
- PDH is *broken* (up) when the open is above it or any high trades above it; PDL mirrors.
- Every other level is *broken* when price crosses it relative to the open: a level above the
  open breaks UP on the first high >= level, a level below the open breaks DOWN on the first
  low <= level. A level exactly at the open is not broken by the open itself.
- Breaking a level UP takes the stops resting above it (shorts' stops → fuel for CE); breaking
  DOWN takes the stops below (longs' stops → fuel for PE). "Riding the break" = trading WITH it.
"""
from __future__ import annotations

import math
from datetime import datetime, time

INDICES = ("NIFTY", "BANKNIFTY", "SENSEX")
DEFAULT_ROUND_STEP = {"NIFTY": 100.0, "BANKNIFTY": 500.0, "SENSEX": 500.0}
OPENING_RANGE_END = time(9, 19)


def _hhmm(ts) -> str:
    """'HH:MM' of a candle timestamp (IST ISO string or datetime)."""
    if isinstance(ts, datetime):
        return ts.strftime("%H:%M")
    s = str(ts)
    return s[11:16] if len(s) >= 16 else s[:5]


def _f(x) -> float:
    return float(x)


def _pct(dist: float, ref: float) -> float:
    return round(dist / ref * 100.0, 3) if ref else 0.0


def round_levels(price: float, step: float) -> tuple[float, float]:
    """(above, below): the nearest round numbers strictly above and at-or-below `price`.

    If `price` sits exactly on a round number, `below` is that number and `above` the next.
    """
    below = math.floor(price / step) * step
    above = below + step
    return float(above), float(below)


def _cross(level: float, open_px: float, candles: list[dict]) -> tuple[bool, str | None, str | None]:
    """Generic cross status relative to the open → (broken, broken_at HH:MM, direction up|down)."""
    if open_px < level:
        for c in candles:
            if _f(c["high"]) >= level:
                return True, _hhmm(c["ts"]), "up"
    elif open_px > level:
        for c in candles:
            if _f(c["low"]) <= level:
                return True, _hhmm(c["ts"]), "down"
    return False, None, None


def _pdh_status(pdh: float, open_px: float, candles: list[dict]) -> tuple[bool, str | None, str | None]:
    if open_px > pdh:
        return True, _hhmm(candles[0]["ts"]) if candles else None, "up"
    for c in candles:
        if _f(c["high"]) > pdh:
            return True, _hhmm(c["ts"]), "up"
    return False, None, None


def _pdl_status(pdl: float, open_px: float, candles: list[dict]) -> tuple[bool, str | None, str | None]:
    if open_px < pdl:
        return True, _hhmm(candles[0]["ts"]) if candles else None, "down"
    for c in candles:
        if _f(c["low"]) < pdl:
            return True, _hhmm(c["ts"]), "down"
    return False, None, None


def opening_range(candles: list[dict], end: time = OPENING_RANGE_END) -> dict | None:
    """Opening range high/low over 09:15..`end` inclusive; None until the `end` candle exists."""
    end_s = end.strftime("%H:%M")
    win = [c for c in candles if _hhmm(c["ts"]) <= end_s]
    if not win or _hhmm(candles[-1]["ts"]) < end_s:
        return None
    return {"high": max(_f(c["high"]) for c in win), "low": min(_f(c["low"]) for c in win)}


def _level(name: str, price: float, last: float, broken: bool, at, direction) -> dict:
    dist = price - last
    return {
        "name": name,
        "price": round(price, 2),
        "dist_pts": round(dist, 2),
        "dist_pct": _pct(dist, last),
        "side": "above" if price > last else ("below" if price < last else "at"),
        "broken": broken,
        "broken_at": at,
        "broken_dir": direction,
    }


def level_facts(
    index: str,
    prev: dict,
    candles: list[dict],
    drawn_levels: list[float] | None = None,
    round_step: float | None = None,
    or_end: time = OPENING_RANGE_END,
) -> dict | None:
    """Stop-level facts for one index as of its last candle. None when there are no candles.

    `prev` = {close, high, low} of the previous trading day.
    """
    if not candles or not prev:
        return None
    step = float(round_step or DEFAULT_ROUND_STEP.get(index, 100.0))
    open_px = _f(candles[0]["open"])
    last = _f(candles[-1]["close"])
    pc, pdh, pdl = _f(prev["close"]), _f(prev["high"]), _f(prev["low"])
    sess_hi = max(_f(c["high"]) for c in candles)
    sess_lo = min(_f(c["low"]) for c in candles)

    levels: list[dict] = []
    levels.append(_level("prev_close", pc, last, *_cross(pc, open_px, candles)))
    levels.append(_level("pdh", pdh, last, *_pdh_status(pdh, open_px, candles)))
    levels.append(_level("pdl", pdl, last, *_pdl_status(pdl, open_px, candles)))

    r_above, r_below = round_levels(last, step)
    levels.append(_level("round_above", r_above, last, *_cross(r_above, open_px, candles)))
    levels.append(_level("round_below", r_below, last, *_cross(r_below, open_px, candles)))

    orng = opening_range(candles, or_end)
    if orng:
        after = [c for c in candles if _hhmm(c["ts"]) > or_end.strftime("%H:%M")]
        up = next((c for c in after if _f(c["high"]) > orng["high"]), None)
        dn = next((c for c in after if _f(c["low"]) < orng["low"]), None)
        levels.append(_level("or_high", orng["high"], last, up is not None,
                             _hhmm(up["ts"]) if up else None, "up" if up else None))
        levels.append(_level("or_low", orng["low"], last, dn is not None,
                             _hhmm(dn["ts"]) if dn else None, "down" if dn else None))

    levels.append(_level("session_high", sess_hi, last, False, None, None))
    levels.append(_level("session_low", sess_lo, last, False, None, None))
    for i, px in enumerate(drawn_levels or []):
        try:
            pxf = float(px)
        except (TypeError, ValueError):
            continue
        levels.append(_level(f"drawn_{i + 1}", pxf, last, *_cross(pxf, open_px, candles)))

    pools = [lv for lv in levels if not lv["name"].startswith("session_")]
    taken = [lv["name"] for lv in pools if lv["broken"]]
    above = sorted([lv for lv in pools if lv["price"] > last and not lv["broken"]],
                   key=lambda x: x["price"])
    below = sorted([lv for lv in pools if lv["price"] < last and not lv["broken"]],
                   key=lambda x: -x["price"])

    def _brief(lv):
        return {"name": lv["name"], "price": lv["price"], "dist_pts": lv["dist_pts"],
                "dist_pct": lv["dist_pct"]} if lv else None

    return {
        "index": index,
        "asof": _hhmm(candles[-1]["ts"]),
        "open": round(open_px, 2),
        "last": round(last, 2),
        "gap_pct": _pct(open_px - pc, pc),
        "change_pct": _pct(last - pc, pc),
        "session_high": round(sess_hi, 2),
        "session_low": round(sess_lo, 2),
        "opening_range": orng,
        "levels": levels,
        "pools_taken": taken,
        # CE rides UP into the untaken pool above; PE rides DOWN into the pool below.
        "nearest_ahead": {"CE": _brief(above[0] if above else None),
                          "PE": _brief(below[0] if below else None)},
        "nearest_behind": {"CE": _brief(below[0] if below else None),
                           "PE": _brief(above[0] if above else None)},
    }


def opening_type(gaps_pct: dict[str, float | None], threshold_pct: float = 0.15) -> str:
    """'gap_up' | 'flat' | 'gap_down' from the basket-average open gap (% vs prev close)."""
    vals = [g for g in gaps_pct.values() if g is not None]
    if not vals:
        return "flat"
    avg = sum(vals) / len(vals)
    if avg >= threshold_pct:
        return "gap_up"
    if avg <= -threshold_pct:
        return "gap_down"
    return "flat"


def _find(facts: dict, name: str) -> dict | None:
    return next((lv for lv in facts.get("levels", []) if lv["name"] == name), None)


def pdh_pdl_break_side(facts: dict) -> str | None:
    """Side implied by this index's PDH/PDL break (riding it): CE / PE / None.

    Both broken → the most recent break wins (ties → None).
    """
    pdh, pdl = _find(facts, "pdh"), _find(facts, "pdl")
    up = pdh and pdh["broken"]
    dn = pdl and pdl["broken"]
    if up and not dn:
        return "CE"
    if dn and not up:
        return "PE"
    if up and dn:
        a, b = pdh["broken_at"] or "", pdl["broken_at"] or ""
        if a > b:
            return "CE"
        if b > a:
            return "PE"
    return None


def rule_a_side(facts_by_index: dict[str, dict], min_agree: int = 2) -> str | None:
    """Rule A: ride the PDH/PDL break when >= `min_agree` of the indices broke the same way."""
    sides = [pdh_pdl_break_side(f) for f in facts_by_index.values() if f]
    for s in ("CE", "PE"):
        if sides.count(s) >= min_agree:
            return s
    return None


def describe_facts(facts: dict) -> list[str]:
    """Plain-language lines (no arithmetic left to do) — for the Call 2 prompt and Jev."""
    if not facts:
        return []
    idx = facts["index"]
    out = [f"{idx}: last {facts['last']}, opened {facts['open']} "
           f"({facts['gap_pct']:+.2f}% gap), now {facts['change_pct']:+.2f}% vs prev close."]
    for lv in facts["levels"]:
        if lv["name"].startswith("session_"):
            continue
        state = (f"BROKEN {lv['broken_dir']} at {lv['broken_at']}" if lv["broken"]
                 else "not broken")
        out.append(f"  {lv['name']} {lv['price']}: {abs(lv['dist_pts']):.0f} pts "
                   f"({abs(lv['dist_pct']):.2f}%) {lv['side']} price, {state}.")
    for side in ("CE", "PE"):
        a = facts["nearest_ahead"][side]
        if a:
            out.append(f"  Nearest untaken pool ahead for {side}: {a['name']} {a['price']} "
                       f"({abs(a['dist_pts']):.0f} pts away).")
    return out
