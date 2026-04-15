import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.enums import SignalStatus
from app.models.signal import Signal
from app.schemas.signal import SignalResponse

router = APIRouter()


@router.get("", response_model=list[SignalResponse])
async def list_signals(
    status: str | None = None,
    strategy: str | None = None,
    limit: int = Query(default=50, le=200),
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
):
    query = select(Signal).order_by(desc(Signal.generated_at))
    if status:
        query = query.where(Signal.status == status)
    if strategy:
        query = query.where(Signal.strategy_name == strategy)
    query = query.offset(offset).limit(limit)
    result = await db.execute(query)
    return result.scalars().all()


@router.get("/active", response_model=list[SignalResponse])
async def active_signals(db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Signal)
        .where(Signal.status == SignalStatus.PENDING)
        .order_by(desc(Signal.generated_at))
    )
    return result.scalars().all()


@router.post("/{signal_id}/execute")
async def execute_signal(signal_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Signal).where(Signal.id == signal_id))
    signal = result.scalar_one_or_none()
    if not signal:
        raise HTTPException(status_code=404, detail="Signal not found")
    if signal.status != SignalStatus.PENDING:
        raise HTTPException(status_code=400, detail="Signal is not pending")

    # TODO: Create trade from signal via trade_service
    signal.status = SignalStatus.EXECUTED
    await db.flush()
    return {"status": "executed", "signal_id": str(signal_id)}


@router.post("/{signal_id}/reject")
async def reject_signal(signal_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Signal).where(Signal.id == signal_id))
    signal = result.scalar_one_or_none()
    if not signal:
        raise HTTPException(status_code=404, detail="Signal not found")

    signal.status = SignalStatus.REJECTED
    await db.flush()
    return {"status": "rejected", "signal_id": str(signal_id)}
