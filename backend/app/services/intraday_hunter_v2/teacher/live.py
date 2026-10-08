"""Live-trade extraction: screen-recorded Kite positions -> entry/exit clock, legs, P&L.

Pipeline: sample the video every 2s, OCR the taskbar clock + top strip to find when positions
are open / all closed (heuristics — need calibration on real samples), then Claude vision on the
entry frame and the closed-screen frame for exact numbers.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from datetime import date

from app.services.intraday_hunter.llm_cli import call_claude_json
from app.services.intraday_hunter_v2.teacher import youtube as yt
from app.services.intraday_hunter_v2.teacher.clock import decode_clock, is_session_time
from app.services.intraday_hunter_v2.teacher.plan import to_float
from app.services.intraday_hunter_v2.teacher.youtube import TeacherIngestError

logger = logging.getLogger(__name__)

_LEG_RE = re.compile(r"(BANKNIFTY|NIFTY|SENSEX|FINNIFTY|MIDCPNIFTY)\w*?\s*(\d{4,6})?\s*(CE|PE)", re.I)


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


def classify_positions_text(text: str) -> tuple[bool | None, bool | None]:
    """Heuristic (positions_open, all_closed) from OCR of the top strip (PURE).

    Looks for option-leg rows (INDEX ... CE/PE). A row with qty 0 and Avg 0.00 is closed.
    Returns (None, None) when no leg rows are visible. Needs calibration on real frames.
    """
    rows = [ln for ln in (text or "").splitlines() if _LEG_RE.search(ln)]
    if not rows:
        return None, None
    open_rows = closed_rows = 0
    for ln in rows:
        nums = re.findall(r"-?\d[\d,]*\.\d+|-?\d[\d,]*", _LEG_RE.sub(" ", ln))
        zero_avg = any(re.fullmatch(r"0\.00", n) for n in nums)
        zero_qty = any(n == "0" for n in nums[:2])
        if zero_avg or zero_qty:
            closed_rows += 1
        else:
            open_rows += 1
    return open_rows > 0, (open_rows == 0 and closed_rows > 0)


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


# ---------------------------------------------------------------- OCR (tesseract)
async def ocr_text(path: str, rotate: bool = False, psm: int = 6) -> str:
    """Run tesseract on an image (optionally rotated 180 degrees first); '' on empty output.

    Raises TeacherIngestError(TOOL_MISSING) if tesseract is absent.
    """
    exe = yt.require_tool("tesseract")
    src = path
    if rotate:
        src = os.path.splitext(path)[0] + "_rot.png"
        await yt.run_cmd([yt.require_tool("ffmpeg"), "-y", "-v", "error", "-i", path,
                          "-vf", "hflip,vflip", src], 30)
    rc, out, _ = await yt.run_cmd([exe, src, "stdout", "--psm", str(psm)], 30)
    return out if rc == 0 else ""


async def _read_one(t: float, clock_img: str, strip_img: str, sem: asyncio.Semaphore) -> dict:
    """OCR one sampled instant into a detect_entry_exit read."""
    async with sem:
        clock_raw = await ocr_text(clock_img, psm=7)
        clock = decode_clock(clock_raw)
        if clock is None:
            clock = decode_clock(await ocr_text(clock_img, rotate=True, psm=7))
        opened, closed = classify_positions_text(await ocr_text(strip_img))
    return {"t": t, "clock": clock, "positions_open": opened, "all_closed": closed}


LIVE_SCHEMA = {
    "side": "CE | PE",
    "legs": [{"index": "BANKNIFTY", "strike": 0, "option_type": "CE|PE", "qty": 0, "avg": 0.0, "pnl": 0.0}],
    "entry_clock": "HH:MM (taskbar clock on the ENTRY frame)",
    "exit_clock": "HH:MM (taskbar clock on the CLOSED frame)",
    "total_pnl": 0.0,
    "index_prices_entry": {"NIFTY": 0.0, "BANKNIFTY": 0.0, "SENSEX": 0.0},
    "index_prices_exit": {"NIFTY": 0.0, "BANKNIFTY": 0.0, "SENSEX": 0.0},
}


def build_live_prompt(trading_date: date, entry_clock, exit_clock) -> str:
    """Vision prompt: image 1 = entry frame, image 2 = closed-screen frame."""
    return (
        f"Screen recording of an Indian options trader on {trading_date.isoformat()}. "
        "Image 1 is the moment positions are first open (Kite positions table, browser tabs with "
        "index prices at top). Image 2 is the moment ALL legs are closed (qty 0, Avg 0.00) showing "
        f"each leg's realized P&L and the Total P&L. OCR guess of clocks: entry {entry_clock}, "
        f"exit {exit_clock} (the Windows clock is bottom-right and may look upside down; 6 and 9 "
        "swap when rotated). Read EXACT numbers: for every traded leg give index, strike, "
        "option_type, qty (the entered quantity, from image 1 if image 2 shows 0), avg entry price, "
        "final per-leg P&L, and the Total P&L; legs' P&L must sum to the total. Also read the index "
        "prices shown in the browser tabs on each image. Reply with ONLY JSON of this shape:\n"
        + json.dumps(LIVE_SCHEMA, indent=2)
    )


async def extract_live(video_path: str, trading_date: date) -> dict:
    """Full live-video extraction -> normalized live dict (see normalize_live).

    Raises TeacherIngestError: TOOL_MISSING (tesseract/ffmpeg), PARSE_FAILED (no entry/exit
    found or Claude unavailable/unusable).
    """
    yt.require_tool("tesseract")
    clocks = await yt.sample_crops(video_path, yt.CLOCK_CROP, 2, "clock", scale=3)
    strips = await yt.sample_crops(video_path, yt.TOP_STRIP_CROP, 2, "strip", scale=2)
    sem = asyncio.Semaphore(4)
    reads = await asyncio.gather(*[_read_one(t, c, s, sem)
                                   for (t, c), (_, s) in zip(clocks, strips)])
    det = detect_entry_exit(list(reads))
    if det["entry_t"] is None or det["closed_frame_t"] is None:
        raise TeacherIngestError("PARSE_FAILED", f"entry/exit not found in OCR reads: {det}")
    base = os.path.splitext(video_path)[0]
    entry_png = await yt.extract_frame(video_path, det["entry_t"] + 2, f"{base}_entry.png")
    closed_png = await yt.extract_frame(video_path, det["closed_frame_t"], f"{base}_closed.png")
    raw = await call_claude_json(build_live_prompt(trading_date, det["entry_clock"], det["exit_clock"]),
                                 image_paths=[entry_png, closed_png], required_keys=("legs", "total_pnl"))
    if raw is None:
        raise TeacherIngestError("PARSE_FAILED", "Claude vision extraction returned nothing")
    raw.setdefault("entry_clock", det["entry_clock"])
    raw.setdefault("exit_clock", det["exit_clock"])
    # OCR-derived clocks are authoritative when the model's own reading is not a session time.
    if not is_session_time(decode_clock(str(raw.get("entry_clock") or ""))):
        raw["entry_clock"] = det["entry_clock"]
    if not is_session_time(decode_clock(str(raw.get("exit_clock") or ""))):
        raw["exit_clock"] = det["exit_clock"]
    raw["frame_times"] = {"entry_t": det["entry_t"], "exit_t": det["exit_t"],
                          "closed_t": det["closed_frame_t"]}
    return normalize_live(raw)
