"""Intraday Hunter v1 basket-level exits (trade_monitor._check_ih_baskets).

v1 legs exit TOGETHER on basket MTM ±T with v1's OWN params (rupees per lot, 11:30 backstop,
no round-hold) — grouped separately from v2's baskets on the same day/book.
"""
from datetime import date, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.constants import IST
from app.core.enums import ExitReason, StrategyName
from app.services.intraday_hunter.params import INTRADAY_HUNTER_DEFAULTS
from app.services.intraday_hunter_v2.basket import LegQuote, basket_target

V1 = StrategyName.INTRADAY_HUNTER.value
V2 = StrategyName.INTRADAY_HUNTER_V2.value
DAY = date(2026, 10, 16)


def _leg(index: str, option_type: str = "CE"):
    return (SimpleNamespace(symbol=index, option_type=option_type), SimpleNamespace())


# v1's real basket: BN ITM-2 + ITM-1 (2 lots each), NIFTY ITM-1, SENSEX ITM-1 (2 lots each).
_V1_LEGS = [_leg("BANKNIFTY"), _leg("BANKNIFTY"), _leg("NIFTY"), _leg("SENSEX")]


def _quotes(pnl_per_leg: float):
    """2-lot legs, each showing `pnl_per_leg` rupees of MTM at the bid."""
    spec = [("BANKNIFTY", 60, 900.0), ("BANKNIFTY", 60, 850.0), ("NIFTY", 150, 100.0),
            ("SENSEX", 20, 530.0)]
    return [LegQuote(index=i, qty=q, entry_price=e, bid=e + pnl_per_leg / q, ltp=e, lots=2)
            for i, q, e in spec]


def test_v1_default_t_is_the_full_four_leg_rupee_book():
    """2 lots on BN ITM-2 + ITM-1 + NIFTY + SENSEX → T = 4×3915 + 2×1640 + 2×862 = ₹20,664."""
    lots = {"BANKNIFTY": 4, "NIFTY": 2, "SENSEX": 2}
    assert basket_target(INTRADAY_HUNTER_DEFAULTS, cost=1.0, lots=lots) == 20664.0
    assert INTRADAY_HUNTER_DEFAULTS["basket_time_exit"] == "11:30"
    assert INTRADAY_HUNTER_DEFAULTS["round_hold_enabled"] is False


async def _run_check(groups: dict, quotes_by_strategy: dict, now: datetime,
                     params_by_strategy=None):
    from app.agent import trade_monitor

    params_by_strategy = params_by_strategy or {V1: dict(INTRADAY_HUNTER_DEFAULTS)}

    async def fake_quotes(legs):
        strat = next(k[0] for k, v in groups.items() if v is legs)
        return quotes_by_strategy[strat], {}

    async def fake_params(strategy):
        return params_by_strategy[strategy]

    trade_monitor._ih_basket_state.clear()
    closer = AsyncMock(
        side_effect=lambda db, legs, reason, action: [{"reason": reason.value}] * len(legs)
    )
    with patch.object(trade_monitor, "_ih_open_baskets", new=AsyncMock(return_value=groups)), \
         patch.object(trade_monitor, "_ih_quotes", side_effect=fake_quotes), \
         patch.object(trade_monitor, "_ih_basket_params", side_effect=fake_params), \
         patch.object(trade_monitor, "_close_ih_basket", new=closer), \
         patch.object(trade_monitor, "now_ist", return_value=now), \
         patch("app.services.trading_config.get_trading_config_sync",
               return_value=SimpleNamespace(fill_model="BID_ASK")), \
         patch("app.agent.notification.send_telegram", new=AsyncMock()):
        actions = await trade_monitor._check_ih_baskets(MagicMock())
    return actions, closer


@pytest.mark.asyncio
async def test_v1_basket_closes_all_legs_together_at_plus_t():
    groups = {(V1, DAY, "prof-1"): list(_V1_LEGS)}
    # +5,200 per leg → MTM +20,800 ≥ T 20,664
    actions, closer = await _run_check(groups, {V1: _quotes(5200.0)},
                                       datetime(2026, 10, 16, 9, 40, tzinfo=IST))
    closer.assert_awaited_once()
    assert closer.await_args.args[2] == ExitReason.BASKET_TARGET
    assert len(actions) == 4  # every leg, including both BANKNIFTY strikes


@pytest.mark.asyncio
async def test_v1_basket_stops_all_legs_together_at_minus_t():
    groups = {(V1, DAY, "SHADOW"): list(_V1_LEGS)}
    actions, closer = await _run_check(groups, {V1: _quotes(-5200.0)},
                                       datetime(2026, 10, 16, 9, 40, tzinfo=IST))
    assert closer.await_args.args[2] == ExitReason.BASKET_STOP
    assert len(actions) == 4


@pytest.mark.asyncio
async def test_v1_basket_holds_inside_band_and_times_out_at_1130():
    groups = {(V1, DAY, "prof-1"): list(_V1_LEGS)}
    actions, closer = await _run_check(groups, {V1: _quotes(2000.0)},
                                       datetime(2026, 10, 16, 10, 15, tzinfo=IST))
    assert actions is None
    closer.assert_not_awaited()
    actions, closer = await _run_check(groups, {V1: _quotes(2000.0)},
                                       datetime(2026, 10, 16, 11, 30, tzinfo=IST))
    assert closer.await_args.args[2] == ExitReason.BASKET_TIME


@pytest.mark.asyncio
async def test_v1_and_v2_baskets_use_their_own_params():
    """Same MTM, different T: v2 (tight pct band) closes, v1 (rupee band) holds."""
    groups = {(V1, DAY, "prof-1"): list(_V1_LEGS), (V2, DAY, "prof-2"): list(_V1_LEGS)}
    q = _quotes(2000.0)  # MTM +8,000 — inside v1's ±20,664 band
    cost = sum(x.entry_price * x.qty for x in q)
    # v2 on a pct band sized so T = 7,000 < MTM 8,000
    v2_params = {**INTRADAY_HUNTER_DEFAULTS, "basket_t_mode": "pct",
                 "basket_tp_sl_pct": 7000.0 / cost}
    actions, closer = await _run_check(
        groups, {V1: q, V2: q}, datetime(2026, 10, 16, 9, 40, tzinfo=IST),
        params_by_strategy={V1: dict(INTRADAY_HUNTER_DEFAULTS), V2: v2_params},
    )
    closer.assert_awaited_once()  # only the v2 basket
    assert closer.await_args.args[1] is groups[(V2, DAY, "prof-2")]
    assert closer.await_args.args[2] == ExitReason.BASKET_TARGET
