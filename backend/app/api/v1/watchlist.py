"""Watchlist API — backend-managed watchlist stored in Redis.

Stores watchlist items in Redis so the agent can also add symbols
programmatically (e.g., after deciding to take a trade).

Redis key: ``watchlist:items`` (hash: fyers_symbol -> JSON metadata)
"""

import json
import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.core.redis import get_redis

logger = logging.getLogger(__name__)

router = APIRouter()

WATCHLIST_KEY = "watchlist:items"


class WatchlistAddRequest(BaseModel):
    symbol: str = Field(..., min_length=1, max_length=100)
    display: str = Field(..., min_length=1, max_length=200)
    segment: str = Field(default="EQ", max_length=10)
    strike: float | None = None
    option_type: str | None = Field(default=None, max_length=5)
    expiry: str = Field(default="", max_length=50)


@router.get("")
async def get_watchlist():
    """Get all watchlist items."""
    r = get_redis()
    items = await r.hgetall(WATCHLIST_KEY)
    result = []
    for symbol, meta_json in items.items():
        try:
            meta = json.loads(meta_json)
            meta["symbol"] = symbol
            result.append(meta)
        except json.JSONDecodeError:
            result.append({"symbol": symbol, "display": symbol})
    return {"items": result}


@router.post("")
async def add_to_watchlist(body: WatchlistAddRequest):
    """Add a symbol to the watchlist and subscribe on live data feed."""
    meta = {
        "display": body.display,
        "segment": body.segment,
        "strike": body.strike,
        "option_type": body.option_type,
        "expiry": body.expiry,
    }

    r = get_redis()
    await r.hset(WATCHLIST_KEY, body.symbol, json.dumps(meta))
    logger.info("Added to watchlist: %s (%s)", body.symbol, body.display)

    # Subscribe on live data feed so watchlist gets real-time ticks
    from app.data_feed.fyers_ws_client import fyers_ws_client
    if fyers_ws_client.is_connected:
        await fyers_ws_client.subscribe_symbols([body.symbol])

    return {"status": "added", "symbol": body.symbol}


@router.delete("/{symbol:path}")
async def remove_from_watchlist(symbol: str):
    """Remove a symbol from the watchlist."""
    if not symbol or len(symbol) > 100:
        raise HTTPException(status_code=400, detail="Invalid symbol")

    r = get_redis()
    removed = await r.hdel(WATCHLIST_KEY, symbol)
    if removed:
        logger.info("Removed from watchlist: %s", symbol)
        return {"status": "removed", "symbol": symbol}
    return {"status": "not_found", "symbol": symbol}
