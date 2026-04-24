"""LLM overlay for signal confidence scoring.

Called once per signal, after all deterministic gates pass and after option/futures
resolution, but before _persist_signal. Never blocks a signal — if the LLM call
times out or fails, the deterministic score is used as-is.

Input: complete indicator snapshot + deterministic confidence breakdown.
Output: confidence_adjustment ±15, ai_summary, ai_rationale, key_supports,
        key_risks, recommended_action, suggested_lot_adjustment.

The LLM adjustment is clamped to ±15 so the deterministic composite stays the
anchor — the LLM can refine but not override.
"""

import asyncio
import json
import logging
from dataclasses import dataclass

from app.config import settings
from app.strategies.base import MarketContext, StrategySignal

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """You are a senior Indian-index options trader reviewing a VWAP Pullback signal before execution. You receive complete technical, macro, and derivatives context as JSON.

Your role:
1. Explain concisely WHY this signal fired — cite actual values from the input, not generalities.
2. Identify 2-4 concrete supports (factors strengthening the trade, with specific values) and 2-4 concrete risks (factors weakening it, with specific values).
3. Judge whether the deterministic confidence captured the setup quality and adjust by -15..+15. Use the full range only for strong disagreement; most adjustments will be |x| <= 5.
4. Produce a one-sentence summary for a trader glancing at an alert, and a 3-5 sentence rationale for post-trade review.

Rules:
- Never invent data. If a field is null, do not reference it.
- Counter-bias trades (direction opposite to intraday_bias.bias): reduce adjustment unless bias is WEAK and reversal_quality > 0.7.
- VIX > 22 or global_alignment < 0.3: treat as major risks.
- Pre-open gap > 0.5% opposite the trade direction: major risk.
- Narrow CPR + trending VWAP slope matching direction: major support.
- Output ONLY strict JSON matching the schema below. No prose outside JSON.

Schema:
{
  "confidence_adjustment": <integer -15 to +15>,
  "summary": "<one sentence, ≤ 200 chars, cite specific indicator values>",
  "rationale": "<3-5 sentences explaining the confluence, regime, and quality>",
  "key_supports": ["<specific factor with value>", ...],
  "key_risks": ["<specific factor with value>", ...],
  "recommended_action": "<PROCEED | PROCEED_WITH_CAUTION | RECONSIDER>",
  "suggested_lot_adjustment": "<NONE | REDUCE_50_PCT | SKIP>"
}"""


@dataclass
class SignalConfidence:
    confidence_adjustment: int       # -15 to +15
    summary: str
    rationale: str
    key_supports: list[str]
    key_risks: list[str]
    recommended_action: str          # PROCEED | PROCEED_WITH_CAUTION | RECONSIDER
    suggested_lot_adjustment: str    # NONE | REDUCE_50_PCT | SKIP


_FALLBACK = SignalConfidence(
    confidence_adjustment=0,
    summary="",
    rationale="",
    key_supports=[],
    key_risks=[],
    recommended_action="PROCEED",
    suggested_lot_adjustment="NONE",
)


async def score_signal(
    signal: StrategySignal,
    ctx: MarketContext,
) -> SignalConfidence:
    """Call the LLM to review a signal and return a confidence overlay.

    Returns _FALLBACK on any error or timeout so the caller is never blocked.
    """
    if not settings.ai_confidence_enabled:
        return _FALLBACK

    if not settings.google_api_key:
        logger.debug("google_api_key not set — skipping AI confidence overlay")
        return _FALLBACK

    try:
        context_json = _build_context_json(signal, ctx)
        result = await asyncio.wait_for(
            _call_llm(context_json),
            timeout=settings.ai_confidence_timeout_seconds,
        )
        return result
    except asyncio.TimeoutError:
        logger.warning("AI confidence overlay timed out for %s %s", signal.symbol, signal.signal_type)
        return _FALLBACK
    except Exception:
        logger.exception("AI confidence overlay failed for %s %s", signal.symbol, signal.signal_type)
        return _FALLBACK


