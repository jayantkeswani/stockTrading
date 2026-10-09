"""IH v2 flow: basket signal shape (his strikes), OTM strike selection, the runtime kill switch,
and the Call 2 watcher cadence (09:16 first, every minute, 09:25 deadline, single-flight)."""
from datetime import date, time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.constants import STRIKE_GAPS
from app.core.enums import SignalType, StrategyName
from app.services.intraday_hunter_v2 import params as v2params
from app.services.intraday_hunter_v2 import signals as v2sig
from app.services.intraday_hunter_v2.params import INTRADAY_HUNTER_V2_DEFAULTS
from app.services.option_resolver import OptionResolution, select_strike_at_itm_depth


class TestOTMStrike:
    def test_negative_depth_is_otm(self):
        assert select_strike_at_itm_depth(56020, SignalType.BUY_CE, 100, -1) == 56100
        assert select_strike_at_itm_depth(56020, SignalType.BUY_PE, 100, -1) == 55900

    def test_v1_depths_unchanged(self):
        assert select_strike_at_itm_depth(56020, SignalType.BUY_CE, 100, 0) == 56000
        assert select_strike_at_itm_depth(56020, SignalType.BUY_CE, 100, 2) == 55800
        assert select_strike_at_itm_depth(56020, SignalType.BUY_PE, 100, 1) == 56100


async def _fake_resolve(*, symbol, index_price, signal_type, sl_pct, rr_multiplier=1.0,
                        itm_offsets=None, **kw):
    strike = select_strike_at_itm_depth(index_price, signal_type, STRIKE_GAPS[symbol], itm_offsets[0])
    ot = "CE" if signal_type == SignalType.BUY_CE else "PE"
    return OptionResolution(strike_price=strike, expiry_date=date(2026, 10, 13), option_premium=200.0,
                            fyers_option_symbol=f"NSE:{symbol}{int(strike)}{ot}",
                            sl_price=0, target_price=0)


def _run(direction="CE", legs=("BANKNIFTY", "NIFTY", "SENSEX"), excluded=()):
    return SimpleNamespace(
        trading_date=date(2026, 10, 9), confidence=55,
        call2_json={"decision": "ENTER", "direction": direction, "_at": "09:16",
                    "legs": [{"index": i} for i in legs],
                    "excluded_indices": [{"index": i, "reason": "x"} for i in excluded],
                    "_gates": {"plan": {"verdict": "AGREES"}}},
    )


SPOTS = {"BANKNIFTY": 56020.0, "NIFTY": 25030.0, "SENSEX": 82140.0}


async def _emit(run, active=True):
    handle = AsyncMock()
    with patch.object(v2sig, "resolve_option_details", side_effect=_fake_resolve), \
         patch.object(v2sig, "already_emitted_today", new=AsyncMock(return_value=False)), \
         patch.object(v2sig, "v2_active", new=AsyncMock(return_value=active)), \
         patch("app.data_feed.fyers_ws_client.fyers_ws_client.subscribe_symbols", new=AsyncMock()), \
         patch("app.services.strategy_runner.strategy_runner._handle_signal", new=handle):
        labels = await v2sig.emit_signals_for_enter(MagicMock(), run, SPOTS, dict(INTRADAY_HUNTER_V2_DEFAULTS))
    return labels, [c.args[0] for c in handle.call_args_list]


class TestV2Signals:
    @pytest.mark.asyncio
    async def test_his_shape_bn_atm_otm_nifty_sensex_atm(self):
        labels, sigs = await _emit(_run("CE"))
        assert [(s.symbol, s.strike_price, s.indicators["ih_leg"]) for s in sigs] == [
            ("NIFTY", 25050.0, "ATM"),           # NIFTY gap 50
            ("BANKNIFTY", 56000.0, "ATM"),
            ("BANKNIFTY", 56100.0, "OTM-1"),     # CE OTM = one strike UP
            ("SENSEX", 82100.0, "ATM"),
        ]
        assert all(s.strategy_name == StrategyName.INTRADAY_HUNTER_V2 for s in sigs)
        assert len(labels) == 4

    @pytest.mark.asyncio
    async def test_pe_otm_down_and_informational_band(self):
        _, sigs = await _emit(_run("PE", legs=("BANKNIFTY",)))
        assert [s.strike_price for s in sigs] == [56000.0, 55900.0]
        s = sigs[0]
        assert s.stop_loss == 160.0 and s.target_price == 240.0  # ±basket_tp_sl_pct of premium
        assert s.indicators["ih_gates"] == {"plan": {"verdict": "AGREES"}}

    @pytest.mark.asyncio
    async def test_excluded_index_and_kill_switch(self):
        _, sigs = await _emit(_run("CE", excluded=("SENSEX",)))
        assert {s.symbol for s in sigs} == {"NIFTY", "BANKNIFTY"}
        labels, sigs = await _emit(_run("CE"), active=False)
        assert labels == [] and sigs == []

    def test_traded_indices_default_all(self):
        assert v2sig.traded_indices({"legs": []}) == ["NIFTY", "BANKNIFTY", "SENSEX"]


