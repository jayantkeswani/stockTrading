"""IH v2 Jev shadow arm: the OpenRouter decisions client never raises, and the minute log
records its answer (or its error) in arms.jev without disturbing the rest of the row."""
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.core.constants import IST
from app.services.intraday_hunter_v2 import jev
from app.services.intraday_hunter_v2 import minute_log as ml
from app.services.intraday_hunter_v2.params import INTRADAY_HUNTER_V2_DEFAULTS

# The decisions response shape seen live (2026-10-09, typesafe/jev-1.13).
LIVE_PAYLOAD = {
    "model": "typesafe/jev-1.13",
    "answers": {
        "side": {"type": "choice", "choice": "CE", "confidence": 0.93,
                 "probabilities": {"CE": 0.93, "PE": 0.04, "WAIT": 0.03}},
        "pool_broken_and_riding": {"type": "noul", "noul": 0.88},
        "hold": {"type": "choice", "choice": "HOLD", "confidence": 0.81,
                 "probabilities": {"HOLD": 0.81, "EXIT": 0.19}},
    },
    "usage": {"cost": 0.0004},
}


def _enabled():
    return patch.multiple("app.config.settings", jev_enabled=True, openrouter_api_key="k")


def _client(post):
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=MagicMock(post=post))
    cm.__aexit__ = AsyncMock(return_value=False)
    return patch.object(jev.httpx, "AsyncClient", return_value=cm)


class TestJevClient:
    @pytest.mark.asyncio
    async def test_disabled_without_flag_or_key(self):
        with patch.multiple("app.config.settings", jev_enabled=True, openrouter_api_key=""):
            assert await jev.ask({}) == {"error": "disabled"}
        with patch.multiple("app.config.settings", jev_enabled=False, openrouter_api_key="k"):
            assert await jev.ask({}) == {"error": "disabled"}

    @pytest.mark.asyncio
    async def test_parses_live_payload(self):
        resp = MagicMock(status_code=200, json=MagicMock(return_value=LIVE_PAYLOAD))
        post = AsyncMock(return_value=resp)
        with _enabled(), _client(post):
            out = await jev.ask({"time": "09:18"}, in_position=True)
        assert out["answers"]["side"]["answer"] == "CE"
        assert out["answers"]["pool_broken_and_riding"] == {"answer": True, "p": 0.88}
        assert out["answers"]["hold"]["answer"] == "HOLD"
        assert out["cost"] == 0.0004 and "latency_ms" in out
        body = post.call_args.kwargs["json"]
        assert body["model"] == "typesafe/jev-1.13" and "hold" in body["questions"]
        assert post.call_args.kwargs["headers"]["Authorization"] == "Bearer k"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("post", [
        AsyncMock(return_value=MagicMock(status_code=429, text="rate limited")),
        AsyncMock(side_effect=httpx.ConnectTimeout("t")),
        AsyncMock(return_value=MagicMock(status_code=200, json=MagicMock(side_effect=ValueError))),
    ])
    async def test_failures_return_error_never_raise(self, post):
        with _enabled(), _client(post):
            out = await jev.ask({"time": "09:18"})
        assert "error" in out and "answers" not in out


