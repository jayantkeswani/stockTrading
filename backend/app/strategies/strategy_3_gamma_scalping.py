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
