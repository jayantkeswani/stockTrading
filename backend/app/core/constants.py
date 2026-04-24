"""Market constants for Indian stock exchanges."""

from datetime import date, time
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

# Strategy-level defaults (not user-configurable; trading_config table holds user params)
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

# ---------------------------------------------------------------------------
# CAN SLIM Strategy Constants
# ---------------------------------------------------------------------------

# Fundamental thresholds (India-adapted from O'Neil's original criteria)
CANSLIM_MIN_MARKET_CAP_CR = 1000       # Rs 1000 crore minimum
CANSLIM_MIN_EPS_GROWTH_PCT = 20.0      # Quarterly EPS growth YoY
CANSLIM_MIN_ROE_PCT = 15.0
CANSLIM_MIN_OPERATING_MARGIN_PCT = 10.0
CANSLIM_MAX_DEBT_TO_EQUITY = 1.0
CANSLIM_MIN_RS_RATING = 80             # Relative Strength >= 80 percentile
CANSLIM_MAX_PCT_FROM_52W_HIGH = 15.0   # Stock within 15% of 52-week high
CANSLIM_MAX_VIX = 20.0                 # Market direction: VIX ceiling

# Entry/exit parameters
CANSLIM_SL_PCT = 8.0                   # 8% stop loss below entry
CANSLIM_TARGET_PCT = 20.0              # 20% profit target
CANSLIM_BREAKOUT_VOLUME_MULTIPLIER = 1.5  # Volume > 1.5x 20-day avg
CANSLIM_TRAILING_SL_ACTIVATION_PCT = 10.0  # Move SL to breakeven after 10% gain
CANSLIM_MAX_POSITIONAL_LOTS = 2        # Cap lots for futures positions

# Scoring weights (total = 1.0)
CANSLIM_SCORE_WEIGHTS = {
    "C": 0.20,  # Current quarterly earnings
    "A": 0.20,  # Annual earnings growth
    "N": 0.10,  # New highs / proximity to 52w high
    "S": 0.10,  # Supply/demand (float, volume, D/E)
    "L": 0.15,  # Leader (Relative Strength)
    "I": 0.10,  # Institutional sponsorship (FII/MF)
    "M": 0.15,  # Market direction
}
CANSLIM_MIN_TOTAL_SCORE = 60.0  # Minimum composite score to qualify

# Stock futures
STOCK_FUTURES_EXPIRY_DOW = 3     # Thursday (last Thursday of month for NSE stock futures)
FUTURES_MARGIN_PCT = 0.18        # ~18% of contract value (SPAN + exposure)
FUTURES_EXPIRY_ROLL_DAYS = 3     # Alert 3 days before futures expiry

# ---------------------------------------------------------------------------
# NSE Trading Holidays
# ---------------------------------------------------------------------------
# Official NSE equity market holidays. Update annually from:
# https://www.nseindia.com/regulations/trading-holiday-calendar
#
# NOTE: If a holiday falls on a weekend it has no market impact and is not
# listed here. The backtest context_builder treats any day with no candles
# in the DB as a non-trading day regardless, so minor gaps in this list are
# handled gracefully (the backtest simply skips that day).

NSE_HOLIDAYS: frozenset[date] = frozenset([
    # 2024
    date(2024, 1, 26),   # Republic Day
    date(2024, 3, 8),    # Mahashivratri
    date(2024, 3, 25),   # Holi
    date(2024, 3, 29),   # Good Friday
    date(2024, 4, 11),   # Id-Ul-Fitr (Ramzan Eid)
    date(2024, 4, 17),   # Ram Navami
    date(2024, 5, 1),    # Maharashtra Day
    date(2024, 5, 20),   # General Election Day (special closure)
    date(2024, 5, 23),   # Buddha Purnima
    date(2024, 6, 17),   # Bakri Id
    date(2024, 7, 17),   # Muharram
    date(2024, 8, 15),   # Independence Day
    date(2024, 10, 2),   # Mahatma Gandhi Jayanti
    date(2024, 11, 1),   # Diwali Laxmi Puja
    date(2024, 11, 15),  # Gurunanak Jayanti
    date(2024, 12, 25),  # Christmas
    # 2025
    date(2025, 2, 26),   # Mahashivratri
    date(2025, 3, 14),   # Holi
    date(2025, 4, 10),   # Id-Ul-Fitr (Ramzan Eid)
    date(2025, 4, 14),   # Dr. Baba Saheb Ambedkar Jayanti
    date(2025, 4, 18),   # Good Friday
    date(2025, 5, 1),    # Maharashtra Day
    date(2025, 5, 12),   # Buddha Purnima
    date(2025, 6, 6),    # Eid ul Adha (Bakri Id)
    date(2025, 7, 7),    # Muharram
    date(2025, 8, 15),   # Independence Day
    date(2025, 8, 27),   # Ganesh Chaturthi
    date(2025, 10, 2),   # Mahatma Gandhi Jayanti / Dussehra
    date(2025, 10, 20),  # Diwali Laxmi Puja
    date(2025, 10, 21),  # Diwali (Balipratipada)
    date(2025, 11, 5),   # Gurunanak Jayanti
    date(2025, 12, 25),  # Christmas
    # 2026 (partial — update when NSE publishes full list)
    date(2026, 1, 26),   # Republic Day
    date(2026, 3, 20),   # Holi
    date(2026, 4, 3),    # Good Friday
    date(2026, 4, 14),   # Dr. Baba Saheb Ambedkar Jayanti
    date(2026, 5, 1),    # Maharashtra Day
    date(2026, 8, 15),   # Independence Day
    date(2026, 10, 2),   # Mahatma Gandhi Jayanti
    date(2026, 12, 25),  # Christmas
])
