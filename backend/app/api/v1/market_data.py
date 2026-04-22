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

    Returns prices for default indices AND any custom watchlist symbols.
    If no prices are cached (first load or cache expired), automatically
    fetches latest quotes from Fyers REST API to populate the cache.
    """
    from app.core.constants import FYERS_SYMBOL_MAP
    from app.core.redis import get_redis
    import json

    r = get_redis()
    prices = {}

    # Default index symbols
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

    # Include watchlist symbols
    watchlist_items = await r.hgetall("watchlist:items")
    for fyers_symbol in watchlist_items:
        if fyers_symbol not in prices:
            data = await r.get(f"price:{fyers_symbol}")
            if data:
                prices[fyers_symbol] = json.loads(data)

    return prices


@router.get("/price/{symbol}", response_model=PriceResponse | None)
async def get_price(symbol: str):
    price_data = await get_cached_price(symbol)
    if not price_data:
        return None
    return PriceResponse(**price_data)


@router.get("/ohlcv/{symbol}")
async def get_ohlcv(
    symbol: str,
    resolution: str = Query(default="5", pattern="^(1|5|15|60|D)$"),
    days: int = Query(default=5, le=365),
):
    """Fetch OHLCV candles for a symbol via Fyers history API.

    Args:
        symbol: Internal symbol name (e.g. "NIFTY", "TCS")
        resolution: Candle timeframe — "1" (1m), "5" (5m), "15" (15m), "60" (1h), "D" (daily)
        days: Number of calendar days of history (default 5, max 365)

    Returns candles directly from Fyers (pre-aggregated, no client-side work needed).
    Falls back to PostgreSQL if Fyers is unavailable.
    """
    import asyncio
    from datetime import datetime, date, timedelta
    from app.core.redis import get_redis
    from app.services.candle_backfill import _resolve_fyers_symbol
    from app.core.constants import FYERS_SYMBOL_MAP

    # Resolve to Fyers symbol
    # Check strategy_configs symbol_map first
    fyers_symbol = await _resolve_symbol_for_chart(symbol)

    r = get_redis()
    token = await r.get("fyers:access_token")
    if not token:
        logger.warning("No Fyers token — falling back to DB for chart data")
        return await _fallback_ohlcv_from_db(symbol, days)

    today = date.today()
    from_date = today - timedelta(days=days)

    try:
        candles = await asyncio.to_thread(
            _fetch_chart_history, token, fyers_symbol, resolution, from_date, today,
        )
        if candles:
            return candles
    except Exception:
        logger.exception("Fyers history failed for %s, falling back to DB", symbol)

    return await _fallback_ohlcv_from_db(symbol, days)


def _fetch_chart_history(
    token: str, fyers_symbol: str, resolution: str, from_date, to_date,
) -> list[dict]:
    """Fetch OHLCV from Fyers SDK history API (synchronous).

    Fyers limits the date range per request depending on resolution.
    For longer ranges, we paginate with sequential chunks.
    """
    from fyers_apiv3.fyersModel import FyersModel
    from datetime import timedelta
    import time as _time

    # Max days per request by resolution (empirically determined)
    max_chunk_days = {
        "1": 7, "5": 30, "15": 60, "60": 100, "D": 365,
    }
    chunk_size = timedelta(days=max_chunk_days.get(resolution, 30))

    fyers = FyersModel(client_id=cfg.fyers_app_id, token=token)
    all_candles: list[dict] = []
    seen: set[int] = set()

    chunk_start = from_date
    while chunk_start <= to_date:
        chunk_end = min(chunk_start + chunk_size, to_date)

        result = fyers.history({
            "symbol": fyers_symbol,
            "resolution": resolution,
            "date_format": "1",
            "range_from": str(chunk_start),
            "range_to": str(chunk_end),
            "cont_flag": "1",
        })

        if result.get("s") == "ok":
            for c in result.get("candles", []):
                ts = c[0]
                if ts not in seen:
                    seen.add(ts)
                    all_candles.append({
                        "timestamp": ts,
                        "open": c[1],
                        "high": c[2],
                        "low": c[3],
                        "close": c[4],
                        "volume": c[5],
                    })

        chunk_start = chunk_end + timedelta(days=1)
        if chunk_start <= to_date:
            _time.sleep(0.3)  # Rate limit between chunks

    all_candles.sort(key=lambda x: x["timestamp"])
    return all_candles


async def _resolve_symbol_for_chart(symbol: str) -> str:
    """Resolve an internal symbol to its Fyers symbol for chart data.

    Checks: already Fyers format → FYERS_SYMBOL_MAP → strategy_configs.symbol_map → fallback.
    """
    from app.core.constants import FYERS_SYMBOL_MAP

    # Already a fully-qualified Fyers symbol (e.g. "NSE:ADANIPORTS-EQ")
    if ":" in symbol:
        return symbol

    if symbol in FYERS_SYMBOL_MAP:
        return FYERS_SYMBOL_MAP[symbol]

    # Check strategy_configs symbol_map
    from app.core.database import async_session_factory
    from app.models.strategy_config import StrategyConfig
    from sqlalchemy import select as sa_select

    try:
        async with async_session_factory() as session:
            result = await session.execute(
                sa_select(StrategyConfig.symbol_map).where(
                    StrategyConfig.is_active == True  # noqa: E712
                )
            )
            for (sym_map,) in result.all():
                if sym_map and symbol in sym_map:
                    return sym_map[symbol]
    except Exception:
        pass

    return f"NSE:{symbol}-EQ"


async def _fallback_ohlcv_from_db(symbol: str, days: int) -> list[dict]:
    """Fallback: fetch 1m candles from PostgreSQL when Fyers is unavailable."""
    from datetime import datetime, timedelta
    from sqlalchemy import and_
    from app.core.constants import MARKET_OPEN
    from app.core.database import async_session_factory

    today = datetime.now(IST).date()
    start_date = today - timedelta(days=int(days * 1.5))
    start_ts = datetime.combine(start_date, MARKET_OPEN, tzinfo=IST)

    async with async_session_factory() as session:
        result = await session.execute(
            select(MarketData1m)
            .where(and_(
                MarketData1m.symbol == symbol,
                MarketData1m.timestamp >= start_ts,
            ))
            .order_by(MarketData1m.timestamp)
        )
        candles = result.scalars().all()

    # Deduplicate and sort ascending
    seen = set()
    result_list = []
    for c in candles:
        ts = int(c.timestamp.timestamp())
        if ts not in seen:
            seen.add(ts)
            result_list.append({
                "timestamp": ts,
                "open": float(c.open),
                "high": float(c.high),
                "low": float(c.low),
                "close": float(c.close),
                "volume": int(c.volume),
            })
    result_list.sort(key=lambda x: x["timestamp"])
    return result_list


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
