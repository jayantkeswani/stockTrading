"""Yahoo Finance client for Indian stock fundamentals.

Uses direct httpx calls to Yahoo Finance APIs — avoids yfinance/curl_cffi whose
bundled libcurl TLS-fails on macOS 15.2 (remote machine).

All calls are synchronous — wrapped with asyncio.to_thread by the async wrappers.
Rate-limit protection: asyncio.Semaphore caps concurrent calls, inter-request
delay spaces them out, and the shared fetch_chart/fetch_quote_summary helpers
retry on 429 with exponential backoff.
"""

import asyncio
import logging
import time
from datetime import date, timedelta

import httpx

from app.data_sources.schemas import (
    AnnualFinancials,
    PriceHistory,
    QuarterlyEarnings,
    StockInfo,
)
from app.data_sources.yahoo_finance_http import (
    YF_HEADERS,
    fetch_chart,
    fetch_quote_summary,
    get_crumb,
)

logger = logging.getLogger(__name__)

_YFINANCE_SEMAPHORE = asyncio.Semaphore(2)
_YFINANCE_INTER_REQUEST_DELAY = 1.0


def _to_ns_ticker(symbol: str) -> str:
    if symbol.endswith(".NS") or symbol.endswith(".BO"):
        return symbol
    return f"{symbol}.NS"


def _fetch_quarterly_earnings_sync(ticker: str) -> list[QuarterlyEarnings]:
    with httpx.Client(headers=YF_HEADERS, follow_redirects=True, timeout=10) as client:
        crumb = get_crumb(client)
        result = fetch_quote_summary(
            client, ticker, crumb,
            modules="incomeStatementHistoryQuarterly",
        )

    if not result:
        return []

    stmts = (
        result.get("incomeStatementHistoryQuarterly", {})
        .get("incomeStatementHistory", [])
    )

    earnings: list[QuarterlyEarnings] = []
    for stmt in stmts:
        try:
            end_date_raw = stmt.get("endDate", {})
            end_ts = end_date_raw.get("raw") if isinstance(end_date_raw, dict) else end_date_raw
            if not end_ts:
                continue
            q_date = date.fromtimestamp(end_ts)

            total_revenue = _raw(stmt.get("totalRevenue")) or 0
            net_income = _raw(stmt.get("netIncome")) or 0
            eps = _raw(stmt.get("dilutedEps")) or _raw(stmt.get("basicEps")) or 0

            if eps == 0:
                shares = _raw(stmt.get("sharesOutstanding"))
                if shares and shares > 0:
                    eps = net_income / shares

            earnings.append(QuarterlyEarnings(
                quarter_end=q_date,
                eps=float(eps),
                revenue_cr=float(total_revenue) / 1e7,
            ))
        except (ValueError, TypeError, KeyError):
            continue

    earnings.sort(key=lambda e: e.quarter_end, reverse=True)

    for i, e in enumerate(earnings):
        target_date = e.quarter_end - timedelta(days=365)
        for older in earnings[i + 1:]:
            if abs((older.quarter_end - target_date).days) < 45:
                if older.eps != 0:
                    e.yoy_eps_growth_pct = ((e.eps - older.eps) / abs(older.eps)) * 100
                if older.revenue_cr != 0:
                    e.yoy_revenue_growth_pct = (
                        (e.revenue_cr - older.revenue_cr) / abs(older.revenue_cr)
                    ) * 100
                break

    return earnings