def _build_context_json(signal: StrategySignal, ctx: MarketContext) -> str:
    """Build the complete indicator snapshot to send to the LLM."""
    indicators = signal.indicators or {}
    bias_info = indicators.get("intraday_bias", {})
    conf_factors = indicators.get("confidence_factors", {})

    is_ce = signal.signal_type.value.endswith("CE")

    # Compute R:R ratio from index levels
    rr_ratio = None
    index_sl = signal.index_sl or indicators.get("index_sl")
    index_target = signal.index_target or indicators.get("index_target")
    if index_sl and index_target and signal.entry_price:
        risk = abs(float(signal.entry_price) - float(index_sl))
        reward = abs(float(index_target) - float(signal.entry_price))
        if risk > 0:
            rr_ratio = round(reward / risk, 2)

    payload = {
        "signal": {
            "symbol": signal.symbol,
            "direction": signal.signal_type.value,
            "instrument": signal.instrument_type.value,
            "strike": float(signal.strike_price) if signal.strike_price else None,
            "expiry": str(signal.expiry_date) if signal.expiry_date else None,
            "option_symbol": signal.fyers_option_symbol,
            "entry_premium": float(signal.entry_price),
            "index_entry_price": float(signal.index_entry_price) if signal.index_entry_price else float(signal.entry_price),
            "index_sl": float(index_sl) if index_sl else None,
            "index_target": float(index_target) if index_target else None,
            "rr_ratio": rr_ratio,
            "lots": signal.lots,
        },
        "intraday_bias": bias_info,
        "vwap": {
            "value": indicators.get("vwap"),
            "distance_pct": indicators.get("vwap_distance_pct"),
            "slope_last_5_candles": bias_info.get("vwap_slope_last_10"),
        },
        "candles_5m_last_3": _candles_snapshot(ctx),
        "previous_day": {
            "pdh": indicators.get("pdh"),
            "pdl": indicators.get("pdl"),
            "pdc": indicators.get("pdc"),
            "bias": indicators.get("day_bias"),
        },
        "cpr": {
            "pivot": indicators.get("cpr_pivot"),
            "tc": indicators.get("cpr_tc"),
            "bc": indicators.get("cpr_bc"),
            "type": indicators.get("cpr_type"),
        },
        "oi_analysis": {
            "pcr": indicators.get("pcr"),
            "max_ce_oi_strike": indicators.get("max_ce_oi_strike"),
            "max_pe_oi_strike": indicators.get("max_pe_oi_strike"),
            "sentiment": indicators.get("oi_sentiment"),
            "oi_confirmed": indicators.get("oi_confirmed"),
        } if indicators.get("pcr") is not None else None,
        "india_vix": indicators.get("india_vix"),
        "global_cues": {
            "global_score": indicators.get("global_score"),
            **({
                "dow_futures_pct": ctx.global_cues.dow_futures_pct,
                "sp500_close_pct": ctx.global_cues.sp500_close_pct,
                "nasdaq_close_pct": ctx.global_cues.nasdaq_close_pct,
                "crude_pct": ctx.global_cues.crude_pct,
                "usdinr_pct": ctx.global_cues.usdinr_pct,
                "us_vix": ctx.global_cues.us_vix,
                "pre_open_gap_pct": ctx.global_cues.pre_open_gap_pct,
            } if ctx.global_cues else {}),
        },
        "deterministic_confidence": {
            "score": float(signal.confidence) if signal.confidence else 0,
            "fire_threshold": settings.fire_confidence_threshold,
            "factors": conf_factors,
            "rationale": indicators.get("confidence_rationale"),
        },
        "trade_window": {
            "current_time_ist": ctx.current_time_ist,
            "window_state": indicators.get("window_state", "UNKNOWN"),
        },
    }

    return json.dumps(payload, default=str)


def _candles_snapshot(ctx: MarketContext) -> list[dict]:
    """Last 3 × 5m candles as a compact list for the LLM."""
    if not ctx.candles_5m:
        return []
    recent = ctx.candles_5m[-3:]
    return [
        {"o": round(c.open, 2), "h": round(c.high, 2), "l": round(c.low, 2),
         "c": round(c.close, 2), "volume": c.volume}
        for c in recent
    ]


async def _call_llm(context_json: str) -> SignalConfidence:
    """Call Gemini and parse the structured response."""
    from app.research.llm_client import create_llm_client

    llm = create_llm_client()
    prompt = f"Analyze this VWAP Pullback signal context and respond with the required JSON schema:\n\n{context_json}"

    raw = await llm.generate_json(
        system_prompt=_SYSTEM_PROMPT,
        user_prompt=prompt,
    )

    if not raw or not isinstance(raw, dict):
        logger.warning("LLM returned invalid JSON for signal confidence")
        return _FALLBACK

    adj = raw.get("confidence_adjustment", 0)
    try:
        adj = int(adj)
        adj = max(-15, min(15, adj))
    except (TypeError, ValueError):
        adj = 0

    return SignalConfidence(
        confidence_adjustment=adj,
        summary=str(raw.get("summary", ""))[:300],
        rationale=str(raw.get("rationale", "")),
        key_supports=_to_str_list(raw.get("key_supports", [])),
        key_risks=_to_str_list(raw.get("key_risks", [])),
        recommended_action=str(raw.get("recommended_action", "PROCEED")),
        suggested_lot_adjustment=str(raw.get("suggested_lot_adjustment", "NONE")),
    )


def _to_str_list(val) -> list[str]:
    if isinstance(val, list):
        return [str(x) for x in val[:4]]
    return []
