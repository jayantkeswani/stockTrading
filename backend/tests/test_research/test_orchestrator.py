"""Tests for the research orchestrator — agent coordination, error handling, concurrency."""

import asyncio
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.research.agents.base import AgentResult, ResearchContext
from app.research.agents.synthesis import synthesize_report, _fallback_report, _build_markdown


# ---------------------------------------------------------------------------
# Synthesis
# ---------------------------------------------------------------------------


class TestSynthesizeReport:
    @pytest.mark.asyncio
    async def test_produces_report_with_all_agents_completed(self):
        ctx = ResearchContext(
            symbol="TCS", display_name="TCS Ltd",
            current_price=3500.0, market_cap_cr=1200000.0,
        )
        agent_results = {
            "fundamental": AgentResult(agent_name="fundamental", status="completed",
                                        findings={"quarterly_earnings": []}, summary="Strong earnings"),
            "technical": AgentResult(agent_name="technical", status="completed",
                                     findings={"trend_direction": "UPTREND"}, summary="Bullish trend"),
            "oi_derivatives": AgentResult(agent_name="oi_derivatives", status="completed",
                                           findings={"pcr": 1.2}, summary="Bullish OI"),
            "institutional": AgentResult(agent_name="institutional", status="completed",
                                          findings={"fii_pct": 25}, summary="FII accumulating"),
            "news_sentiment": AgentResult(agent_name="news_sentiment", status="completed",
                                           findings={"overall_sentiment": "positive"}, summary="Positive news"),
            "valuation": AgentResult(agent_name="valuation", status="completed",
                                      findings={"pe_ratio": 28}, summary="Fair valued"),
        }

        llm = AsyncMock()
        llm.generate_json = AsyncMock(return_value={
            "executive_summary": "TCS is a strong buy.",
            "recommendation": "BUY",
            "confidence_score": 78,
            "long_term_outlook": {"timeframe": "1-3 years", "assessment": "Good", "suitability": "Suitable"},
            "short_term_opportunity": {"entry_zone_low": 3400, "entry_zone_high": 3500,
                                       "stop_loss": 3200, "target_1": 3800, "target_2": 4000,
                                       "risk_reward_ratio": "1:1.5", "timeframe": "2-4 weeks"},
            "key_risks": ["Market risk"],
            "key_catalysts": ["Earnings"],
            "conflicts_noted": "None",
            "section_verdicts": {},
        })

        result = await synthesize_report(ctx, agent_results, llm)

        assert result["recommendation"] == "BUY"
        assert result["confidence_score"] == 78
        assert "TCS is a strong buy" in result["executive_summary"]
        assert result["report_json"] is not None
        assert result["report_markdown"] is not None

    @pytest.mark.asyncio
    async def test_handles_llm_failure_with_fallback(self):
        ctx = ResearchContext(symbol="TCS", display_name="TCS Ltd", current_price=3500.0)
        agent_results = {
            "fundamental": AgentResult(agent_name="fundamental", status="completed",
                                        findings={}, summary="Test"),
        }

        llm = AsyncMock()
        llm.generate_json = AsyncMock(side_effect=Exception("LLM down"))

        result = await synthesize_report(ctx, agent_results, llm)

        # Should use fallback — 1/1 completed = 50% of 50 cap = 50
        assert result["recommendation"] == "HOLD"
        assert result["confidence_score"] == 50

    @pytest.mark.asyncio
    async def test_fallback_zero_confidence_when_all_failed(self):
        ctx = ResearchContext(symbol="TCS", display_name="TCS Ltd", current_price=3500.0)
        agent_results = {
            "fundamental": AgentResult(agent_name="fundamental", status="failed",
                                        findings={}, summary="", error="Timeout"),
            "technical": AgentResult(agent_name="technical", status="failed",
                                     findings={}, summary="", error="Timeout"),
        }

        llm = AsyncMock()
        llm.generate_json = AsyncMock(side_effect=Exception("LLM down"))

        result = await synthesize_report(ctx, agent_results, llm)

        assert result["recommendation"] == "AVOID"
        assert result["confidence_score"] == 0

    @pytest.mark.asyncio
    async def test_handles_failed_agents_in_synthesis(self):
        ctx = ResearchContext(symbol="TCS", display_name="TCS Ltd", current_price=3500.0)
        agent_results = {
            "fundamental": AgentResult(agent_name="fundamental", status="completed",
                                        findings={}, summary="Good fundamentals"),
            "technical": AgentResult(agent_name="technical", status="failed",
                                     findings={}, summary="", error="Timeout"),
            "oi_derivatives": AgentResult(agent_name="oi_derivatives", status="failed",
                                           findings={}, summary="", error="No data"),
        }

        llm = AsyncMock()
        llm.generate_json = AsyncMock(return_value={
            "executive_summary": "Partial analysis",
            "recommendation": "HOLD",
            "confidence_score": 45,
            "section_verdicts": {},
        })

        result = await synthesize_report(ctx, agent_results, llm)

        # Should still produce a report even with failed agents
        assert result["recommendation"] == "HOLD"


