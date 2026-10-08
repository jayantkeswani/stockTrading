from enum import StrEnum


class OptionType(StrEnum):
    CE = "CE"
    PE = "PE"


class OrderSide(StrEnum):
    BUY = "BUY"
    SELL = "SELL"


class TradeStatus(StrEnum):
    OPEN = "OPEN"
    CLOSED = "CLOSED"
    CANCELLED = "CANCELLED"


class ExitReason(StrEnum):
    SL_HIT = "SL_HIT"
    TARGET_HIT = "TARGET_HIT"
    MANUAL = "MANUAL"
    AGENT_SL = "AGENT_SL"
    AGENT_PROFIT = "AGENT_PROFIT"
    TIME_EXIT = "TIME_EXIT"
    EOD = "EOD"
    DRAWDOWN_HALT = "DRAWDOWN_HALT"
    PROFIT_CAP = "PROFIT_CAP"
    TRAILING_SL = "TRAILING_SL"
    EXPIRY_ROLL = "EXPIRY_ROLL"
    MARKET_EXIT = "MARKET_EXIT"
    STALE_DATA = "STALE_DATA"
    INVALIDATION = "INVALIDATION"  # Thesis-invalidation exit: index bias flipped STRONG-against the trade
    LOSS_CAP = "LOSS_CAP"  # Per-profile daily loss cap hit (symmetric twin of PROFIT_CAP)
    PER_LOT_STOP = "PER_LOT_STOP"  # Per-profile per-lot MTM loss stop hit (hard money stop)
    BASKET_TARGET = "BASKET_TARGET"  # IH v2: basket MTM >= +T (incl. round-number-hold exits) — all legs close together
    BASKET_STOP = "BASKET_STOP"  # IH v2: basket MTM <= -T — all legs close together
    BASKET_TIME = "BASKET_TIME"  # IH v2: basket backstop time (default 11:30 IST)
    MANUAL_BASKET = "MANUAL_BASKET"  # IH v2: user's emergency "Close v2 basket" button


class SignalStatus(StrEnum):
    PENDING = "PENDING"
    EXECUTED = "EXECUTED"
    EXPIRED = "EXPIRED"
    REJECTED = "REJECTED"


class InstrumentType(StrEnum):
    OPTION = "OPTION"
    FUTURE = "FUTURE"
    EQUITY = "EQUITY"


class SignalType(StrEnum):
    BUY_CE = "BUY_CE"
    BUY_PE = "BUY_PE"
    BUY_FUT = "BUY_FUT"
    SELL_FUT = "SELL_FUT"


class StrategyName(StrEnum):
    ORB = "orb"
    VWAP_PULLBACK = "vwap_pullback"
    GAMMA_SCALPING = "gamma_scalping"
    CAN_SLIM = "can_slim"
    INTRADAY_FUTURES = "intraday_futures"
    BREAKOUT_RETEST = "breakout_retest"
    VWAP_RECLAIM = "vwap_reclaim"
    INTRADAY_HUNTER = "intraday_hunter"  # LLM agent (Call 2 ENTER) — emits index-option signals, not candle-evaluated
    INTRADAY_HUNTER_V2 = "intraday_hunter_v2"  # IH v2 (parallel paper) — basket-level exits, not candle-evaluated


class PositionType(StrEnum):
    INTRADAY = "INTRADAY"
    POSITIONAL = "POSITIONAL"


class IndexSymbol(StrEnum):
    NIFTY = "NIFTY"
    BANKNIFTY = "BANKNIFTY"
    FINNIFTY = "FINNIFTY"
    SENSEX = "SENSEX"
    MIDCPNIFTY = "MIDCPNIFTY"


class AgentAutonomyLevel(StrEnum):
    MANUAL = "manual"   # Signals shown, user executes
    SEMI = "semi"       # SL auto-close, profit needs confirmation
    YOLO = "yolo"       # Auto-execute signals, auto-close SL, auto-book profits


class AgentActionType(StrEnum):
    SL_TRIGGERED = "SL_TRIGGERED"
    PROFIT_BOOK_REQUEST = "PROFIT_BOOK_REQUEST"
    PROFIT_BOOKED = "PROFIT_BOOKED"
    POSITION_MONITOR = "POSITION_MONITOR"
    NOTIFICATION_SENT = "NOTIFICATION_SENT"
    DRAWDOWN_HALT = "DRAWDOWN_HALT"
    TIME_EXIT = "TIME_EXIT"
    AUTO_EXECUTED = "AUTO_EXECUTED"
    MANUAL_EXECUTED = "MANUAL_EXECUTED"
    AUTO_PROFIT_BOOKED = "AUTO_PROFIT_BOOKED"
    EXPIRY_ROLL = "EXPIRY_ROLL"
    SHADOW_EXECUTED = "SHADOW_EXECUTED"
    PROFIT_CAP_CLOSE = "PROFIT_CAP_CLOSE"
    INVALIDATION_CLOSE = "INVALIDATION_CLOSE"
    LOSS_CAP_CLOSE = "LOSS_CAP_CLOSE"
    PER_LOT_STOP_CLOSE = "PER_LOT_STOP_CLOSE"
    BASKET_CLOSE = "BASKET_CLOSE"  # IH v2 basket-level exit (target/stop/time/round-hold)
    IH_V2_ROUND_HOLD = "IH_V2_ROUND_HOLD"  # IH v2 round-number-hold activation + its result
    MANUAL_BASKET_CLOSE = "MANUAL_BASKET_CLOSE"  # IH v2 emergency basket close (+ system counterfactual)


class TradeSource(StrEnum):
    MANUAL = "MANUAL"
    YOLO = "YOLO"
    SHADOW = "SHADOW"


class ConfirmationStatus(StrEnum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class CPRType(StrEnum):
    NARROW = "NARROW"
    WIDE = "WIDE"


class DayBias(StrEnum):
    BULLISH = "BULLISH"
    BEARISH = "BEARISH"
    NEUTRAL = "NEUTRAL"
