"""Decode the Windows taskbar clock from noisy tesseract output (PURE — no I/O).

The teacher's screen recording is often OCR'd rotated 180 degrees: "09:18" reads as
"$ 81:60" (characters reversed, 6<->9 swapped). We try the normal reading first and fall
back to the rotated reading, accepting only plausible session times (09:00-15:30).
"""
from __future__ import annotations

import re

_OCR_FIX = str.maketrans({"O": "0", "o": "0", "D": "0", "l": "1", "I": "1", "|": "1",
                          "i": "1", ".": ":", ";": ":"})
_ROT_SWAP = str.maketrans({"6": "9", "9": "6"})
_TIME_RE = re.compile(r"(\d{1,2}):(\d{2})")
SESSION_START = "09:00"
SESSION_END = "15:30"


def is_session_time(hhmm: str | None) -> bool:
    """True if `hhmm` ('HH:MM') is a valid time inside 09:00-15:30."""
    if not hhmm or not re.fullmatch(r"\d{2}:\d{2}", hhmm):
        return False
    h, m = int(hhmm[:2]), int(hhmm[3:])
    if h > 23 or m > 59:
        return False
    return SESSION_START <= hhmm <= SESSION_END


def _parse(cleaned: str) -> str | None:
    """First plausible HH:MM in an already-cleaned string, else None."""
    for m in _TIME_RE.finditer(cleaned):
        cand = f"{int(m.group(1)):02d}:{m.group(2)}"
        if is_session_time(cand):
            return cand
    return None


def decode_clock(raw: str) -> str | None:
    """Decode OCR text of the clock into 'HH:MM' (session time), or None if implausible.

    Tries the normal reading, then the 180-degree-rotated reading (reverse + 6<->9 swap).
    Tolerates junk characters ('$', spaces) and common confusions (O->0, l/I->1).
    """
    if not raw:
        return None
    s = raw.translate(_OCR_FIX)
    s = "".join(c for c in s if c.isdigit() or c == ":")
    if not s:
        return None
    normal = _parse(s)
    if normal:
        return normal
    return _parse(s[::-1].translate(_ROT_SWAP))
