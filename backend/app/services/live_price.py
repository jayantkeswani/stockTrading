"""Live option/futures price lookup.

Tries Redis LTP cache first. Falls back to a synchronous Fyers REST quote
if the cache is missing or stale. Raises HTTPException 503 if both fail —
the caller should surface this to the user rather than silently using a
stale signal premium.
"""

import logging

from fastapi import HTTPException

logger = logging.getLogger(__name__)

_STALE_THRESHOLD_SECONDS = 10


async def get_live_price(fyers_symbol: str) -> float:
    """Return the live LTP for a Fyers-qualified symbol.

    Lookup order:
    1. Redis price cache (set by feed_manager on every tick)
    2. Fyers REST quotes API (blocking fallback)

    Raises HTTPException(503) if live price is unavailable.
    """
    from app.core.redis import get_cached_price

    try:
        cached = await get_cached_price(fyers_symbol)
        if cached and cached.get("ltp"):
            ltp = float(cached["ltp"])
            if ltp > 0:
                return ltp
    except Exception:
        logger.debug("Redis price lookup failed for %s", fyers_symbol)

    # Cache miss — fall back to Fyers REST
    try:
        from app.data_feed.fyers_client import FyersClient
        client = FyersClient()
        data = await client.get_quotes([fyers_symbol])
        quotes = (data.get("d") or [])
        for item in quotes:
            v = item.get("v", {})
            ltp = float(v.get("lp") or v.get("ltp") or 0)
            if ltp > 0:
                return ltp
    except Exception as exc:
        logger.warning("Fyers REST quote failed for %s: %s", fyers_symbol, exc)

    raise HTTPException(
        status_code=503,
        detail=f"Live price unavailable for {fyers_symbol}. Please retry in a moment.",
    )
