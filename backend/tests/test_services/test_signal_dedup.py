"""Tests for signal deduplication in strategy_runner."""

import pytest
from datetime import date, datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

from app.core.enums import InstrumentType, SignalStatus, SignalType, StrategyName
from app.strategies.base import StrategySignal


def _make_signal(**overrides) -> StrategySignal:
    defaults = dict(
        strategy_name=StrategyName.VWAP_PULLBACK,
        symbol="NIFTY",
        signal_type=SignalType.BUY_CE,
        instrument_type=InstrumentType.OPTION,
        strike_price=24000.0,
        expiry_date=date(2026, 4, 22),
        entry_price=250.0,
        stop_loss=175.0,
        target_price=362.5,
        confidence=75.0,
        reason="Test signal",
        indicators={"sl_pct": 0.30},
    )
    defaults.update(overrides)
    return StrategySignal(**defaults)


def _make_db_signal(**overrides) -> MagicMock:
    """Create a mock Signal DB row."""
    defaults = dict(
        id="existing-uuid",
        strategy_name="vwap_pullback",
        symbol="NIFTY",
        signal_type="BUY_CE",
        status="PENDING",
        entry_price=Decimal("250.0"),
        stop_loss=Decimal("175.0"),
        target_price=Decimal("362.5"),
        confidence=Decimal("75.0"),
        generated_at=datetime(2026, 4, 17, 10, 0),
    )
    defaults.update(overrides)
    mock = MagicMock()
    for k, v in defaults.items():
        setattr(mock, k, v)
    return mock


