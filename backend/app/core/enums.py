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
    TRAILING_SL = "TRAILING_SL"
    EXPIRY_ROLL = "EXPIRY_ROLL"
    MARKET_EXIT = "MARKET_EXIT"


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
