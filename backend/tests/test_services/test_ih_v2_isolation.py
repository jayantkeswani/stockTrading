"""IH v2 must never change v1 (or any other strategy) — isolation tests.

Mock-level: the trade monitor still runs v1's per-leg SL/target exactly as before when v2 legs
exist (v2 legs are skipped by the per-leg checks), and a crashing v2 basket check never stops
the per-position monitor.
DB-level (local Postgres; skipped when unreachable): with v2 rows present, v1's run lookup,
its open-position check and its signal dedup behave exactly as if v2 did not exist; v2 itself is
strike-aware and isolated.
"""
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.enums import ExitReason, InstrumentType, SignalType, StrategyName

# ───────────────────────────── trade monitor (mocks) ─────────────────────────────


def _pos(strategy, price_symbol="NSE:NIFTY26OCT25000CE", **kw):
    pos = MagicMock()
    pos.id, pos.trade_id = uuid.uuid4(), uuid.uuid4()
    pos.symbol = kw.get("symbol", "NIFTY")
    pos.entry_price, pos.stop_loss, pos.target_price = Decimal("200"), Decimal("80"), Decimal("320")
    pos.current_price = None
    pos.unrealized_pnl = None
    pos.fyers_option_symbol = price_symbol
    pos.strategy_name = strategy
    pos.position_type = "INTRADAY"
    pos.lots, pos.quantity = 2, 150
    pos.is_shadow = kw.get("is_shadow", False)
    pos.yolo_profile_id = None
    pos.created_at = datetime.now(timezone.utc) - timedelta(minutes=10)
    pos.high_since_entry = None
    return pos


def _db_returning_trade():
    trade = MagicMock()
    trade.entry_price, trade.quantity, trade.side, trade.option_type = Decimal("200"), 150, "BUY", "CE"
    trade.stop_loss = Decimal("80")
    trade.fill_meta = {}
    res = MagicMock()
    res.scalar_one_or_none.return_value = trade
    db = AsyncMock()
    db.execute = AsyncMock(return_value=res)
    db.add = MagicMock()
    return db


@pytest.mark.asyncio
@patch("app.agent.trade_monitor.ws_manager")
@patch("app.agent.trade_monitor.notify_sl_hit", new_callable=AsyncMock)
@patch("app.agent.trade_monitor.get_cached_price", new_callable=AsyncMock)
async def test_v1_leg_still_hits_its_own_sl(mock_price, _n, mock_ws):
    from app.agent.trade_monitor import _check_position

    mock_ws.broadcast = AsyncMock()
    mock_price.return_value = {"ltp": 75.0}
    db = _db_returning_trade()
    with patch("app.agent.trade_monitor._check_per_lot_stop", new=AsyncMock(return_value=None)), \
         patch("app.services.live_price.get_fill_price", side_effect=Exception("no quote")):
        action = await _check_position(db, _pos(StrategyName.INTRADAY_HUNTER.value))
    assert action is not None and action["details"]["reason"] == ExitReason.AGENT_SL.value


@pytest.mark.asyncio
@patch("app.agent.trade_monitor.is_past_close_deadline", return_value=False)
@patch("app.agent.trade_monitor.ws_manager")
@patch("app.agent.trade_monitor.get_cached_price", new_callable=AsyncMock)
async def test_v2_leg_bypasses_per_leg_sl_and_target(mock_price, mock_ws, _d):
    from app.agent.trade_monitor import _check_position

    mock_ws.broadcast = AsyncMock()
    db = _db_returning_trade()
    for ltp in (10.0, 900.0):  # far below SL and far above target
        mock_price.return_value = {"ltp": ltp}
        pos = _pos(StrategyName.INTRADAY_HUNTER_V2.value)
        assert await _check_position(db, pos) is None
        assert pos.current_price == Decimal(str(ltp))  # MTM still updated
    db.delete.assert_not_called()


@pytest.mark.asyncio
@patch("app.agent.trade_monitor.is_past_close_deadline", return_value=True)
@patch("app.agent.trade_monitor.ws_manager")
@patch("app.agent.trade_monitor.get_cached_price", new_callable=AsyncMock)
async def test_v2_leg_falls_back_to_1525_close_when_basket_check_throws(mock_price, mock_ws, _d):
    """A permanently failing basket check must never leave v2 legs open past 15:25."""
    from app.agent import trade_monitor

    mock_ws.broadcast = AsyncMock()
    mock_price.return_value = {"ltp": 150.0}
    leg = _pos(StrategyName.INTRADAY_HUNTER_V2.value)
    res = MagicMock()
    res.scalars.return_value.all.return_value = [leg]
    res.scalar_one_or_none.return_value = _db_returning_trade().execute.return_value.scalar_one_or_none()
    db = AsyncMock()
    db.execute = AsyncMock(return_value=res)
    db.add = MagicMock()
    trade_monitor._ih_v2_basket_alerted.clear()
    with patch.object(trade_monitor, "_check_pnl_caps", new=AsyncMock(return_value=None)), \
         patch.object(trade_monitor, "_check_ih_v2_baskets", new=AsyncMock(side_effect=RuntimeError("boom"))), \
         patch("app.services.live_price.get_fill_price", side_effect=Exception("no quote")), \
         patch("app.agent.notification.send_telegram", new=AsyncMock()) as tg:
        actions = await trade_monitor.monitor_positions(db, yolo_mode=True)
        await trade_monitor.monitor_positions(db, yolo_mode=True)
    assert actions and actions[0]["details"]["reason"] == ExitReason.TIME_EXIT.value
    basket_alerts = [c for c in tg.await_args_list if "basket exit check is FAILING" in c.args[0]]
    assert len(basket_alerts) == 1  # once per day, not once per poll


