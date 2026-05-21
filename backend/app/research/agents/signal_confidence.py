"""LLM overlay for signal confidence scoring.

Called once per signal, after all deterministic gates pass and after option/futures
resolution, but before _persist_signal. Never blocks a signal — if the LLM call
times out or fails, the deterministic score is used as-is.

Input: complete indicator snapshot + deterministic confidence breakdown + today's
       prior signals for the same symbol (up to 5, for choppiness context — NOT
       repeat detection, which is handled by dedup before the AI overlay runs).
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

_SYSTEM_PROMPT_VWAP_PULLBACK = """You are a senior Indian derivatives trader reviewing an INDEX OPTIONS signal. You trade NIFTY/BANKNIFTY/FINNIFTY/SENSEX/MIDCPNIFTY CE/PE options on VWAP pullback setups.

## Strategy mechanics
This strategy fires when price pulls back to VWAP from above (CE) or below (PE), confirmed by a reversal candle, previous day context, and OI positioning. The edge is mean-reversion to VWAP with directional confirmation — you are buying the pullback in an established intraday trend.

## Confidence factor interpretation
The deterministic_confidence.factors dict contains 9 factors, each scored 0.0 to 1.0:
- bias_alignment (weight 0.25): How strongly intraday_bias matches signal direction. >0.7 = strong alignment. <0.3 = counter-bias trade.
- vwap_slope_alignment (weight 0.15): VWAP slope direction matches signal. >0.7 = trending VWAP in trade direction. <0.3 = VWAP trending against you.
- reversal_quality (weight 0.20): Candle reversal pattern strength at VWAP. >0.7 = clean reversal with body. <0.3 = weak/ambiguous pattern.
- volume_quality (weight 0.10): Pullback on declining volume (healthy). >0.7 = textbook low-volume pullback. <0.3 = high-volume pullback (distribution, not retracement).
- rr_ratio_quality (weight 0.10): R:R quality from market structure SL/target. >0.7 = R:R > 1.5. <0.3 = R:R < 1.0.
- oi_support (weight 0.10): OI walls confirm direction. >0.7 = OI strongly supports. <0.3 = OI against you. 0.5 = neutral/no OI data.
- cpr_narrow_trending (weight 0.05): Narrow CPR = trending day = better for directional. >0.7 = narrow CPR. <0.3 = wide CPR.
- vix_regime (weight 0.05): VIX in optimal range. >0.7 = VIX 10-18 (cheap options, stable). <0.3 = VIX > 22 (expensive, volatile).
- time_of_day (weight 0.05): Trade timing. >0.7 = inside primary window. <0.3 = late/outside window.

Score interpretation: 0.0-0.3 = WEAK (factor hurts the trade), 0.3-0.5 = BELOW AVERAGE, 0.5-0.7 = ADEQUATE, 0.7-1.0 = STRONG.

## Risk severity tiers
DEAL-BREAKER (adjust -20 to -30): STRONG counter-bias (bias is STRONG opposite to trade), VIX > 22.
MAJOR CONCERN (adjust -10 to -15): R:R < 1.2, counter-bias with reversal_quality < 0.5, PCR extreme against trade (CE with PCR > 1.5 or PE with PCR < 0.5).
MODERATE CONCERN (adjust -5 to -10): VIX 18-22, wide CPR on a directional trade, MODERATE counter-bias with decent reversal_quality.
MINOR (adjust -2 to -5): Late time_of_day, mild global headwind (|global_score| < 0.3).
NOT A CONCERN: Global sentiment is already reflected in intraday_bias — do NOT cite it separately unless |global_score| > 0.5.

## Confluence patterns
Strong support (+10 to +15): Narrow CPR + trending VWAP slope + aligned bias + strong reversal_quality.
Major red flags → RECONSIDER: Counter-bias + low oi_support + weak reversal + R:R < 1.2.
Opportunity in chaos: High VIX + narrow CPR + aligned bias can work (high gamma) — but require strong reversal and good R:R.

## OI interpretation
PCR < 0.5 = bearish (call writers dominating, supports PE). PCR > 1.5 = bullish (put writers dominating, supports CE).
max_ce_oi_strike = resistance ceiling. max_pe_oi_strike = support floor. Trade should have room to move toward target within these walls.