def _fetch_annual_financials_sync(ticker: str) -> list[AnnualFinancials]:
    with httpx.Client(headers=YF_HEADERS, follow_redirects=True, timeout=10) as client:
        crumb = get_crumb(client)
        result = fetch_quote_summary(
            client, ticker, crumb,
            modules="incomeStatementHistory,balanceSheetHistory",
        )

    if not result:
        return []

    stmts = result.get("incomeStatementHistory", {}).get("incomeStatementHistory", [])
    balance_stmts = result.get("balanceSheetHistory", {}).get("balanceSheetStatements", [])
    balance_by_date: dict[int, dict] = {}
    for b in balance_stmts:
        ts = _raw((b.get("endDate") or {}) if isinstance(b.get("endDate"), dict) else {"raw": b.get("endDate")})
        if ts:
            balance_by_date[int(ts)] = b

    results: list[AnnualFinancials] = []
    for stmt in stmts:
        try:
            end_date_raw = stmt.get("endDate", {})
            end_ts = end_date_raw.get("raw") if isinstance(end_date_raw, dict) else end_date_raw
            if not end_ts:
                continue
            year = date.fromtimestamp(end_ts).year

            net_income = float(_raw(stmt.get("netIncome")) or 0)
            total_revenue = float(_raw(stmt.get("totalRevenue")) or _raw(stmt.get("operatingRevenue")) or 0)
            operating_income = float(_raw(stmt.get("operatingIncome")) or _raw(stmt.get("ebit")) or 0)
            revenue_cr = total_revenue / 1e7
            opm = (operating_income / total_revenue * 100) if total_revenue else None

            roe = None
            de = None
            bs = balance_by_date.get(int(end_ts))
            if bs:
                equity = float(_raw(bs.get("totalStockholderEquity")) or _raw(bs.get("totalEquityGrossMinorityInterest")) or 0)
                total_debt = float(_raw(bs.get("totalDebt")) or _raw(bs.get("longTermDebt")) or 0)
                if equity and equity > 0:
                    roe = (net_income / equity) * 100
                    de = total_debt / equity

            results.append(AnnualFinancials(
                fiscal_year=year,
                eps=0,
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
    with httpx.Client(headers=YF_HEADERS, follow_redirects=True, timeout=10) as client:
        crumb = get_crumb(client)
        result = fetch_quote_summary(
            client, ticker, crumb,
            modules="price,summaryDetail,defaultKeyStatistics",
        )

    if not result:
        return StockInfo(symbol=ticker.replace(".NS", "").replace(".BO", ""))

    price = result.get("price", {})
    summary = result.get("summaryDetail", {})
    stats = result.get("defaultKeyStatistics", {})

    market_cap = _raw(price.get("marketCap"))
    market_cap_cr = (market_cap / 1e7) if market_cap else None

    float_shares = _raw(stats.get("floatShares"))
    shares_outstanding = _raw(price.get("sharesOutstanding"))
    free_float_pct = None
    if float_shares and shares_outstanding and shares_outstanding > 0:
        free_float_pct = (float_shares / shares_outstanding) * 100

    current_price = (
        _raw(price.get("regularMarketPrice"))
        or _raw(price.get("postMarketPrice"))
    )

    return StockInfo(
        symbol=ticker.replace(".NS", "").replace(".BO", ""),
        market_cap_cr=market_cap_cr,
        free_float_pct=free_float_pct,
        fifty_two_week_high=_raw(summary.get("fiftyTwoWeekHigh")),
        fifty_two_week_low=_raw(summary.get("fiftyTwoWeekLow")),
        current_price=current_price,
    )


def _fetch_price_history_sync(ticker: str, period: str = "1y") -> list[PriceHistory]:
    with httpx.Client(headers=YF_HEADERS, follow_redirects=True, timeout=10) as client:
        crumb = get_crumb(client)
        result = fetch_chart(client, ticker, crumb, interval="1d", range_=period)

    if not result:
        return []

    timestamps = result.get("timestamp", [])
    quotes = result.get("indicators", {}).get("quote", [{}])[0]
    opens = quotes.get("open", [])
    highs = quotes.get("high", [])
    lows = quotes.get("low", [])
    closes = quotes.get("close", [])
    volumes = quotes.get("volume", [])

    bars: list[PriceHistory] = []
    for i, ts in enumerate(timestamps):
        try:
            if closes[i] is None:
                continue
            bars.append(PriceHistory(
                date=date.fromtimestamp(ts),
                open=float(opens[i] or 0),
                high=float(highs[i] or 0),
                low=float(lows[i] or 0),
                close=float(closes[i]),
                volume=int(volumes[i] or 0),
            ))
        except (ValueError, TypeError, IndexError):
            continue

    return bars


def _raw(field) -> float | None:
    """Extract numeric value from Yahoo Finance's {raw: x, fmt: '...'} dicts or plain values."""
    if field is None:
        return None
    if isinstance(field, dict):
        return field.get("raw")
    try:
        return float(field)
    except (ValueError, TypeError):
        return None


# ---------------------------------------------------------------------------
# Async wrappers — same interface as before
# ---------------------------------------------------------------------------


async def _throttled_call(fn, *args, **kwargs):
    async with _YFINANCE_SEMAPHORE:
        await asyncio.sleep(_YFINANCE_INTER_REQUEST_DELAY)
        return await asyncio.to_thread(fn, *args, **kwargs)


async def get_quarterly_earnings(symbol: str) -> list[QuarterlyEarnings]:
    ticker = _to_ns_ticker(symbol)
    try:
        return await _throttled_call(_fetch_quarterly_earnings_sync, ticker)
    except Exception:
        logger.exception("Failed to fetch quarterly earnings for %s", symbol)
        return []


async def get_annual_financials(symbol: str) -> list[AnnualFinancials]:
    ticker = _to_ns_ticker(symbol)
    try:
        return await _throttled_call(_fetch_annual_financials_sync, ticker)
    except Exception:
        logger.exception("Failed to fetch annual financials for %s", symbol)
        return []


async def get_stock_info(symbol: str) -> StockInfo | None:
    ticker = _to_ns_ticker(symbol)
    try:
        return await _throttled_call(_fetch_stock_info_sync, ticker)
    except Exception:
        logger.exception("Failed to fetch stock info for %s", symbol)
        return None


async def get_price_history(symbol: str, period: str = "1y") -> list[PriceHistory]:
    ticker = _to_ns_ticker(symbol)
    try:
        return await _throttled_call(_fetch_price_history_sync, ticker)
    except Exception:
        logger.exception("Failed to fetch price history for %s", symbol)
        return []
