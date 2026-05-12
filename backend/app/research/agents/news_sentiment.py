"""News & Sentiment analysis agent.

Uses Gemini's built-in Google Search grounding to fetch real-time Indian stock news.
LLM classifies sentiment and extracts key themes, catalysts, and risk events.
Returns both a summary AND individual news articles with source URLs.
"""

from __future__ import annotations

import json
import logging

from app.research.agents.base import AgentResult, BaseResearchAgent, ResearchContext
from app.research.llm_client import LLMClient

logger = logging.getLogger(__name__)

SEARCH_SYSTEM_PROMPT = """You are a financial news analyst specializing in Indian stock markets (NSE/BSE).
Search for and analyze recent news about the specified stock.
Focus on news from Indian financial media: MoneyControl, Economic Times, LiveMint, Business Standard, NDTV Profit, and NSE/BSE announcements.
Return factual, source-attributed information. Distinguish between stock-specific news (earnings, corporate actions, analyst calls) and general sector/industry commentary. Stock-specific events matter far more for trading decisions."""

ANALYSIS_PROMPT_TEMPLATE = """Search for the latest news and developments about {symbol} ({display_name}) stock in the Indian market.

Include:
1. Recent price movements and market reactions
2. Quarterly earnings announcements (if recent)
3. Corporate actions (dividends, splits, bonus, buybacks)
4. Management commentary or guidance changes
5. Sector-specific developments affecting this stock
6. Any regulatory or policy changes impacting the company
7. Analyst upgrades/downgrades (if any)

For each piece of news, note the source and approximate date.
Focus on news from the last 30 days. Be factual and specific."""

SENTIMENT_PROMPT_TEMPLATE = """Analyze the news below for {symbol} ({display_name}) and score its sentiment for INTRADAY TRADING impact.

NEWS AND DEVELOPMENTS:
{news_text}

SOURCES:
{sources_text}

## Scoring guide
Your sentiment_score directly adjusts this stock's ranking: score × 10 points are added to a composite quant score (typical range 30-90). A score of +0.8 adds +8 points — enough to jump several ranking positions. Be calibrated:

  +0.7 to +1.0: STRONG POSITIVE — stock-specific catalyst with clear price impact (earnings beat >10%, major contract win, analyst upgrade with >15% target raise, block deal by marquee institution). Requires a concrete, time-bound event.
  +0.3 to +0.6: MILD POSITIVE — supportive news but no single strong catalyst (steady results, minor positive commentary, sector tailwind). Most "positive" news falls here.
  -0.1 to +0.2: NEUTRAL — no material news, routine updates, or balanced mix of positive/negative. This is the DEFAULT when news is unremarkable. Use 0.0 when there is genuinely no news.
  -0.3 to -0.6: MILD NEGATIVE — concerning but not disqualifying (analyst downgrade, margin pressure, sector headwind, promoter pledge).
  -0.7 to -1.0: STRONG NEGATIVE — stock-specific red flag for today's trading (regulatory action, earnings miss >10%, fraud allegation, SEBI order, credit downgrade).

Common calibration errors to avoid:
  - Routine positive results (revenue up 5%, in-line with estimates) are NOT +0.8. They are +0.2 to +0.3.
  - General sector commentary ("IT sector outlook positive") is NOT stock-specific. Score 0.0 to +0.1.
  - A mix of positive and negative news is "mixed", NOT positive. Score near 0.0.
  - No significant news found = neutral, score 0.0. Do NOT default to positive.

## Event classification
risk_events: Only stock-specific, time-bound risks that could move price TODAY or THIS WEEK (earnings tonight, regulatory hearing, SEBI order). Do NOT include general industry trends.
catalyst_events: Only stock-specific, time-bound catalysts (dividend ex-date, contract award, results beat). Do NOT include vague sector optimism.

Respond in JSON:
{{
    "overall_sentiment": "positive" | "negative" | "neutral" | "mixed",
    "sentiment_score": <float -1.0 to 1.0, calibrated per the guide above>,
    "articles": [
        {{
            "headline": "<news headline>",
            "source": "<publication name>",
            "url": "<source URL if available, otherwise null>",
            "date": "<approximate date>",
            "sentiment": "positive" | "negative" | "neutral"
        }}
    ],
    "key_themes": ["<theme 1>", "<theme 2>"],
    "risk_events": ["<stock-specific risk only>"],
    "catalyst_events": ["<stock-specific catalyst only>"]
}}

Extract up to 7 articles. Include source URLs from the search results where available."""


