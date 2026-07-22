"""Tests for Intraday Hunter signal emission — leg structure + symmetric 1:1 SL/target.

BANKNIFTY expands to two legs (ITM-2 + ITM-1); NIFTY/SENSEX one ITM-1 leg. SL/target are
sized at IH_SL_TGT_PCT of premium (informational index_sl/index_target are also derived
via a delta approximation, for display only — the monitor exits on the option premium).
"""
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.constants import STRIKE_GAPS
from app.core.enums import SignalType
from app.services.intraday_hunter import signals as ih_signals
from app.services.intraday_hunter.signals import IH_SL_TGT_PCT, emit_signals_for_enter
from app.services.option_resolver import OptionResolution, select_strike_at_itm_depth

_PREMIUM = 300.0


async def _fake_resolve(*, symbol, index_price, signal_type, sl_pct, rr_multiplier=1.0,
                        itm_offsets=None, **kw):
    """Resolve to the strike at the FIRST requested ITM depth (mirrors the real fallback order)."""
    gap = STRIKE_GAPS[symbol]
    depth = itm_offsets[0] if itm_offsets else 0
    strike = select_strike_at_itm_depth(index_price, signal_type, gap, depth)
    ot = "CE" if signal_type == SignalType.BUY_CE else "PE"
    return OptionResolution(
        strike_price=strike,
        expiry_date=date(2026, 7, 29),
        option_premium=_PREMIUM,
        fyers_option_symbol=f"NSE:{symbol}{int(strike)}{ot}",
        sl_price=210.0,
        target_price=390.0,
    )


def _run(direction="PE", indices=("BANKNIFTY", "NIFTY", "SENSEX")):
    legs = [{"index": idx, "option_type": direction} for idx in indices]
    return SimpleNamespace(
        trading_date=date(2026, 7, 1),
        confidence=62,
        call2_json={
            "decision": "ENTER", "direction": direction, "thesis": "PDL breakdown",
            "regime": "momentum", "trapped_side": "buyers", "_at": "09:18", "legs": legs,
        },
    )


_LIVE = {"indices": {
    "BANKNIFTY": {"last_price": 55230.0},
    "NIFTY": {"last_price": 24800.0},
    "SENSEX": {"last_price": 80500.0},
}}


async def _emit(run, live=_LIVE):
    mock_handle = AsyncMock()
    with patch.object(ih_signals, "resolve_option_details", side_effect=_fake_resolve), \
         patch.object(ih_signals, "_already_emitted_today", new=AsyncMock(return_value=False)), \
         patch("app.data_feed.fyers_ws_client.fyers_ws_client.subscribe_symbols",
               new=AsyncMock()), \
         patch("app.services.strategy_runner.strategy_runner._handle_signal", new=mock_handle):
        emitted = await emit_signals_for_enter(MagicMock(), run, live)
    sigs = [c.args[0] for c in mock_handle.call_args_list]
    return emitted, sigs


@pytest.mark.asyncio
async def test_banknifty_two_legs_nifty_sensex_one():
    """BANKNIFTY → ITM-2 + ITM-1 (2 legs); NIFTY/SENSEX → ITM-1 (1 leg each) = 4 signals."""
    _, sigs = await _emit(_run("PE"))
    assert len(sigs) == 4

    by_index: dict[str, list] = {}
    for s in sigs:
        by_index.setdefault(s.symbol, []).append(s)

    # BANKNIFTY: two distinct ITM strikes (PE goes UP: ATM 55200 -> 55400 / 55300).
    bn_strikes = sorted(s.strike_price for s in by_index["BANKNIFTY"])
    assert bn_strikes == [55300, 55400]
    bn_depths = sorted(s.indicators["ih_itm_depth"] for s in by_index["BANKNIFTY"])
    assert bn_depths == [1, 2]

    # NIFTY / SENSEX: single ITM-1 leg.
    assert len(by_index["NIFTY"]) == 1
    assert by_index["NIFTY"][0].strike_price == 24850  # ATM 24800 + 50 (PE ITM)
    assert len(by_index["SENSEX"]) == 1
    assert by_index["SENSEX"][0].strike_price == 80600  # ATM 80500 + 100


@pytest.mark.asyncio
async def test_premium_sl_target_is_symmetric():
    """Premium stop/target = entry ∓ IH_SL_TGT_PCT of premium (1:1)."""
    _, sigs = await _emit(_run("PE"))
    pm = IH_SL_TGT_PCT * _PREMIUM
    for s in sigs:
        assert s.entry_price == _PREMIUM
        assert s.stop_loss == round(_PREMIUM - pm, 2)
        assert s.target_price == round(_PREMIUM + pm, 2)


@pytest.mark.asyncio
async def test_index_levels_symmetric_around_spot_pe():
    """PE: index_sl ABOVE spot, index_target BELOW, equidistant (1:1 on the index)."""
    _, sigs = await _emit(_run("PE"))
    nifty = next(s for s in sigs if s.symbol == "NIFTY")
    spot = 24800.0
    up = nifty.index_sl - spot
    down = spot - nifty.index_target
    assert nifty.index_sl > spot and nifty.index_target < spot
    assert abs(up - down) < 0.01  # symmetric
    # ITM-1 delta 0.60 → index move = (IH_SL_TGT_PCT * premium) / 0.60
    expected_move = (IH_SL_TGT_PCT * _PREMIUM) / 0.60
    assert abs(up - expected_move) < 0.5


@pytest.mark.asyncio
async def test_ce_index_levels_mirror():
    """CE: index_sl BELOW spot, index_target ABOVE."""
    _, sigs = await _emit(_run("CE"))
    nifty = next(s for s in sigs if s.symbol == "NIFTY")
    assert nifty.index_sl < 24800.0 < nifty.index_target


@pytest.mark.asyncio
async def test_idempotent_when_already_emitted():
    """No re-emit if an IH signal already exists for the day."""
    mock_handle = AsyncMock()
    with patch.object(ih_signals, "resolve_option_details", side_effect=_fake_resolve), \
         patch.object(ih_signals, "_already_emitted_today", new=AsyncMock(return_value=True)), \
         patch("app.services.strategy_runner.strategy_runner._handle_signal", new=mock_handle):
        emitted = await emit_signals_for_enter(MagicMock(), _run("PE"), _LIVE)
    assert emitted == []
    mock_handle.assert_not_called()
