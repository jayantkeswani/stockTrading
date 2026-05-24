"""Fyers API client wrapper.

Handles REST API calls for:
- Historical OHLCV data
- Option chain / OI data
- Quotes
- Symbol search
"""

import logging
from datetime import datetime, date

import httpx

from app.config import settings
from app.core.retry import async_retry

logger = logging.getLogger(__name__)

API_URL = "https://api-t1.fyers.in/api/v3"
DATA_URL = "https://api-t1.fyers.in/data"

# Fyers JSON error codes that indicate an expired/invalid token
# _FYERS_AUTH_ERROR_CODES = {-16, -17, -300}
_FYERS_AUTH_ERROR_CODES = {-16, -17}
FYERS_TOKEN_KEY = "fyers:access_token"


class _FyersAuthError(Exception):
    """Raised internally when Fyers JSON body signals an auth failure."""

    def __init__(self, code: int):
        self.code = code
        super().__init__(f"Fyers auth error code {code}")


class FyersClient:
    def __init__(self, access_token: str | None = None):
        self.access_token = access_token
        self.app_id = settings.fyers_app_id
        self._simulated = settings.market_mode == "simulated"
        self._client = httpx.AsyncClient(timeout=30.0)

    @property
    def _data_url(self) -> str:
        if self._simulated:
            return f"{settings.simulator_url}/data"
        return DATA_URL

    @property
    def _headers(self) -> dict:
        if self._simulated:
            return {}
        return {"Authorization": f"{self.app_id}:{self.access_token}"}

    async def _request_with_auth(self, method: str, url: str, **kwargs) -> httpx.Response:
        """Make an httpx request with 401-triggered reauth and transient-error retry.

        On HTTP 401 or Fyers JSON auth error codes: triggers one reauth then retries.
        On 5xx / network errors: retries up to 3 times with exponential backoff.
        4xx other than 401 are raised immediately without retry.
        In simulated mode: direct request, no auth, no reauth.
        """
        if self._simulated:
            resp = await getattr(self._client, method)(url, **kwargs)
            resp.raise_for_status()
            return resp

        from app.core.redis import get_redis
        from app.data_feed.fyers_auto_login import trigger_reauth

        _reauth_done = False

        async def _do() -> httpx.Response:
            # Always read the freshest token from Redis so prior reauths are picked up
            r = get_redis()
            token = await r.get(FYERS_TOKEN_KEY)
            if token:
                self.access_token = token

            resp = await getattr(self._client, method)(
                url, headers=self._headers, **kwargs
            )

            # Check for Fyers-format auth failures embedded in a 200 body
            if resp.status_code == 200:
                try:
                    body = resp.json()
                    if body.get("s") == "error" and body.get("code") in _FYERS_AUTH_ERROR_CODES:
                        raise _FyersAuthError(body["code"])
                except (ValueError, AttributeError, KeyError):
                    pass

            resp.raise_for_status()
            return resp

        def _should_retry(exc: BaseException) -> bool:
            nonlocal _reauth_done
            if isinstance(exc, _FyersAuthError) and not _reauth_done:
                return True
            if isinstance(exc, httpx.HTTPStatusError):
                if exc.response.status_code == 401 and not _reauth_done:
                    return True
                if exc.response.status_code >= 500:
                    return True
                return False
            return isinstance(exc, (httpx.NetworkError, httpx.TimeoutException, ConnectionError))

        async def _on_retry(attempt: int, exc: BaseException) -> None:
            nonlocal _reauth_done
            is_auth = isinstance(exc, _FyersAuthError) or (
                isinstance(exc, httpx.HTTPStatusError)
                and exc.response.status_code == 401
            )
            if is_auth and not _reauth_done:
                logger.info("Auth error on REST — triggering Fyers reauth")
                try:
                    await trigger_reauth()
                    _reauth_done = True
                except Exception as e:
                    logger.error("Reauth failed during REST retry: %s", e)

        return await async_retry(
            _do,
            retries=3,
            base_delay=1.0,
            should_retry=_should_retry,
            on_retry=_on_retry,
            label=f"fyers_rest:{url.rsplit('/', 1)[-1]}",
        )

    async def get_quotes(self, symbols: list[str]) -> dict:
        """Get real-time quotes for symbols."""
        try:
            response = await self._request_with_auth(
                "get",
                f"{self._data_url}/quotes",
                params={"symbols": ",".join(symbols)},
            )
            return response.json()
        except Exception as e:
            logger.error("Failed to fetch quotes: %s", e)
            return {}

    async def get_historical_data(
        self,
        symbol: str,
        resolution: str = "1",  # 1, 5, 15, 30, 60, D
        from_date: date | None = None,
        to_date: date | None = None,
    ) -> list[dict]:
        """Fetch historical OHLCV candles."""
        try:
            params = {
                "symbol": symbol,
                "resolution": resolution,
                "date_format": "1",
                "range_from": str(from_date) if from_date else "",
                "range_to": str(to_date) if to_date else "",
                "cont_flag": "1",
            }
            response = await self._request_with_auth(
                "get",
                f"{self._data_url}/history",
                params=params,
            )
            data = response.json()
            candles = data.get("candles", [])
            return [
                {
                    "timestamp": c[0],
                    "open": c[1],
                    "high": c[2],
                    "low": c[3],
                    "close": c[4],
                    "volume": c[5],
                }
                for c in candles
            ]
        except Exception as e:
            logger.error("Failed to fetch historical data: %s", e)
            return []

    async def get_option_chain(self, symbol: str, expiry_date: str | None = None) -> dict:
        """Fetch option chain data for a symbol.

        Args:
            symbol: Fyers symbol (e.g. "NSE:NIFTY50-INDEX"). If a short name
                    like "NIFTY" is passed, it is resolved via FYERS_SYMBOL_MAP.
            expiry_date: Optional expiry timestamp.
        """
        from app.core.constants import FYERS_SYMBOL_MAP

        # Resolve short names to full Fyers symbols
        fyers_symbol = FYERS_SYMBOL_MAP.get(symbol, symbol)

        try:
            params: dict = {"symbol": fyers_symbol, "strikecount": 20}
            if expiry_date:
                params["timestamp"] = expiry_date
            response = await self._request_with_auth(
                "get",
                f"{self._data_url}/options-chain-v3",
                params=params,
            )
            return response.json()
        except Exception as e:
            logger.error("Failed to fetch option chain: %s", e)
            return {}

    async def get_market_depth(self, symbol: str) -> dict:
        """Fetch market depth (Level 2) data."""
        try:
            response = await self._request_with_auth(
                "get",
                f"{self._data_url}/depth",
                params={"symbol": symbol, "ohlcv_flag": "1"},
            )
            return response.json()
        except Exception as e:
            logger.error("Failed to fetch market depth: %s", e)
            return {}

    async def close(self):
        """Close the underlying httpx async client."""
        await self._client.aclose()
