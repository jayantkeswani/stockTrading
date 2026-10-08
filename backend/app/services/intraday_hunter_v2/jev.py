"""Jev arm — TypeSafe AI's decision model via OpenRouter. SHADOW ONLY: it never trades.

Behind `JEV_ENABLED` + `OPENROUTER_API_KEY` (+ `JEV_MODEL`, default the pinned latest stable
`typesafe/jev-1.13`). Jev answers typed questions (Choice / Score / Noul) with probabilities in
~70-500ms; it is weak at arithmetic, so it gets plain-language precomputed facts only. From 09:15
to 09:25 each minute: side ∈ {CE, PE, WAIT} + pool_broken_and_riding (Noul); while v2 holds a
position: hold ∈ {HOLD, EXIT}. The answer, probabilities and latency go into
ih_minute_log.arms.jev. Any failure returns {"error": ...} — never raises.

API: POST https://openrouter.ai/api/alpha/decisions
     {"model", "state": {...}, "questions": {key: {"type", "instructions", "criteria"}}}
"""
from __future__ import annotations

import logging
import time as _time

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

JEV_URL = "https://openrouter.ai/api/alpha/decisions"
TIMEOUT_S = 3.0


def is_enabled() -> bool:
    return bool(settings.jev_enabled and settings.openrouter_api_key)


def build_questions(in_position: bool) -> dict:
    """The typed questions for this minute (pure)."""
    q = {
        "side": {
            "type": "choice",
            "instructions": "Given the stop-level facts, which side should an intraday options "
                            "buyer take right now, or wait?",
            "criteria": {
                "CE": "A stop pool above has broken and price is riding higher — buy calls",
                "PE": "A stop pool below has broken and price is riding lower — buy puts",
                "WAIT": "No pool has cleanly broken yet, or price is chopping both ways",
            },
        },
        "pool_broken_and_riding": {
            "type": "noul",
            "instructions": "Has a stop pool (prev close, PDH/PDL, round number, opening range, "
                            "drawn level) broken and is price still running in that direction?",
            "criteria": {"true": "a pool broke and price is riding the break",
                         "false": "no clean break being ridden"},
        },
    }
    if in_position:
        q["hold"] = {
            "type": "choice",
            "instructions": "A basket is open in the stated direction. Hold it or exit now?",
            "criteria": {"HOLD": "the run toward the next pool is intact",
                         "EXIT": "the run has stalled or reversed through the broken pool"},
        }
    return q


def parse_answers(payload: dict) -> dict:
    """Compact {question: {answer, probabilities|p}} from the decisions response (pure)."""
    out: dict = {}
    for key, a in (payload.get("answers") or {}).items():
        if not isinstance(a, dict):
            continue
        if a.get("type") == "noul":
            out[key] = {"answer": (a.get("noul") or 0) >= 0.5, "p": a.get("noul")}
        else:
            out[key] = {"answer": a.get("choice"), "confidence": a.get("confidence"),
                        "probabilities": a.get("probabilities")}
    return out


async def ask(state: dict, in_position: bool = False) -> dict:
    """One Jev call. Returns {model, answers, latency_ms} or {error, latency_ms}."""
    if not is_enabled():
        return {"error": "disabled"}
    body = {"model": settings.jev_model, "state": state,
            "questions": build_questions(in_position)}
    t0 = _time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_S) as client:
            r = await client.post(
                JEV_URL, json=body,
                headers={"Authorization": f"Bearer {settings.openrouter_api_key}"},
            )
        latency = int((_time.monotonic() - t0) * 1000)
        if r.status_code != 200:
            return {"error": f"http_{r.status_code}", "detail": r.text[:200], "latency_ms": latency}
        payload = r.json()
        return {"model": payload.get("model"), "answers": parse_answers(payload),
                "latency_ms": latency, "cost": (payload.get("usage") or {}).get("cost")}
    except Exception as e:  # noqa: BLE001 — a shadow arm must never disturb anything
        return {"error": type(e).__name__, "latency_ms": int((_time.monotonic() - t0) * 1000)}
