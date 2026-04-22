"""Fundamental analysis agent.

Analyzes earnings growth, profitability, financial health, and CAN SLIM scores.
Uses yfinance for raw data, existing CAN SLIM scoring functions, and LLM for interpretation.
"""

from __future__ import annotations

import logging

from app.data_sources import yfinance_client
from app.research.agents.base import AgentResult, BaseResearchAgent, ResearchContext
from app.research.llm_client import LLMClient

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are a fundamental analyst specializing in Indian equities (NSE/BSE).
Analyze the provided financial data and produce a concise assessment.
Focus on earnings quality, growth trajectory, profitability trends, and financial health.
Be specific with numbers. Identify both strengths and concerns."""


class FundamentalAgent(BaseResearchAgent):
    name = "fundamental"
    description = "Analyzing earnings, growth, and financial health"

    async def research(self, ctx: ResearchContext, llm: LLMClient) -> AgentResult:
        data_sources = []
        findings: dict = {}

        # 1. Fetch quarterly earnings
        quarterly = await yfinance_client.get_quarterly_earnings(ctx.symbol)
        if quarterly:
            data_sources.append("yfinance_quarterly_earnings")
            findings["quarterly_earnings"] = [
                {
                    "quarter_end": str(q.quarter_end),
                    "eps": q.eps,
                    "revenue_cr": round(q.revenue_cr, 2),
                    "yoy_eps_growth_pct": round(q.yoy_eps_growth_pct, 1) if q.yoy_eps_growth_pct else None,
                    "yoy_revenue_growth_pct": round(q.yoy_revenue_growth_pct, 1) if q.yoy_revenue_growth_pct else None,
                }
                for q in quarterly[:8]  # Last 8 quarters
            ]

        # 2. Fetch annual financials
        annual = await yfinance_client.get_annual_financials(ctx.symbol)
        if annual:
            data_sources.append("yfinance_annual_financials")
            findings["annual_financials"] = [
                {
                    "fiscal_year": a.fiscal_year,
                    "revenue_cr": round(a.revenue_cr, 2),
                    "roe_pct": round(a.roe_pct, 1) if a.roe_pct else None,
                    "operating_margin_pct": round(a.operating_margin_pct, 1) if a.operating_margin_pct else None,
                    "debt_to_equity": round(a.debt_to_equity, 2) if a.debt_to_equity else None,
                }
                for a in annual[:5]  # Last 5 years
            ]

        # 3. Compute CAN SLIM scores if we have data
        canslim_scores = {}
        if ctx.existing_fundamental:
            data_sources.append("stock_fundamentals_db")
            f = ctx.existing_fundamental
            canslim_scores = {
                "c_score": float(f.c_score) if f.c_score else None,
                "a_score": float(f.a_score) if f.a_score else None,
                "n_score": float(f.n_score) if f.n_score else None,
                "s_score": float(f.s_score) if f.s_score else None,
                "l_score": float(f.l_score) if f.l_score else None,
                "i_score": float(f.i_score) if f.i_score else None,
                "composite": float(f.canslim_score) if f.canslim_score else None,
            }
        elif quarterly and annual:
            # Compute on-the-fly from raw data
            canslim_scores = _compute_scores_on_the_fly(quarterly, annual, ctx)

        if canslim_scores:
            findings["canslim_scores"] = canslim_scores

        # 4. Stock info metrics
        if ctx.stock_info:
            findings["stock_info"] = {
                "market_cap_cr": ctx.stock_info.market_cap_cr,
                "free_float_pct": ctx.stock_info.free_float_pct,
                "fifty_two_week_high": ctx.stock_info.fifty_two_week_high,
                "fifty_two_week_low": ctx.stock_info.fifty_two_week_low,
                "current_price": ctx.stock_info.current_price,
            }

        if not findings:
            return AgentResult(
                agent_name=self.name,
                status="failed",
                findings={},
                summary="No fundamental data available for this stock.",
                data_sources=data_sources,
                error="No data from yfinance",
            )

        # 5. LLM interpretation
        prompt = f"""Analyze the fundamental data for {ctx.symbol} ({ctx.display_name}):

