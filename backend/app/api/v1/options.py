"""Options (Strategy 2 — VWAP Pullback) API endpoints."""

from fastapi import APIRouter

from app.core.utils import now_ist
from app.services.agent_log import get_agent_log

router = APIRouter()


@router.get("/agent-log")
async def options_agent_log(date: str | None = None, offset: int = 0, limit: int = 0):
    date_str = date or str(now_ist().date())
    if limit > 0:
        entries, total = await get_agent_log("strat2", date_str, offset=offset, limit=limit)
        return {"entries": entries, "total": total}
    return await get_agent_log("strat2", date_str)
