"""Evening plan extraction: 'Prediction For DD MON YYYY' video -> structured plan."""
from __future__ import annotations

import json
import re
from datetime import date

from app.services.intraday_hunter.llm_cli import call_claude_json
from app.services.intraday_hunter_v2.teacher.youtube import TeacherIngestError

INDICES = ("NIFTY", "BANKNIFTY", "SENSEX")

PLAN_SCHEMA = {
    "gap_up_side": "CE | PE | none",
    "flat_side": "CE | PE | none",
    "gap_down_side": "CE | PE | none",
    "bias": "short string, e.g. 'bullish above 25150'",
    "levels_onscreen": {"NIFTY": [0.0], "BANKNIFTY": [0.0], "SENSEX": [0.0]},
    "levels_audio": {"NIFTY": [0.0], "BANKNIFTY": [0.0], "SENSEX": [0.0]},
    "summary": "2-3 sentence English summary of his plan",
}


def build_plan_prompt(subs_text: str, trading_date: date) -> str:
    """Prompt for Claude: Hindi transcript + 3 keyframes -> JSON matching PLAN_SCHEMA."""
    return (
        "You are reading the evening video of an Indian index-options trader ('the teacher') "
        f"giving his plan for the trading date {trading_date.isoformat()}. The attached images are "
        "keyframes (30%, 60%, 90% of the video) showing charts of NIFTY / BANKNIFTY / SENSEX with "
        "red resistance and green support lines carrying exact price labels and B/S marks.\n"
        "Below is the Hindi auto-generated transcript (noisy).\n\n"
        f"TRANSCRIPT:\n{subs_text[:12000]}\n\n"
        "Determine which side he will trade if the market opens gap-up, flat, or gap-down: "
        "CE (buy calls), PE (buy puts) or none. Extract price levels labelled on the charts "
        "(levels_onscreen) and levels he speaks aloud (levels_audio), per index, as plain numbers. "
        "Reply with ONLY a JSON object of this shape (no prose):\n"
        + json.dumps(PLAN_SCHEMA, indent=2)
    )


def _side(v) -> str:
    """Coerce free text to 'CE' | 'PE' | 'none'."""
    s = str(v or "").strip().lower()
    if not s:
        return "none"
    if s in ("ce", "call", "calls") or "buy ce" in s or "call" in s or re.search(r"\bce\b", s):
        return "CE"
    if s in ("pe", "put", "puts") or "buy pe" in s or "put" in s or re.search(r"\bpe\b", s):
        return "PE"
    return "none"


def to_float(v) -> float | None:
    """'25,150' / 25150 / '25150.5' -> float; anything non-numeric -> None."""
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        m = re.search(r"-?\d[\d,]*\.?\d*", v)
        if m:
            try:
                return float(m.group(0).replace(",", ""))
            except ValueError:
                return None
    return None


def _levels(raw) -> dict:
    """Normalize {index: [levels]} -> floats, dropping junk; always has all three indices."""
    out: dict[str, list[float]] = {k: [] for k in INDICES}
    if not isinstance(raw, dict):
        return out
    for key, vals in raw.items():
        k = str(key).upper().replace(" ", "").replace("BANKNIFTY", "BANKNIFTY")
        k = {"BANK": "BANKNIFTY", "NIFTYBANK": "BANKNIFTY", "NIFTY50": "NIFTY"}.get(k, k)
        if k not in out:
            continue
        if not isinstance(vals, (list, tuple)):
            vals = [vals]
        out[k] = [f for f in (to_float(x) for x in vals) if f is not None]
    return out


def normalize_plan(raw: dict) -> dict:
    """Coerce a raw plan dict (LLM or pushed) into the canonical shape (PURE)."""
    raw = raw if isinstance(raw, dict) else {}
    return {
        "gap_up_side": _side(raw.get("gap_up_side")),
        "flat_side": _side(raw.get("flat_side")),
        "gap_down_side": _side(raw.get("gap_down_side")),
        "bias": str(raw.get("bias") or ""),
        "levels_onscreen": _levels(raw.get("levels_onscreen")),
        "levels_audio": _levels(raw.get("levels_audio")),
        "summary": str(raw.get("summary") or ""),
    }


async def extract_plan(subs_text: str, keyframe_paths: list[str], trading_date: date) -> dict:
    """Ask Claude (transcript + keyframes) for the plan; returns the normalized plan.

    Raises TeacherIngestError(PARSE_FAILED) if Claude is unavailable or the reply is unusable.
    """
    raw = await call_claude_json(build_plan_prompt(subs_text, trading_date),
                                 image_paths=keyframe_paths,
                                 required_keys=("gap_up_side", "flat_side", "gap_down_side"))
    if raw is None:
        raise TeacherIngestError("PARSE_FAILED", "Claude plan extraction returned nothing")
    return normalize_plan(raw)
