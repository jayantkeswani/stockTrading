"""Tests for trading_config service layer.

Uses mocks so tests don't require a live DB.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.trading_config import (
    TradingConfigDTO,
    _row_to_dto,
    update_trading_config,
    get_trading_config,
)
import app.services.trading_config as _svc


# ── DTO helpers ────────────────────────────────────────────────────────────────

def test_dto_yolo_mode_property():
    dto = TradingConfigDTO(
        capital=1_000_000, max_daily_drawdown_pct=5.0, max_risk_per_trade_pct=2.0,
        max_trades_per_day=3, paper_trading=True, autonomy_level="YOLO",
        min_confidence_to_persist=30.0, min_confidence_for_shadow=70.0, min_confidence_for_execution=70.0,
        shadow_skip_permanent_watchlist=True, yolo_skip_permanent_watchlist=True,
    )
    assert dto.yolo_mode is True


def test_dto_semi_not_yolo():
    dto = TradingConfigDTO(
        capital=1_000_000, max_daily_drawdown_pct=5.0, max_risk_per_trade_pct=2.0,
        max_trades_per_day=3, paper_trading=True, autonomy_level="SEMI",
        min_confidence_to_persist=30.0, min_confidence_for_shadow=70.0, min_confidence_for_execution=70.0,
        shadow_skip_permanent_watchlist=True, yolo_skip_permanent_watchlist=True,
    )
    assert dto.yolo_mode is False


def test_dto_max_drawdown_amount():
    dto = TradingConfigDTO(
        capital=1_000_000, max_daily_drawdown_pct=5.0, max_risk_per_trade_pct=2.0,
        max_trades_per_day=3, paper_trading=True, autonomy_level="SEMI",
        min_confidence_to_persist=30.0, min_confidence_for_shadow=70.0, min_confidence_for_execution=70.0,
        shadow_skip_permanent_watchlist=True, yolo_skip_permanent_watchlist=True,
    )
    assert dto.max_drawdown_amount == 50_000.0


# ── get_trading_config — cache hit ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_trading_config_returns_cache():
    dto = TradingConfigDTO(
        capital=999_999, max_daily_drawdown_pct=4.0, max_risk_per_trade_pct=1.5,
        max_trades_per_day=2, paper_trading=False, autonomy_level="MANUAL",
        min_confidence_to_persist=30.0, min_confidence_for_shadow=70.0, min_confidence_for_execution=70.0,
        shadow_skip_permanent_watchlist=True, yolo_skip_permanent_watchlist=True,
    )
    _svc._cache = dto
    result = await get_trading_config()
    assert result is dto
    _svc._cache = None  # cleanup


# ── update_trading_config ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_update_rejects_unknown_field():
    with pytest.raises(ValueError, match="Unknown trading config fields"):
        await update_trading_config(nonexistent_field=42)


@pytest.mark.asyncio
async def test_update_rejects_invalid_autonomy_level():
    with pytest.raises(ValueError, match="Invalid autonomy_level"):
        await update_trading_config(autonomy_level="SUPER")


@pytest.mark.asyncio
async def test_update_writes_db_and_updates_cache():
    """Partial update: cache is updated and DB commit is called."""
    original = TradingConfigDTO(
        capital=1_000_000, max_daily_drawdown_pct=5.0, max_risk_per_trade_pct=2.0,
        max_trades_per_day=3, paper_trading=True, autonomy_level="SEMI",
        min_confidence_to_persist=30.0, min_confidence_for_shadow=70.0, min_confidence_for_execution=70.0,
        shadow_skip_permanent_watchlist=True, yolo_skip_permanent_watchlist=True,
    )
    _svc._cache = original

    updated_row = MagicMock()
    updated_row.capital = 2_000_000
    updated_row.max_daily_drawdown_pct = 5.0
    updated_row.max_risk_per_trade_pct = 2.0
    updated_row.max_trades_per_day = 3
    updated_row.paper_trading = True
    updated_row.autonomy_level = "SEMI"
    updated_row.min_confidence_to_persist = 30.0
    updated_row.min_confidence_for_shadow = 70.0
    updated_row.min_confidence_for_execution = 70.0
    updated_row.shadow_skip_permanent_watchlist = True
    updated_row.yolo_skip_permanent_watchlist = True

    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    mock_session.execute = AsyncMock(return_value=MagicMock(scalar_one=MagicMock(return_value=updated_row)))
    mock_session.refresh = AsyncMock()

    mock_redis = AsyncMock()

    with (
        patch("app.services.trading_config.async_session_factory", return_value=mock_session),
        patch("app.core.redis.get_redis", return_value=mock_redis),
    ):
        result = await update_trading_config(capital=2_000_000)

    assert result.capital == 2_000_000
    assert _svc._cache is result

    _svc._cache = None  # cleanup


@pytest.mark.asyncio
async def test_update_rejects_shadow_above_execution():
    """min_confidence_for_shadow must be <= min_confidence_for_execution."""
    _svc._cache = TradingConfigDTO(
        capital=1_000_000, max_daily_drawdown_pct=5.0, max_risk_per_trade_pct=2.0,
        max_trades_per_day=3, paper_trading=True, autonomy_level="SEMI",
        min_confidence_to_persist=30.0, min_confidence_for_shadow=70.0, min_confidence_for_execution=70.0,
        shadow_skip_permanent_watchlist=True, yolo_skip_permanent_watchlist=True,
    )
    with pytest.raises(ValueError, match="persist < shadow <= execution"):
        await update_trading_config(min_confidence_for_shadow=80.0, min_confidence_for_execution=70.0)
    _svc._cache = None  # cleanup


@pytest.mark.asyncio
async def test_update_rejects_persist_above_shadow():
    """min_confidence_to_persist must be < min_confidence_for_shadow."""
    _svc._cache = TradingConfigDTO(
        capital=1_000_000, max_daily_drawdown_pct=5.0, max_risk_per_trade_pct=2.0,
        max_trades_per_day=3, paper_trading=True, autonomy_level="SEMI",
        min_confidence_to_persist=30.0, min_confidence_for_shadow=70.0, min_confidence_for_execution=70.0,
        shadow_skip_permanent_watchlist=True, yolo_skip_permanent_watchlist=True,
    )
    with pytest.raises(ValueError, match="persist < shadow <= execution"):
        await update_trading_config(min_confidence_to_persist=75.0)
    _svc._cache = None  # cleanup
