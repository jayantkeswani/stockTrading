"""LLM overlay for signal confidence scoring.

Called once per signal, after all deterministic gates pass and after option/futures
resolution, but before _persist_signal. Never blocks a signal — if the LLM call
times out or fails, the deterministic score is used as-is.

Input: complete indicator snapshot + deterministic confidence breakdown + today's
       prior signals for the same symbol (up to 5, for repetition detection).
Output: confidence_adjustment ±30, ai_summary, ai_rationale, key_supports,
        key_risks, recommended_action, suggested_lot_adjustment.

The LLM adjustment is clamped to ±30. Adjustments below -20 indicate the LLM
believes the signal is fundamentally flawed and should not be traded. Most
adjustments will be |x| <= 10.
"""

import asyncio
import json
import logging
from dataclasses import dataclass

from app.config import settings
from app.strategies.base import MarketContext, StrategySignal

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """You are a senior Indian derivatives trader reviewing a signal before execution. You receive complete technical, macro, and derivatives context as JSON.

Your role:
1. Explain concisely WHY this signal fired — cite actual values from the input, not generalities.
2. Identify 2-4 concrete supports (factors strengthening the trade, with specific values) and 2-4 concrete risks (factors weakening it, with specific values).
3. Judge whether the deterministic confidence captured the setup quality and adjust by -30..+30. Scale guide:
   -30 to -20: Fundamental flaw — direct repeat with no new structural context, strong counter-setup, or multiple adverse signals today. This signal has no merit.
   -20 to -10: Significant concern — weak setup, recent identical signal at similar price, or poor history today.
   -10 to +10: Normal range. Most adjustments fall here.
   +10 to +20: Strong multi-factor confirmation (bias + VWAP + OI + pattern all aligned).
   +20 to +30: Exceptional convergence — rare. Requires citing 3+ independent supporting factors.
   Do NOT go to ±20 or beyond without citing at least two independent reasons.
4. Produce a one-sentence summary for a trader glancing at an alert, and a 3-5 sentence rationale for post-trade review.

CRITICAL — value accuracy:
- Every number you write in summary, rationale, key_supports, and key_risks MUST appear EXACTLY in the input JSON. Do not round, interpolate, or reconstruct values. If VWAP is 1455.38 in the input, write 1455.38, not 1455 or 1444.
- If a field is null or missing, do not reference it or guess its value.
- Cross-check: before writing any number, verify it matches a value in the input.

Prior signals today (when prior_signals_today is non-empty):
- If a prior signal for the same symbol/direction fired within 30 minutes at a similar price (within 0.5%), apply -15 to -25 unless the new signal shows a meaningfully different structure (new candle pattern, price broke through a key level).
- If prior signals today had ai_adjustment of -10 or worse, treat the current signal as elevated risk; increase your downward adjustment accordingly.
- A new entry point that broke through a key structural level since the last signal warrants independent evaluation.

Rules:
- Counter-bias trades (direction opposite to intraday_bias.bias): reduce adjustment unless bias is WEAK and reversal_quality > 0.7.
- VIX > 22: treat as major risk.
- Pre-open gap > 0.5% opposite the trade direction: major risk.
- Narrow CPR + trending VWAP slope matching direction: major support.
- Global sentiment (global_score) is already reflected in intraday_bias. Do NOT cite individual global tickers (Dow, Nasdaq, S&P) as primary supports or risks unless |global_score| > 0.5 (strong conviction). Focus on the setup's structural factors: VWAP position, candle patterns, OI walls, R:R ratio.
- Output ONLY strict JSON matching the schema below. No prose outside JSON.

Schema:
{
  "confidence_adjustment": <integer -30 to +30>,
  "summary": "<one sentence, ≤ 200 chars, cite specific indicator values>",
  "rationale": "<3-5 sentences explaining the confluence, regime, and quality>",
  "key_supports": ["<specific factor with value>", ...],
  "key_risks": ["<specific factor with value>", ...],
  "recommended_action": "<PROCEED | PROCEED_WITH_CAUTION | RECONSIDER>",
  "suggested_lot_adjustment": "<NONE | REDUCE_50_PCT | SKIP>"
}"""


@dataclass
class SignalConfidence:
    confidence_adjustment: int       # -30 to +30
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
    prior_signals: list[dict] | None = None,
) -> SignalConfidence:
    """Call the LLM to review a signal and return a confidence overlay.

    Returns _FALLBACK on any error or timeout so the caller is never blocked.
    prior_signals: up to 5 prior signals today for the same symbol, most recent first.
    """
    if not settings.ai_confidence_enabled:
        return _FALLBACK

    if not settings.google_api_key:
        logger.debug("google_api_key not set — skipping AI confidence overlay")
        return _FALLBACK

    try:
        context_json = _build_context_json(signal, ctx, prior_signals=prior_signals)
        indicators = signal.indicators or {}
        strategy_name = signal.strategy_name if hasattr(signal, "strategy_name") else "unknown"
        setup_type = indicators.get("setup_type", "unknown")
        result = await asyncio.wait_for(
            _call_llm(context_json, strategy_name=strategy_name, setup_type=setup_type),
            timeout=settings.ai_confidence_timeout_seconds,
        )
        return result
    except asyncio.TimeoutError:
        logger.warning("AI confidence overlay timed out for %s %s", signal.symbol, signal.signal_type)
        return _FALLBACK
    except Exception:
        logger.exception("AI confidence overlay failed for %s %s", signal.symbol, signal.signal_type)
        return _FALLBACK


