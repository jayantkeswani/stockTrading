"""Live-trade extraction: screen-recorded Kite positions -> entry/exit clock, legs, P&L.

Pipeline: sample frames every 5s, find the Kite positions screens with a cheap PIL detector (his
positions table sits behind a large blue disclosure card; charts are white), Claude vision reads
the clock / open-closed state / legs / totals / index prices on a few representative frames, then
the teaser-aware entry/exit rule picks the entry and closed frames. (Tesseract was dropped: the
recordings are only served at 360p via the bot-check-safe client, too small for OCR.)
"""
from __future__ import annotations

import json
import logging
from datetime import date

from app.services.intraday_hunter.llm_cli import call_claude_json
from app.services.intraday_hunter_v2.teacher import youtube as yt
from app.services.intraday_hunter_v2.teacher.clock import decode_clock, is_session_time
from app.services.intraday_hunter_v2.teacher.plan import to_float
from app.services.intraday_hunter_v2.teacher.youtube import TeacherIngestError

logger = logging.getLogger(__name__)



# ---------------------------------------------------------------- pure helpers
def detect_entry_exit(reads: list[dict]) -> dict:
    """Entry/exit clock from per-frame reads, honoring the teaser rule (PURE).

    Each read: {t, clock 'HH:MM'|None, positions_open, all_closed}. The video opens with a
    teaser from later in the session, so entry = EARLIEST clock with positions open and exit =
    earliest clock strictly AFTER entry with all legs closed. Reads without a plausible
    session clock are ignored.
    """
    usable = [r for r in reads if is_session_time(r.get("clock"))]
    opens = [r for r in usable if r.get("positions_open")]
    res = {"entry_clock": None, "exit_clock": None, "entry_t": None, "exit_t": None,
           "closed_frame_t": None}
    if not opens:
        return res
    entry = min(opens, key=lambda r: (r["clock"], r["t"]))
    res["entry_clock"], res["entry_t"] = entry["clock"], entry["t"]
    closed = [r for r in usable if r.get("all_closed") and r["clock"] > entry["clock"]]
    if closed:
        ex = min(closed, key=lambda r: (r["clock"], r["t"]))
        res["exit_clock"], res["exit_t"], res["closed_frame_t"] = ex["clock"], ex["t"], ex["t"]
    return res


def sum_check(legs: list[dict], total: float | None, tol: float = 1.0) -> bool:
    """True if the legs' P&L sum to the reported total within tol (PURE)."""
    if total is None or not legs:
        return False
    pnls = [to_float(l.get("pnl")) for l in legs]
    if any(p is None for p in pnls):
        return False
    return abs(sum(pnls) - float(total)) <= tol


def normalize_live(raw: dict) -> dict:
    """Coerce a raw live dict (LLM or pushed) into the canonical shape (PURE)."""
    raw = raw if isinstance(raw, dict) else {}
    legs = []
    for l in raw.get("legs") or []:
        if not isinstance(l, dict):
            continue
        ot = str(l.get("option_type") or "").upper()
        legs.append({
            "index": str(l.get("index") or "").upper() or None,
            "strike": to_float(l.get("strike")),
            "option_type": ot if ot in ("CE", "PE") else None,
            "qty": to_float(l.get("qty")),
            "avg": to_float(l.get("avg")),
            "pnl": to_float(l.get("pnl")),
            "entry_clock": decode_clock(str(l.get("entry_clock") or "")),
        })
    side = str(raw.get("side") or "").upper()
    if side not in ("CE", "PE"):
        types = {l["option_type"] for l in legs if l["option_type"]}
        side = types.pop() if len(types) == 1 else None
    total = to_float(raw.get("total_pnl"))
    ft = raw.get("frame_times") if isinstance(raw.get("frame_times"), dict) else {}

    def _prices(d):
        return {str(k).upper(): f for k, v in (d or {}).items()
                if (f := to_float(v)) is not None} if isinstance(d, dict) else {}

    return {
        "side": side,
        "legs": legs,
        "entry_clock": decode_clock(str(raw.get("entry_clock") or "")),
        "exit_clock": decode_clock(str(raw.get("exit_clock") or "")),
        "total_pnl": total,
        "legs_sum_ok": sum_check(legs, total),
        "index_prices_entry": _prices(raw.get("index_prices_entry")),
        "index_prices_exit": _prices(raw.get("index_prices_exit")),
        "frame_times": {"entry_t": to_float(ft.get("entry_t")), "exit_t": to_float(ft.get("exit_t")),
                        "closed_t": to_float(ft.get("closed_t"))},
    }


