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

logger = logging.getLogger(__name__)

BASE_URL = "https://api-t1.fyers.in/api/v3"


class FyersClient:
    def __init__(self, access_token: str | None = None):
        self.access_token = access_token
        self.app_id = settings.fyers_app_id
        self._client = httpx.AsyncClient(timeout=30.0)

    @property
    def _headers(self) -> dict:
        return {"Authorization": f"{self.app_id}:{self.access_token}"}

    async def get_quotes(self, symbols: list[str]) -> dict:
        """Get real-time quotes for symbols."""
        try:
            response = await self._client.get(
                f"{BASE_URL}/quotes/",
                params={"symbols": ",".join(symbols)},
                headers=self._headers,
            )
            response.raise_for_status()
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
                "range_from": str(int(datetime.combine(from_date, datetime.min.time()).timestamp())) if from_date else "",
                "range_to": str(int(datetime.combine(to_date, datetime.min.time()).timestamp())) if to_date else "",
                "cont_flag": "1",
            }
            response = await self._client.get(
                f"{BASE_URL}/history/",
                params=params,
                headers=self._headers,
            )
            response.raise_for_status()
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
        """Fetch option chain data for a symbol."""
        try:
            params = {"symbol": f"NSE:{symbol}-INDEX", "strikecount": 20}
            if expiry_date:
                params["timestamp"] = expiry_date
            response = await self._client.get(
                f"{BASE_URL}/option-chain",
                params=params,
                headers=self._headers,
            )
            response.raise_for_status()
            return response.json()
        except Exception as e:
            logger.error("Failed to fetch option chain: %s", e)
            return {}

    async def get_market_depth(self, symbol: str) -> dict:
        """Fetch market depth (Level 2) data."""
        try:
            response = await self._client.get(
                f"{BASE_URL}/depth/",
                params={"symbol": symbol, "ohlcv_flag": "1"},
                headers=self._headers,
            )
            response.raise_for_status()
            return response.json()
        except Exception as e:
            logger.error("Failed to fetch market depth: %s", e)
            return {}

    async def close(self):
        await self._client.aclose()