class TestFallbackReport:
    def test_produces_basic_report_with_completed_agents(self):
        ctx = ResearchContext(symbol="TCS", display_name="TCS Ltd", current_price=3500.0)
        agent_results = {
            "fundamental": AgentResult(agent_name="fundamental", status="completed",
                                        findings={}, summary="Strong"),
        }

        result = _fallback_report(ctx, agent_results)

        assert result["recommendation"] == "HOLD"
        # 1/1 completed × 50 = 50
        assert result["confidence_score"] == 50
        assert "fundamental" in result["section_verdicts"]

    def test_zero_confidence_when_all_failed(self):
        ctx = ResearchContext(symbol="TCS", display_name="TCS Ltd", current_price=3500.0)
        agent_results = {
            "fundamental": AgentResult(agent_name="fundamental", status="failed",
                                        findings={}, summary="", error="error"),
            "technical": AgentResult(agent_name="technical", status="failed",
                                     findings={}, summary="", error="error"),
        }

        result = _fallback_report(ctx, agent_results)

        assert result["recommendation"] == "AVOID"
        assert result["confidence_score"] == 0

    def test_partial_confidence(self):
        ctx = ResearchContext(symbol="TCS", display_name="TCS Ltd", current_price=3500.0)
        agent_results = {
            "fundamental": AgentResult(agent_name="fundamental", status="completed",
                                        findings={}, summary="Ok"),
            "technical": AgentResult(agent_name="technical", status="failed",
                                     findings={}, summary="", error="err"),
            "valuation": AgentResult(agent_name="valuation", status="completed",
                                     findings={}, summary="Ok"),
            "news_sentiment": AgentResult(agent_name="news_sentiment", status="failed",
                                           findings={}, summary="", error="err"),
        }

        result = _fallback_report(ctx, agent_results)

        # 2/4 completed × 50 = 25
        assert result["confidence_score"] == 25
        assert result["recommendation"] == "HOLD"


class TestBuildMarkdown:
    def test_includes_all_sections(self):
        ctx = ResearchContext(symbol="TCS", display_name="TCS Ltd", current_price=3500.0)
        report_json = {
            "executive_summary": "Test summary",
            "recommendation": "BUY",
            "confidence_score": 75,
            "short_term_opportunity": {
                "entry_zone_low": 3400, "entry_zone_high": 3500,
                "stop_loss": 3200, "target_1": 3800, "target_2": 4000,
                "risk_reward_ratio": "1:1.5", "timeframe": "2-4 weeks",
            },
            "long_term_outlook": {"suitability": "Suitable", "timeframe": "1-3y", "assessment": "Good"},
            "key_risks": ["Risk 1", "Risk 2"],
            "key_catalysts": ["Catalyst 1"],
            "section_verdicts": {"fundamental": "Strong"},
        }

        md = _build_markdown(ctx, report_json, {})

        assert "# TCS" in md
        assert "BUY" in md
        assert "Test summary" in md
        assert "3,400" in md or "3400" in md
        assert "Risk 1" in md
        assert "Catalyst 1" in md


# ---------------------------------------------------------------------------
# Orchestrator concurrency & error handling
# ---------------------------------------------------------------------------


class TestOrchestratorConcurrency:
    def test_active_session_count(self):
        from app.research.orchestrator import get_active_research_count, _active_sessions

        # Start clean
        _active_sessions.clear()
        assert get_active_research_count() == 0

    @pytest.mark.asyncio
    async def test_concurrency_limit_enforced(self):
        from app.research.orchestrator import start_research, _active_sessions

        _active_sessions.clear()

        # Add 3 fake running tasks
        for i in range(3):
            fake_task = MagicMock()
            fake_task.done.return_value = False
            _active_sessions[f"fake-{i}"] = fake_task

        # 4th should raise
        with pytest.raises(ValueError, match="Maximum"):
            await start_research("TCS", uuid.uuid4())

        # Cleanup
        _active_sessions.clear()
