import uuid
from datetime import datetime
from typing import Optional

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
from app.services.trading_config import get_trading_config, update_trading_config

router = APIRouter()

@router.get("/status", response_model=AgentStatusResponse)
async def agent_status(db: AsyncSession = Depends(get_db)):
    cfg = await get_trading_config()

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
    if agent_runner.is_running and agent_runner.started_at:
        uptime = int((now_ist() - agent_runner.started_at).total_seconds())

    return AgentStatusResponse(
        running=agent_runner.is_running,
        yolo_mode=cfg.yolo_mode,
        autonomy_level=cfg.autonomy_level,
        pending_confirmations=pending,
        positions_monitored=positions_monitored,
        uptime_seconds=uptime,
    )


@router.post("/start")
async def start_agent():
    await agent_runner.start()
    cfg = await get_trading_config()
    return {"status": "started", "yolo_mode": cfg.yolo_mode}


@router.post("/stop")
async def stop_agent():
    await agent_runner.stop()
    return {"status": "stopped"}


@router.patch("/yolo")
async def toggle_yolo(body: YoloToggleRequest):
    """Toggle YOLO mode — persisted to DB and reflected immediately."""
    autonomy_level = "YOLO" if body.enabled else "SEMI"
    cfg = await update_trading_config(autonomy_level=autonomy_level)
    return {
        "yolo_mode": cfg.yolo_mode,
        "autonomy_level": cfg.autonomy_level,
    }


@router.get("/logs", response_model=list[AgentLogResponse])
async def agent_logs(
    limit: int = Query(default=100, le=500),
    offset: int = 0,
    since: Optional[datetime] = Query(default=None),
    until: Optional[datetime] = Query(default=None),
    db: AsyncSession = Depends(get_db),
):
    q = select(AgentLog).order_by(desc(AgentLog.created_at)).offset(offset).limit(limit)
    if since is not None:
        q = q.where(AgentLog.created_at >= since)
    if until is not None:
        q = q.where(AgentLog.created_at <= until)
    result = await db.execute(q)
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