# ---------------------------------------------------------------- frame selection (PIL)
POSITIONS_SCORE_MIN = 0.4  # blue-card share of the lower frame; positions screens score ~0.68, charts ~0


def positions_score(image_path: str) -> float:
    """Share of saturated-blue pixels in the lower 45% of a frame.

    The teacher shows his Kite positions table behind a large blue risk-disclosure card; chart
    screens are white. Real frames separate cleanly (~0.68 vs 0.0). Cheap — no OCR.
    """
    from PIL import Image

    im = Image.open(image_path).convert("RGB").resize((160, 90))
    w, h = im.size
    px = [im.getpixel((x, y)) for y in range(int(h * 0.55), h) for x in range(w)]
    blue = sum(1 for r, g, b in px if b > 120 and b > r + 40 and b > g + 20)
    return blue / len(px) if px else 0.0


def positions_segments(scored: list[tuple[float, float]], threshold: float = POSITIONS_SCORE_MIN
                       ) -> list[list[float]]:
    """Group consecutive positions-screen sample times into segments (PURE).

    `scored` = [(t, score)] in time order; a single low frame inside a segment does not split it.
    """
    segs: list[list[float]] = []
    cur: list[float] = []
    gap = 0
    for t, sc in scored:
        if sc >= threshold:
            cur.append(t)
            gap = 0
        elif cur:
            gap += 1
            if gap > 1:
                segs.append(cur)
                cur, gap = [], 0
    if cur:
        segs.append(cur)
    return segs


def pick_frames(segments: list[list[float]], stride_s: float = 20.0, cap: int = 14) -> list[float]:
    """Representative times: each segment's first + last + every `stride_s` between (PURE)."""
    picks: list[float] = []
    for seg in segments:
        sel = [seg[0]]
        for t in seg[1:-1]:
            if t - sel[-1] >= stride_s:
                sel.append(t)
        if seg[-1] != sel[-1]:
            sel.append(seg[-1])
        picks.extend(sel)
    if len(picks) > cap:  # keep the ends of every segment, thin the middles
        step = len(picks) / cap
        picks = [picks[int(i * step)] for i in range(cap - 1)] + [picks[-1]]
    return sorted(set(picks))


def merge_live(open_reads: list[dict], closed: dict) -> dict:
    """Combine the OPEN-position reads (earliest first: per-leg qty/avg + entry clock + index
    prices) with the CLOSED read (final per-leg P&L, total, exit clock + prices) (PURE).

    Each leg's qty/avg come from the first read in which THAT leg shows a non-zero qty — legs are
    often added over a few minutes (e.g. BANKNIFTY first, NIFTY/SENSEX later) — and its
    `entry_clock` is that read's clock. The basket entry clock is the earliest open read's.
    """
    def key(l):
        return (str(l.get("index") or "").upper(), to_float(l.get("strike")),
                str(l.get("option_type") or "").upper())

    first_open: dict = {}
    for r in open_reads:
        for l in r.get("legs") or []:
            if isinstance(l, dict) and (to_float(l.get("qty")) or 0) > 0 and key(l) not in first_open:
                first_open[key(l)] = (l, r.get("clock"))
    legs = []
    for l in closed.get("legs") or []:
        if not isinstance(l, dict):
            continue
        e, clk = first_open.get(key(l), ({}, None))
        legs.append({"index": l.get("index"), "strike": l.get("strike"),
                     "option_type": l.get("option_type"),
                     "qty": to_float(e.get("qty")) or to_float(l.get("qty")),
                     "avg": e.get("avg") if e else l.get("avg"), "pnl": l.get("pnl"),
                     "entry_clock": clk})
    first = open_reads[0] if open_reads else {}
    return {"legs": legs, "total_pnl": closed.get("total_pnl"),
            "entry_clock": first.get("clock"), "exit_clock": closed.get("clock"),
            "index_prices_entry": first.get("index_prices") or {},
            "index_prices_exit": closed.get("index_prices") or {}}


FRAMES_SCHEMA = {
    "frames": [{
        "image": 1,
        "clock": "HH:MM — the Windows taskbar clock, bottom-right (may be upside down)",
        "positions_open": "true if any leg row shows a NON-ZERO Qty",
        "all_closed": "true if every leg row shows Qty 0 and Avg 0.00",
        "total_pnl": 0.0,
        "legs": [{"index": "BANKNIFTY|NIFTY|SENSEX", "strike": 0, "option_type": "CE|PE",
                  "qty": 0, "avg": 0.0, "pnl": 0.0}],
        "index_prices": {"NIFTY": 0.0, "BANKNIFTY": 0.0, "SENSEX": 0.0},
    }]
}


