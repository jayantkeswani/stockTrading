"""Tests for global cues mid-day shift detection in strategy_runner._enrich_strategy5_params."""

import json
from unittest.mock import AsyncMock, patch

import pytest

from app.indicators.global_market import GlobalCues


def _mock_redis(morning_cues: dict | None = None, debounce_keys: dict | None = None):
    """Build an AsyncMock Redis with morning cues and optional debounce state."""
    store: dict[str, str] = {}
    if debounce_keys:
        store.update(debounce_keys)

    mock = AsyncMock()

    async def mock_get(key: str):
        if "global_cues" in key and morning_cues is not None:
            return json.dumps(morning_cues)
        if "global_shift_logged" in key:
            return store.get(key)
        if key == "price:INDIA VIX":
            return "15.0"
        return None

    async def mock_set(key: str, value, **kwargs):
        store[key] = value

    mock.get = AsyncMock(side_effect=mock_get)
    mock.set = AsyncMock(side_effect=mock_set)
    mock.rpush = AsyncMock()
    mock.expire = AsyncMock()

    return mock


class TestGlobalCuesMidDayShift:
    """Tests for crude/VIX shift detection + debounce in _enrich_strategy5_params."""

    @pytest.mark.asyncio
    @patch("app.services.strategy_runner.async_session_factory")
    async def test_crude_shift_logs_when_above_threshold(self, mock_factory):
        from app.services.strategy_runner import strategy_runner

        morning = {"crude_pct": 1.0, "us_vix": 15.0}
        current_cues = GlobalCues(crude_pct=3.5, us_vix=15.0)

        mock_redis = _mock_redis(morning_cues=morning)

        mock_session = AsyncMock()
        mock_result = AsyncMock()
        mock_result.scalar = AsyncMock(return_value=0)
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_factory.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_factory.return_value.__aexit__ = AsyncMock(return_value=False)

        params: dict = {}

        with (
            patch("app.services.strategy_runner.get_redis", return_value=mock_redis),
            patch("app.services.strategy_runner._get_global_cues_from_redis", new_callable=AsyncMock, return_value=current_cues),
            patch("app.services.morning_screener._append_agent_log", new_callable=AsyncMock) as mock_log,
        ):
            await strategy_runner._enrich_strategy5_params("ADANIPORTS", params)

        mock_log.assert_called_once()
        call_args = mock_log.call_args
        assert call_args[0][1] == "GLOBAL_SHIFT"
        assert "crude" in call_args[0][2].lower()
        assert "2.5" in call_args[0][2]

    @pytest.mark.asyncio
    @patch("app.services.strategy_runner.async_session_factory")
    async def test_vix_shift_logs_when_above_threshold(self, mock_factory):
        from app.services.strategy_runner import strategy_runner

        morning = {"crude_pct": 1.0, "us_vix": 15.0}
        current_cues = GlobalCues(crude_pct=1.0, us_vix=17.5)

        mock_redis = _mock_redis(morning_cues=morning)

        mock_session = AsyncMock()
        mock_result = AsyncMock()
        mock_result.scalar = AsyncMock(return_value=0)
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_factory.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_factory.return_value.__aexit__ = AsyncMock(return_value=False)

        params: dict = {}

        with (
            patch("app.services.strategy_runner.get_redis", return_value=mock_redis),
            patch("app.services.strategy_runner._get_global_cues_from_redis", new_callable=AsyncMock, return_value=current_cues),
            patch("app.services.morning_screener._append_agent_log", new_callable=AsyncMock) as mock_log,
        ):
            await strategy_runner._enrich_strategy5_params("ADANIPORTS", params)

        mock_log.assert_called_once()
        call_args = mock_log.call_args
        assert call_args[0][1] == "GLOBAL_SHIFT"
        assert "vix" in call_args[0][2].lower()

    @pytest.mark.asyncio
    @patch("app.services.strategy_runner.async_session_factory")
    async def test_no_log_when_below_threshold(self, mock_factory):
        from app.services.strategy_runner import strategy_runner

        morning = {"crude_pct": 1.0, "us_vix": 15.0}
        current_cues = GlobalCues(crude_pct=2.0, us_vix=16.0)

        mock_redis = _mock_redis(morning_cues=morning)

        mock_session = AsyncMock()
        mock_result = AsyncMock()
        mock_result.scalar = AsyncMock(return_value=0)
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_factory.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_factory.return_value.__aexit__ = AsyncMock(return_value=False)

        params: dict = {}

        with (
            patch("app.services.strategy_runner.get_redis", return_value=mock_redis),
            patch("app.services.strategy_runner._get_global_cues_from_redis", new_callable=AsyncMock, return_value=current_cues),
            patch("app.services.morning_screener._append_agent_log", new_callable=AsyncMock) as mock_log,
        ):
            await strategy_runner._enrich_strategy5_params("ADANIPORTS", params)

        mock_log.assert_not_called()

    @pytest.mark.asyncio
    @patch("app.services.strategy_runner.async_session_factory")
    async def test_debounce_prevents_duplicate_log(self, mock_factory):
        """Once a shift is logged, the 60-min debounce key prevents re-logging."""
        from app.services.strategy_runner import strategy_runner
        from app.core.utils import now_ist

        morning = {"crude_pct": 1.0, "us_vix": 15.0}
        current_cues = GlobalCues(crude_pct=3.5, us_vix=15.0)

        today = now_ist().date()
        debounce = {f"strat5:global_shift_logged:{today}:crude": "1"}
        mock_redis = _mock_redis(morning_cues=morning, debounce_keys=debounce)

        mock_session = AsyncMock()
        mock_result = AsyncMock()
        mock_result.scalar = AsyncMock(return_value=0)
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_factory.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_factory.return_value.__aexit__ = AsyncMock(return_value=False)

        params: dict = {}

        with (
            patch("app.services.strategy_runner.get_redis", return_value=mock_redis),
            patch("app.services.strategy_runner._get_global_cues_from_redis", new_callable=AsyncMock, return_value=current_cues),
            patch("app.services.morning_screener._append_agent_log", new_callable=AsyncMock) as mock_log,
        ):
            await strategy_runner._enrich_strategy5_params("ADANIPORTS", params)

        mock_log.assert_not_called()

    @pytest.mark.asyncio
    @patch("app.services.strategy_runner.async_session_factory")
    async def test_both_shifts_logged_simultaneously(self, mock_factory):
        from app.services.strategy_runner import strategy_runner

        morning = {"crude_pct": 1.0, "us_vix": 15.0}
        current_cues = GlobalCues(crude_pct=4.0, us_vix=17.5)

        mock_redis = _mock_redis(morning_cues=morning)

        mock_session = AsyncMock()
        mock_result = AsyncMock()
        mock_result.scalar = AsyncMock(return_value=0)
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_factory.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_factory.return_value.__aexit__ = AsyncMock(return_value=False)

        params: dict = {}

        with (
            patch("app.services.strategy_runner.get_redis", return_value=mock_redis),
            patch("app.services.strategy_runner._get_global_cues_from_redis", new_callable=AsyncMock, return_value=current_cues),
            patch("app.services.morning_screener._append_agent_log", new_callable=AsyncMock) as mock_log,
        ):
            await strategy_runner._enrich_strategy5_params("ADANIPORTS", params)

        assert mock_log.call_count == 2
        categories = [c[0][1] for c in mock_log.call_args_list]
        assert all(c == "GLOBAL_SHIFT" for c in categories)
        messages = [c[0][2] for c in mock_log.call_args_list]
        assert any("crude" in m.lower() for m in messages)
        assert any("vix" in m.lower() for m in messages)

    @pytest.mark.asyncio
    @patch("app.services.strategy_runner.async_session_factory")
    async def test_no_crash_when_morning_cues_missing(self, mock_factory):
        """If no morning cues snapshot exists, shift detection is silently skipped."""
        from app.services.strategy_runner import strategy_runner

        mock_redis = _mock_redis(morning_cues=None)

        mock_session = AsyncMock()
        mock_result = AsyncMock()
        mock_result.scalar = AsyncMock(return_value=0)
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_factory.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_factory.return_value.__aexit__ = AsyncMock(return_value=False)

        params: dict = {}

        with (
            patch("app.services.strategy_runner.get_redis", return_value=mock_redis),
            patch("app.services.morning_screener._append_agent_log", new_callable=AsyncMock) as mock_log,
        ):
            await strategy_runner._enrich_strategy5_params("ADANIPORTS", params)

        mock_log.assert_not_called()

    @pytest.mark.asyncio
    @patch("app.services.strategy_runner.async_session_factory")
    async def test_no_crash_when_current_cues_unavailable(self, mock_factory):
        """If current global cues can't be fetched, shift detection is skipped."""
        from app.services.strategy_runner import strategy_runner

        morning = {"crude_pct": 1.0, "us_vix": 15.0}
        mock_redis = _mock_redis(morning_cues=morning)

        mock_session = AsyncMock()
        mock_result = AsyncMock()
        mock_result.scalar = AsyncMock(return_value=0)
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_factory.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_factory.return_value.__aexit__ = AsyncMock(return_value=False)

        params: dict = {}

        with (
            patch("app.services.strategy_runner.get_redis", return_value=mock_redis),
            patch("app.services.strategy_runner._get_global_cues_from_redis", new_callable=AsyncMock, return_value=None),
            patch("app.services.morning_screener._append_agent_log", new_callable=AsyncMock) as mock_log,
        ):
            await strategy_runner._enrich_strategy5_params("ADANIPORTS", params)

        mock_log.assert_not_called()
