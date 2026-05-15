"""API endpoints for Strategy 5 — Intraday Stock Futures."""

import json

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.services.morning_screener import (
    get_agent_log,
    get_agent_status,
    get_global_cues,
    get_morning_briefing,
    get_setup_performance,
    get_watchlist,
    run_morning_briefing,
    run_morning_screener,
    run_preopen_reassessment,
    set_agent_status,
    snapshot_global_cues,
)
from app.strategies.strategy_5_intraday_futures import get_current_phase
from app.services.strategy_runner import strategy_runner

router = APIRouter()

_PERM_WATCHLIST_KEY = "strat5:watchlist:permanent"


class _SymbolBody(BaseModel):
    symbol: str


async def _sync_perm_watchlist_to_db(symbols: list[str]) -> None:
    from sqlalchemy import select

    from app.core.database import async_session_factory
    from app.models.strategy_config import StrategyConfig

    async with async_session_factory() as session:
        result = await session.execute(
            select(StrategyConfig).where(StrategyConfig.strategy_name == "intraday_futures")
        )
        config = result.scalar_one_or_none()
        if config:
            config.symbols = symbols
            await session.commit()


@router.get("/permanent-watchlist")
async def get_permanent_watchlist():
    from app.core.redis import get_redis

    r = get_redis()
    raw = await r.get(_PERM_WATCHLIST_KEY)
    if raw:
        return {"symbols": json.loads(raw)}

    # Fall back to DB and re-hydrate Redis
    from sqlalchemy import select

    from app.core.database import async_session_factory
    from app.models.strategy_config import StrategyConfig

    async with async_session_factory() as session:
        result = await session.execute(
            select(StrategyConfig).where(StrategyConfig.strategy_name == "intraday_futures")
        )
        config = result.scalar_one_or_none()
        symbols = list(config.symbols or []) if config else []

    await r.set(_PERM_WATCHLIST_KEY, json.dumps(symbols))
    return {"symbols": symbols}


@router.post("/permanent-watchlist")
async def add_to_permanent_watchlist(body: _SymbolBody):
    from app.core.redis import get_redis
    from app.data_sources.nse_client import get_fo_lot_sizes

    symbol = body.symbol.strip().upper()

    lot_sizes = await get_fo_lot_sizes()
    if symbol not in (lot_sizes or {}):
        raise HTTPException(status_code=422, detail=f"{symbol} is not an F&O-eligible stock")

    r = get_redis()
    raw = await r.get(_PERM_WATCHLIST_KEY)
    symbols: list[str] = json.loads(raw) if raw else []

    if symbol in symbols:
        return {"symbols": symbols}

    symbols.append(symbol)
    await r.set(_PERM_WATCHLIST_KEY, json.dumps(symbols))
    await _sync_perm_watchlist_to_db(symbols)
    return {"symbols": symbols}


@router.delete("/permanent-watchlist/{symbol}")
async def remove_from_permanent_watchlist(symbol: str):
    from app.core.redis import get_redis

    symbol = symbol.strip().upper()
    r = get_redis()
    raw = await r.get(_PERM_WATCHLIST_KEY)
    symbols: list[str] = json.loads(raw) if raw else []

    symbols = [s for s in symbols if s != symbol]
    await r.set(_PERM_WATCHLIST_KEY, json.dumps(symbols))
    await _sync_perm_watchlist_to_db(symbols)
    return {"symbols": symbols}


@router.get("/watchlist")
async def watchlist(date: str | None = None):
    from app.core.utils import now_ist

    date_str = date or str(now_ist().date())
    return await get_watchlist(date_str)


@router.get("/agent-log")
async def agent_log(date: str | None = None, offset: int = 0, limit: int = 0):
    from app.core.utils import now_ist

    date_str = date or str(now_ist().date())
    if limit > 0:
        entries, total = await get_agent_log(date_str, offset=offset, limit=limit)
        return {"entries": entries, "total": total}
    return await get_agent_log(date_str)


@router.get("/global-cues")
async def global_cues(date: str | None = None, force: bool = False):
    from app.core.utils import now_ist

    date_str = date or str(now_ist().date())
    if not force:
        result = await get_global_cues(date_str)
        if result:
            return result
    return await snapshot_global_cues(force=force)


@router.get("/morning-briefing")
async def morning_briefing(date: str | None = None):
    from app.core.utils import now_ist

    date_str = date or str(now_ist().date())
    return await get_morning_briefing(date_str)


@router.get("/phase")
async def phase(date: str | None = None):
    from app.core.utils import now_ist

    if date and date != str(now_ist().date()):
        from app.core.redis import get_redis

        r = get_redis()
        stored = await r.get(f"strat5:phase:{date}")
        return {"phase": stored or "DONE"}
    return {"phase": get_current_phase()}


@router.get("/daily-stats")
async def daily_stats(date: str | None = None):
    from datetime import date as date_type

    from sqlalchemy import and_, func, select

    from app.core.database import async_session_factory
    from app.core.enums import TradeSource
    from app.core.utils import now_ist
    from app.models.position import Position
    from app.models.trade import Trade

    today = date_type.fromisoformat(date) if date else now_ist().date()

    async with async_session_factory() as session:
        # Today's trades (exclude shadow)
        result = await session.execute(
            select(Trade).where(
                and_(
                    Trade.strategy_name == "intraday_futures",
                    func.date(Trade.entry_time) == today,
                    Trade.source != TradeSource.SHADOW.value,
                )
            )
        )
        trades = result.scalars().all()

        # Active positions (exclude shadow)
        result = await session.execute(
            select(func.count()).select_from(Position).where(
                and_(
                    Position.strategy_name == "intraday_futures",
                    Position.is_shadow == False,
                )
            )
        )
        active_positions = result.scalar() or 0

    total_trades = len(trades)
    closed = [t for t in trades if t.status == "CLOSED"]
    net_pnl = sum(float(t.pnl or 0) for t in closed)
    wins = sum(1 for t in closed if t.pnl and t.pnl > 0)
    losses = sum(1 for t in closed if t.pnl and t.pnl <= 0)

    return {
        "date": str(today),
        "total_trades": total_trades,
        "active_positions": active_positions,
        "closed_trades": len(closed),
        "wins": wins,
        "losses": losses,
        "net_pnl": round(net_pnl, 2),
        "win_rate": round(wins / max(wins + losses, 1) * 100, 1),
    }


