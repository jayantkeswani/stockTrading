"""Shared httpx-based Yahoo Finance helper.

Provides cookie+crumb management and low-level fetch primitives used by both
global_market_task (world indices) and yfinance_client (stock fundamentals).

Uses httpx with Python's native SSL stack — avoids curl_cffi whose bundled
libcurl TLS-fails on macOS 15.2 (remote machine).
"""

import logging
import time

logger = logging.getLogger(__name__)

YF_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json,text/plain,*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://finance.yahoo.com/",
}

_yf_crumb: str | None = None
_yf_crumb_at: float = 0.0
_CRUMB_TTL = 3600

MAX_RETRIES = 3
BASE_RETRY_DELAY = 5  # seconds; doubles each attempt


def get_crumb(client) -> str | None:
    """Fetch (or return cached) Yahoo Finance crumb using an existing httpx.Client."""
    global _yf_crumb, _yf_crumb_at

    now = time.time()
    if _yf_crumb and now - _yf_crumb_at < _CRUMB_TTL:
        return _yf_crumb

    try:
        client.get("https://fc.yahoo.com", timeout=5)
        r = client.get("https://query2.finance.yahoo.com/v1/test/getcrumb", timeout=5)
        if r.status_code == 200 and r.text and "\n" not in r.text:
            _yf_crumb = r.text.strip()
            _yf_crumb_at = now
            return _yf_crumb
    except Exception:
        pass
    return None


def fetch_chart(client, ticker: str, crumb: str | None, *, interval: str = "1d", range_: str = "1y") -> dict | None:
    """Fetch v8 chart data for one ticker. Returns the first result dict or None."""
    params: dict = {"range": range_, "interval": interval, "includePrePost": "false"}
    if crumb:
        params["crumb"] = crumb

    for attempt in range(MAX_RETRIES + 1):
        try:
            r = client.get(
                f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}",
                params=params,
                timeout=10,
            )
            if r.status_code == 429:
                if attempt < MAX_RETRIES:
                    wait = BASE_RETRY_DELAY * (2 ** attempt)
                    logger.warning(
                        "Yahoo Finance rate limit for %s (attempt %d/%d), retrying in %ds",
                        ticker, attempt + 1, MAX_RETRIES, wait,
                    )
                    time.sleep(wait)
                    continue
                logger.warning("Yahoo Finance rate limit for %s — giving up", ticker)
                return None
            if r.status_code != 200:
                logger.warning("Yahoo Finance chart %s → HTTP %d", ticker, r.status_code)
                return None
            result = (r.json().get("chart") or {}).get("result") or []
            return result[0] if result else None
        except Exception as e:
            logger.warning("Failed to fetch chart for %s: %s", ticker, e)
            return None

    return None


def fetch_quote_summary(client, ticker: str, crumb: str | None, modules: str) -> dict | None:
    """Fetch quoteSummary for one ticker. Returns the quoteSummary result dict or None."""
    params: dict = {"modules": modules, "formatted": "false", "lang": "en-US", "region": "US"}
    if crumb:
        params["crumb"] = crumb

    for attempt in range(MAX_RETRIES + 1):
        try:
            r = client.get(
                f"https://query1.finance.yahoo.com/v10/finance/quoteSummary/{ticker}",
                params=params,
                timeout=10,
            )
            if r.status_code == 429:
                if attempt < MAX_RETRIES:
                    wait = BASE_RETRY_DELAY * (2 ** attempt)
                    logger.warning(
                        "Yahoo Finance rate limit for %s (attempt %d/%d), retrying in %ds",
                        ticker, attempt + 1, MAX_RETRIES, wait,
                    )
                    time.sleep(wait)
                    continue
                logger.warning("Yahoo Finance rate limit for %s — giving up", ticker)
                return None
            if r.status_code != 200:
                logger.warning("Yahoo Finance quoteSummary %s → HTTP %d", ticker, r.status_code)
                return None
            data = r.json()
            result = (data.get("quoteSummary") or {}).get("result") or []
            return result[0] if result else None
        except Exception as e:
            logger.warning("Failed to fetch quoteSummary for %s: %s", ticker, e)
            return None

    return None
