"""Synthesis agent — combines all sub-agent findings into final report.

Runs after all other agents complete. Uses LLM to produce:
- Executive summary
- BUY/HOLD/SELL/AVOID recommendation with confidence score
- Actionable levels (entry, SL, targets)
- Long-term and short-term outlook
"""

from __future__ import annotations

import json
import logging

from app.research.agents.base import AgentResult, ResearchContext
from app.research.llm_client import LLMClient

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are a CFA-level equity research analyst at a top Indian brokerage.
You are writing a comprehensive research report for a stock.
You have received analysis from 6 specialized research teams (fundamental, technical, derivatives, institutional, news/sentiment, valuation).
Your job is to synthesize ALL findings into a single actionable report.

Rules:
- Be objective and balanced — weigh both bull and bear cases
- If different analyses conflict (e.g., fundamentals are strong but technicals are weak), explicitly note the conflict
- Confidence score should reflect the degree of agreement across analyses
- Entry/exit levels must be specific price ranges, not vague
- Risk-reward ratio must be realistic
- For Indian markets, consider: market hours 9:15-15:30 IST, F&O expiry cycles, FII/DII flow patterns
- Amounts in Rs (INR), use Indian number system (lakhs, crores)"""

SYNTHESIS_PROMPT = """Based on the following research from 6 specialized analysts for {symbol} ({display_name}) at Rs {current_price}:

{agent_summaries}

DETAILED DATA:
{agent_findings_summary}

Produce a comprehensive research report in the following JSON format:
{{
    "executive_summary": "<3-4 sentence summary of the investment thesis>",
    "recommendation": "BUY" | "HOLD" | "SELL" | "AVOID",
    "confidence_score": <0-100>,
    "long_term_outlook": {{
        "timeframe": "1-3 years",
        "assessment": "<2-3 sentences on long-term investment potential>",
        "suitability": "Suitable for long-term" | "Monitor for entry" | "Not suitable for long-term"
    }},
    "short_term_opportunity": {{
        "timeframe": "<e.g., 2-4 weeks>",
        "entry_zone_low": <price>,
        "entry_zone_high": <price>,
        "stop_loss": <price>,
        "target_1": <price>,
        "target_2": <price>,
        "risk_reward_ratio": "<e.g., 1:1.5>"
    }},
    "key_risks": ["<risk 1>", "<risk 2>", "<risk 3>"],
    "key_catalysts": ["<catalyst 1>", "<catalyst 2>", "<catalyst 3>"],
    "conflicts_noted": "<where different analyses disagree, if any>",
    "section_verdicts": {{
        "fundamental": "<one-line verdict>",
        "technical": "<one-line verdict>",
        "oi_derivatives": "<one-line verdict>",
        "institutional": "<one-line verdict>",
        "news_sentiment": "<one-line verdict>",
        "valuation": "<one-line verdict>"
    }}
}}