class TestKillSwitch:
    @pytest.mark.asyncio
    async def test_env_flag_off(self):
        v2params.reset_active_cache()
        with patch("app.config.settings.intraday_hunter_v2_enabled", False):
            assert await v2params.v2_active() is False

    @pytest.mark.asyncio
    async def test_reads_is_active_and_fails_closed(self):
        v2params.reset_active_cache()

        def factory(val=None, exc=None):
            sess = AsyncMock()
            res = MagicMock()
            res.scalar_one_or_none.return_value = val
            sess.execute = AsyncMock(side_effect=exc, return_value=res)
            cm = MagicMock()
            cm.__aenter__ = AsyncMock(return_value=sess)
            cm.__aexit__ = AsyncMock(return_value=False)
            return MagicMock(return_value=cm)

        with patch("app.config.settings.intraday_hunter_v2_enabled", True):
            with patch("app.core.database.async_session_factory", factory(val=False)):
                assert await v2params.v2_active() is False
            v2params.reset_active_cache()
            with patch("app.core.database.async_session_factory", factory(val=True)):
                assert await v2params.v2_active() is True
                # cached within the TTL even if the DB flips
            with patch("app.core.database.async_session_factory", factory(val=False)):
                assert await v2params.v2_active() is True
            v2params.reset_active_cache()
            with patch("app.core.database.async_session_factory", factory(exc=RuntimeError("db"))):
                assert await v2params.v2_active() is False
        v2params.reset_active_cache()


class TestWatcherCadence:
    async def _drive(self, minutes, decisions):
        from app.services.intraday_hunter_v2 import watcher as wmod

        w = wmod.V2Watcher()
        calls = []

        async def fake_run_once(d, now, t_hook):
            calls.append(now.strftime("%H:%M"))
            if decisions.get(now.strftime("%H:%M")) in ("ENTER", "SKIP"):
                w._done = True

        with patch.object(wmod, "v2_active", new=AsyncMock(return_value=True)), \
             patch.object(wmod, "v2_params_async", new=AsyncMock(return_value=dict(INTRADAY_HUNTER_V2_DEFAULTS))), \
             patch.object(wmod, "is_trading_day", return_value=True), \
             patch.object(w, "_run_once", side_effect=fake_run_once):
            for m in minutes:
                await w.maybe_run(date(2026, 10, 9), time(9, m))
        return calls

    @pytest.mark.asyncio
    async def test_first_at_0916_every_minute_to_deadline(self):
        calls = await self._drive(range(14, 30), {})
        assert calls == [f"09:{m}" for m in range(16, 26)]

    @pytest.mark.asyncio
    async def test_enter_finalizes(self):
        assert await self._drive(range(15, 30), {"09:18": "ENTER"}) == ["09:16", "09:17", "09:18"]

    @pytest.mark.asyncio
    async def test_kill_switch_stops_watcher(self):
        from app.services.intraday_hunter_v2 import watcher as wmod

        w = wmod.V2Watcher()
        with patch.object(wmod, "v2_active", new=AsyncMock(return_value=False)), \
             patch.object(wmod, "is_trading_day", return_value=True), \
             patch.object(w, "_run_once", new=AsyncMock()) as ro:
            await w.maybe_run(date(2026, 10, 9), time(9, 16))
        ro.assert_not_called()


class TestLlmCliLoginOptIn:
    """llm_cli default is unchanged: no token → no call. IH_ALLOW_CLI_LOGIN=1 (dev only) uses
    the CLI's own login and never injects an empty token."""

    @pytest.mark.asyncio
    async def test_default_requires_token(self, monkeypatch):
        from app.services.intraday_hunter import llm_cli

        monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
        monkeypatch.delenv("IH_ALLOW_CLI_LOGIN", raising=False)
        with patch("asyncio.create_subprocess_exec", new=AsyncMock()) as spawn:
            assert await llm_cli.call_claude_json("hi") is None
        spawn.assert_not_called()

    def test_opt_in_keeps_cli_login(self, monkeypatch):
        from app.services.intraday_hunter import llm_cli

        monkeypatch.setenv("IH_ALLOW_CLI_LOGIN", "1")
        monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
        assert llm_cli._cli_login_allowed()
        assert "CLAUDE_CODE_OAUTH_TOKEN" not in llm_cli._child_env(None)
        assert llm_cli._child_env("tok")["CLAUDE_CODE_OAUTH_TOKEN"] == "tok"


class TestCaptureJobAlerts:
    """capture_job alerts loudly when an index ends with no contracts (the silent-zero case)."""

    async def _run(self, res, contracts):
        from app.services.intraday_hunter_v2 import capture as cap_mod
        from app.tasks import intraday_hunter_v2_task as task

        alert = AsyncMock()
        with patch.object(task, "_enabled_today", new=AsyncMock(return_value=True)), \
             patch.object(task, "alert", new=alert), \
             patch.object(cap_mod, "capture_atm_ladder", new=AsyncMock(return_value=res)), \
             patch.object(cap_mod, "load_capture",
                          new=AsyncMock(return_value={"symbols": [], "contracts": contracts})):
            await task.capture_job("09:10")
        return alert

    @pytest.mark.asyncio
    async def test_zero_contracts_alerts(self):
        alert = await self._run({"added": 0, "total": 0}, {})
        alert.assert_awaited_once()
        assert alert.call_args.args[0] == "capture_empty"
        assert "NIFTY, BANKNIFTY, SENSEX" in alert.call_args.args[1]

    @pytest.mark.asyncio
    async def test_one_index_missing_alerts_only_that_index(self):
        row = [{"strike": 1, "type": "CE", "symbol": "X"}]
        alert = await self._run({"added": 20, "total": 20}, {"NIFTY": row, "BANKNIFTY": row})
        assert "SENSEX" in alert.call_args.args[1] and "NIFTY" not in alert.call_args.args[1]

    @pytest.mark.asyncio
    async def test_full_capture_and_kill_switch_are_quiet(self):
        row = [{"strike": 1, "type": "CE", "symbol": "X"}]
        full = {"NIFTY": row, "BANKNIFTY": row, "SENSEX": row}
        (await self._run({"added": 30, "total": 30}, full)).assert_not_awaited()
        (await self._run({"skipped": "kill switch off"}, {})).assert_not_awaited()
