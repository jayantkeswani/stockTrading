"""Template-based report builder — fallback when LLM synthesis fails.

Assembles a readable report directly from agent findings without LLM.
"""

from __future__ import annotations

from app.research.agents.base import AgentResult, ResearchContext


def build_fallback_report(
    ctx: ResearchContext,
    agent_results: dict[str, AgentResult],
) -> str:
    """Build a markdown report from raw agent data (no LLM needed)."""
    lines = []
    lines.append(f"# {ctx.symbol} — {ctx.display_name}")
    lines.append(f"**Price:** Rs {ctx.current_price or 'N/A'} | "
                 f"**MCap:** Rs {ctx.market_cap_cr or 'N/A'} Cr")
    lines.append("")
    lines.append("*Note: AI synthesis was unavailable. Showing raw agent findings.*")
    lines.append("")

    for name in ["fundamental", "technical", "oi_derivatives",
                  "institutional", "news_sentiment", "valuation"]:
        result = agent_results.get(name)
        title = name.replace("_", " ").title()

        if not result or result.status == "failed":
            lines.append(f"## {title}")
            error = result.error if result else "Agent did not run"
            lines.append(f"*{error}*")
            lines.append("")
            continue

        lines.append(f"## {title}")
        if result.summary:
            lines.append(result.summary)
        lines.append("")

    return "\n".join(lines)