def build_frames_prompt(trading_date: date, n: int) -> str:
    """Vision prompt: read each of `n` positions-screen frames into a structured record."""
    return (
        f"These {n} images are frames (in order, image 1..{n}) from an Indian options trader's "
        f"screen recording on {trading_date.isoformat()}. Each shows his Zerodha Kite POSITIONS "
        "table (Instrument, Qty, Avg, LTP, P&L, and a Total P&L row) partly behind a blue "
        "disclosure card. Browser tabs at the very top show NIFTY / BANKNIFTY / SENSEX prices. "
        "For EVERY image read: the Windows taskbar clock (bottom-right, HH:MM; if it looks upside "
        "down read it rotated — 6 and 9 swap), whether any leg is open (non-zero Qty) or all are "
        "closed (Qty 0, Avg 0.00), the Total P&L, each leg (index, strike, CE/PE, qty, avg, P&L — "
        "Indian digit grouping like 1,78,881.25 = 178881.25; negatives are losses) and the three "
        "index prices from the tabs. Read exact numbers; use null when unreadable. Reply with "
        "ONLY JSON of this shape:\n" + json.dumps(FRAMES_SCHEMA, indent=2)
    )


async def _read_frames(paths: list[str], trading_date: date) -> list[dict]:
    """Claude vision over the chosen frames (batches of 6). Missing replies → empty reads."""
    out: list[dict] = []
    for i in range(0, len(paths), 6):
        batch = paths[i:i + 6]
        raw = await call_claude_json(build_frames_prompt(trading_date, len(batch)),
                                     image_paths=batch, required_keys=("frames",))
        frames = (raw or {}).get("frames") or []
        by_img = {int(f.get("image", j + 1)): f for j, f in enumerate(frames) if isinstance(f, dict)}
        out.extend(by_img.get(k + 1, {}) for k in range(len(batch)))
    return out


async def extract_live(video_path: str, trading_date: date, every_s: int = 5) -> dict:
    """Full live-video extraction → normalized live dict (see normalize_live).

    1. sample a frame every `every_s` s (scaled to 1280x720), score each for the positions screen
       (PIL blue-card detector — the 360p recordings are too small for tesseract);
    2. group into positions segments and pick representative frames;
    3. Claude vision reads clock / open-closed / legs / totals / index prices per frame;
    4. entry = earliest clock with positions open, exit = first all-closed clock after it
       (teaser-aware, `detect_entry_exit`); legs merged from the entry + closed frames.
    Raises TeacherIngestError: TOOL_MISSING (ffmpeg), PARSE_FAILED.
    """
    frames = await yt.sample_frames(video_path, every_s)
    scored = []
    for t, p in frames:
        try:
            scored.append((t, positions_score(p)))
        except Exception:  # noqa: BLE001 — an unreadable frame just doesn't count
            scored.append((t, 0.0))
    segs = positions_segments(scored)
    if not segs:
        raise TeacherIngestError("PARSE_FAILED", "no positions-screen frames found")
    by_t = dict(frames)
    picks = pick_frames(segs)
    reads_raw = await _read_frames([by_t[t] for t in picks], trading_date)
    if not any(reads_raw):
        raise TeacherIngestError("PARSE_FAILED", "Claude vision returned no frame reads")
    reads = []
    for t, r in zip(picks, reads_raw):
        reads.append({"t": t, "clock": decode_clock(str(r.get("clock") or "")),
                      "positions_open": bool(r.get("positions_open")),
                      "all_closed": bool(r.get("all_closed")), "_raw": r})
    det = detect_entry_exit(reads)
    if det["entry_t"] is None or det["closed_frame_t"] is None:
        raise TeacherIngestError("PARSE_FAILED", f"entry/exit not found in frame reads: {det}")
    open_reads = sorted(
        ({**r["_raw"], "clock": r["clock"]} for r in reads
         if r["positions_open"] and r["clock"] and det["entry_clock"] <= r["clock"] <= det["exit_clock"]),
        key=lambda x: x["clock"],
    )
    closed_raw = next(r["_raw"] for r in reads if r["t"] == det["closed_frame_t"])
    merged = merge_live(open_reads, {**closed_raw, "clock": det["exit_clock"]})
    merged["frame_times"] = {"entry_t": det["entry_t"], "exit_t": det["exit_t"],
                             "closed_t": det["closed_frame_t"]}
    live = normalize_live(merged)
    live["frames_read"] = [{"t": r["t"], "clock": r["clock"], "open": r["positions_open"],
                            "closed": r["all_closed"]} for r in reads]
    return live
