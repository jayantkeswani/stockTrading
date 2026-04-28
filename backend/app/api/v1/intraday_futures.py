"""API endpoints for Strategy 5 — Intraday Stock Futures."""

from fastapi import APIRouter, HTTPException

from app.services.morning_screener import (
    get_agent_log,
    get_agent_status,
    get_global_cues,
    get_morning_briefing,
    get_setup_performance,
    get_watchlist,
    run_morning_briefing,
    run_morning_screener,
    set_agent_status,
    snapshot_global_cues,
)
from app.strategies.strategy_5_intraday_futures import get_current_phase

router = APIRouter()


@router.get("/watchlist")
async def watchlist(date: str | None = None):
    from app.core.utils import now_ist

    date_str = date or str(now_ist().date())
    return await get_watchlist(date_str)


@router.get("/agent-log")
async def agent_log(date: str | None = None):
    from app.core.utils import now_ist

    date_str = date or str(now_ist().date())
    return await get_agent_log(date_str)


@router.get("/global-cues")
async def global_cues(date: str | None = None):
    from app.core.utils import now_ist

    date_str = date or str(now_ist().date())
    result = await get_global_cues(date_str)
    if not result:
        return await snapshot_global_cues()
    return result


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
    from app.core.utils import now_ist
    from app.models.position import Position
    from app.models.trade import Trade

    today = date_type.fromisoformat(date) if date else now_ist().date()

    async with async_session_factory() as session:
        # Today's trades
        result = await session.execute(
            select(Trade).where(
                and_(
                    Trade.strategy_name == "intraday_futures",
                    func.date(Trade.entry_time) == today,
                )
            )
        )
        trades = result.scalars().all()

        # Active positions
        result = await session.execute(
            select(func.count()).select_from(Position).where(
                Position.strategy_name == "intraday_futures",
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


@router.post("/screener/run")
async def run_screener():
    try:
        watchlist = await run_morning_screener()
        return {"status": "ok", "watchlist_count": len(watchlist)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/briefing/run")
async def run_briefing():
    try:
        briefing = await run_morning_briefing()
        return {"status": "ok", "briefing": briefing}
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