def test_basket_keys_separate_shadow_and_yolo():
    from app.agent.trade_monitor import _ih_v2_basket_key

    prof = uuid.uuid4()
    entry = datetime(2026, 10, 9, 3, 47, tzinfo=timezone.utc)  # 09:17 IST
    t = MagicMock(entry_time=entry)
    shadow = _pos(StrategyName.INTRADAY_HUNTER_V2.value, is_shadow=True)
    yolo = _pos(StrategyName.INTRADAY_HUNTER_V2.value)
    yolo.yolo_profile_id = prof
    ks, ky = _ih_v2_basket_key(shadow, t), _ih_v2_basket_key(yolo, t)
    assert ks == (date(2026, 10, 9), "SHADOW") and ky == (date(2026, 10, 9), str(prof))
    assert ks != ky
    # entry_time missing → falls back to opened_at
    t2 = MagicMock(entry_time=None)
    yolo.opened_at = entry
    assert _ih_v2_basket_key(yolo, t2) == ky


@pytest.mark.asyncio
@patch("app.agent.trade_monitor.get_cached_price", new_callable=AsyncMock, return_value=None)
async def test_v2_shadow_leg_is_never_stale_closed_alone(_p):
    from app.agent.trade_monitor import _check_position

    pos = _pos(StrategyName.INTRADAY_HUNTER_V2.value, is_shadow=True)
    pos.created_at = datetime.now(timezone.utc) - timedelta(minutes=30)
    with patch("app.agent.trade_monitor._fetch_option_price_rest", new=AsyncMock(return_value=None)), \
         patch("app.agent.trade_monitor._close_stale_shadow", new=AsyncMock()) as stale:
        assert await _check_position(AsyncMock(), pos) is None
    stale.assert_not_called()


@pytest.mark.asyncio
async def test_v2_basket_crash_never_stops_the_monitor():
    from app.agent import trade_monitor

    v1 = _pos(StrategyName.INTRADAY_HUNTER.value)
    res = MagicMock()
    res.scalars.return_value.all.return_value = [v1]
    db = AsyncMock()
    db.execute = AsyncMock(return_value=res)
    with patch.object(trade_monitor, "_check_pnl_caps", new=AsyncMock(return_value=None)), \
         patch.object(trade_monitor, "_check_ih_v2_baskets", new=AsyncMock(side_effect=RuntimeError("boom"))), \
         patch.object(trade_monitor, "_check_position", new=AsyncMock(return_value={"ok": 1})) as chk:
        actions = await trade_monitor.monitor_positions(db, yolo_mode=True)
    chk.assert_awaited_once()
    assert actions == [{"ok": 1}]


# ───────────────────────────── DB-backed (local Postgres) ─────────────────────────────
TEST_DATE = date(2099, 1, 5)
SYM = "ZZIHTEST"


@pytest.fixture
async def dbf():
    """A session factory on a fresh engine (own event loop); skips if Postgres is down."""
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.config import settings

    eng = create_async_engine(settings.database_url, pool_pre_ping=True)
    try:
        async with eng.connect() as c:
            await c.execute(text("SELECT 1 FROM intraday_hunter_runs LIMIT 1"))
            await c.execute(text("SELECT variant FROM intraday_hunter_runs LIMIT 1"))
    except Exception as e:  # noqa: BLE001
        await eng.dispose()
        pytest.skip(f"local Postgres with the v2 migration not available: {e}")
    factory = async_sessionmaker(eng, expire_on_commit=False)
    yield factory
    async with factory() as s:
        await s.execute(text("DELETE FROM positions WHERE symbol = :s"), {"s": SYM})
        await s.execute(text("DELETE FROM signals WHERE symbol = :s"), {"s": SYM})
        await s.execute(text("DELETE FROM intraday_hunter_runs WHERE trading_date = :d"), {"d": TEST_DATE})
        await s.commit()
    await eng.dispose()


