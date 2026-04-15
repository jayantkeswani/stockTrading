"""Custom exception hierarchy."""


class TradingError(Exception):
    """Base exception for all trading errors."""


class RiskLimitExceeded(TradingError):
    """Raised when a risk limit would be exceeded."""


class DrawdownHalt(TradingError):
    """Raised when daily drawdown limit is hit."""


class MaxTradesExceeded(TradingError):
    """Raised when max trades per day is reached."""


class MarketClosedError(TradingError):
    """Raised when trying to trade outside market hours."""


class DataFeedError(TradingError):
    """Raised when data feed has issues."""


class InsufficientDataError(TradingError):
    """Raised when not enough data for indicator calculation."""


class BrokerError(TradingError):
    """Raised when broker API returns an error."""
