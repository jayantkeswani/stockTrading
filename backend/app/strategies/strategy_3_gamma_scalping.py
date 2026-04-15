"""Strategy 3: Expiry Day Gamma Scalping.

STATUS: STUB — not yet implemented.
See docs/strategies/strategy-3-gamma-scalping.md for specification.
"""

import logging

from app.core.enums import StrategyName
from app.strategies.base import BaseStrategy, ExitSignal, MarketContext, StrategySignal

logger = logging.getLogger(__name__)


class GammaScalpingStrategy(BaseStrategy):
    name = StrategyName.GAMMA_SCALPING

    def evaluate(self, ctx: MarketContext) -> StrategySignal | None:
        logger.debug("Gamma Scalping strategy not yet implemented")
        return None

    def should_exit(self, ctx, entry_price, stop_loss, target_price) -> ExitSignal | None:
        return None

    def get_position_size(self, capital, risk_per_trade_pct, entry_price, stop_loss, lot_size, vix_multiplier=1.0) -> int:
        return 1
