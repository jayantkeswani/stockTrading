"""Strategy registry — discovers and manages active strategies."""

from app.core.enums import StrategyName
from app.strategies.base import BaseStrategy
from app.strategies.strategy_1_orb import ORBStrategy
from app.strategies.strategy_2_vwap_pullback import VWAPPullbackStrategy
from app.strategies.strategy_3_gamma_scalping import GammaScalpingStrategy
from app.strategies.strategy_4_canslim import CANSLIMStrategy
from app.strategies.strategy_5_intraday_futures import IntradayFuturesStrategy

# All available strategies
_STRATEGIES: dict[StrategyName, BaseStrategy] = {
    StrategyName.ORB: ORBStrategy(),
    StrategyName.VWAP_PULLBACK: VWAPPullbackStrategy(),
    StrategyName.GAMMA_SCALPING: GammaScalpingStrategy(),
    StrategyName.CAN_SLIM: CANSLIMStrategy(),
    StrategyName.INTRADAY_FUTURES: IntradayFuturesStrategy(),
}


def get_strategy(name: StrategyName) -> BaseStrategy | None:
    return _STRATEGIES.get(name)


def get_all_strategies() -> dict[StrategyName, BaseStrategy]:
    return _STRATEGIES.copy()


def get_active_strategies(active_names: list[StrategyName]) -> list[BaseStrategy]:
    """Return only strategies that are marked as active."""
    return [s for name, s in _STRATEGIES.items() if name in active_names]
