"""Tests for the strategies API endpoints — evaluate, auto-mode toggle, batch evaluate."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.core.enums import SignalType, StrategyName


# ---------------------------------------------------------------------------
# Tests for POST /evaluate (single symbol)
# ---------------------------------------------------------------------------


class TestEvaluateEndpoint:
    """Tests for the manual single-symbol evaluation endpoint."""

    @pytest.mark.asyncio
    @patch("app.services.strategy_runner.strategy_runner")
    async def test_evaluate_returns_signal_info(self, mock_runner):
        """Successful evaluation returns signal details."""
        from app.api.v1.strategies import evaluate_strategy, ManualEvaluateRequest

        mock_signal = MagicMock()
        mock_signal.signal_type = SignalType.BUY_CE
        mock_signal.confidence = 75.0
        mock_runner.evaluate_manual = AsyncMock(return_value=mock_signal)

        body = ManualEvaluateRequest(strategy_name="vwap_pullback", symbol="NIFTY")
        result = await evaluate_strategy(body)

        assert result["signal_generated"] is True
        assert result["signal_type"] == "BUY_CE"
        assert result["confidence"] == 75.0
        assert result["symbol"] == "NIFTY"

    @pytest.mark.asyncio
    @patch("app.services.strategy_runner.strategy_runner")
    async def test_evaluate_no_signal(self, mock_runner):
        """When no signal is generated, signal_generated=False."""
        from app.api.v1.strategies import evaluate_strategy, ManualEvaluateRequest

        mock_runner.evaluate_manual = AsyncMock(return_value=None)

        body = ManualEvaluateRequest(strategy_name="vwap_pullback", symbol="NIFTY")
        result = await evaluate_strategy(body)

        assert result["signal_generated"] is False
        assert result["signal_type"] is None

    @pytest.mark.asyncio
    async def test_evaluate_invalid_strategy_name(self):
        """Unknown strategy name should raise HTTPException."""
        from fastapi import HTTPException
        from app.api.v1.strategies import evaluate_strategy, ManualEvaluateRequest

        body = ManualEvaluateRequest(strategy_name="nonexistent", symbol="NIFTY")

        with pytest.raises(HTTPException) as exc_info:
            await evaluate_strategy(body)
        assert exc_info.value.status_code == 400


# ---------------------------------------------------------------------------
# Tests for POST /evaluate/batch
# ---------------------------------------------------------------------------


class TestBatchEvaluateEndpoint:
    """Tests for the batch evaluation endpoint (all configured symbols)."""

    @pytest.mark.asyncio
    @patch("app.services.strategy_runner.strategy_runner")
    @patch("app.core.database.async_session_factory")
    async def test_batch_evaluate_scans_all_symbols(self, mock_session_factory, mock_runner):
        """Batch evaluate should iterate over all configured symbols."""
        from app.api.v1.strategies import evaluate_strategy_batch, BatchEvaluateRequest

        # Mock DB query for configured symbols
        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = ["NIFTY", "BANKNIFTY"]
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_session_factory.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session_factory.return_value.__aexit__ = AsyncMock(return_value=False)

        # First call returns signal, second returns None
        mock_signal = MagicMock()
        mock_signal.signal_type = SignalType.BUY_CE
        mock_signal.confidence = 80.0
        mock_runner.evaluate_manual = AsyncMock(side_effect=[mock_signal, None])

        body = BatchEvaluateRequest(strategy_name="vwap_pullback")
        result = await evaluate_strategy_batch(body)

        assert result["symbols_scanned"] == 2
        assert result["signals_generated"] == 1
        assert len(result["results"]) == 2
        assert result["results"][0]["signal_generated"] is True
        assert result["results"][1]["signal_generated"] is False

    @pytest.mark.asyncio
    async def test_batch_evaluate_invalid_strategy(self):
        """Unknown strategy name should raise HTTPException."""
        from fastapi import HTTPException
        from app.api.v1.strategies import evaluate_strategy_batch, BatchEvaluateRequest

        body = BatchEvaluateRequest(strategy_name="nonexistent")

        with pytest.raises(HTTPException) as exc_info:
            await evaluate_strategy_batch(body)
        assert exc_info.value.status_code == 400

    @pytest.mark.asyncio
    @patch("app.core.database.async_session_factory")
    async def test_batch_evaluate_no_symbols_configured(self, mock_session_factory):
        """When no symbols are configured, raise HTTPException."""
        from fastapi import HTTPException
        from app.api.v1.strategies import evaluate_strategy_batch, BatchEvaluateRequest

        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = []
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_session_factory.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session_factory.return_value.__aexit__ = AsyncMock(return_value=False)

        body = BatchEvaluateRequest(strategy_name="vwap_pullback")

        with pytest.raises(HTTPException) as exc_info:
            await evaluate_strategy_batch(body)
        assert exc_info.value.status_code == 400
