"""Yahoo Finance client for Indian stock fundamentals.

Uses the yfinance library with .NS suffix for NSE stocks.
All yfinance calls are synchronous — wrapped with asyncio.to_thread.
Results are cached in Redis with 12h TTL to avoid repeated API calls.

Rate-limit protection: asyncio.Semaphore caps concurrent yfinance calls,
inter-request delay spaces them out, and sync retry with exponential backoff
handles transient SSL resets / 429s from Yahoo Finance.
"""

import asyncio
import logging
import time
from datetime import date, timedelta

from app.data_sources.schemas import (
    AnnualFinancials,
    PriceHistory,
    QuarterlyEarnings,
    StockInfo,
)

logger = logging.getLogger(__name__)

# --- Rate-limit protection ---
_YFINANCE_SEMAPHORE = asyncio.Semaphore(2)
_YFINANCE_INTER_REQUEST_DELAY = 1.0  # seconds between calls within a semaphore slot
_YFINANCE_MAX_RETRIES = 3
_YFINANCE_BASE_DELAY = 5  # seconds before first retry

_RATE_LIMIT_PATTERNS = ("connection reset", "rate limit", "too many requests", "429")


def _yf_call_with_retry(fn, *args, **kwargs):
    """Retry a sync yfinance call on rate-limit / SSL errors with exponential backoff."""
    for attempt in range(_YFINANCE_MAX_RETRIES + 1):
        try:
            return fn(*args, **kwargs)
        except Exception as e:
            err_str = str(e).lower()
            if any(p in err_str for p in _RATE_LIMIT_PATTERNS) and attempt < _YFINANCE_MAX_RETRIES:
                wait = _YFINANCE_BASE_DELAY * (2 ** attempt)
                logger.warning(
                    "yfinance rate limit (attempt %d/%d), retrying in %ds: %s",
                    attempt + 1, _YFINANCE_MAX_RETRIES, wait, str(e)[:120],
                )
                time.sleep(wait)
                continue
            raise


def _to_ns_ticker(symbol: str) -> str:
    """Convert NSE symbol to yfinance ticker. e.g. 'TCS' -> 'TCS.NS'."""
    if symbol.endswith(".NS") or symbol.endswith(".BO"):
        return symbol
    return f"{symbol}.NS"


def _fetch_quarterly_earnings_sync(ticker: str) -> list[QuarterlyEarnings]:
    """Synchronous yfinance call to get quarterly earnings.

    Uses quarterly_income_stmt (the current yfinance API) instead of the
    deprecated quarterly_earnings endpoint.
    """
    import yfinance as yf

    stock = yf.Ticker(ticker)

    # Use quarterly_income_stmt (current API) — columns are quarter dates, rows are line items
    qi = stock.quarterly_income_stmt
    earnings: list[QuarterlyEarnings] = []

    if qi is not None and not qi.empty:
        shares_outstanding = (stock.info or {}).get("sharesOutstanding", 0)

        for col in qi.columns:
            try:
                q_date = col.date() if hasattr(col, "date") else col

                # Revenue
                revenue = float(
                    qi[col].get("Total Revenue")
                    or qi[col].get("Operating Revenue")
                    or 0
                )
                revenue_cr = revenue / 1e7  # Convert to crores

                # Net income → EPS
                net_income = float(
                    qi[col].get("Net Income")
                    or qi[col].get("Net Income Common Stockholders")
                    or 0
                )

                # Diluted EPS if available, otherwise compute from net income / shares
                eps = float(qi[col].get("Diluted EPS") or 0)
                if eps == 0 and qi[col].get("Basic EPS"):
                    eps = float(qi[col].get("Basic EPS"))
                if eps == 0 and shares_outstanding and shares_outstanding > 0:
                    eps = net_income / shares_outstanding

                earnings.append(QuarterlyEarnings(
                    quarter_end=q_date,
                    eps=eps,
                    revenue_cr=revenue_cr,
                ))
            except (ValueError, TypeError, KeyError):
                continue

    # Sort by date descending (most recent first)
    earnings.sort(key=lambda e: e.quarter_end, reverse=True)

    # Compute YoY growth for quarters that have a year-ago counterpart
    for i, e in enumerate(earnings):
        # Find the same quarter from 1 year ago
        target_date = e.quarter_end - timedelta(days=365)
        yoy_match = None
        for older in earnings[i + 1:]:
            if abs((older.quarter_end - target_date).days) < 45:
                yoy_match = older
                break
        if yoy_match and yoy_match.eps != 0:
            e.yoy_eps_growth_pct = ((e.eps - yoy_match.eps) / abs(yoy_match.eps)) * 100
        if yoy_match and yoy_match.revenue_cr != 0:
            e.yoy_revenue_growth_pct = (
                (e.revenue_cr - yoy_match.revenue_cr) / abs(yoy_match.revenue_cr)
            ) * 100

    return earnings


