"""Lot computation at execution time — centralizes all lot-sizing logic.

Each executor calls the appropriate function:
- Shadow: always 1 lot (clean per-lot P&L measurement)
- YOLO: capital-based risk sizing with strategy-specific overrides
- Manual: same as YOLO (user can override in the modal)
"""

import logging

from app.core.constants import LOT_SIZES
from app.services.position_sizing import calculate_lots, vix_to_multiplier
from app.services.trading_config import get_trading_config

logger = logging.getLogger(__name__)


async def compute_lots_for_yolo(
    signal,
    lot_size: int,
    india_vix: float | None = None,
) -> tuple[int, dict]:
    """Compute lots for YOLO execution.

    Uses capital-based risk sizing with strategy-specific overrides
    (e.g., S5 conviction logic). Returns (lots, sizing_meta dict).
    """
    cfg = await get_trading_config()

    from app.strategies.registry import get_strategy
    from app.core.enums import StrategyName
    try:
        strategy = get_strategy(StrategyName(signal.strategy_name))
    except (ValueError, KeyError):
        strategy = None

    max_lots = getattr(strategy, "max_lots", None) if strategy else None
    vix_mult = vix_to_multiplier(india_vix)

    if signal.strategy_name == "intraday_futures" and strategy and hasattr(strategy, "_compute_lots"):
        indicators = signal.indicators or {}
        lots = strategy._compute_lots(
            rvol=indicators.get("rvol"),
            params={
                "_india_vix": india_vix,
                "_briefing_max_lots": indicators.get("briefing_max_lots", 2),
                "_nifty_bias_strength": indicators.get("nifty_bias_strength"),
                "_screener_score": indicators.get("screener_score", 0) or 0,
                "_briefing_approach": indicators.get("briefing_approach"),
                "_stock_trend_direction": indicators.get("stock_trend_direction"),
                "_stock_trend_strength": indicators.get("stock_trend_strength"),
                "_enhanced_orb": indicators.get("enhanced_orb", False),
                "setup_type": indicators.get("setup_type"),
            },
            indicators=indicators,
        )
    else:
        lots = calculate_lots(
            capital=cfg.capital,
            risk_per_trade_pct=cfg.max_risk_per_trade_pct,
            entry_price=float(signal.entry_price),
            stop_loss=float(signal.stop_loss),
            lot_size=lot_size,
            vix_multiplier=vix_mult,
            max_lots=max_lots,
        )

    sizing_meta = {
        "capital": cfg.capital,
        "risk_pct": cfg.max_risk_per_trade_pct,
        "vix": india_vix,
        "vix_multiplier": vix_mult,
        "max_lots": max_lots,
        "lot_size": lot_size,
        "strategy": signal.strategy_name,
    }

    return lots, sizing_meta


def compute_lots_for_shadow(lot_size: int) -> int:
    """Shadow always uses 1 lot for clean per-lot P&L measurement."""
    return 1


async def compute_lots_for_manual(
    signal,
    lot_size: int,
    india_vix: float | None = None,
) -> tuple[int, dict]:
    """Compute recommended lots for manual execution.

    Same logic as YOLO — user can override in the modal.
    """
    return await compute_lots_for_yolo(signal, lot_size, india_vix)
