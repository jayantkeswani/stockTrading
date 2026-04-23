"""Strategy 1: Opening Range Breakout (ORB) + VWAP Confirmation.

STATUS: STUB — not yet implemented.
See docs/strategies/strategy-1-orb.md for specification.
"""

import logging

from app.core.enums import StrategyName
from app.strategies.base import BaseStrategy, ExitSignal, MarketContext, StrategySignal

logger = logging.getLogger(__name__)


class ORBStrategy(BaseStrategy):
    name = StrategyName.ORB

    def evaluate(self, ctx: MarketContext) -> StrategySignal | None:
        logger.debug("ORB strategy not yet implemented")
        return None

    def should_exit(self, ctx, entry_price, stop_loss, target_price) -> ExitSignal | None:
        return None