Be specific with all price levels. The entry/SL/target must be realistic based on the technical and OI data."""


async def synthesize_report(
    ctx: ResearchContext,
    agent_results: dict[str, AgentResult],
    llm: LLMClient,
) -> dict:
    """Synthesize all agent findings into a final report.

    Returns dict with keys: executive_summary, recommendation, confidence_score,
    report_json (full structured data), report_markdown.
    """
    # Build agent summaries
    summaries_parts = []
    findings_parts = []

    agent_order = [
        "fundamental", "technical", "oi_derivatives",
        "institutional", "news_sentiment", "valuation",
    ]

    for name in agent_order:
        result = agent_results.get(name)
        if not result:
            summaries_parts.append(f"**{name.upper()}**: No data (agent did not run)")
            continue

        if result.status == "failed":
            summaries_parts.append(f"**{name.upper()}**: Failed — {result.error or 'unknown error'}")
            continue

        summaries_parts.append(f"**{name.upper()}**: {result.summary}")

        # Include key findings (truncated)
        if result.findings:
            findings_str = json.dumps(result.findings, default=str)
            if len(findings_str) > 1500:
                findings_str = findings_str[:1500] + "..."
            findings_parts.append(f"{name}: {findings_str}")

    prompt = SYNTHESIS_PROMPT.format(
        symbol=ctx.symbol,
        display_name=ctx.display_name,
        current_price=ctx.current_price or "N/A",
        agent_summaries="\n\n".join(summaries_parts),
        agent_findings_summary="\n\n".join(findings_parts),
    )

    # Generate structured report
    try:
        report_json = await llm.generate_json(prompt, system=SYSTEM_PROMPT, max_tokens=8192)
    except Exception as e:
        logger.warning("Synthesis JSON generation failed: %s", e)
        report_json = _fallback_report(ctx, agent_results)

    # Generate markdown narrative
    report_markdown = _build_markdown(ctx, report_json, agent_results)

    return {
        "executive_summary": report_json.get("executive_summary", ""),
        "recommendation": report_json.get("recommendation", "HOLD"),
        "confidence_score": report_json.get("confidence_score", 0),
        "report_json": report_json,
        "report_markdown": report_markdown,
    }


def _fallback_report(ctx: ResearchContext, agent_results: dict[str, AgentResult]) -> dict:
    """Template-based fallback when LLM synthesis fails."""
    completed = sum(1 for r in agent_results.values() if r.status == "completed")
    total = max(len(agent_results), 1)
    # Confidence proportional to agent success: 0 agents = 0, all agents = 50 (capped — no LLM synthesis)
    confidence = round((completed / total) * 50)

    return {
        "executive_summary": f"Research report for {ctx.symbol}. LLM synthesis unavailable — see individual agent findings below.",
        "recommendation": "HOLD" if completed > 0 else "AVOID",
        "confidence_score": confidence,
        "long_term_outlook": {"timeframe": "N/A", "assessment": "Insufficient synthesis", "suitability": "Monitor for entry"},
        "short_term_opportunity": {},
        "key_risks": ["LLM synthesis failed — review raw data"],
        "key_catalysts": [],
        "conflicts_noted": "Unable to synthesize — partial data",
        "section_verdicts": {
            name: result.summary[:100] if result.status == "completed" else "Failed"
            for name, result in agent_results.items()
        },
    }


def _build_markdown(
    ctx: ResearchContext,
    report_json: dict,
    agent_results: dict[str, AgentResult],
) -> str:
    """Build a markdown narrative from the structured report."""
    lines = []
    lines.append(f"# {ctx.symbol} — {ctx.display_name}")
    lines.append(f"**Recommendation: {report_json.get('recommendation', 'N/A')}** | "
                 f"Confidence: {report_json.get('confidence_score', 'N/A')}/100 | "
                 f"Price: Rs {ctx.current_price or 'N/A'}")
    lines.append("")

    # Executive summary
    lines.append("## Executive Summary")
    lines.append(report_json.get("executive_summary", "N/A"))
    lines.append("")

    # Actionable levels
    short = report_json.get("short_term_opportunity", {})
    if short and short.get("entry_zone_low"):
        lines.append("## Actionable Levels")
        lines.append(f"- **Entry Zone:** Rs {short.get('entry_zone_low')} - Rs {short.get('entry_zone_high')}")
        lines.append(f"- **Stop Loss:** Rs {short.get('stop_loss')}")
        lines.append(f"- **Target 1:** Rs {short.get('target_1')}")
        lines.append(f"- **Target 2:** Rs {short.get('target_2')}")
        lines.append(f"- **R:R Ratio:** {short.get('risk_reward_ratio')}")
        lines.append(f"- **Timeframe:** {short.get('timeframe')}")
        lines.append("")

    # Long-term outlook
    long_term = report_json.get("long_term_outlook", {})
    if long_term:
        lines.append("## Long-Term Outlook")
        lines.append(f"**{long_term.get('suitability', 'N/A')}** ({long_term.get('timeframe', '')})")
        lines.append(long_term.get("assessment", ""))
        lines.append("")

    # Section verdicts
    verdicts = report_json.get("section_verdicts", {})
    if verdicts:
        lines.append("## Analysis Summary")
        for section, verdict in verdicts.items():
            lines.append(f"- **{section.replace('_', ' ').title()}:** {verdict}")
        lines.append("")

    # Risks and catalysts
    risks = report_json.get("key_risks", [])
    if risks:
        lines.append("## Key Risks")
        for r in risks:
            lines.append(f"- {r}")
        lines.append("")

    catalysts = report_json.get("key_catalysts", [])
    if catalysts:
        lines.append("## Key Catalysts")
        for c in catalysts:
            lines.append(f"- {c}")
        lines.append("")

    # Conflicts
    conflicts = report_json.get("conflicts_noted")
    if conflicts:
        lines.append("## Conflicts Noted")
        lines.append(conflicts)
        lines.append("")

    return "\n".join(lines)