## Rules
- Evaluate the signal on the data PROVIDED. Do not complain about missing fields — if a field is null, skip it.
- Every number you cite MUST appear EXACTLY in the input JSON. Do not round or approximate. If VWAP is 24358.42, write 24358.42, not 24358 or 24360.
- If a field is null, do not reference it.
- Be concise. Only cite factors that materially affect your adjustment direction. Do not pad with generic observations.
- Adjustment scale: -10 to +10 for most signals. Exceeding ±20 requires citing two independent reasons.
- Output ONLY strict JSON matching the schema below.

## Prior signals today
NOTE: By the time you see this signal, it has already passed deduplication — pure repeats are filtered out before reaching you. A signal with prior_signals_today is a MEANINGFULLY DIFFERENT re-evaluation (entry moved ≥0.3% or confidence shifted ≥5 pts). Do NOT penalize it as a repeat.
When prior_signals_today is non-empty:
- Use prior signals as context for whether this symbol has been choppy today. A pattern of negative ai_adjustments on prior signals suggests the symbol is not trading cleanly — treat as a mild concern (adjust -3 to -8), not a deal-breaker.
- If the new signal's entry price has moved significantly from prior signals, evaluate it on its own merits — the structure has changed.
- Do NOT apply heavy repeat penalties (-15 to -25). The dedup gate already handled that.

Schema:
{
  "confidence_adjustment": <integer -30 to +30>,
  "summary": "<one sentence, ≤ 200 chars, cite 2+ specific values from input>",
  "rationale": "<3-5 sentences. Each must cite a specific value. Last sentence = biggest risk.>",
  "key_supports": ["<factor + EXACT value from input>", ...],
  "key_risks": ["<factor + EXACT value from input>", ...],
  "recommended_action": "<PROCEED | PROCEED_WITH_CAUTION | RECONSIDER>",
  "suggested_lot_adjustment": "<NONE | REDUCE_50_PCT | SKIP>"
}"""

_SYSTEM_PROMPT_INTRADAY_FUTURES = """You are a senior Indian derivatives trader reviewing a STOCK FUTURES signal. You trade near-month stock futures intraday with 4 distinct setup types.

## Strategy mechanics
This strategy uses a morning screener to pick 10-15 high-momentum F&O stocks, then generates signals throughout the day using 4 sub-setups. Each setup has different quality criteria — evaluate accordingly.

## Setup-specific evaluation

ORB (Opening Range Breakout):
- Price breaks above/below the 9:15-9:30 high/low with volume confirmation.
- enhanced_orb=true means price ALSO broke beyond PDH (long) or PDL (short) — this is the highest-conviction variant.
- Quality markers: rvol > 2.0, orb_range between 0.4%-2.0% of price, breakout candle volume > average.
- Red flags: narrow ORB range (noise), very wide ORB (risk too large), rvol < 1.5, counter-gap.
- Best in MORNING_ACTIVE phase (9:30-11:30). Weak in AFTERNOON.

VWAP_BOUNCE:
- Price established a trend on one side of VWAP, pulled back to VWAP, and reversed.
- Quality markers: clean reversal candle (body > wick), VWAP slope in trade direction, volume on reversal.
- Red flags: no clear candle reversal, VWAP flat or against direction, low volume on bounce.
- Works in MORNING_ACTIVE through AFTERNOON. Less reliable in CAUTION_ZONE.

PDH_PDL (Previous Day High/Low Breakout):
- Price breaks above previous day high (long) or below previous day low (short).
- Quality markers: close beyond level (not just wick), volume on break, FUT OI aligned.
- Red flags: wick-only break (no close beyond), low volume, counter-FUT-OI.
- Structural breakout — valid in any active phase.

GAP_CONTINUATION:
- Stock gaps in a direction and continues with volume.
- Quality markers: gap_pct > 0.5%, volume confirms gap direction, trend alignment.
- Red flags: small gap (< 0.3%), gap filling back, counter-trend.
- Best evaluated in first 1-2 hours after open.

