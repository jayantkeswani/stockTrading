"""Open Interest & Derivatives analysis agent.

Analyzes option chain OI, PCR ratio, max pain, and derivatives sentiment.
Only runs for F&O-eligible stocks.
"""

from __future__ import annotations

import logging

from app.research.agents.base import AgentResult, BaseResearchAgent, ResearchContext
from app.research.llm_client import LLMClient

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are a derivatives analyst specializing in Indian F&O markets (NSE).
Analyze the provided open interest data and produce a concise assessment.
Focus on PCR ratio, max pain level, OI buildup at key strikes, and what it implies for price direction.
Be specific with strike prices and OI numbers."""


class OIDerivativesAgent(BaseResearchAgent):
    name = "oi_derivatives"
    description = "Analyzing open interest and derivatives data"

    async def research(self, ctx: ResearchContext, llm: LLMClient) -> AgentResult:
        """Fetch Fyers option chain OI for F&O-eligible stocks and analyze PCR/max pain via LLM."""
        # Skip for non-F&O stocks
        if not ctx.is_fo_eligible:
            return AgentResult(
                agent_name=self.name,
                status="completed",
                findings={"available": False, "reason": "Stock is not F&O eligible"},
                summary="Open interest analysis is not available — this stock is not traded in the F&O segment.",
                data_sources=[],
            )

        data_sources = []
        findings: dict = {"available": True}

        # Fetch option chain from Fyers
        try:
            from app.core.redis import get_redis
            from app.data_feed.fyers_client import FyersClient

            r = get_redis()
            token = await r.get("fyers:access_token")

            if not token:
                return AgentResult(
                    agent_name=self.name,
                    status="partial",
                    findings={"available": True, "reason": "Fyers not authenticated"},
                    summary="Could not fetch OI data — Fyers authentication required.",
                    data_sources=[],
                )

            token_str = token.decode() if isinstance(token, bytes) else token
            client = FyersClient(access_token=token_str)
            try:
                # Use the Fyers-qualified symbol (NSE:ADANIPORTS-EQ)
                # get_option_chain only resolves indices via FYERS_SYMBOL_MAP, not stocks
                oc_symbol = ctx.fyers_symbol or f"NSE:{ctx.symbol}-EQ"
                oc_data = await client.get_option_chain(oc_symbol)
                data_sources.append("fyers_option_chain_v3")
            finally:
                await client.close()

            if not oc_data or not oc_data.get("data"):
                return AgentResult(
                    agent_name=self.name,
                    status="partial",
                    findings={"available": True, "reason": "No option chain data returned"},
                    summary="Option chain data unavailable from Fyers for this stock.",
                    data_sources=data_sources,
                )

            # Parse option chain — data.optionsChain is the list of strikes
            chain_data = oc_data["data"]
            option_rows = chain_data.get("optionsChain", []) if isinstance(chain_data, dict) else chain_data
            strikes = _parse_option_chain(option_rows)
            if not strikes:
                return AgentResult(
                    agent_name=self.name,
                    status="partial",
                    findings={"available": True, "reason": "Could not parse option chain"},
                    summary="Option chain data could not be parsed.",
                    data_sources=data_sources,
                )

            # Analyze using existing OI indicator functions
            from app.indicators.open_interest import analyze_option_chain

            analysis = analyze_option_chain(strikes)

            if analysis:
                findings.update({
                    "pcr": round(analysis.pcr, 2),
                    "max_pain": analysis.max_pain,
                    "sentiment": analysis.sentiment,
                    "total_call_oi": analysis.total_ce_oi,
                    "total_put_oi": analysis.total_pe_oi,
                    "oi_resistance": analysis.max_ce_oi_strike,
                    "oi_support": analysis.max_pe_oi_strike,
                })

            # Top OI strikes
            findings["top_call_oi_strikes"] = _top_oi_strikes(strikes, "CE", limit=5)
            findings["top_put_oi_strikes"] = _top_oi_strikes(strikes, "PE", limit=5)

        except Exception as e:
            logger.warning("OI analysis failed for %s: %s", ctx.symbol, e)
            return AgentResult(
                agent_name=self.name,
                status="failed",
                findings={"available": True},
                summary="",
                error=str(e),
                data_sources=data_sources,
            )

        # LLM interpretation
        prompt = f"""Analyze the derivatives data for {ctx.symbol} at Rs {ctx.current_price or 'N/A'}:

PCR Ratio: {findings.get('pcr')}
Max Pain: {findings.get('max_pain')}
Sentiment: {findings.get('sentiment')}
Total Call OI: {findings.get('total_call_oi')}, Total Put OI: {findings.get('total_put_oi')}

Top Call OI Strikes (resistance): {findings.get('top_call_oi_strikes')}
Top Put OI Strikes (support): {findings.get('top_put_oi_strikes')}

OI-based Support: {findings.get('oi_support')}
OI-based Resistance: {findings.get('oi_resistance')}

Provide a concise derivatives assessment:
1. What does the PCR ratio suggest about sentiment?
2. Where is max pain and what does it mean for near-term price?
3. Key OI walls (support/resistance from heavy OI strikes)
4. Overall derivatives outlook (bullish/bearish/neutral)

Keep it under 150 words."""

        summary = await llm.generate(prompt, system=SYSTEM_PROMPT, max_tokens=2048)

        return AgentResult(
            agent_name=self.name,
            status="completed",
            findings=findings,
            summary=summary,
            data_sources=data_sources,
        )


def _parse_option_chain(rows: list[dict]) -> list[dict]:
    """Parse Fyers v3 option chain rows into strikes list for analyze_option_chain.

    Fyers v3 keys: strike_price, option_type ("CE"/"PE"/""), oi, oich, volume, ltp.
    The first row (option_type="", strike_price=-1) is the underlying — skip it.
    """
    by_strike: dict[float, dict] = {}
    for row in rows:
        strike = row.get("strike_price")
        if strike is None or strike < 0:
            continue  # Skip underlying row (strike_price=-1)
        strike = float(strike)

        if strike not in by_strike:
            # Keys match what analyze_option_chain expects: strike_price, ce_oi, pe_oi
            by_strike[strike] = {"strike_price": strike, "ce_oi": 0, "pe_oi": 0}

        opt_type = row.get("option_type", "")
        oi = int(row.get("oi", 0) or 0)

        if opt_type == "CE":
            by_strike[strike]["ce_oi"] = oi
        elif opt_type == "PE":
            by_strike[strike]["pe_oi"] = oi

    strikes = list(by_strike.values())
    strikes.sort(key=lambda s: s["strike_price"])
    return strikes


def _top_oi_strikes(strikes: list[dict], opt_type: str, limit: int = 5) -> list[dict]:
    """Get top N strikes by OI for calls or puts."""
    key = "ce_oi" if opt_type == "CE" else "pe_oi"
    sorted_strikes = sorted(strikes, key=lambda s: s.get(key, 0), reverse=True)
    return [
        {"strike": s["strike_price"], "oi": s.get(key, 0)}
        for s in sorted_strikes[:limit]
        if s.get(key, 0) > 0
    ]