class TestDedupSignal:
    def _no_trade_result(self):
        """Mock execute result that returns None (no trade found)."""
        r = MagicMock()
        r.scalar_one_or_none.return_value = None
        return r

    def _version_count_result(self, count: int = 0):
        """Mock execute result for the SignalHistory version count query."""
        r = MagicMock()
        r.scalar.return_value = count
        return r

    @pytest.mark.asyncio
    @patch("app.services.strategy_runner.async_session_factory")
    async def test_skip_identical_pending_signal(self, mock_sf):
        """If an identical PENDING signal exists with no trade, return 'skip'."""
        from app.services.strategy_runner import strategy_runner

        existing = _make_db_signal(executed_trade_id=None)
        mock_session = AsyncMock()
        signal_result = MagicMock()
        signal_result.scalar_one_or_none.return_value = existing
        # Two execute calls: (1) find existing signal, (2) check for trades
        mock_session.execute = AsyncMock(side_effect=[signal_result, self._no_trade_result()])
        mock_sf.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_sf.return_value.__aexit__ = AsyncMock(return_value=False)

        signal = _make_signal()  # Same values as existing, 5 min old → noise
        now = datetime(2026, 4, 17, 10, 5)

        result = await strategy_runner._dedup_signal(signal, now, True, None)
        assert result == "skip"

    @pytest.mark.asyncio
    @patch("app.services.strategy_runner.async_session_factory")
    async def test_update_when_values_changed(self, mock_sf):
        """If a PENDING signal exists but entry price moved ≥0.3%, update it."""
        from app.services.strategy_runner import strategy_runner

        existing = _make_db_signal(executed_trade_id=None)
        mock_session = AsyncMock()
        signal_result = MagicMock()
        signal_result.scalar_one_or_none.return_value = existing
        # Three execute calls: (1) find existing, (2) check trades, (3) count history versions
        mock_session.execute = AsyncMock(side_effect=[
            signal_result,
            self._no_trade_result(),
            self._version_count_result(0),
        ])
        mock_session.commit = AsyncMock()
        mock_session.refresh = AsyncMock()
        mock_sf.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_sf.return_value.__aexit__ = AsyncMock(return_value=False)

        # 4% entry move — clearly meaningful
        signal = _make_signal(entry_price=260.0, stop_loss=182.0, target_price=375.0)
        now = datetime(2026, 4, 17, 10, 5)

        result = await strategy_runner._dedup_signal(signal, now, True, None)

        assert result is not None
        assert result != "skip"
        assert existing.entry_price == Decimal("260.0")
        assert existing.stop_loss == Decimal("182.0")
        assert existing.target_price == Decimal("375.0")
        mock_session.add.assert_called_once()  # SignalHistory snapshot was archived
        mock_session.commit.assert_called_once()

    @pytest.mark.asyncio
    @patch("app.services.strategy_runner.async_session_factory")
    async def test_new_signal_when_no_pending_exists(self, mock_sf):
        """If no PENDING signal exists, return None (caller creates new)."""
        from app.services.strategy_runner import strategy_runner

        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_sf.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_sf.return_value.__aexit__ = AsyncMock(return_value=False)

        signal = _make_signal()
        now = datetime(2026, 4, 17, 10, 5)

        result = await strategy_runner._dedup_signal(signal, now, True, None)
        assert result is None

    @pytest.mark.asyncio
    @patch("app.services.strategy_runner.async_session_factory")
    async def test_update_on_confidence_change(self, mock_sf):
        """If confidence changed ≥5 pts with no trade, update the signal."""
        from app.services.strategy_runner import strategy_runner

        existing = _make_db_signal(executed_trade_id=None)
        mock_session = AsyncMock()
        signal_result = MagicMock()
        signal_result.scalar_one_or_none.return_value = existing
        # Three execute calls: (1) find existing, (2) check trades, (3) count history versions
        mock_session.execute = AsyncMock(side_effect=[
            signal_result,
            self._no_trade_result(),
            self._version_count_result(0),
        ])
        mock_session.commit = AsyncMock()
        mock_session.refresh = AsyncMock()
        mock_sf.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_sf.return_value.__aexit__ = AsyncMock(return_value=False)

        signal = _make_signal(confidence=85.0)  # 10-point shift — meaningful
        now = datetime(2026, 4, 17, 10, 5)

        result = await strategy_runner._dedup_signal(signal, now, True, None)
        assert result is not None
        assert result != "skip"
        assert existing.confidence == Decimal("85.0")
        mock_session.add.assert_called_once()  # SignalHistory snapshot was archived

    @pytest.mark.asyncio
    @patch("app.services.strategy_runner.async_session_factory")
    async def test_different_strategy_creates_new(self, mock_sf):
        """Signals from different strategies are not deduplicated."""
        from app.services.strategy_runner import strategy_runner

        mock_session = AsyncMock()
        mock_result = MagicMock()
        # Query for CAN_SLIM finds no match (existing is VWAP_PULLBACK)
        mock_result.scalar_one_or_none.return_value = None
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_sf.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_sf.return_value.__aexit__ = AsyncMock(return_value=False)

        signal = _make_signal(strategy_name=StrategyName.CAN_SLIM)
        now = datetime(2026, 4, 17, 10, 5)

        result = await strategy_runner._dedup_signal(signal, now, True, None)
        assert result is None  # No match → create new


