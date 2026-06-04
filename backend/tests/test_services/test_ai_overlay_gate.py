"""Tests for the two-level AI-overlay kill switch in strategy_runner.

The overlay (the up-to-25s LLM call) runs only when BOTH the master flag
(trading_config.ai_overlay_enabled) AND the per-strategy flag
(strategy_configs.parameters.ai_overlay_enabled, default true) are on. When gated
off it must short-circuit BEFORE calling score_signal — that's what removes the lag.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.strategy_runner import strategy_runner
from app.services.trading_config import TradingConfigDTO


def _cfg(ai_overlay_enabled: bool) -> TradingConfigDTO:
    return TradingConfigDTO(
        capital=1_000_000, max_daily_drawdown_pct=5.0, max_risk_per_trade_pct=2.0,
        max_trades_per_day=3, paper_trading=True, autonomy_level="YOLO",
        min_confidence_to_persist=30.0, min_confidence_for_shadow=70.0,
        min_confidence_for_execution=70.0, shadow_skip_permanent_watchlist=True,
        yolo_skip_permanent_watchlist=True, ai_overlay_enabled=ai_overlay_enabled,
    )


def _signal():
    s = MagicMock()
    s.confidence = 80.0          # above the overlay floor (default 50)
    s.strategy_name = "breakout_retest"
    s.symbol = "VEDL"
    s.signal_type = "BUY_FUT"
    return s


def _ctx(strategy_params: dict):
    return SimpleNamespace(strategy_params=strategy_params)


@pytest.mark.asyncio
@patch("app.config.settings.ai_confidence_min_confidence", 50)
@patch("app.config.settings.ai_confidence_enabled", True)
async def test_master_off_skips_overlay():
    """Master flag off → overlay returns {} and never calls the LLM."""
    score = AsyncMock()
    with patch("app.services.strategy_runner.get_trading_config", AsyncMock(return_value=_cfg(False))), \
         patch("app.research.agents.signal_confidence.score_signal", score):
        result = await strategy_runner._run_ai_confidence_overlay(_signal(), _ctx({}))
    assert result == {}
    score.assert_not_called()


@pytest.mark.asyncio
@patch("app.config.settings.ai_confidence_min_confidence", 50)
@patch("app.config.settings.ai_confidence_enabled", True)
async def test_per_strategy_off_skips_overlay():
    """Master on but per-strategy flag off → overlay returns {} and never calls the LLM."""
    score = AsyncMock()
    with patch("app.services.strategy_runner.get_trading_config", AsyncMock(return_value=_cfg(True))), \
         patch("app.research.agents.signal_confidence.score_signal", score):
        result = await strategy_runner._run_ai_confidence_overlay(
            _signal(), _ctx({"ai_overlay_enabled": False})
        )
    assert result == {}
    score.assert_not_called()


@pytest.mark.asyncio
@patch("app.config.settings.ai_confidence_min_confidence", 50)
@patch("app.config.settings.ai_confidence_enabled", True)
async def test_both_on_runs_overlay():
    """Master on + per-strategy default (absent = on) → the overlay proceeds to score_signal."""
    score = AsyncMock(return_value=SimpleNamespace(
        confidence_adjustment=0, summary="", rationale="",
        key_supports=[], key_risks=[], recommended_action="PROCEED",
        suggested_lot_adjustment="NONE",
    ))
    with patch("app.services.strategy_runner.get_trading_config", AsyncMock(return_value=_cfg(True))), \
         patch("app.research.agents.signal_confidence.score_signal", score), \
         patch("app.services.strategy_runner.async_session_factory") as msf:
        # Prior-signals query returns an empty result.
        session = AsyncMock()
        res = MagicMock()
        res.scalars.return_value.all.return_value = []
        session.execute = AsyncMock(return_value=res)
        msf.return_value.__aenter__ = AsyncMock(return_value=session)
        msf.return_value.__aexit__ = AsyncMock(return_value=False)
        await strategy_runner._run_ai_confidence_overlay(_signal(), _ctx({}))
    score.assert_called_once()