@router.get("/setup-performance")
async def setup_performance(date: str | None = None, days: int = 5):
    from datetime import date as date_type

    from app.core.utils import now_ist

    end = date_type.fromisoformat(date) if date else now_ist().date()
    return await get_setup_performance(end, days=days)


@router.post("/backfill-symbols")
async def backfill_symbols(symbols: list[str] | None = None):
    """One-shot backfill: fetch today's candles, compute ORB, subscribe on WS.

    If symbols is None, backfills all watchlist symbols missing ORB data.
    """
    import json
    from datetime import time as dt_time
    from decimal import Decimal

    from sqlalchemy import and_, select

    from app.core.database import async_session_factory
    from app.core.redis import get_redis
    from app.core.utils import now_ist
    from app.data_feed.fyers_ws_client import fyers_ws_client
    from app.models.market_data import MarketData1m
    from app.services.candle_backfill import _backfill_symbol

    today = now_ist().date()
    r = get_redis()

    # Resolve target symbols: explicit list or watchlist minus already-ORB'd
    if symbols:
        target = symbols
    else:
        raw = await r.get(f"strat5:watchlist:{today}")
        if not raw:
            raise HTTPException(status_code=404, detail="No watchlist for today")
        watchlist = json.loads(raw)
        target = []
        for item in watchlist:
            sym = item.get("symbol", "")
            existing = await r.get(f"strat5:orb:{today}:{sym}")
            if not existing:
                target.append(sym)

    if not target:
        return {"status": "ok", "message": "All symbols already have ORB", "backfilled": []}

    token = await r.get("fyers:access_token")
    if not token:
        raise HTTPException(status_code=503, detail="No Fyers token")

    results = []
    for sym in target:
        fyers_sym = f"NSE:{sym}-EQ"
        try:
            count = await _backfill_symbol(token, sym, fyers_sym, today)

            # Query 9:15-9:30 candles to compute ORB
            from datetime import datetime, timezone
            from zoneinfo import ZoneInfo
            IST = ZoneInfo("Asia/Kolkata")
            orb_start = datetime.combine(today, dt_time(9, 15), tzinfo=IST)
            orb_end = datetime.combine(today, dt_time(9, 30), tzinfo=IST)

            async with async_session_factory() as session:
                rows = await session.execute(
                    select(MarketData1m).where(
                        and_(
                            MarketData1m.symbol == sym,
                            MarketData1m.timestamp >= orb_start,
                            MarketData1m.timestamp <= orb_end,
                        )
                    )
                )
                candles = rows.scalars().all()

            if candles:
                orb_high = float(max(c.high for c in candles))
                orb_low = float(min(c.low for c in candles))
                orb_data = {"high": orb_high, "low": orb_low, "range": round(orb_high - orb_low, 2)}
                await r.set(
                    f"strat5:orb:{today}:{sym}",
                    json.dumps(orb_data),
                    ex=86400 * 90,
                )

                # Load into live strategy instance
                from app.strategies.registry import get_active_strategies
                from app.core.enums import StrategyName
                strategies = get_active_strategies([StrategyName.INTRADAY_FUTURES])
                for s in strategies:
                    s.load_orb_from_redis(sym, orb_data)

                results.append({"symbol": sym, "candles": count, "orb": orb_data})
            else:
                results.append({"symbol": sym, "candles": count, "orb": None})

            # Subscribe on WS + seed REST price
            if fyers_ws_client.is_connected:
                await fyers_ws_client.subscribe_symbols(
                    [fyers_sym], symbol_map={sym: fyers_sym},
                )
            await fyers_ws_client.fetch_quotes_rest(extra_symbols={sym: fyers_sym})

        except Exception as e:
            results.append({"symbol": sym, "error": str(e)})

    return {"status": "ok", "backfilled": results}


@router.post("/screener/run")
async def run_screener():
    try:
        watchlist = await run_morning_screener()
        strategy_runner.clear_s5_session_cache()
        return {"status": "ok", "watchlist_count": len(watchlist)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/briefing/run")
async def run_briefing():
    try:
        briefing = await run_morning_briefing(force=True)
        strategy_runner.clear_s5_session_cache()
        return {"status": "ok", "briefing": briefing}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/preopen/run")
async def run_preopen():
    try:
        result = await run_preopen_reassessment()
        strategy_runner.clear_s5_session_cache()
        return {"status": "ok", "result": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/agent/{action}")
async def agent_action(action: str):
    from app.core.utils import now_ist

    date_str = str(now_ist().date())

    if action == "pause":
        await set_agent_status(date_str, "PAUSED")
        return {"status": "PAUSED"}
    elif action == "resume":
        await set_agent_status(date_str, "ACTIVE")
        return {"status": "ACTIVE"}
    else:
        raise HTTPException(status_code=400, detail=f"Unknown action: {action}")


@router.get("/agent-status")
async def agent_status_endpoint():
    from app.core.utils import now_ist

    date_str = str(now_ist().date())
    status = await get_agent_status(date_str)
    return {"status": status}