class TestSameDayDedupScoping:
    """Intraday strategies only dedup against same-day signals."""

    def _no_trade_result(self):
        r = MagicMock()
        r.scalar_one_or_none.return_value = None
        return r

    @pytest.mark.asyncio
    @patch("app.services.strategy_runner.now_ist")
    @patch("app.services.strategy_runner.async_session_factory")
    async def test_intraday_dedup_ignores_yesterday_signal(self, mock_sf, mock_now):
        """An intraday strategy should NOT dedup against a PENDING signal from yesterday."""
        from app.services.strategy_runner import strategy_runner

        mock_now.return_value = datetime(2026, 5, 22, 10, 0)

        mock_session = AsyncMock()
        # Query with today_start filter finds no match (yesterday's signal excluded)
        no_match = MagicMock()
        no_match.scalar_one_or_none.return_value = None
        mock_session.execute = AsyncMock(return_value=no_match)
        mock_sf.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_sf.return_value.__aexit__ = AsyncMock(return_value=False)

        signal = _make_signal(strategy_name=StrategyName.VWAP_PULLBACK)
        now = datetime(2026, 5, 22, 10, 0)

        result = await strategy_runner._dedup_signal(signal, now, True, None)
        assert result is None  # No match → create new signal

    @pytest.mark.asyncio
    @patch("app.services.strategy_runner.now_ist")
    @patch("app.services.strategy_runner.async_session_factory")
    async def test_intraday_dedup_matches_today_signal(self, mock_sf, mock_now):
        """An intraday strategy SHOULD dedup against a same-day PENDING signal."""
        from app.services.strategy_runner import strategy_runner

        mock_now.return_value = datetime(2026, 5, 22, 10, 0)

        existing = _make_db_signal(
            executed_trade_id=None,
            generated_at=datetime(2026, 5, 22, 9, 30),
        )
        mock_session = AsyncMock()
        signal_result = MagicMock()
        signal_result.scalar_one_or_none.return_value = existing
        mock_session.execute = AsyncMock(side_effect=[
            signal_result, self._no_trade_result(),
        ])
        mock_sf.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_sf.return_value.__aexit__ = AsyncMock(return_value=False)

        signal = _make_signal(strategy_name=StrategyName.VWAP_PULLBACK)
        now = datetime(2026, 5, 22, 10, 0)

        result = await strategy_runner._dedup_signal(signal, now, True, None)
        assert result == "skip"  # Identical → noise suppression

    @pytest.mark.asyncio
    @patch("app.services.strategy_runner.async_session_factory")
    async def test_positional_dedup_matches_across_days(self, mock_sf):
        """A positional strategy (CAN SLIM) SHOULD dedup against prior-day signals."""
        from app.services.strategy_runner import strategy_runner

        existing = _make_db_signal(
            executed_trade_id=None,
            strategy_name="can_slim",
            signal_type="BUY_FUT",
            generated_at=datetime(2026, 5, 19, 11, 0),
        )
        mock_session = AsyncMock()
        signal_result = MagicMock()
        signal_result.scalar_one_or_none.return_value = existing
        mock_session.execute = AsyncMock(side_effect=[
            signal_result, self._no_trade_result(),
        ])
        mock_sf.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_sf.return_value.__aexit__ = AsyncMock(return_value=False)

        signal = _make_signal(
            strategy_name=StrategyName.CAN_SLIM,
            signal_type=SignalType.BUY_FUT,
            instrument_type=InstrumentType.FUTURE,
        )
        now = datetime(2026, 5, 22, 10, 0)

        result = await strategy_runner._dedup_signal(signal, now, True, None)
        assert result == "skip"  # Cross-day dedup still works for positional

    @pytest.mark.asyncio
    @patch("app.services.strategy_runner.now_ist")
    @patch("app.services.strategy_runner.async_session_factory")
    async def test_is_dedup_skip_intraday_scoped_to_today(self, mock_sf, mock_now):
        """_is_dedup_skip should also scope intraday strategies to today only."""
        from app.services.strategy_runner import strategy_runner

        mock_now.return_value = datetime(2026, 5, 22, 10, 0)

        mock_session = AsyncMock()
        # Query with today filter finds nothing (yesterday's signal is excluded)
        no_match = MagicMock()
        no_match.scalar_one_or_none.return_value = None
        mock_session.execute = AsyncMock(return_value=no_match)
        mock_sf.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_sf.return_value.__aexit__ = AsyncMock(return_value=False)

        signal = _make_signal(strategy_name=StrategyName.INTRADAY_FUTURES,
                              signal_type=SignalType.BUY_FUT,
                              instrument_type=InstrumentType.FUTURE)

        result = await strategy_runner._is_dedup_skip(signal)
        assert result is False  # No same-day match → not a skip


