from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models.strategy_config import StrategyConfig

router = APIRouter()


@router.get("")
async def list_strategies(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(StrategyConfig))
    return result.scalars().all()


@router.get("/{name}")
async def get_strategy(name: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(StrategyConfig).where(StrategyConfig.strategy_name == name)
    )
    config = result.scalar_one_or_none()
    if not config:
        raise HTTPException(status_code=404, detail="Strategy not found")
    return config


@router.put("/{name}")
async def update_strategy(name: str, body: dict, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(StrategyConfig).where(StrategyConfig.strategy_name == name)
    )
    config = result.scalar_one_or_none()
    if not config:
        raise HTTPException(status_code=404, detail="Strategy not found")

    if "parameters" in body:
        config.parameters = body["parameters"]
    if "risk_params" in body:
        config.risk_params = body["risk_params"]
    if "symbols" in body:
        config.symbols = body["symbols"]
    if "timeframes" in body:
        config.timeframes = body["timeframes"]

    await db.flush()
    return config


@router.patch("/{name}/toggle")
async def toggle_strategy(name: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(StrategyConfig).where(StrategyConfig.strategy_name == name)
    )
    config = result.scalar_one_or_none()
    if not config:
        raise HTTPException(status_code=404, detail="Strategy not found")

    config.is_active = not config.is_active
    await db.flush()
    return {"strategy": name, "is_active": config.is_active}
