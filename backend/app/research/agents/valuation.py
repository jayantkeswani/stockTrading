"""Valuation analysis agent.

Analyzes PE, PB, PEG ratios, dividend yield, and relative valuation vs sector.
Uses yfinance for data and LLM for interpretation.
"""

from __future__ import annotations

import logging

from app.data_sources import yfinance_client
from app.research.agents.base import AgentResult, BaseResearchAgent, ResearchContext
from app.research.llm_client import LLMClient

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are a valuation analyst specializing in Indian equities.
Analyze the provided valuation metrics and assess whether the stock is undervalued, fairly valued, or overvalued.
Compare against sector averages where available. Be specific with numbers.
Consider both absolute and relative valuation."""


class ValuationAgent(BaseResearchAgent):
    name = "valuation"
    description = "Analyzing valuation metrics and fair value"

    async def research(self, ctx: ResearchContext, llm: LLMClient) -> AgentResult:
        data_sources = []
        findings: dict = {}

        # 1. Get detailed stock info (has PE, PB, dividend yield, sector, etc.)
        info = ctx.stock_info
        if not info:
            info = await yfinance_client.get_stock_info(ctx.symbol)

        if not info:
            return AgentResult(
                agent_name=self.name,
                status="failed",
                findings={},
                summary="No valuation data available.",
                error="yfinance returned no stock info",
            )

        data_sources.append("yfinance_stock_info")

        # 2. Get extended info via yfinance for valuation metrics
        try:
            import asyncio
            import yfinance as yf

            ticker = f"{ctx.symbol}.NS"
            stock_data = await asyncio.to_thread(lambda: yf.Ticker(ticker).info)

            if stock_data:
                data_sources.append("yfinance_detailed_info")

                findings["pe_ratio"] = stock_data.get("trailingPE")
                findings["forward_pe"] = stock_data.get("forwardPE")
                findings["pb_ratio"] = stock_data.get("priceToBook")
                findings["peg_ratio"] = stock_data.get("pegRatio")
                findings["dividend_yield_pct"] = (
                    round(stock_data.get("dividendYield", 0) * 100, 2)
                    if stock_data.get("dividendYield")
                    else None
                )
                findings["ev_to_ebitda"] = stock_data.get("enterpriseToEbitda")
                findings["ev_to_revenue"] = stock_data.get("enterpriseToRevenue")
                findings["price_to_sales"] = stock_data.get("priceToSalesTrailing12Months")

                # Sector and industry
                findings["sector"] = stock_data.get("sector")
                findings["industry"] = stock_data.get("industry")

                # Market cap
                market_cap = stock_data.get("marketCap")
                if market_cap:
                    findings["market_cap_cr"] = round(market_cap / 1e7, 2)

                # Book value
                findings["book_value"] = stock_data.get("bookValue")

                # Earnings growth
                findings["earnings_growth_pct"] = (
                    round(stock_data.get("earningsGrowth", 0) * 100, 1)
                    if stock_data.get("earningsGrowth")
                    else None
                )
                findings["revenue_growth_pct"] = (
                    round(stock_data.get("revenueGrowth", 0) * 100, 1)
                    if stock_data.get("revenueGrowth")
                    else None
                )

                # 52-week range
                findings["fifty_two_week_high"] = stock_data.get("fiftyTwoWeekHigh")
                findings["fifty_two_week_low"] = stock_data.get("fiftyTwoWeekLow")
                findings["current_price"] = ctx.current_price

                # Sector PE for comparison (use industry average if available)
                findings["sector_pe"] = stock_data.get("industryPE") or stock_data.get("sectorPE")

        except Exception:
            logger.debug("Extended yfinance info fetch failed", exc_info=True)

        if not findings:
            return AgentResult(
                agent_name=self.name,
                status="failed",
                findings={},
                summary="Could not retrieve valuation metrics.",
                error="No valuation data available",
            )

        # Clean up None values for cleaner output
        findings = {k: v for k, v in findings.items() if v is not None}

        # 3. LLM valuation assessment
        prompt = f"""Analyze the valuation metrics for {ctx.symbol} ({ctx.display_name}):

{_format_findings(findings)}

Provide a concise valuation assessment:
1. Is the stock expensive, fairly valued, or cheap based on PE/PB/PEG?
2. How does it compare to its sector (if sector PE available)?
3. Dividend yield attractiveness
4. Overall valuation verdict (Undervalued / Fair Value / Overvalued)
5. Estimated fair value range (if possible based on available metrics)

Keep it under 150 words. Be specific with numbers."""

        summary = await llm.generate(prompt, system=SYSTEM_PROMPT, max_tokens=2048)

        return AgentResult(
            agent_name=self.name,
            status="completed",
            findings=findings,
            summary=summary,
            data_sources=data_sources,
        )


def _format_findings(findings: dict) -> str:
    parts = []

    # Valuation ratios
    ratios = []
    if "pe_ratio" in findings:
        ratios.append(f"Trailing PE={findings['pe_ratio']:.1f}")
    if "forward_pe" in findings:
        ratios.append(f"Forward PE={findings['forward_pe']:.1f}")
    if "pb_ratio" in findings:
        ratios.append(f"P/B={findings['pb_ratio']:.2f}")
    if "peg_ratio" in findings:
        ratios.append(f"PEG={findings['peg_ratio']:.2f}")
    if ratios:
        parts.append(f"VALUATION RATIOS: {', '.join(ratios)}")

    if "sector" in findings:
        parts.append(f"SECTOR: {findings['sector']} / {findings.get('industry', 'N/A')}")
    if "sector_pe" in findings:
        parts.append(f"SECTOR PE: {findings['sector_pe']}")

    if "ev_to_ebitda" in findings:
        parts.append(f"EV/EBITDA: {findings['ev_to_ebitda']:.1f}")
    if "price_to_sales" in findings:
        parts.append(f"P/S: {findings['price_to_sales']:.2f}")

    if "dividend_yield_pct" in findings:
        parts.append(f"DIVIDEND YIELD: {findings['dividend_yield_pct']:.2f}%")

    if "market_cap_cr" in findings:
        parts.append(f"MARKET CAP: Rs {findings['market_cap_cr']:.0f} Cr")

    if "earnings_growth_pct" in findings:
        parts.append(f"EARNINGS GROWTH: {findings['earnings_growth_pct']:.1f}%")

    if "current_price" in findings:
        parts.append(
            f"PRICE: Rs {findings['current_price']}, "
            f"52W Range: {findings.get('fifty_two_week_low')}-{findings.get('fifty_two_week_high')}"
        )

    return "\n".join(parts)