def _patches(jev_result):
    """Stub every collaborator of _log_minute; capture the rows handed to the upsert."""
    captured: dict = {}
    fake_insert = MagicMock()

    def values(rows):
        captured["rows"] = rows
        return MagicMock()

    fake_insert.return_value.values.side_effect = values
    sess = MagicMock(execute=AsyncMock(), commit=AsyncMock())
    cm = MagicMock(__aenter__=AsyncMock(return_value=sess), __aexit__=AsyncMock(return_value=False))
    facts = {"NIFTY": {"last": 22200.0}, "BANKNIFTY": {"last": 54400.0}}
    ps = [
        patch.object(ml, "v2_active", new=AsyncMock(return_value=True)),
        patch.object(ml, "v2_params_async", new=AsyncMock(return_value=dict(INTRADAY_HUNTER_V2_DEFAULTS))),
        patch.object(ml, "async_session_factory", return_value=cm),
        patch.object(ml, "_v2_position_open", new=AsyncMock(return_value=False)),
        patch.object(ml.ctx_mod, "prev_day_all", new=AsyncMock(return_value={})),
        patch.object(ml.ctx_mod, "today_candles", new=AsyncMock(return_value={})),
        patch.object(ml.ctx_mod, "get_teacher_day", new=AsyncMock(return_value=None)),
        patch.object(ml.ctx_mod, "facts_by_index", return_value=facts),
        patch.object(ml.ctx_mod, "opening_of", return_value="gap_down"),
        patch.object(ml.ctx_mod, "plan_side_today", return_value=None),
        patch.object(ml.ctx_mod, "vix_now", new=AsyncMock(return_value=15.2)),
        patch.object(ml.oi_flow, "opening_oi_flow", new=AsyncMock(return_value={"side": "PE"})),
        patch.object(ml.store, "get_run", new=AsyncMock(return_value=None)),
        patch.object(ml.levels, "rule_a_side", return_value="PE"),
        patch.object(ml.levels, "describe_facts", return_value=["NIFTY broke PDL"]),
        patch.object(ml.capture, "load_capture", new=AsyncMock(return_value={"contracts": {}})),
        patch.object(ml, "get_cached_price", new=AsyncMock(return_value={"ltp": 22200.0})),
        patch.object(ml, "pg_insert", fake_insert),
        patch.object(ml.jev, "is_enabled", return_value=True),
        patch.object(ml.jev, "ask", new=AsyncMock(return_value=jev_result)),
    ]
    return ps, captured


async def _log(jev_result, hhmm=(9, 17)):
    ps, captured = _patches(jev_result)
    for p in ps:
        p.start()
    try:
        n = await ml.log_minute(datetime(2026, 10, 9, *hhmm, tzinfo=IST), settle_s=0)
        asked = ml.jev.ask.await_count
    finally:
        for p in ps:
            p.stop()
    return n, captured.get("rows") or [], asked


class TestMinuteLogJevArm:
    @pytest.mark.asyncio
    async def test_fills_arms_jev_in_call2_window(self):
        ans = {"model": "typesafe/jev-1.13", "answers": {"side": {"answer": "PE"}}, "latency_ms": 600}
        n, rows, asked = await _log(ans)
        assert n == 2 and asked == 1
        assert all(r["arms"]["jev"] == ans for r in rows)
        assert rows[0]["arms"]["rule_a"] == "PE"  # the other arms are still built

    @pytest.mark.asyncio
    async def test_jev_error_is_isolated(self):
        n, rows, _ = await _log({"error": "ConnectTimeout", "latency_ms": 3000})
        assert n == 2
        assert rows[0]["arms"]["jev"]["error"] == "ConnectTimeout"
        assert rows[0]["arms"]["oi_flow_side"] == "PE" and rows[0]["features"]["india_vix"] == 15.2

    @pytest.mark.asyncio
    async def test_not_asked_outside_window_when_flat(self):
        # 10:00 candle → decision 10:01, past the 09:25 deadline and no open position.
        n, rows, asked = await _log({"answers": {}}, hhmm=(10, 0))
        assert asked == 0 and all(r["arms"]["jev"] is None for r in rows)


class TestMinuteLogReseedsOrderFlow:
    @pytest.mark.asyncio
    async def test_restart_reseeds_tracker_from_capture(self):
        from app.services.intraday_hunter_v2.orderflow import OrderFlowTracker

        tracker = OrderFlowTracker()  # a fresh process after a mid-day restart
        ps, _ = _patches({"answers": {}})
        cap = {"contracts": {}, "symbols": ["BSE:SENSEX26O1571900CE", "NSE:NIFTY26O1322350PE"]}
        ps += [patch.object(ml.capture, "load_capture", new=AsyncMock(return_value=cap)),
               patch.object(ml, "orderflow_tracker", tracker)]
        for p in ps:
            p.start()
        try:
            await ml.log_minute(datetime(2026, 10, 9, 9, 40, tzinfo=IST), settle_s=0)
        finally:
            for p in ps:
                p.stop()
        assert {"BSE:SENSEX26O1571900CE", "NSE:NIFTY26O1322350PE",
                "NIFTY_FUT", "BANKNIFTY_FUT"} <= tracker.tracked
