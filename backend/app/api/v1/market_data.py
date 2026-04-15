import logging

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select, desc

logger = logging.getLogger(__name__)
from sqlalchemy.ext.asyncio import AsyncSession

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
    """Get all cached prices at once. Used by frontend on page load."""
    from app.core.constants import FYERS_SYMBOL_MAP
    from app.core.redis import get_redis
    import json

    r = get_redis()
    prices = {}
    for symbol in FYERS_SYMBOL_MAP:
        data = await r.get(f"price:{symbol}")
        if data:
            prices[symbol] = json.loads(data)
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
    """Search for option contract symbols.

    Returns matching Fyers symbols for options contracts.
    Query format examples: "NIFTY 24000", "BANKNIFTY 56000 CE"
    """
    from app.core.redis import get_redis
    from app.config import settings as cfg

    r = get_redis()
    token = await r.get("fyers:access_token")
    if not token:
        return {"results": []}

    try:
        from fyers_apiv3.fyersModel import FyersModel

        fyers = FyersModel(client_id=cfg.fyers_app_id, token=token)

        # Parse the query to identify index and strike
        parts = q.upper().split()
        index = parts[0] if parts else ""

        # Map common names to Fyers format
        index_map = {
            "NIFTY": "NIFTY",
            "BANKNIFTY": "BANKNIFTY",
            "FINNIFTY": "FINNIFTY",
            "SENSEX": "SENSEX",
            "MIDCPNIFTY": "MIDCPNIFTY",
        }

        matched_index = index_map.get(index)
        if not matched_index:
            return {"results": []}

        # Use option chain to get available strikes
        exchange = "BSE" if matched_index == "SENSEX" else "NSE"
        oc_symbol = f"{exchange}:{matched_index}-INDEX"
        result = fyers.optionchain({"symbol": oc_symbol, "strikecount": 10})

        if result.get("s") != "ok":
            return {"results": []}

        options = []
        for opt in result.get("data", {}).get("optionsChain", []):
            symbol = opt.get("symbol", "")
            strike = opt.get("strikePrice", 0)
            opt_type = opt.get("option_type", "")
            type_label = "CE" if opt_type == "CE" else "PE"
            ltp = opt.get("ltp", 0)
            expiry = opt.get("expiryDate", "")

            # Filter by strike if user specified one
            if len(parts) > 1:
                try:
                    target_strike = float(parts[1])
                    if abs(strike - target_strike) > 500:
                        continue
                except ValueError:
                    pass

            # Filter by option type if specified
            if len(parts) > 2 and parts[2] in ("CE", "PE"):
                if type_label != parts[2]:
                    continue

            options.append({
                "symbol": symbol,
                "display": f"{matched_index} {int(strike)} {type_label}",
                "strike": strike,
                "type": type_label,
                "ltp": ltp,
                "expiry": expiry,
            })

        # Sort by strike proximity to ATM
        options.sort(key=lambda x: x["strike"])
        return {"results": options[:20]}

    except Exception as e:
        logger.exception("Symbol search failed")
        return {"results": []}


@router.post("/feed/stop")
async def stop_data_feed():
    """Stop the Fyers live data feed."""
    await fyers_ws_client.stop()
    return {"status": "stopped"}
