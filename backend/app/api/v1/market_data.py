import logging

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import select, desc

logger = logging.getLogger(__name__)
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings as cfg
from app.core.constants import IST
from app.core.database import get_db
from app.core.redis import get_cached_price
from app.core.utils import is_market_open, is_in_trading_window, is_in_dead_zone, time_to_market_close_minutes
from app.data_feed.fyers_ws_client import fyers_ws_client
from app.models.market_data import MarketData1m
from app.schemas.market_data import CandleResponse, PriceResponse
from app.schemas.risk import MarketStatusResponse

router = APIRouter()


@router.get("/prices")
async def get_all_prices():
    """Get all cached prices at once. Used by frontend on page load.

    If no prices are cached (first load or cache expired), automatically
    fetches latest quotes from Fyers REST API to populate the cache.
    """
    from app.core.constants import FYERS_SYMBOL_MAP
    from app.core.redis import get_redis
    import json

    r = get_redis()
    prices = {}
    for symbol in FYERS_SYMBOL_MAP:
        data = await r.get(f"price:{symbol}")
        if data:
            prices[symbol] = json.loads(data)

    # If cache is empty, try to fetch fresh quotes via REST API
    if not prices:
        try:
            await fyers_ws_client.fetch_quotes_rest()
            # Re-read from cache after refresh
            for symbol in FYERS_SYMBOL_MAP:
                data = await r.get(f"price:{symbol}")
                if data:
                    prices[symbol] = json.loads(data)
        except Exception:
            logger.warning("Auto-refresh of quotes failed on empty cache")

    return prices


@router.get("/price/{symbol}", response_model=PriceResponse | None)
async def get_price(symbol: str):
    price_data = await get_cached_price(symbol)
    if not price_data:
        return None
    return PriceResponse(**price_data)


@router.get("/ohlcv/{symbol}", response_model=list[CandleResponse])
async def get_ohlcv(
    symbol: str,
    limit: int = Query(default=200, le=1000),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(MarketData1m)
        .where(MarketData1m.symbol == symbol)
        .order_by(desc(MarketData1m.timestamp))
        .limit(limit)
    )
    candles = result.scalars().all()
    return list(reversed(candles))


@router.get("/status", response_model=MarketStatusResponse)
async def market_status():
    return MarketStatusResponse(
        is_open=is_market_open(),
        in_trading_window=is_in_trading_window(),
        in_dead_zone=is_in_dead_zone(),
        minutes_to_close=time_to_market_close_minutes(),
        fyers_connected=fyers_ws_client.is_connected,
    )


@router.post("/feed/start")
async def start_data_feed():
    """Start the Fyers live data feed."""
    if fyers_ws_client.is_connected:
        return {"status": "already_connected"}
    await fyers_ws_client.start()
    return {"status": "connecting"}


@router.post("/feed/refresh")
async def refresh_quotes():
    """Fetch latest quotes via REST API (works even when market is closed)."""
    await fyers_ws_client.fetch_quotes_rest()
    return {"status": "refreshed"}


@router.get("/symbols/search")
async def search_symbols(q: str = Query(min_length=2, max_length=50)):
    """Search for tradeable symbols — stocks, futures, and options.

    Searches the local symbol master (refreshed daily from Fyers).
    No Fyers API call is made; only local data is searched.

    Query format examples:
      - "TCS"              → TCS equity + futures + options
      - "TCS FUT"          → TCS futures only
      - "NIFTY 24000"      → NIFTY options near strike 24000
      - "NIFTY 24000CE"    → NIFTY 24000 CE options
      - "NIFTY 24000 CE"   → same as above
      - "RELIANCE"         → RELIANCE equity + derivatives
    """
    from app.data_feed.symbol_master import symbol_master

    if not symbol_master.is_loaded:
        logger.warning("Symbol master not loaded, attempting load...")
        try:
            await symbol_master.load()
        except Exception:
            logger.exception("Failed to load symbol master")
            return {"results": []}

    matches = symbol_master.search(q, limit=20)

    results = []
    for m in matches:
        results.append({
            "symbol": m["s"],
            "display": m["d"],
            "short_name": m["n"],
            "segment": m["g"],
            "strike": m.get("k") or 0,
            "type": m.get("t") or "",
            "ltp": 0,  # Price fetched separately by frontend
            "expiry": m.get("x") or "",
            "lot_size": m.get("l") or 1,
        })

    return {"results": results}


class BatchPriceRequest(BaseModel):
    symbols: list[str] = Field(..., max_length=50)


@router.post("/prices/batch")
async def get_batch_prices(body: BatchPriceRequest):
    """Fetch prices for a list of Fyers symbols (max 50).

    Checks Redis cache first, then fetches missing prices from Fyers REST API.
    Used by the watchlist to get prices for custom symbols.
    """
    from app.core.redis import get_redis
    import json

    if not body.symbols:
        return {}

    r = get_redis()
    prices = {}

    # Check cache first
    uncached = []
    for sym in body.symbols:
        data = await r.get(f"price:{sym}")
        if data:
            prices[sym] = json.loads(data)
        else:
            uncached.append(sym)

    # Fetch uncached from Fyers REST API
    if uncached:
        token = await r.get("fyers:access_token")
        if token:
            try:
                from datetime import datetime
                from fyers_apiv3.fyersModel import FyersModel
                from app.core.redis import cache_price

                fyers = FyersModel(client_id=cfg.fyers_app_id, token=token)
                # Fyers quotes() accepts max 50 symbols at a time
                for i in range(0, len(uncached), 50):
                    batch = uncached[i:i + 50]
                    result = fyers.quotes({"symbols": ",".join(batch)})
                    if result.get("s") == "ok":
                        for item in result.get("d", []):
                            v = item.get("v", {})
                            fyers_symbol = item.get("n", "")
                            if not fyers_symbol:
                                continue
                            price_data = {
                                "symbol": fyers_symbol,
                                "ltp": v.get("lp", 0),
                                "bid": v.get("bid", v.get("lp", 0)),
                                "ask": v.get("ask", v.get("lp", 0)),
                                "volume": v.get("volume", 0),
                                "change": v.get("ch", 0),
                                "change_pct": v.get("chp", 0),
                                "timestamp": datetime.now(IST).isoformat(),
                            }
                            await cache_price(fyers_symbol, price_data)
                            prices[fyers_symbol] = price_data
            except Exception:
                logger.exception("Failed to fetch batch prices from Fyers")

    return prices


@router.post("/feed/stop")
async def stop_data_feed():
    """Stop the Fyers live data feed."""
    await fyers_ws_client.stop()
    return {"status": "stopped"}
