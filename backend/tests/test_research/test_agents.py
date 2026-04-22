"""Tests for research sub-agents — each agent with mocked data sources and LLM."""

import pytest
from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

from app.data_sources.schemas import PriceHistory, StockInfo
from app.research.agents.base import AgentResult, ResearchContext


def _make_context(
    symbol: str = "TCS",
    display_name: str = "Tata Consultancy Services",
    current_price: float = 3500.0,
    is_fo: bool = True,
    with_prices: bool = True,
    with_fundamental: bool = False,
) -> ResearchContext:
    """Build a test ResearchContext."""
    prices = []
    if with_prices:
        # Generate 250 daily bars
        for i in range(250):
            d = date(2025, 1, 1)
            prices.append(PriceHistory(
                date=d,
                open=3400 + i * 0.5,
                high=3450 + i * 0.5,
                low=3380 + i * 0.5,
                close=3400 + i * 0.5,
                volume=1_000_000 + i * 10_000,
            ))

    fundamental = None
    if with_fundamental:
        fundamental = MagicMock()
        fundamental.c_score = 75.0
        fundamental.a_score = 80.0
        fundamental.n_score = 60.0
        fundamental.s_score = 65.0
        fundamental.l_score = 70.0
        fundamental.i_score = 55.0
        fundamental.canslim_score = 68.0
        fundamental.relative_strength_rating = 85.0
        fundamental.is_fo_eligible = is_fo
        fundamental.fii_pct = 25.0
        fundamental.mf_pct = 10.0
        fundamental.promoter_pct = 40.0

    return ResearchContext(
        symbol=symbol,
        display_name=display_name,
        stock_info=StockInfo(
            symbol=symbol,
            market_cap_cr=1200000.0,
            free_float_pct=55.0,
            fifty_two_week_high=3800.0,
            fifty_two_week_low=2900.0,
            current_price=current_price,
        ),
        price_history_1y=prices,
        current_price=current_price,
        market_cap_cr=1200000.0,
        is_fo_eligible=is_fo,
        existing_fundamental=fundamental,
        fyers_symbol="NSE:TCS-EQ",
    )


def _make_mock_llm() -> AsyncMock:
    """Build a mock LLM client that returns canned responses."""
    llm = AsyncMock()
    llm.generate = AsyncMock(return_value="This is a test analysis summary.")
    llm.generate_json = AsyncMock(return_value={
        "overall_sentiment": "positive",
        "sentiment_score": 0.7,
        "articles": [{"headline": "Test news", "source": "Test", "sentiment": "positive"}],
        "key_themes": ["growth"],
        "risk_events": [],
        "catalyst_events": ["earnings"],
    })
    llm.generate_with_search = AsyncMock(return_value=MagicMock(
        text="Recent news about TCS shows positive sentiment.",
        search_queries=["TCS stock news"],
        sources=[{"url": "https://example.com", "title": "TCS News"}],
    ))
    return llm


# ---------------------------------------------------------------------------
# FundamentalAgent
# ---------------------------------------------------------------------------


