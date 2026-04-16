import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.enums import StrategyName
from app.models.strategy_config import StrategyConfig

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("")
async def list_strategies(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(StrategyConfig))
    return result.scalars().all()


@router.get("/{name}")
async def get_strategy_config(name: str, db: AsyncSession = Depends(get_db)):
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

    # Track new symbols to provision data for them
    old_symbols = set(config.symbols or [])

    if "parameters" in body:
        config.parameters = body["parameters"]
    if "risk_params" in body:
        config.risk_params = body["risk_params"]
    if "symbols" in body:
        config.symbols = body["symbols"]
    if "timeframes" in body:
        config.timeframes = body["timeframes"]
    if "is_active" in body:
        config.is_active = body["is_active"]
    if "auto_mode" in body:
        config.auto_mode = body["auto_mode"]

    await db.commit()
    await db.refresh(config)

    # Provision newly added symbols: fetch price, backfill candles, subscribe WS
    if "symbols" in body:
        new_symbols = set(body["symbols"]) - old_symbols
        if new_symbols:
            import asyncio
            asyncio.create_task(
                _provision_new_symbols(list(new_symbols)),
                name="provision_strategy_symbols",
            )

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
    await db.commit()
    return {"strategy": name, "is_active": config.is_active}


@router.patch("/{name}/auto-mode")
async def toggle_auto_mode(name: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(StrategyConfig).where(StrategyConfig.strategy_name == name)
    )
    config = result.scalar_one_or_none()
    if not config:
        raise HTTPException(status_code=404, detail="Strategy not found")

    config.auto_mode = not config.auto_mode
    await db.commit()
    return {"strategy": name, "auto_mode": config.auto_mode}


# ------------------------------------------------------------------
# Manual strategy evaluation
# ------------------------------------------------------------------


class ManualEvaluateRequest(BaseModel):
    strategy_name: str = Field(..., min_length=1)
    symbol: str = Field(..., min_length=1)


class BatchEvaluateRequest(BaseModel):
    strategy_name: str = Field(..., min_length=1)


@router.post("/evaluate")
async def evaluate_strategy(body: ManualEvaluateRequest):
    """Manually trigger a single strategy evaluation for a symbol."""
    try:
        strategy_name = StrategyName(body.strategy_name)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Unknown strategy: {body.strategy_name}")

    from app.services.strategy_runner import strategy_runner
    signal = await strategy_runner.evaluate_manual(body.symbol, strategy_name)

    return {
        "symbol": body.symbol,
        "strategy": body.strategy_name,
        "signal_generated": signal is not None,
        "signal_type": signal.signal_type.value if signal else None,
        "confidence": signal.confidence if signal else None,
    }


@router.post("/evaluate/batch")
async def evaluate_strategy_batch(body: BatchEvaluateRequest):
    """Manually trigger a strategy evaluation across all its configured symbols."""
    try:
        strategy_name = StrategyName(body.strategy_name)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Unknown strategy: {body.strategy_name}")

    # Look up configured symbols for this strategy
    from app.core.database import async_session_factory
    async with async_session_factory() as session:
        result = await session.execute(
            select(StrategyConfig.symbols).where(
                StrategyConfig.strategy_name == body.strategy_name
            )
        )
        row = result.scalar_one_or_none()

    symbols = row if row else []
    if not symbols:
        raise HTTPException(status_code=400, detail="No symbols configured for this strategy")

    from app.services.strategy_runner import strategy_runner
    results = []
    signals_count = 0
    for symbol in symbols:
        signal = await strategy_runner.evaluate_manual(symbol, strategy_name)
        generated = signal is not None
        if generated:
            signals_count += 1
        results.append({
            "symbol": symbol,
            "signal_generated": generated,
            "signal_type": signal.signal_type.value if signal else None,
            "confidence": signal.confidence if signal else None,
        })

    return {
        "strategy": body.strategy_name,
        "symbols_scanned": len(symbols),
        "signals_generated": signals_count,
        "results": results,
    }


# ------------------------------------------------------------------
# Symbol provisioning (background)
# ------------------------------------------------------------------


async def _provision_new_symbols(symbols: list[str]):
    """Provision newly added strategy symbols: fetch price, backfill candles, subscribe WS.

    Runs as a fire-and-forget background task so the API response isn't blocked.
    """
    from app.services.candle_backfill import _resolve_fyers_symbol, _backfill_symbol
    from app.data_feed.fyers_ws_client import fyers_ws_client
    from app.core.redis import get_redis
    from datetime import datetime
    from app.core.constants import IST
    from app.services.candle_backfill import _previous_trading_day

    logger.info("Provisioning %d new strategy symbols: %s", len(symbols), symbols)

    r = get_redis()
    token = await r.get("fyers:access_token")
    if not token:
        logger.warning("No Fyers token — cannot provision new symbols")
        return

    symbol_map = {sym: _resolve_fyers_symbol(sym) for sym in symbols}

    # 1. Fetch REST quotes → Redis price cache
    try:
        await fyers_ws_client.fetch_quotes_rest(extra_symbols=symbol_map)
    except Exception:
        logger.exception("Failed to fetch quotes for new symbols")

    # 2. Backfill candles (previous day + today)
    today = datetime.now(IST).date()
    prev_day = _previous_trading_day(today)

    for sym, fyers_sym in symbol_map.items():
        try:
            await _backfill_symbol(token, sym, fyers_sym, prev_day)
            await _backfill_symbol(token, sym, fyers_sym, today)
        except Exception:
            logger.exception("Failed to backfill candles for %s", sym)

    # 3. Subscribe on WebSocket for live ticks
    if fyers_ws_client.is_connected:
        fyers_symbols = list(symbol_map.values())
        await fyers_ws_client.subscribe_symbols(fyers_symbols)

    logger.info("Provisioning complete for %s", symbols)
