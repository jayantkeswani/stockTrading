"""Tests for the signal-confidence LLM overlay throttle + confidence floor.

Covers the two guards added to curb the candle-close timeout storms:
1. Confidence floor — score_signal skips the LLM call (returns _FALLBACK) when the
   raw signal confidence is below settings.ai_confidence_min_confidence.
2. Concurrency throttle — concurrent score_signal calls never run more than
   settings.ai_confidence_max_concurrency LLM calls at once.
"""

import asyncio
from types import SimpleNamespace

import pytest

from app.config import settings
from app.research.agents import signal_confidence as sc


def _make_signal(confidence: float) -> SimpleNamespace:
    """Minimal stand-in for a StrategySignal (only fields score_signal reads)."""
    return SimpleNamespace(
        confidence=confidence,
        symbol="TATASTEEL",
        signal_type="BUY_FUT",
        strategy_name="intraday_futures",
        indicators={"setup_type": "ORB"},
    )


@pytest.fixture(autouse=True)
def _reset_semaphore_and_settings(monkeypatch):
    """Each test gets a fresh semaphore + enabled overlay with a dummy API key."""
    monkeypatch.setattr(settings, "ai_confidence_enabled", True)
    monkeypatch.setattr(settings, "google_api_key", "test-key")
    monkeypatch.setattr(settings, "ai_confidence_min_confidence", 50.0)
    monkeypatch.setattr(settings, "ai_confidence_timeout_seconds", 5)
    sc._llm_semaphore = None
    yield
    sc._llm_semaphore = None


@pytest.mark.asyncio
async def test_below_floor_skips_llm(monkeypatch):
    """Confidence below the floor returns _FALLBACK without calling the LLM."""
    calls = []

    async def _fake_call_llm(*args, **kwargs):
        calls.append(1)
        return sc.SignalConfidence(
            confidence_adjustment=10, summary="x", rationale="y",
            key_supports=[], key_risks=[], recommended_action="PROCEED",
            suggested_lot_adjustment="NONE",
        )

    monkeypatch.setattr(sc, "_call_llm", _fake_call_llm)

    result = await sc.score_signal(_make_signal(49.0), ctx=None)

    assert result is sc._FALLBACK
    assert calls == []  # LLM never invoked


@pytest.mark.asyncio
async def test_at_floor_calls_llm(monkeypatch):
    """Confidence at/above the floor proceeds to the LLM call."""
    calls = []

    async def _fake_call_llm(*args, **kwargs):
        calls.append(1)
        return sc.SignalConfidence(
            confidence_adjustment=8, summary="ok", rationale="r",
            key_supports=[], key_risks=[], recommended_action="PROCEED",
            suggested_lot_adjustment="NONE",
        )

    monkeypatch.setattr(sc, "_call_llm", _fake_call_llm)
    monkeypatch.setattr(sc, "_build_context_json", lambda *a, **k: "{}")

    result = await sc.score_signal(_make_signal(50.0), ctx=None)

    assert len(calls) == 1
    assert result.confidence_adjustment == 8


@pytest.mark.asyncio
async def test_concurrency_throttle(monkeypatch):
    """No more than ai_confidence_max_concurrency LLM calls run simultaneously."""
    monkeypatch.setattr(settings, "ai_confidence_max_concurrency", 2)
    monkeypatch.setattr(settings, "ai_confidence_timeout_seconds", 30)
    sc._llm_semaphore = None

    state = {"current": 0, "peak": 0}

    async def _slow_call_llm(*args, **kwargs):
        state["current"] += 1
        state["peak"] = max(state["peak"], state["current"])
        try:
            await asyncio.sleep(0.05)
            return sc.SignalConfidence(
                confidence_adjustment=0, summary="", rationale="",
                key_supports=[], key_risks=[], recommended_action="PROCEED",
                suggested_lot_adjustment="NONE",
            )
        finally:
            state["current"] -= 1

    monkeypatch.setattr(sc, "_call_llm", _slow_call_llm)
    monkeypatch.setattr(sc, "_build_context_json", lambda *a, **k: "{}")

    await asyncio.gather(*[sc.score_signal(_make_signal(70.0), ctx=None) for _ in range(6)])

    assert state["peak"] <= 2
