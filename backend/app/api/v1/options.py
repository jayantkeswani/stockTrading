"""Options (Strategy 2 — VWAP Pullback) API endpoints."""

from fastapi import APIRouter

from app.core.utils import get_custom_window_state, is_market_open, now_ist
from app.services.agent_log import get_agent_log
from app.services.strategy_params import get_strategy_params, parse_dead_zone, parse_trading_windows

router = APIRouter()


@router.get("/window-state")
async def options_window_state():
    params = await get_strategy_params("vwap_pullback")
    windows = parse_trading_windows(params)
    dead_zone = parse_dead_zone(params)
    state = get_custom_window_state(windows=windows, dead_zone=dead_zone)
    return {"window_state": state, "market_open": is_market_open()}


@router.get("/agent-log")
async def options_agent_log(date: str | None = None, offset: int = 0, limit: int = 0):
    date_str = date or str(now_ist().date())
    if limit > 0:
        entries, total = await get_agent_log("strat2", date_str, offset=offset, limit=limit)
        return {"entries": entries, "total": total}
    return await get_agent_log("strat2", date_str)