def _build_context_json(
    signal: StrategySignal,
    ctx: MarketContext,
    prior_signals: list[dict] | None = None,
) -> str:
    """Build the complete indicator snapshot to send to the LLM."""
    indicators = signal.indicators or {}
    bias_info = indicators.get("intraday_bias", {})
    conf_factors = indicators.get("confidence_factors", {})

    # Compute R:R ratio from index levels
    rr_ratio = None
    index_sl = signal.index_sl or indicators.get("index_sl")
    index_target = signal.index_target or indicators.get("index_target")
    if index_sl and index_target and signal.entry_price:
        risk = abs(float(signal.entry_price) - float(index_sl))
        reward = abs(float(index_target) - float(signal.entry_price))
        if risk > 0:
            rr_ratio = round(reward / risk, 2)

    strategy_name = signal.strategy_name if hasattr(signal, "strategy_name") else "unknown"
    setup_type = indicators.get("setup_type", "unknown")

    payload = {
        "strategy": {
            "name": strategy_name,
            "setup_type": setup_type,
        },
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
        "global_sentiment": {
            "global_score": indicators.get("global_score"),
            "note": "Already reflected in intraday_bias at 10% weight. Only cite if |global_score| > 0.5.",
        },
        "deterministic_confidence": {
            "score": float(signal.confidence) if signal.confidence else 0,
            "persist_threshold": (ctx.strategy_params or {}).get("min_confidence_to_persist", 30.0),
            "factors": conf_factors,
            "rationale": indicators.get("confidence_rationale"),
        },
        "trade_window": {
            "current_time_ist": ctx.current_time_ist,
            "window_state": indicators.get("window_state", "UNKNOWN"),
        },
    }

    if prior_signals:
        payload["prior_signals_today"] = prior_signals

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


_USER_PROMPT_TEMPLATE = """=== {strategy_label} SIGNAL FOR REVIEW ===
Strategy: {strategy_name} | Setup: {setup_type}

{context_json}

=== REQUIRED OUTPUT ===

Produce a JSON object with EXACTLY these fields:

{{
  "confidence_adjustment": <integer -30 to +30 — how much to adjust the deterministic score above>,
  "summary": "<One sentence ≤ 200 chars. Lead with direction + symbol + key reason. Cite at least two specific values EXACTLY as they appear in the input JSON above.>",
  "rationale": "<3-5 sentences. Focus on setup quality: VWAP position, candle pattern, OI walls, R:R ratio. Each sentence must cite a specific value from the input. Last sentence should name the biggest risk.>",
  "key_supports": [
    "<Support 1: specific factor + EXACT value from input>",
    "<Support 2>",
    "<Support 3 (optional)>",
    "<Support 4 (optional)>"
  ],
  "key_risks": [
    "<Risk 1: specific factor + EXACT value from input>",
    "<Risk 2>",
    "<Risk 3 (optional)>",
    "<Risk 4 (optional)>"
  ],
  "recommended_action": "<Exactly one of: PROCEED, PROCEED_WITH_CAUTION, RECONSIDER>",
  "suggested_lot_adjustment": "<Exactly one of: NONE, REDUCE_50_PCT, SKIP>"
}}

=== EXAMPLES (for value accuracy) ===

GOOD summary (values match input):
  Input has: vwap=24358.42, vwap_distance_pct=-0.12, pcr=0.49, max_ce_oi_strike=24400
  Output: "PE on NIFTY pulling back to VWAP 24358.42 (dist -0.12%); PCR 0.49 with CE OI wall at 24400 confirms resistance."

BAD summary (values fabricated):
  Input has: vwap=24358.42, pdh=24400.5
  Output: "PE on NIFTY after pullback to VWAP 24350 near PDH 24395" ← WRONG: 24350 and 24395 do not appear in the input. Must write 24358.42 and 24400.5 exactly.

=== FIELD GUIDANCE ===
- confidence_adjustment: -10 to +10 for most signals. -20 to -30 only for fundamental flaws (direct repeat with no new context, strong counter-setup). +15 to +30 for rare multi-factor convergence (cite ≥3 independent factors). Do not exceed ±20 without two independent reasons.
- recommended_action: PROCEED if adjustment >= 0 and no major risk. PROCEED_WITH_CAUTION if -15 to 0 or one major risk. RECONSIDER if <= -15 or multiple major risks.
- suggested_lot_adjustment: REDUCE_50_PCT if VIX > 20 or R:R < 1.2 or strong counter-bias. SKIP if adjustment <= -20. NONE otherwise."""

_STRATEGY_LABELS = {
    "vwap_pullback": "VWAP PULLBACK",
    "intraday_futures": "INTRADAY FUTURES",
    "can_slim": "CAN SLIM",
    "orb": "ORB",
    "gamma_scalping": "GAMMA SCALPING",
}


async def _call_llm(context_json: str, strategy_name: str = "unknown", setup_type: str = "unknown") -> SignalConfidence:
    """Call Gemini and parse the structured response."""
    from app.research.llm_client import create_llm_client

    llm = create_llm_client()
    strategy_label = _STRATEGY_LABELS.get(strategy_name, strategy_name.upper().replace("_", " "))
    prompt = _USER_PROMPT_TEMPLATE.format(
        context_json=context_json,
        strategy_label=strategy_label,
        strategy_name=strategy_name,
        setup_type=setup_type,
    )

    raw = await llm.generate_json(
        prompt=prompt,
        system=_SYSTEM_PROMPT,
    )

    if not raw or not isinstance(raw, dict):
        logger.warning("LLM returned invalid JSON for signal confidence")
        return _FALLBACK

    adj = raw.get("confidence_adjustment", 0)
    try:
        adj = int(adj)
        adj = max(-30, min(30, adj))
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