class NewsSentimentAgent(BaseResearchAgent):
    name = "news_sentiment"
    description = "Searching for recent news and analyzing sentiment"

    async def research(self, ctx: ResearchContext, llm: LLMClient) -> AgentResult:
        data_sources = ["google_search_grounding"]

        # Step 1: Search for news using Gemini's grounded search
        search_prompt = ANALYSIS_PROMPT_TEMPLATE.format(
            symbol=ctx.symbol,
            display_name=ctx.display_name,
        )

        try:
            search_result = await llm.generate_with_search(
                search_prompt,
                system=SEARCH_SYSTEM_PROMPT,
                max_tokens=4096,
            )
        except Exception as e:
            logger.warning("News search failed for %s: %s", ctx.symbol, e)
            return AgentResult(
                agent_name=self.name,
                status="failed",
                findings={},
                summary="",
                error=f"News search failed: {e}",
                data_sources=data_sources,
            )

        news_text = search_result.text
        sources = search_result.sources

        if not news_text:
            return AgentResult(
                agent_name=self.name,
                status="partial",
                findings={"overall_sentiment": "neutral", "articles": []},
                summary="No recent news found for this stock.",
                data_sources=data_sources,
            )

        # Step 2: Structured sentiment analysis
        sources_text = "\n".join(
            f"- {s.get('title', 'Unknown')}: {s.get('url', 'N/A')}"
            for s in sources
        ) or "No source URLs available"

        sentiment_prompt = SENTIMENT_PROMPT_TEMPLATE.format(
            symbol=ctx.symbol,
            display_name=ctx.display_name,
            news_text=news_text,
            sources_text=sources_text,
        )

        try:
            findings = await llm.generate_json(sentiment_prompt, max_tokens=4096)
        except Exception:
            logger.debug("Sentiment JSON parsing failed, using text summary", exc_info=True)
            findings = {
                "overall_sentiment": "neutral",
                "articles": [],
                "raw_news": news_text[:2000],
            }

        # Ensure required keys exist
        findings.setdefault("overall_sentiment", "neutral")
        findings.setdefault("sentiment_score", 0.0)
        findings.setdefault("articles", [])
        findings.setdefault("key_themes", [])
        findings.setdefault("risk_events", [])
        findings.setdefault("catalyst_events", [])

        # Enrich articles with source URLs from grounding metadata
        if sources and findings["articles"]:
            _enrich_article_urls(findings["articles"], sources)

        # Generate summary from the structured data
        sentiment = findings["overall_sentiment"]
        score = findings.get("sentiment_score", 0)
        n_articles = len(findings["articles"])
        themes = ", ".join(findings.get("key_themes", [])[:3]) or "no specific themes"
        risks = findings.get("risk_events", [])
        catalysts = findings.get("catalyst_events", [])

        summary_parts = [
            f"News sentiment for {ctx.symbol} is {sentiment} (score: {score:+.1f}).",
            f"Based on {n_articles} recent articles. Key themes: {themes}.",
        ]
        if catalysts:
            summary_parts.append(f"Catalysts: {', '.join(catalysts[:2])}.")
        if risks:
            summary_parts.append(f"Risks: {', '.join(risks[:2])}.")

        return AgentResult(
            agent_name=self.name,
            status="completed",
            findings=findings,
            summary=" ".join(summary_parts),
            data_sources=data_sources,
        )


def _enrich_article_urls(articles: list[dict], sources: list[dict]) -> None:
    """Try to match articles with grounding source URLs."""
    for article in articles:
        if article.get("url"):
            continue
        # Try to match by source name or headline keywords
        headline = (article.get("headline") or "").lower()
        source_name = (article.get("source") or "").lower()
        for src in sources:
            title = (src.get("title") or "").lower()
            url = src.get("url", "")
            if source_name and source_name in title:
                article["url"] = url
                break
            elif headline and any(word in title for word in headline.split()[:3] if len(word) > 3):
                article["url"] = url
                break