class TestFundamentalAgent:
    @pytest.mark.asyncio
    @patch("app.research.agents.fundamental.yfinance_client")
    async def test_returns_completed_with_data(self, mock_yf):
        from app.data_sources.schemas import QuarterlyEarnings, AnnualFinancials
        from app.research.agents.fundamental import FundamentalAgent

        mock_yf.get_quarterly_earnings = AsyncMock(return_value=[
            QuarterlyEarnings(quarter_end=date(2025, 3, 31), eps=45.0, revenue_cr=60000,
                              yoy_eps_growth_pct=25.0, yoy_revenue_growth_pct=15.0),
            QuarterlyEarnings(quarter_end=date(2024, 12, 31), eps=42.0, revenue_cr=58000,
                              yoy_eps_growth_pct=20.0, yoy_revenue_growth_pct=12.0),
        ])
        mock_yf.get_annual_financials = AsyncMock(return_value=[
            AnnualFinancials(fiscal_year=2025, eps=170, revenue_cr=230000,
                             roe_pct=45.0, operating_margin_pct=28.0, debt_to_equity=0.1),
        ])

        agent = FundamentalAgent()
        ctx = _make_context()
        llm = _make_mock_llm()

        result = await agent.research(ctx, llm)

        assert result.agent_name == "fundamental"
        assert result.status == "completed"
        assert "quarterly_earnings" in result.findings
        assert "annual_financials" in result.findings
        assert len(result.data_sources) > 0
        assert result.summary != ""
        llm.generate.assert_called_once()

    @pytest.mark.asyncio
    @patch("app.research.agents.fundamental.yfinance_client")
    async def test_returns_failed_with_no_data(self, mock_yf):
        from app.research.agents.fundamental import FundamentalAgent

        mock_yf.get_quarterly_earnings = AsyncMock(return_value=[])
        mock_yf.get_annual_financials = AsyncMock(return_value=[])

        agent = FundamentalAgent()
        ctx = _make_context()
        ctx.stock_info = None
        ctx.existing_fundamental = None
        llm = _make_mock_llm()

        result = await agent.research(ctx, llm)

        assert result.status == "failed"
        assert result.error is not None

    @pytest.mark.asyncio
    @patch("app.research.agents.fundamental.yfinance_client")
    async def test_uses_existing_canslim_scores(self, mock_yf):
        from app.research.agents.fundamental import FundamentalAgent

        mock_yf.get_quarterly_earnings = AsyncMock(return_value=[])
        mock_yf.get_annual_financials = AsyncMock(return_value=[])

        agent = FundamentalAgent()
        ctx = _make_context(with_fundamental=True)
        llm = _make_mock_llm()

        result = await agent.research(ctx, llm)

        assert result.status == "completed"
        assert "canslim_scores" in result.findings
        assert result.findings["canslim_scores"]["c_score"] == 75.0


# ---------------------------------------------------------------------------
# TechnicalAgent
# ---------------------------------------------------------------------------


class TestTechnicalAgent:
    @pytest.mark.asyncio
    async def test_returns_completed_with_prices(self):
        from app.research.agents.technical import TechnicalAgent

        agent = TechnicalAgent()
        ctx = _make_context()
        llm = _make_mock_llm()

        result = await agent.research(ctx, llm)

        assert result.agent_name == "technical"
        assert result.status == "completed"
        assert "trend_direction" in result.findings
        assert "moving_averages" in result.findings
        assert "support_levels" in result.findings
        assert "resistance_levels" in result.findings
        assert "rsi_14" in result.findings
        assert "performance" in result.findings
        llm.generate.assert_called_once()

    @pytest.mark.asyncio
    async def test_returns_failed_without_prices(self):
        from app.research.agents.technical import TechnicalAgent

        agent = TechnicalAgent()
        ctx = _make_context(with_prices=False)
        llm = _make_mock_llm()

        result = await agent.research(ctx, llm)

        assert result.status == "failed"
        assert result.error is not None

    @pytest.mark.asyncio
    async def test_moving_averages_computed(self):
        from app.research.agents.technical import TechnicalAgent

        agent = TechnicalAgent()
        ctx = _make_context()  # 250 bars
        llm = _make_mock_llm()

        result = await agent.research(ctx, llm)
        ma = result.findings["moving_averages"]

        assert ma["sma_20"] is not None
        assert ma["sma_50"] is not None
        assert ma["sma_200"] is not None
        assert isinstance(ma["above_50_dma"], bool)
        assert isinstance(ma["above_200_dma"], bool)

    @pytest.mark.asyncio
    async def test_rsi_in_valid_range(self):
        from app.research.agents.technical import TechnicalAgent

        agent = TechnicalAgent()
        ctx = _make_context()
        llm = _make_mock_llm()

        result = await agent.research(ctx, llm)
        rsi = result.findings.get("rsi_14")

        assert rsi is not None
        assert 0 <= rsi <= 100


# ---------------------------------------------------------------------------
# OIDerivativesAgent
# ---------------------------------------------------------------------------