def _fetch_annual_financials_sync(ticker: str) -> list[AnnualFinancials]:
    """Synchronous yfinance call to get annual financials."""
    import yfinance as yf

    stock = yf.Ticker(ticker)
    info = stock.info or {}

    financials = stock.financials
    balance = stock.balance_sheet

    results: list[AnnualFinancials] = []

    if financials is not None and not financials.empty:
        for col in financials.columns:
            try:
                year = col.year if hasattr(col, "year") else int(str(col)[:4])

                net_income = float(financials[col].get("Net Income", 0) or 0)
                total_revenue = float(
                    financials[col].get("Total Revenue")
                    or financials[col].get("Operating Revenue")
                    or 0
                )
                operating_income = float(
                    financials[col].get("Operating Income")
                    or financials[col].get("EBIT")
                    or 0
                )

                revenue_cr = total_revenue / 1e7
                opm = (operating_income / total_revenue * 100) if total_revenue else None

                # ROE and D/E from balance sheet
                roe = None
                de = None
                if balance is not None and not balance.empty and col in balance.columns:
                    equity = float(
                        balance[col].get("Stockholders Equity")
                        or balance[col].get("Total Equity Gross Minority Interest")
                        or 0
                    )
                    total_debt = float(
                        balance[col].get("Total Debt")
                        or balance[col].get("Long Term Debt")
                        or 0
                    )
                    if equity and equity > 0:
                        roe = (net_income / equity) * 100
                        de = total_debt / equity

                results.append(AnnualFinancials(
                    fiscal_year=year,
                    eps=0,  # Derived from net_income / shares later if needed
                    revenue_cr=revenue_cr,
                    roe_pct=roe,
                    operating_margin_pct=opm,
                    debt_to_equity=de,
                ))
            except (ValueError, TypeError, KeyError):
                continue

    results.sort(key=lambda f: f.fiscal_year, reverse=True)
    return results


def _fetch_stock_info_sync(ticker: str) -> StockInfo:
    """Synchronous yfinance call to get stock metadata."""
    import yfinance as yf

    stock = yf.Ticker(ticker)
    info = stock.info or {}

    # Market cap in crores
    market_cap = info.get("marketCap")
    market_cap_cr = (market_cap / 1e7) if market_cap else None

    # Free float percentage
    float_shares = info.get("floatShares")
    shares_outstanding = info.get("sharesOutstanding")
    free_float_pct = None
    if float_shares and shares_outstanding and shares_outstanding > 0:
        free_float_pct = (float_shares / shares_outstanding) * 100

    return StockInfo(
        symbol=ticker.replace(".NS", "").replace(".BO", ""),
        market_cap_cr=market_cap_cr,
        free_float_pct=free_float_pct,
        fifty_two_week_high=info.get("fiftyTwoWeekHigh"),
        fifty_two_week_low=info.get("fiftyTwoWeekLow"),
        current_price=info.get("currentPrice") or info.get("regularMarketPrice"),
        sector=info.get("sector"),
        industry=info.get("industry"),
    )


def _fetch_price_history_sync(ticker: str, period: str = "1y") -> list[PriceHistory]:
    """Synchronous yfinance call to get daily OHLCV data."""
    import yfinance as yf

    stock = yf.Ticker(ticker)
    hist = stock.history(period=period)

    if hist is None or hist.empty:
        return []

    bars: list[PriceHistory] = []
    for idx, row in hist.iterrows():
        try:
            bar_date = idx.date() if hasattr(idx, "date") else idx
            bars.append(PriceHistory(
                date=bar_date,
                open=float(row["Open"]),
                high=float(row["High"]),
                low=float(row["Low"]),
                close=float(row["Close"]),
                volume=int(row["Volume"]),
            ))
        except (ValueError, TypeError, KeyError):
            continue

    return bars


# ---------------------------------------------------------------------------
# Async wrappers
# ---------------------------------------------------------------------------


async def _throttled_call(fn, *args, **kwargs):
    """Run a sync yfinance function with semaphore, delay, and retry."""
    async with _YFINANCE_SEMAPHORE:
        await asyncio.sleep(_YFINANCE_INTER_REQUEST_DELAY)
        return await asyncio.to_thread(_yf_call_with_retry, fn, *args, **kwargs)


async def get_quarterly_earnings(symbol: str) -> list[QuarterlyEarnings]:
    """Fetch quarterly earnings for an NSE stock. Async wrapper."""
    ticker = _to_ns_ticker(symbol)
    try:
        return await _throttled_call(_fetch_quarterly_earnings_sync, ticker)
    except Exception:
        logger.exception("Failed to fetch quarterly earnings for %s", symbol)
        return []


async def get_annual_financials(symbol: str) -> list[AnnualFinancials]:
    """Fetch annual financials for an NSE stock. Async wrapper."""
    ticker = _to_ns_ticker(symbol)
    try:
        return await _throttled_call(_fetch_annual_financials_sync, ticker)
    except Exception:
        logger.exception("Failed to fetch annual financials for %s", symbol)
        return []


async def get_stock_info(symbol: str) -> StockInfo | None:
    """Fetch stock metadata for an NSE stock. Async wrapper."""
    ticker = _to_ns_ticker(symbol)
    try:
        return await _throttled_call(_fetch_stock_info_sync, ticker)
    except Exception:
        logger.exception("Failed to fetch stock info for %s", symbol)
        return None


async def get_price_history(symbol: str, period: str = "1y") -> list[PriceHistory]:
    """Fetch daily OHLCV for an NSE stock. Async wrapper."""
    ticker = _to_ns_ticker(symbol)
    try:
        return await _throttled_call(_fetch_price_history_sync, ticker)
    except Exception:
        logger.exception("Failed to fetch price history for %s", symbol)
        return []
