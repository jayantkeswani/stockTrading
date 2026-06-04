"""Trading settings API — read and update user-editable trading parameters."""

from fastapi import APIRouter, HTTPException

from app.schemas.settings import TradingConfigResponse, TradingConfigUpdate
from app.services.trading_config import get_trading_config, update_trading_config

router = APIRouter()


@router.get("/trading", response_model=TradingConfigResponse)
async def get_trading_settings():
    """Return the current trading configuration."""
    cfg = await get_trading_config()
    return TradingConfigResponse(
        capital=cfg.capital,
        max_daily_drawdown_pct=cfg.max_daily_drawdown_pct,
        max_risk_per_trade_pct=cfg.max_risk_per_trade_pct,
        max_trades_per_day=cfg.max_trades_per_day,
        paper_trading=cfg.paper_trading,
        autonomy_level=cfg.autonomy_level,
        min_confidence_to_persist=cfg.min_confidence_to_persist,
        min_confidence_for_shadow=cfg.min_confidence_for_shadow,
        min_confidence_for_execution=cfg.min_confidence_for_execution,
        shadow_skip_permanent_watchlist=cfg.shadow_skip_permanent_watchlist,
        yolo_skip_permanent_watchlist=cfg.yolo_skip_permanent_watchlist,
        ai_overlay_enabled=cfg.ai_overlay_enabled,
    )


@router.patch("/trading", response_model=TradingConfigResponse)
async def patch_trading_settings(body: TradingConfigUpdate):
    """Partially update trading configuration. Changes take effect immediately."""
    fields = body.model_dump(exclude_none=True)
    if not fields:
        raise HTTPException(status_code=400, detail="No fields to update")

    try:
        cfg = await update_trading_config(**fields)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))

    return TradingConfigResponse(
        capital=cfg.capital,
        max_daily_drawdown_pct=cfg.max_daily_drawdown_pct,
        max_risk_per_trade_pct=cfg.max_risk_per_trade_pct,
        max_trades_per_day=cfg.max_trades_per_day,
        paper_trading=cfg.paper_trading,
        autonomy_level=cfg.autonomy_level,
        min_confidence_to_persist=cfg.min_confidence_to_persist,
        min_confidence_for_shadow=cfg.min_confidence_for_shadow,
        min_confidence_for_execution=cfg.min_confidence_for_execution,
        shadow_skip_permanent_watchlist=cfg.shadow_skip_permanent_watchlist,
        yolo_skip_permanent_watchlist=cfg.yolo_skip_permanent_watchlist,
        ai_overlay_enabled=cfg.ai_overlay_enabled,
    )