class TestOIDerivativesAgent:
    @pytest.mark.asyncio
    async def test_skips_non_fo_stocks(self):
        from app.research.agents.oi_derivatives import OIDerivativesAgent

        agent = OIDerivativesAgent()
        ctx = _make_context(is_fo=False)
        llm = _make_mock_llm()

        result = await agent.research(ctx, llm)

        assert result.status == "completed"
        assert result.findings["available"] is False
        assert "not" in result.summary.lower() and "f&o" in result.summary.lower()

    @pytest.mark.asyncio
    async def test_returns_partial_without_fyers_token(self):
        from app.research.agents.oi_derivatives import OIDerivativesAgent

        mock_r = AsyncMock()
        mock_r.get = AsyncMock(return_value=None)

        agent = OIDerivativesAgent()
        ctx = _make_context(is_fo=True)
        llm = _make_mock_llm()

        with patch("app.core.redis.get_redis", return_value=mock_r):
            result = await agent.research(ctx, llm)

        assert result.status == "partial"
        assert "authentication" in result.summary.lower()


# ---------------------------------------------------------------------------
# InstitutionalAgent
# ---------------------------------------------------------------------------


class TestInstitutionalAgent:
    @pytest.mark.asyncio
    @patch("app.research.agents.institutional.nse_client")
    async def test_returns_completed_with_shareholding(self, mock_nse):
        from app.data_sources.schemas import ShareholdingPattern
        from app.research.agents.institutional import InstitutionalAgent

        mock_nse.get_shareholding_pattern = AsyncMock(return_value=[
            ShareholdingPattern(quarter_end=date(2025, 3, 31), promoter_pct=40.0,
                                fii_pct=25.0, dii_pct=15.0, mf_pct=10.0, public_pct=20.0),
            ShareholdingPattern(quarter_end=date(2024, 12, 31), promoter_pct=41.0,
                                fii_pct=23.0, dii_pct=14.0, mf_pct=9.0, public_pct=22.0),
        ])

        agent = InstitutionalAgent()
        ctx = _make_context()
        llm = _make_mock_llm()

        result = await agent.research(ctx, llm)

        assert result.status == "completed"
        assert "shareholding_pattern" in result.findings
        assert "qoq_changes" in result.findings
        assert result.findings["qoq_changes"]["fii_change"] == 2.0
        assert result.findings["institutional_conviction"] == "ACCUMULATING"

    @pytest.mark.asyncio
    @patch("app.research.agents.institutional.nse_client")
    async def test_detects_distribution(self, mock_nse):
        from app.data_sources.schemas import ShareholdingPattern
        from app.research.agents.institutional import InstitutionalAgent

        mock_nse.get_shareholding_pattern = AsyncMock(return_value=[
            ShareholdingPattern(quarter_end=date(2025, 3, 31), promoter_pct=40.0,
                                fii_pct=20.0, dii_pct=12.0, mf_pct=8.0, public_pct=28.0),
            ShareholdingPattern(quarter_end=date(2024, 12, 31), promoter_pct=40.0,
                                fii_pct=25.0, dii_pct=15.0, mf_pct=10.0, public_pct=20.0),
        ])

        agent = InstitutionalAgent()
        ctx = _make_context()
        llm = _make_mock_llm()

        result = await agent.research(ctx, llm)

        assert result.findings["institutional_conviction"] == "DISTRIBUTING"

    @pytest.mark.asyncio
    @patch("app.research.agents.institutional.nse_client")
    async def test_promoter_pledge_warning(self, mock_nse):
        from app.data_sources.schemas import ShareholdingPattern
        from app.research.agents.institutional import InstitutionalAgent

        mock_nse.get_shareholding_pattern = AsyncMock(return_value=[
            ShareholdingPattern(quarter_end=date(2025, 3, 31), promoter_pct=40.0,
                                fii_pct=25.0, dii_pct=15.0, mf_pct=10.0, public_pct=20.0,
                                pledge_pct=12.5),
        ])

        agent = InstitutionalAgent()
        ctx = _make_context()
        llm = _make_mock_llm()

        result = await agent.research(ctx, llm)

        assert result.findings.get("promoter_pledge_warning") is True
        assert result.findings["promoter_pledge_pct"] == 12.5


# ---------------------------------------------------------------------------
# NewsSentimentAgent
# ---------------------------------------------------------------------------