class TestHandleSignalDedup:
    """Integration test: _handle_signal should use dedup before persisting."""

    @pytest.mark.asyncio
    @patch("app.services.strategy_runner.strategy_runner._broadcast_signal", new_callable=AsyncMock)
    @patch("app.services.strategy_runner.strategy_runner._persist_signal", new_callable=AsyncMock)
    @patch("app.services.strategy_runner.strategy_runner._dedup_signal", new_callable=AsyncMock)
    async def test_skips_when_dedup_returns_skip(self, mock_dedup, mock_persist, mock_broadcast):
        """When dedup says skip, neither persist nor broadcast should be called."""
        from app.services.strategy_runner import strategy_runner

        mock_dedup.return_value = "skip"

        signal = _make_signal()
        await strategy_runner._handle_signal(signal, True, None)

        mock_persist.assert_not_called()
        mock_broadcast.assert_not_called()

    @pytest.mark.asyncio
    @patch("app.services.strategy_runner.strategy_runner._broadcast_signal", new_callable=AsyncMock)
    @patch("app.services.strategy_runner.strategy_runner._persist_signal", new_callable=AsyncMock)
    @patch("app.services.strategy_runner.strategy_runner._dedup_signal", new_callable=AsyncMock)
    async def test_broadcasts_update_when_dedup_returns_signal(self, mock_dedup, mock_persist, mock_broadcast):
        """When dedup updates an existing signal, broadcast as signal:updated."""
        from app.services.strategy_runner import strategy_runner
        from app.models.signal import Signal

        updated_record = MagicMock(spec=Signal)
        updated_record.id = "existing-uuid"
        mock_dedup.return_value = updated_record

        signal = _make_signal()
        await strategy_runner._handle_signal(signal, True, None)

        mock_persist.assert_not_called()  # Should NOT create new
        mock_broadcast.assert_called_once()
        # Check it was called with event="signal:updated"
        call_kwargs = mock_broadcast.call_args
        assert call_kwargs.kwargs.get("event") == "signal:updated"

    @pytest.mark.asyncio
    @patch("app.services.strategy_runner.strategy_runner._broadcast_signal", new_callable=AsyncMock)
    @patch("app.services.strategy_runner.strategy_runner._persist_signal", new_callable=AsyncMock)
    @patch("app.services.strategy_runner.strategy_runner._dedup_signal", new_callable=AsyncMock)
    async def test_shadow_execute_fired_on_case2_update(self, mock_dedup, mock_persist, mock_broadcast):
        """Case-2 dedup (update in place) must schedule shadow_execute for the updated signal.

        Regression: signals that start below min_confidence_for_shadow (e.g. AI overlay
        knocks confidence to 44) then improve via Case-2 dedup were never shadow-executed
        because shadow_execute only fired on new signal creation.
        """
        from app.services.strategy_runner import strategy_runner
        from app.models.signal import Signal

        updated_record = MagicMock(spec=Signal)
        updated_record.id = "existing-uuid"
        mock_dedup.return_value = updated_record

        signal = _make_signal()
        with patch("asyncio.create_task"), \
             patch("app.agent.shadow_executor.shadow_execute_signal") as mock_shadow:
            await strategy_runner._handle_signal(signal, True, None)

        mock_persist.assert_not_called()
        mock_broadcast.assert_called_once()
        mock_shadow.assert_called_once_with("existing-uuid")

    @pytest.mark.asyncio
    @patch("app.services.strategy_runner.strategy_runner._broadcast_signal", new_callable=AsyncMock)
    @patch("app.services.strategy_runner.strategy_runner._persist_signal", new_callable=AsyncMock)
    @patch("app.services.strategy_runner.strategy_runner._dedup_signal", new_callable=AsyncMock)
    async def test_creates_new_when_dedup_returns_none(self, mock_dedup, mock_persist, mock_broadcast):
        """When dedup finds no match, create a new signal normally."""
        from app.services.strategy_runner import strategy_runner

        mock_dedup.return_value = None
        mock_persist.return_value = _make_db_signal()

        signal = _make_signal()
        await strategy_runner._handle_signal(signal, True, None)

        mock_persist.assert_called_once()
        mock_broadcast.assert_called_once()