def _strategy_signal(strategy, fyers_sym, entry=200.0):
    from app.strategies.base import StrategySignal

    return StrategySignal(
        strategy_name=strategy, symbol=SYM, signal_type=SignalType.BUY_CE,
        instrument_type=InstrumentType.OPTION, strike_price=56000.0, expiry_date=TEST_DATE,
        entry_price=entry, stop_loss=entry * 0.8, target_price=entry * 1.2, confidence=60.0,
        reason="t", indicators={}, fyers_option_symbol=fyers_sym, option_resolved=True,
    )


@pytest.mark.asyncio
async def test_v1_run_lookup_unaffected_by_v2_row(dbf):
    from app.models.intraday_hunter_run import IntradayHunterRun
    from app.services.intraday_hunter import store

    async with dbf() as s:
        s.add(IntradayHunterRun(trading_date=TEST_DATE, status="ENTER", variant="v2", decision="ENTER"))
        await s.commit()
        assert await store.get_run(s, TEST_DATE) is None  # v1 default sees no v1 row
        v1 = await store.get_or_create_run(s, TEST_DATE)
        await s.commit()
        assert v1.variant == "v1" and v1.status == "PENDING"
        assert (await store.get_run(s, TEST_DATE)).id == v1.id
        assert (await store.get_run(s, TEST_DATE, "v2")).decision == "ENTER"
        assert [r.variant for r in await store.history(s, 500) if r.trading_date == TEST_DATE] == ["v1"]


async def _add_position(s, strategy, fyers_sym):
    from app.models.position import Position

    s.add(Position(
        trade_id=uuid.uuid4(), symbol=SYM, strike_price=Decimal("56000"), option_type="CE",
        expiry_date=TEST_DATE, lots=1, quantity=30, entry_price=Decimal("200"),
        stop_loss=Decimal("160"), target_price=Decimal("240"), fyers_option_symbol=fyers_sym,
        strategy_name=strategy, is_paper=True, opened_at=datetime.now(timezone.utc),
        is_shadow=False,
    ))
    await s.commit()


@pytest.mark.asyncio
async def test_open_position_check_isolated(dbf):
    from app.services.strategy_runner import strategy_runner

    v1, v2 = StrategyName.INTRADAY_HUNTER, StrategyName.INTRADAY_HUNTER_V2
    with patch("app.services.strategy_runner.async_session_factory", dbf):
        async with dbf() as s:
            await _add_position(s, v2.value, "NSE:ZZ56000CE")
        # a v2 position never blocks v1 (or anyone else)…
        assert await strategy_runner._has_open_position(_strategy_signal(v1, "NSE:ZZ56000CE")) is False
        # …and blocks v2 only on the SAME contract
        assert await strategy_runner._has_open_position(_strategy_signal(v2, "NSE:ZZ56000CE")) is True
        assert await strategy_runner._has_open_position(_strategy_signal(v2, "NSE:ZZ56100CE")) is False
        async with dbf() as s:
            await _add_position(s, v1.value, "NSE:ZZ55900CE")
        # v1 behaves exactly as before against its own/other strategies' positions
        assert await strategy_runner._has_open_position(_strategy_signal(v1, "NSE:ANY")) is True
        # v1 positions do not block v2 either (v2 is fully isolated)
        assert await strategy_runner._has_open_position(_strategy_signal(v2, "NSE:ZZ57000CE")) is False


@pytest.mark.asyncio
async def test_dedup_isolated_and_v2_strike_aware(dbf):
    from app.models.signal import Signal
    from app.services.strategy_runner import strategy_runner

    v1, v2 = StrategyName.INTRADAY_HUNTER, StrategyName.INTRADAY_HUNTER_V2
    now = datetime.now(timezone.utc)
    async with dbf() as s:
        s.add(Signal(strategy_name=v2.value, symbol=SYM, signal_type="BUY_CE", instrument_type="OPTION",
                     strike_price=Decimal("56000"), expiry_date=TEST_DATE, entry_price=Decimal("200"),
                     stop_loss=Decimal("160"), target_price=Decimal("240"), confidence=Decimal("60"),
                     status="PENDING", reason="t", indicators={}, fyers_option_symbol="NSE:ZZ56000CE",
                     generated_at=now))
        await s.commit()
    with patch("app.services.strategy_runner.async_session_factory", dbf):
        # v1 never dedups against a v2 signal
        assert await strategy_runner._dedup_signal(_strategy_signal(v1, "NSE:ZZ56000CE"), now, True, None) is None
        # v2's OTM leg (different contract) is a NEW signal, not an update of the ATM leg
        assert await strategy_runner._dedup_signal(_strategy_signal(v2, "NSE:ZZ56100CE", 120.0), now, True, None) is None
        # the same v2 contract re-firing identically is noise
        assert await strategy_runner._dedup_signal(_strategy_signal(v2, "NSE:ZZ56000CE"), now, True, None) == "skip"
