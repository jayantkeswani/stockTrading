"""Market constants for Indian stock exchanges."""

from datetime import time
from zoneinfo import ZoneInfo

# Timezone
IST = ZoneInfo("Asia/Kolkata")

# Market hours (IST)
MARKET_OPEN = time(9, 15)
MARKET_CLOSE = time(15, 30)
PRE_MARKET_OPEN = time(9, 0)
POSITION_CLOSE_DEADLINE = time(15, 15)  # Close all positions by this time

# Trading windows for Strategy 2
WINDOW_1_START = time(9, 45)
WINDOW_1_END = time(11, 0)
WINDOW_2_START = time(13, 45)
WINDOW_2_END = time(14, 45)
DEAD_ZONE_START = time(11, 30)
DEAD_ZONE_END = time(13, 30)

# Lot sizes (updated Jan 2026)
LOT_SIZES = {
    "NIFTY": 75,
    "BANKNIFTY": 30,
    "FINNIFTY": 25,
    "SENSEX": 10,
    "MIDCPNIFTY": 50,
}

# Strike price gaps per index
STRIKE_GAPS = {
    "NIFTY": 50,
    "BANKNIFTY": 100,
    "FINNIFTY": 50,
    "SENSEX": 100,
    "MIDCPNIFTY": 25,
}

# Weekly expiry days (day of week: 0=Monday, 6=Sunday)
# Post-SEBI Nov 2024: only NIFTY (NSE) and SENSEX (BSE) have weekly expiries.
# BANKNIFTY, FINNIFTY, MIDCPNIFTY are monthly-only (last Tuesday of month).
WEEKLY_EXPIRY_DAYS = {
    "NIFTY": 1,       # Tuesday (changed from Thursday, effective Sep 2025)
    "SENSEX": 3,      # Thursday
}

# Indices that only have monthly expiry (no weekly contracts)
MONTHLY_ONLY_INDICES = {"BANKNIFTY", "FINNIFTY", "MIDCPNIFTY"}

# Monthly expiry day of week per exchange
MONTHLY_EXPIRY_DOW = {
    "NSE": 1,  # Last Tuesday of month
    "BSE": 3,  # Last Thursday of month
}

# Exchange for each index (used for option symbol construction)
OPTION_EXCHANGE = {
    "NIFTY": "NSE",
    "BANKNIFTY": "NSE",
    "FINNIFTY": "NSE",
    "SENSEX": "BSE",
    "MIDCPNIFTY": "NSE",
}

# Preferred option premium range (INR)
PREMIUM_RANGE_MIN = 150.0
PREMIUM_RANGE_MAX = 400.0

# Exchange codes
EXCHANGE_NSE = "NSE"
EXCHANGE_BSE = "BSE"

# Fyers symbol format
FYERS_SYMBOL_MAP = {
    "NIFTY": "NSE:NIFTY50-INDEX",
    "BANKNIFTY": "NSE:NIFTYBANK-INDEX",
    "FINNIFTY": "NSE:FINNIFTY-INDEX",
    "SENSEX": "BSE:SENSEX-INDEX",
    "MIDCPNIFTY": "NSE:MIDCPNIFTY-INDEX",
    "INDIA VIX": "NSE:INDIAVIX-INDEX",
}

# Risk defaults
DEFAULT_CAPITAL = 1_000_000  # 10 Lakhs INR
DEFAULT_MAX_DAILY_DRAWDOWN_PCT = 5.0
DEFAULT_MAX_RISK_PER_TRADE_PCT = 2.0
DEFAULT_MAX_TRADES_PER_DAY = 3
DEFAULT_SL_PCT = 30.0  # 30% of premium
DEFAULT_TARGET_MULTIPLIER = 1.5  # 1:1.5 risk-reward

# VIX thresholds
VIX_LOW = 14.0   # Options cheap, full position
VIX_HIGH = 18.0  # Options expensive, reduce 30%
VIX_EXTREME = 22.0  # Sit out

# VWAP pullback proximity (%)
VWAP_PROXIMITY_PCT = 0.15

# Candle timeframes (in minutes)
TIMEFRAME_1M = 1
TIMEFRAME_5M = 5
TIMEFRAME_15M = 15
