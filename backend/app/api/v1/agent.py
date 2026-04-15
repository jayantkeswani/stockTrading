import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import desc, select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.agent_runner import agent_runner
from app.core.database import get_db
from app.core.enums import ConfirmationStatus
from app.core.utils import now_ist
from app.models.agent_log import AgentLog
from app.models.position import Position
from app.schemas.agent import (
    AgentConfirmRequest,
    AgentLogResponse,
    AgentStatusResponse,
    YoloToggleRequest,
)

router = APIRouter()

# In-memory agent state (will be managed by agent_runner in production)
_agent_state = {"running": False, "started_at": None}


@router.get("/status", response_model=AgentStatusResponse)
async def agent_status(db: AsyncSession = Depends(get_db)):
    # Count pending confirmations
    result = await db.execute(
        select(func.count(AgentLog.id)).where(
            AgentLog.requires_confirmation == True,
            AgentLog.confirmation_status == ConfirmationStatus.PENDING,
        )
    )
    pending = result.scalar() or 0

    # Count monitored positions
    pos_result = await db.execute(select(func.count(Position.id)))
    positions_monitored = pos_result.scalar() or 0

    uptime = None
    if _agent_state["running"] and _agent_state["started_at"]:
        uptime = int((now_ist() - _agent_state["started_at"]).total_seconds())

    return AgentStatusResponse(
        running=_agent_state["running"],
        yolo_mode=agent_runner.yolo_mode,
        autonomy_level=agent_runner.autonomy_level.value,
        pending_confirmations=pending,
        positions_monitored=positions_monitored,
        uptime_seconds=uptime,
    )


@router.post("/start")
async def start_agent():
    _agent_state["running"] = True
    _agent_state["started_at"] = now_ist()
    await agent_runner.start()
    return {"status": "started", "yolo_mode": agent_runner.yolo_mode}


@router.post("/stop")
async def stop_agent():
    _agent_state["running"] = False
    _agent_state["started_at"] = None
    await agent_runner.stop()
    return {"status": "stopped"}


@router.patch("/yolo")
async def toggle_yolo(body: YoloToggleRequest):
    """Toggle YOLO mode on/off at runtime."""
    agent_runner.set_yolo_mode(body.enabled)
    return {
        "yolo_mode": agent_runner.yolo_mode,
        "autonomy_level": agent_runner.autonomy_level.value,
    }


@router.get("/logs", response_model=list[AgentLogResponse])
async def agent_logs(
    limit: int = Query(default=50, le=200),
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(AgentLog)
        .order_by(desc(AgentLog.created_at))
        .offset(offset)
        .limit(limit)
    )
    return result.scalars().all()


@router.post("/confirm/{log_id}")
async def confirm_action(
    log_id: uuid.UUID,
    body: AgentConfirmRequest,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(AgentLog).where(AgentLog.id == log_id))
    log = result.scalar_one_or_none()
    if not log:
        raise HTTPException(status_code=404, detail="Agent log not found")
    if not log.requires_confirmation:
        raise HTTPException(status_code=400, detail="This action does not require confirmation")

    log.confirmation_status = (
        ConfirmationStatus.APPROVED if body.approved else ConfirmationStatus.REJECTED
    )
    log.confirmed_at = now_ist()
    await db.flush()
    return {"status": log.confirmation_status, "log_id": str(log_id)}