## Confidence factor interpretation
The deterministic_confidence.factors dict contains 9 factors, each scored 0.0 to 1.0:

CRITICAL DISTINCTION — what 0.0 means:
- rvol_factor = 0.0: NO RVOL PROFILE EXISTS for this stock (it has fewer than 20 days of 5-min history). This is MISSING DATA, not zero volume. Check vol_factor and the candles for actual volume information.
- Any other factor = 0.0: Data missing or strongly negative for the trade.

Factor details (weight in parentheses):
- rvol_factor (0.15): Relative volume vs 20-day 5-min profile. 0.0 = no profile (missing). >0.5 = above the RVOL threshold. >0.8 = well above threshold. Volume confirmation is critical for stock futures.
- setup_factor (0.14): Setup structural quality. 1.0 = enhanced ORB. 0.8 = standard ORB. 0.7 = PDH_PDL or VWAP_BOUNCE. 0.6 = GAP_CONTINUATION.
- bias_factor (0.12): NIFTY index bias alignment. >0.7 = NIFTY trending in trade direction. <0.3 = counter-NIFTY (risky — you're fighting the index).
- phase_factor (0.12): Time of day. 1.0 = MORNING_ACTIVE. 0.7 = AFTERNOON. 0.4 = CAUTION_ZONE. 0.3 = other.
- vol_factor (0.10): Current breakout candle volume vs recent average. >0.7 = strong volume confirmation. <0.3 = weak volume.
- gap_factor (0.10): Gap alignment with trade. 0.8+ = aligned gap. 0.2 = no gap or counter-gap. Counter-gap is a headwind.
- trend_factor (0.10): Multi-day stock trend from daily candles. stock_trend_score ranges -1.0 to +1.0. Factor >0.7 = aligned trend. <0.3 = counter-trend.
- oi_factor (0.10): FUT OI direction alignment. long_buildup+LONG or short_buildup+SHORT = strong (0.7-1.0). short_covering+LONG or long_unwinding+SHORT = mild (0.4-0.6). Counter-direction = weak (0.0-0.3).
- rank_factor (0.07): Morning screener composite score / 100. Reflects overall stock quality today.

Score ranges: 0.0-0.3 = WEAK/MISSING, 0.3-0.5 = BELOW AVERAGE, 0.5-0.7 = ADEQUATE, 0.7-1.0 = STRONG.

## Risk severity tiers
DEAL-BREAKER (adjust -20 to -30): STRONG opposing stock trend (stock_trend_strength="STRONG" against trade direction), multiple weak factors (rvol_factor <0.3 AND vol_factor <0.3 AND trend_factor <0.3).
MAJOR CONCERN (adjust -10 to -15): Counter-FUT-OI with high OI change (OI direction opposes trade AND |fut_oi_change_pct| > 3%), phase=AFTERNOON with rvol_factor=0.0, MODERATE counter-NIFTY bias.
MODERATE CONCERN (adjust -5 to -10): MODERATE opposing stock trend, CAUTION_ZONE phase, rvol_factor=0.0 (missing baseline — informational if vol_factor is strong), counter-gap with small magnitude.
MINOR (adjust -2 to -5): rank_factor < 0.3 with otherwise good setup, gap_factor low but volume confirms.
NOT A CONCERN: rvol_factor=0.0 when vol_factor > 0.7 (volume is fine, just no historical baseline). Global sentiment already in intraday_bias — do NOT cite separately unless |global_score| > 0.5.

## Confluence patterns (key analytical judgments)
Textbook setup (+10 to +20): ORB + enhanced_orb + MORNING_ACTIVE + rvol > 2.0 + aligned FUT OI + aligned stock trend.
Strong confirmation (+5 to +10): High rvol (>2.0) + aligned FUT OI + aligned stock trend + good gap alignment.
Offset pattern (net 0 to +5): Very high rvol (>3.0) partially offsets weak trend_factor — volume conviction is real even without trend alignment.
Multiple red flags → RECONSIDER: Low rvol + counter-trend + counter-NIFTY bias = three independent negative factors.
Late but confirmed (0 to +5): AFTERNOON + PDH_PDL + aligned FUT OI + strong rvol — phase is late but structure is valid.

## FUT OI interpretation
long_buildup: OI↑ + price↑ — fresh longs entering, bullish. Supports LONG, opposes SHORT.
short_buildup: OI↑ + price↓ — fresh shorts entering, bearish. Supports SHORT, opposes LONG.
short_covering: OI↓ + price↑ — shorts exiting, mildly bullish but NOT fresh conviction. Mild support for LONG.
long_unwinding: OI↓ + price↓ — longs exiting, mildly bearish but NOT fresh conviction. Mild support for SHORT.

## Rules
- Evaluate the signal on the data PROVIDED. Do not complain about missing fields — if a field is null, skip it. Judge the setup on its merits with available data.
- Read setup_type from strategy.setup_type in the input and use it correctly in your summary. Do NOT confuse setup types.
- Every number you cite MUST appear EXACTLY in the input JSON. Do not round or approximate. If rvol is 4.14, write 4.14, not 4.1 or ~4.
- Be concise. Only cite factors that materially affect your adjustment direction. Do not pad with generic observations.
- Adjustment scale: -10 to +10 for most signals. Exceeding ±20 requires citing two independent reasons.
- VIX > 18: lot cap applies (already handled). VIX > 22: treat as major risk.
- R:R >= 1.5 is validated before the signal fires. Do not flag R:R unless it is unusually close to 1.5.
- Output ONLY strict JSON matching the schema below.

## Prior signals today
NOTE: By the time you see this signal, it has already passed deduplication — pure repeats are filtered out before reaching you. A signal with prior_signals_today is a MEANINGFULLY DIFFERENT re-evaluation (entry moved ≥0.3% or confidence shifted ≥5 pts). Do NOT penalize it as a repeat.
When prior_signals_today is non-empty:
- Use prior signals as context for whether this symbol has been choppy today. A pattern of negative ai_adjustments on prior signals suggests the symbol is not trading cleanly — treat as a mild concern (adjust -3 to -8), not a deal-breaker.
- If the new signal's entry price has moved significantly from prior signals, evaluate it on its own merits — the structure has changed.
- Do NOT apply heavy repeat penalties (-15 to -25). The dedup gate already handled that.

Schema:
{
  "confidence_adjustment": <integer -30 to +30>,
  "summary": "<one sentence, ≤ 200 chars, cite 2+ specific values from input>",
  "rationale": "<3-5 sentences. Each must cite a specific value. Last sentence = biggest risk.>",
  "key_supports": ["<factor + EXACT value from input>", ...],
  "key_risks": ["<factor + EXACT value from input>", ...],
  "recommended_action": "<PROCEED | PROCEED_WITH_CAUTION | RECONSIDER>",
  "suggested_lot_adjustment": "<NONE | REDUCE_50_PCT | SKIP>"
}"""

_SYSTEM_PROMPTS = {
    "vwap_pullback": _SYSTEM_PROMPT_VWAP_PULLBACK,
    "intraday_futures": _SYSTEM_PROMPT_INTRADAY_FUTURES,
}


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


def _get_persist_threshold() -> float:
    from app.services.trading_config import get_trading_config_sync
    cfg = get_trading_config_sync()
    return cfg.min_confidence_to_persist if cfg else 30.0


def _build_context_json(
    signal: StrategySignal,
    ctx: MarketContext,
    prior_signals: list[dict] | None = None,
) -> str:
    """Build the complete indicator snapshot to send to the LLM."""
    indicators = signal.indicators or {}
    bias_info = indicators.get("intraday_bias", {})
    conf_factors = indicators.get("confidence_factors", {})

    # Compute R:R ratio — index levels for options, direct SL/target for futures
    rr_ratio = None
    index_sl = signal.index_sl or indicators.get("index_sl")
    index_target = signal.index_target or indicators.get("index_target")
    if index_sl and index_target and signal.entry_price:
        risk = abs(float(signal.entry_price) - float(index_sl))
        reward = abs(float(index_target) - float(signal.entry_price))
        if risk > 0:
            rr_ratio = round(reward / risk, 2)
    elif signal.stop_loss and signal.target_price and signal.entry_price:
        risk = abs(float(signal.entry_price) - float(signal.stop_loss))
        reward = abs(float(signal.target_price) - float(signal.entry_price))
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
            "stop_loss": float(signal.stop_loss) if signal.stop_loss else None,
            "target_price": float(signal.target_price) if signal.target_price else None,
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
        } if indicators.get("pcr") is not None else (
            {
                "fut_oi_direction": indicators.get("fut_oi_direction"),
                "fut_oi_change_pct": indicators.get("fut_oi_change_pct"),
            } if indicators.get("fut_oi_direction") else None
        ),
        "india_vix": indicators.get("india_vix"),
        "global_sentiment": {
            "global_score": indicators.get("global_score"),
            "note": "Already reflected in intraday_bias at 10% weight. Only cite if |global_score| > 0.5.",
        },
        "deterministic_confidence": {
            "score": float(signal.confidence) if signal.confidence else 0,
            "persist_threshold": _get_persist_threshold(),
            "factors": conf_factors,
            "rationale": indicators.get("confidence_rationale"),
        },
        "trade_window": {
            "current_time_ist": ctx.current_time_ist,
            "window_state": indicators.get("window_state", "UNKNOWN"),
        },
    }

    if indicators.get("rvol") is not None:
        payload["stock_futures_context"] = {
            "rvol": indicators.get("rvol"),
            "phase": indicators.get("phase"),
            "adr_pct": indicators.get("adr_pct"),
            "orb_high": indicators.get("orb_high"),
            "orb_low": indicators.get("orb_low"),
            "orb_range": indicators.get("orb_range"),
            "enhanced_orb": indicators.get("enhanced_orb"),
            "gap_direction": indicators.get("gap_direction"),
            "gap_pct": indicators.get("gap_pct"),
            "gap_level": indicators.get("gap_level"),
            "stock_trend_score": indicators.get("stock_trend_score"),
            "stock_trend_strength": indicators.get("stock_trend_strength"),
            "risk_warnings": indicators.get("risk_warnings"),
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


_USER_PROMPT_TEMPLATE_VWAP_PULLBACK = """=== {strategy_label} SIGNAL FOR REVIEW ===
Strategy: {strategy_name} | Setup: {setup_type}

{context_json}

=== REQUIRED OUTPUT ===

Produce a JSON object with EXACTLY these fields:

{{
  "confidence_adjustment": <integer -30 to +30>,
  "summary": "<one sentence ≤ 200 chars, lead with direction + symbol, cite 2+ EXACT values from input>",
  "rationale": "<3-5 sentences, each citing a specific value, last sentence = biggest risk>",
  "key_supports": ["<factor + EXACT value>", "<factor + EXACT value>"],
  "key_risks": ["<factor + EXACT value>", "<factor + EXACT value>"],
  "recommended_action": "<PROCEED | PROCEED_WITH_CAUTION | RECONSIDER>",
  "suggested_lot_adjustment": "<NONE | REDUCE_50_PCT | SKIP>"
}}

=== ANALYTICAL EXAMPLES ===

Example 1 — Strong confluence (+8):
  Input: vwap=24358.42, distance_pct=-0.12, bias=BULLISH score=0.45, pcr=0.42, max_ce_oi_strike=24500, cpr type=NARROW, slope=0.08, reversal_quality=0.82, india_vix=13.5, rr_ratio=1.72
  Analysis: CE pullback to VWAP with NARROW CPR + positive VWAP slope + clean reversal + bullish bias = textbook VWAP pullback. PCR 0.42 confirms bearish put writing (supports CE). R:R 1.72 is above standard. All major factors aligned.
  Output: {{"confidence_adjustment": 8, "summary": "CE NIFTY pullback to VWAP 24358.42 (dist -0.12%); NARROW CPR + PCR 0.42 + R:R 1.72 — strong alignment.", ...}}

Example 2 — Counter-bias concern (-7):
  Input: vwap=51200.5, distance_pct=0.08, bias=BEARISH score=-0.38 strength=MODERATE, pcr=1.12, reversal_quality=0.45, rr_ratio=1.35
  Analysis: PE signal but bias is MODERATE BEARISH and reversal_quality only 0.45 — the pullback candle is unconvincing. R:R 1.35 is below standard 1.5. PCR 1.12 is neutral (not extreme). The weak reversal + low R:R together warrant caution.
  Output: {{"confidence_adjustment": -7, "summary": "PE BANKNIFTY near VWAP 51200.5 but weak reversal (0.45) and R:R 1.35 below standard.", "recommended_action": "PROCEED_WITH_CAUTION", ...}}

Example 3 — Value accuracy rule:
  Input has: vwap=24358.42, pdh=24400.5
  WRONG: "pullback to VWAP 24350 near PDH 24395" — 24350 and 24395 are fabricated.
  RIGHT: "pullback to VWAP 24358.42 near PDH 24400.5" — exact values from input.

=== DECISION GUIDE ===
- confidence_adjustment: -10 to +10 for most signals. Beyond ±20 requires two independent reasons.
- recommended_action: PROCEED if adjustment >= 0 and no major risk. PROCEED_WITH_CAUTION if -15 to 0 or one major risk. RECONSIDER if <= -15 or multiple major risks.
- suggested_lot_adjustment: REDUCE_50_PCT if VIX > 20 or R:R < 1.2 or strong counter-bias. SKIP if adjustment <= -20. NONE otherwise."""

_USER_PROMPT_TEMPLATE_INTRADAY_FUTURES = """=== {strategy_label} SIGNAL FOR REVIEW ===
Strategy: {strategy_name} | Setup: {setup_type}

{context_json}

=== REQUIRED OUTPUT ===

Produce a JSON object with EXACTLY these fields:

{{
  "confidence_adjustment": <integer -30 to +30>,
  "summary": "<one sentence ≤ 200 chars, lead with direction + symbol + setup type, cite 2+ EXACT values from input>",
  "rationale": "<3-5 sentences, each citing a specific value, last sentence = biggest risk>",
  "key_supports": ["<factor + EXACT value>", "<factor + EXACT value>"],
  "key_risks": ["<factor + EXACT value>", "<factor + EXACT value>"],
  "recommended_action": "<PROCEED | PROCEED_WITH_CAUTION | RECONSIDER>",
  "suggested_lot_adjustment": "<NONE | REDUCE_50_PCT | SKIP>"
}}

=== ANALYTICAL EXAMPLES ===

Example 1 — Textbook ORB (+12):
  Input: setup_type=ORB, rvol=3.2, enhanced_orb=true, phase=MORNING_ACTIVE, fut_oi_direction=long_buildup, fut_oi_change_pct=4.5, stock_trend_score=0.55, orb_high=318.5, pdh=316.2, vwap=315.42
  Analysis: Enhanced ORB (above both orb_high and PDH) in MORNING_ACTIVE with rvol 3.2 (excellent volume), long_buildup at 4.5% OI change, and aligned stock trend at 0.55. Three independent factors confirm: volume + OI + trend. This is a high-conviction setup.
  Output: {{"confidence_adjustment": 12, "summary": "LONG VEDL enhanced ORB above 318.5 (>PDH 316.2); rvol 3.2 + long_buildup 4.5% OI — textbook setup.", "recommended_action": "PROCEED", ...}}

Example 2 — Missing RVOL offset by strong volume (-3):
  Input: setup_type=PDH_PDL, rvol_factor=0.0, vol_factor=0.78, phase=AFTERNOON, fut_oi_direction=short_buildup, stock_trend_score=-0.42, pdl=285.3, vwap=288.6
  Analysis: rvol_factor=0.0 means no RVOL baseline exists — but vol_factor 0.78 shows breakout candle volume is strong vs recent average. AFTERNOON phase is late but PDH_PDL is structural and short_buildup confirms bearish OI. stock_trend_score -0.42 aligns with SHORT direction. The missing RVOL baseline is a minor data gap, not a red flag. Slight negative for late phase only.
  Output: {{"confidence_adjustment": -3, "summary": "SHORT below PDL 285.3 with short_buildup OI and vol_factor 0.78; AFTERNOON phase is only concern.", "recommended_action": "PROCEED_WITH_CAUTION", ...}}

Example 3 — Multiple red flags (-18):
  Input: setup_type=ORB, rvol=0.8, rvol_factor=0.0, vol_factor=0.15, phase=CAUTION_ZONE, stock_trend_score=0.65, stock_trend_strength=STRONG, direction=SELL_FUT, bias=BULLISH score=0.4
  Analysis: SHORT against STRONG BULLISH stock trend (score 0.65) is a deal-breaker on its own. Adding: rvol 0.8 below threshold (rvol_factor=0.0), vol_factor 0.15 (very weak volume), CAUTION_ZONE phase, and BULLISH Nifty bias opposing SHORT. Four independent negative factors.
  Output: {{"confidence_adjustment": -18, "summary": "SHORT fights STRONG BULLISH trend (0.65) with rvol 0.8 and vol_factor 0.15 in CAUTION_ZONE — multiple red flags.", "recommended_action": "RECONSIDER", "suggested_lot_adjustment": "SKIP", ...}}

Example 4 — Value accuracy rule:
  Input has: vwap=315.42, pdh=320.8, setup_type=ORB
  WRONG: "LONG breaking above VWAP 315 near PDH 321" — 315 and 321 are fabricated, and you must read setup_type from the data.
  RIGHT: "LONG ORB breaking above VWAP 315.42 near PDH 320.8" — exact values and correct setup_type.

=== DECISION GUIDE ===
- confidence_adjustment: -10 to +10 for most signals. Beyond ±20 requires two independent reasons.
- recommended_action: PROCEED if adjustment >= 0 and no major risk. PROCEED_WITH_CAUTION if -15 to 0 or one major risk. RECONSIDER if <= -15 or multiple major risks.
- suggested_lot_adjustment: REDUCE_50_PCT if VIX > 18 or rvol < 1.0 or strong counter-trend. SKIP if adjustment <= -20. NONE otherwise."""

_USER_PROMPT_TEMPLATES = {
    "vwap_pullback": _USER_PROMPT_TEMPLATE_VWAP_PULLBACK,
    "intraday_futures": _USER_PROMPT_TEMPLATE_INTRADAY_FUTURES,
}

_STRATEGY_LABELS = {
    "vwap_pullback": "VWAP PULLBACK",
    "intraday_futures": "INTRADAY FUTURES",
    "can_slim": "CAN SLIM",
    "orb": "ORB",
    "gamma_scalping": "GAMMA SCALPING",
}


_SIGNAL_CONFIDENCE_SCHEMA = {
    "type": "object",
    "properties": {
        "confidence_adjustment": {"type": "integer"},
        "summary": {"type": "string"},
        "rationale": {"type": "string"},
        "recommended_action": {"type": "string"},
        "key_supports": {"type": "array", "items": {"type": "string"}},
        "key_risks": {"type": "array", "items": {"type": "string"}},
        "suggested_lot_adjustment": {"type": "string"},
    },
    "required": [
        "confidence_adjustment", "summary", "rationale",
        "recommended_action", "key_supports", "key_risks",
    ],
}


async def _call_llm(context_json: str, strategy_name: str = "unknown", setup_type: str = "unknown") -> SignalConfidence:
    """Call Gemini and parse the structured response."""
    from app.research.llm_client import create_llm_client

    llm = create_llm_client()
    strategy_label = _STRATEGY_LABELS.get(strategy_name, strategy_name.upper().replace("_", " "))
    user_template = _USER_PROMPT_TEMPLATES.get(strategy_name, _USER_PROMPT_TEMPLATE_VWAP_PULLBACK)
    system_prompt = _SYSTEM_PROMPTS.get(strategy_name, _SYSTEM_PROMPT_VWAP_PULLBACK)
    prompt = user_template.format(
        context_json=context_json,
        strategy_label=strategy_label,
        strategy_name=strategy_name,
        setup_type=setup_type,
    )

    raw = await llm.generate_json(
        prompt=prompt,
        system=system_prompt,
        max_tokens=8192,
        response_schema=_SIGNAL_CONFIDENCE_SCHEMA,
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
