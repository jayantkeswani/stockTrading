"""Institutional activity analysis agent.

Analyzes FII/DII/MF shareholding patterns and QoQ changes.
Uses NSE client for live data and fundamental_history table for trends.
"""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.data_sources import nse_client
from app.models.fundamental_data import FundamentalHistory
from app.research.agents.base import AgentResult, BaseResearchAgent, ResearchContext
from app.research.llm_client import LLMClient

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are an institutional flow analyst specializing in Indian equities.
Analyze FII, DII, and mutual fund shareholding patterns.
Focus on whether institutional investors are accumulating or distributing.
Note promoter pledge levels as a risk factor. Be specific with percentages."""


class InstitutionalAgent(BaseResearchAgent):
    name = "institutional"
    description = "Analyzing FII/DII/MF shareholding trends"

    async def research(self, ctx: ResearchContext, llm: LLMClient) -> AgentResult:
        data_sources = []
        findings: dict = {}

        # 1. Fetch latest shareholding from NSE
        shareholding = await nse_client.get_shareholding_pattern(ctx.symbol)
        if shareholding:
            data_sources.append("nse_shareholding_api")
            findings["shareholding_pattern"] = [
                {
                    "quarter_end": str(s.quarter_end),
                    "promoter_pct": s.promoter_pct,
                    "fii_pct": s.fii_pct,
                    "dii_pct": s.dii_pct,
                    "mf_pct": s.mf_pct,
                    "public_pct": s.public_pct,
                    "pledge_pct": s.pledge_pct,
                }
                for s in shareholding[:4]  # Last 4 quarters
            ]

            # Compute QoQ changes
            if len(shareholding) >= 2:
                latest = shareholding[0]
                prev = shareholding[1]
                findings["qoq_changes"] = {
                    "fii_change": round(latest.fii_pct - prev.fii_pct, 2),
                    "mf_change": round(latest.mf_pct - prev.mf_pct, 2),
                    "promoter_change": round(latest.promoter_pct - prev.promoter_pct, 2),
                    "dii_change": round(latest.dii_pct - prev.dii_pct, 2),
                }

                # Determine conviction
                fii_up = latest.fii_pct > prev.fii_pct
                mf_up = latest.mf_pct > prev.mf_pct
                if fii_up and mf_up:
                    conviction = "ACCUMULATING"
                elif not fii_up and not mf_up:
                    conviction = "DISTRIBUTING"
                else:
                    conviction = "MIXED"
                findings["institutional_conviction"] = conviction

            # Promoter pledge warning
            if shareholding[0].pledge_pct and shareholding[0].pledge_pct > 5:
                findings["promoter_pledge_warning"] = True
                findings["promoter_pledge_pct"] = shareholding[0].pledge_pct

        # 2. Check fundamental_history for longer trend
        if ctx.existing_fundamental:
            data_sources.append("fundamental_history_db")
            findings["existing_data"] = {
                "fii_pct": float(ctx.existing_fundamental.fii_pct) if ctx.existing_fundamental.fii_pct else None,
                "mf_pct": float(ctx.existing_fundamental.mf_pct) if ctx.existing_fundamental.mf_pct else None,
                "promoter_pct": float(ctx.existing_fundamental.promoter_pct) if ctx.existing_fundamental.promoter_pct else None,
            }

        if not findings:
            return AgentResult(
                agent_name=self.name,
                status="failed",
                findings={},
                summary="No institutional data available for this stock.",
                error="NSE shareholding API returned no data",
            )

        # LLM interpretation
        prompt = f"""Analyze the institutional shareholding data for {ctx.symbol}:

{_format_findings(findings)}

Provide a concise institutional assessment:
1. Are FIIs accumulating or reducing?
2. Are mutual funds building positions?
3. Promoter holding trend and pledge level (risk factor?)
4. Overall institutional conviction (strong/moderate/weak)

Keep it under 150 words."""

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

    if "shareholding_pattern" in findings:
        parts.append("SHAREHOLDING PATTERN (quarterly, most recent first):")
        for s in findings["shareholding_pattern"]:
            parts.append(
                f"  {s['quarter_end']}: Promoter={s['promoter_pct']}%, "
                f"FII={s['fii_pct']}%, MF={s['mf_pct']}%, "
                f"DII={s['dii_pct']}%, Public={s['public_pct']}%"
                + (f", Pledge={s['pledge_pct']}%" if s.get('pledge_pct') else "")
            )

    if "qoq_changes" in findings:
        c = findings["qoq_changes"]
        parts.append(
            f"\nQoQ CHANGES: FII={c['fii_change']:+.2f}%, "
            f"MF={c['mf_change']:+.2f}%, Promoter={c['promoter_change']:+.2f}%"
        )

    if "institutional_conviction" in findings:
        parts.append(f"CONVICTION: {findings['institutional_conviction']}")

    if findings.get("promoter_pledge_warning"):
        parts.append(f"WARNING: Promoter pledge at {findings['promoter_pledge_pct']}%")

    return "\n".join(parts)