class TestNewsSentimentAgent:
    @pytest.mark.asyncio
    async def test_returns_completed_with_search_results(self):
        from app.research.agents.news_sentiment import NewsSentimentAgent

        agent = NewsSentimentAgent()
        ctx = _make_context()
        llm = _make_mock_llm()

        result = await agent.research(ctx, llm)

        assert result.status == "completed"
        assert result.findings["overall_sentiment"] == "positive"
        assert len(result.findings["articles"]) >= 1
        assert "google_search_grounding" in result.data_sources

    @pytest.mark.asyncio
    async def test_handles_search_failure(self):
        from app.research.agents.news_sentiment import NewsSentimentAgent

        agent = NewsSentimentAgent()
        ctx = _make_context()
        llm = _make_mock_llm()
        llm.generate_with_search = AsyncMock(side_effect=Exception("API error"))

        result = await agent.research(ctx, llm)

        assert result.status == "failed"
        assert "API error" in result.error

    @pytest.mark.asyncio
    async def test_handles_empty_search_results(self):
        from app.research.agents.news_sentiment import NewsSentimentAgent

        agent = NewsSentimentAgent()
        ctx = _make_context()
        llm = _make_mock_llm()
        llm.generate_with_search = AsyncMock(return_value=MagicMock(
            text="", search_queries=[], sources=[],
        ))

        result = await agent.research(ctx, llm)

        assert result.status == "partial"


# ---------------------------------------------------------------------------
# ValuationAgent
# ---------------------------------------------------------------------------


class TestValuationAgent:
    @pytest.mark.asyncio
    async def test_returns_completed_with_stock_info(self):
        from app.research.agents.valuation import ValuationAgent

        mock_info = {
            "trailingPE": 28.5,
            "forwardPE": 24.0,
            "priceToBook": 10.2,
            "pegRatio": 1.8,
            "dividendYield": 0.012,
            "enterpriseToEbitda": 20.5,
            "sector": "Technology",
            "industry": "IT Services",
            "marketCap": 12_000_000_000_000,
        }

        mock_ticker = MagicMock()
        mock_ticker.info = mock_info

        agent = ValuationAgent()
        ctx = _make_context()
        llm = _make_mock_llm()

        with patch("yfinance.Ticker", return_value=mock_ticker):
            result = await agent.research(ctx, llm)

        assert result.status == "completed"
        assert result.findings.get("pe_ratio") == 28.5
        assert result.findings.get("sector") == "Technology"

    @pytest.mark.asyncio
    async def test_returns_failed_without_info(self):
        from app.research.agents.valuation import ValuationAgent

        agent = ValuationAgent()
        ctx = _make_context()
        ctx.stock_info = None
        llm = _make_mock_llm()

        with patch("app.data_sources.yfinance_client.get_stock_info", new_callable=AsyncMock, return_value=None):
            result = await agent.research(ctx, llm)

        assert result.status == "failed"


# ---------------------------------------------------------------------------
# BaseResearchAgent._safe_research
# ---------------------------------------------------------------------------


class TestBaseAgentSafeResearch:
    @pytest.mark.asyncio
    async def test_safe_research_catches_exceptions(self):
        from app.research.agents.base import BaseResearchAgent

        class CrashingAgent(BaseResearchAgent):
            name = "crash"
            description = "test"
            async def research(self, ctx, llm):
                raise RuntimeError("boom")

        agent = CrashingAgent()
        ctx = _make_context()
        llm = _make_mock_llm()

        result = await agent._safe_research(ctx, llm)

        assert result.status == "failed"
        assert "boom" in result.error
        assert result.duration_seconds >= 0

    @pytest.mark.asyncio
    async def test_safe_research_records_duration(self):
        from app.research.agents.base import BaseResearchAgent, AgentResult

        class SlowAgent(BaseResearchAgent):
            name = "slow"
            description = "test"
            async def research(self, ctx, llm):
                import asyncio
                await asyncio.sleep(0.05)
                return AgentResult(agent_name="slow", status="completed",
                                   findings={}, summary="done")

        agent = SlowAgent()
        ctx = _make_context()
        llm = _make_mock_llm()

        result = await agent._safe_research(ctx, llm)

        assert result.status == "completed"
        assert result.duration_seconds >= 0.04