{_format_findings(findings)}

Provide a concise assessment covering:
1. Earnings quality and growth trajectory
2. Profitability trends (ROE, margins)
3. Financial health (debt levels)
4. Overall fundamental rating (Strong/Moderate/Weak)

Keep it under 200 words. Be specific with numbers."""

        summary = await llm.generate(prompt, system=SYSTEM_PROMPT, max_tokens=2048)

        return AgentResult(
            agent_name=self.name,
            status="completed",
            findings=findings,
            summary=summary,
            data_sources=data_sources,
        )


def _compute_scores_on_the_fly(quarterly, annual, ctx) -> dict:
    """Compute CAN SLIM factor scores from raw data."""
    try:
        from app.strategies.canslim.scoring import score_a, score_c, score_n, score_s

        scores = {}

        # C score
        if quarterly and quarterly[0].yoy_eps_growth_pct is not None:
            eps_accel = (
                len(quarterly) >= 2
                and quarterly[0].yoy_eps_growth_pct is not None
                and quarterly[1].yoy_eps_growth_pct is not None
                and quarterly[0].yoy_eps_growth_pct > quarterly[1].yoy_eps_growth_pct
            )
            scores["c_score"] = score_c(
                latest_qtr_eps_growth=quarterly[0].yoy_eps_growth_pct,
                latest_qtr_revenue_growth=quarterly[0].yoy_revenue_growth_pct,
                eps_accelerating=eps_accel,
            )

        # A score
        if annual:
            a = annual[0]
            scores["a_score"] = score_a(
                annual_eps_growth_3yr=None,  # Would need 3+ years
                roe=a.roe_pct,
                operating_margin=a.operating_margin_pct,
            )

        # N score
        if ctx.stock_info and ctx.stock_info.fifty_two_week_high and ctx.stock_info.current_price:
            pct_from_high = (
                (ctx.stock_info.fifty_two_week_high - ctx.stock_info.current_price)
                / ctx.stock_info.fifty_two_week_high * 100
            )
            scores["n_score"] = score_n(pct_from_high)

        # S score
        if annual and ctx.stock_info:
            scores["s_score"] = score_s(
                free_float_pct=ctx.stock_info.free_float_pct,
                volume_ratio=None,
                debt_to_equity=annual[0].debt_to_equity,
            )

        return scores
    except Exception:
        logger.debug("Could not compute CAN SLIM scores on-the-fly", exc_info=True)
        return {}


def _format_findings(findings: dict) -> str:
    """Format findings dict into readable text for LLM prompt."""
    parts = []

    if "quarterly_earnings" in findings:
        parts.append("QUARTERLY EARNINGS (most recent first):")
        for q in findings["quarterly_earnings"][:4]:
            parts.append(
                f"  {q['quarter_end']}: EPS={q['eps']:.2f}, "
                f"Revenue={q['revenue_cr']:.0f}cr, "
                f"EPS Growth={q['yoy_eps_growth_pct']}%, "
                f"Rev Growth={q['yoy_revenue_growth_pct']}%"
            )

    if "annual_financials" in findings:
        parts.append("\nANNUAL FINANCIALS:")
        for a in findings["annual_financials"][:3]:
            parts.append(
                f"  FY{a['fiscal_year']}: Revenue={a['revenue_cr']:.0f}cr, "
                f"ROE={a['roe_pct']}%, OPM={a['operating_margin_pct']}%, "
                f"D/E={a['debt_to_equity']}"
            )

    if "canslim_scores" in findings:
        cs = findings["canslim_scores"]
        parts.append(f"\nCAN SLIM SCORES: {cs}")

    if "stock_info" in findings:
        si = findings["stock_info"]
        parts.append(
            f"\nSTOCK INFO: MCap={si['market_cap_cr']}cr, "
            f"52wH={si['fifty_two_week_high']}, 52wL={si['fifty_two_week_low']}, "
            f"CMP={si['current_price']}"
        )

    return "\n".join(parts)
