"""Live option/futures price lookup + paper fill pricing.

Two entry points:

- ``get_live_price`` — bare LTP. Used for SL/target TRIGGER detection, MTM and
  display. Tries Redis LTP cache first, falls back to a synchronous Fyers REST
  quote. Raises HTTPException 503 if both fail.

- ``get_fill_price`` — the price a paper execution FILLS at. Under the
  ``trading_config.fill_model = "BID_ASK"`` regime a BUY fills at the ask and a
  SELL fills at the bid (LTP is what the last trader got, not what a marketable
  order gets). Falls back to LTP per fill when the top-of-book is missing or
  stale, and records that it fell back. ``fill_model = "LTP"`` fills everything
  at LTP (the pre-cutover behaviour). Every fill returns a ``FillResult`` whose
  ``to_record()`` is persisted into ``Trade.fill_meta`` — the paper→live
  slippage dataset.
"""

import logging
from dataclasses import dataclass
from datetime import datetime

from fastapi import HTTPException

from app.core.constants import IST

logger = logging.getLogger(__name__)

_STALE_THRESHOLD_SECONDS = 10

FILL_MODEL_BID_ASK = "BID_ASK"
FILL_MODEL_LTP = "LTP"


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
    quote = await _quote_from_rest(fyers_symbol)
    if quote and quote.get("ltp"):
        return float(quote["ltp"])

    raise HTTPException(
        status_code=503,
        detail=f"Live price unavailable for {fyers_symbol}. Please retry in a moment.",
    )


@dataclass(frozen=True)
class FillResult:
    """Outcome of a single paper fill: the booked price + the quote snapshot.

    ``model`` is the model ACTUALLY used for this fill ("BID_ASK" or "LTP") —
    it can differ from the configured regime when bid/ask was missing/stale
    (``fallback_reason`` says why). ``spread_cost`` is the per-unit cost of the
    fill versus LTP (>= 0 under normal books): multiply by quantity for rupees.
    """

    price: float
    model: str  # model actually used for THIS fill
    side: str  # "BUY" | "SELL"
    ltp: float | None
    bid: float | None
    ask: float | None
    fallback_reason: str | None = None  # None when model == BID_ASK

    @property
    def spread_bps(self) -> float | None:
        """Full bid/ask spread in basis points of the mid, when a book was seen."""
        if not self.bid or not self.ask:
            return None
        mid = (self.bid + self.ask) / 2
        if mid <= 0:
            return None
        return round((self.ask - self.bid) / mid * 10000, 2)

    @property
    def spread_cost(self) -> float:
        """Per-unit cost of this fill vs LTP (BUY: fill−ltp, SELL: ltp−fill)."""
        if self.ltp is None:
            return 0.0
        cost = self.price - self.ltp if self.side == "BUY" else self.ltp - self.price
        return round(cost, 4)

    def to_record(self) -> dict:
        """JSONB-safe per-fill record for Trade.fill_meta."""
        return {
            "model": self.model,
            "fallback": self.fallback_reason,
            "side": self.side,
            "price": self.price,
            "ltp": self.ltp,
            "bid": self.bid,
            "ask": self.ask,
            "spread_bps": self.spread_bps,
            "spread_cost": self.spread_cost,
            "ts": datetime.now(IST).isoformat(),
        }


async def get_fill_price(fyers_symbol: str, side: str) -> FillResult:
    """Return the paper fill price for a marketable order on ``fyers_symbol``.

    side: "BUY" fills at the ask, "SELL" fills at the bid (when the configured
    ``trading_config.fill_model`` is BID_ASK and a fresh valid book exists).

    Quote sourcing: fresh Redis tick (≤10s old) → Fyers REST quotes → stale
    Redis tick (LTP only). Per-fill LTP fallback (recorded in
    ``fallback_reason``) when the book is missing/invalid/stale. Raises
    HTTPException(503) when no price of any kind is available — same contract
    as get_live_price.
    """
    if side not in ("BUY", "SELL"):
        raise ValueError(f"Invalid fill side: {side!r}")

    from app.services.trading_config import get_trading_config

    cfg = await get_trading_config()
    configured = getattr(cfg, "fill_model", FILL_MODEL_BID_ASK)

    cached = await _quote_from_cache(fyers_symbol)
    quote = None
    if cached and cached["fresh"]:
        quote = cached
    else:
        quote = await _quote_from_rest(fyers_symbol)

    fallback_reason = None
    if quote is None:
        # No fresh tick and REST failed — a stale cached LTP is the last resort.
        if cached and cached.get("ltp"):
            quote = cached
            fallback_reason = "stale_quote"
        else:
            raise HTTPException(
                status_code=503,
                detail=f"Live price unavailable for {fyers_symbol}. Please retry in a moment.",
            )

    ltp = quote.get("ltp") or None
    bid = quote.get("bid") or None
    ask = quote.get("ask") or None

    if configured == FILL_MODEL_LTP:
        fallback_reason = "config"
    elif fallback_reason is None and not _book_valid(bid, ask):
        fallback_reason = "missing_bid_ask"

    if fallback_reason is None:
        price = ask if side == "BUY" else bid
        return FillResult(
            price=float(price), model=FILL_MODEL_BID_ASK, side=side,
            ltp=float(ltp) if ltp else None, bid=float(bid), ask=float(ask),
        )

    if not ltp or float(ltp) <= 0:
        raise HTTPException(
            status_code=503,
            detail=f"Live price unavailable for {fyers_symbol}. Please retry in a moment.",
        )
    return FillResult(
        price=float(ltp), model=FILL_MODEL_LTP, side=side,
        ltp=float(ltp),
        bid=float(bid) if bid else None,
        ask=float(ask) if ask else None,
        fallback_reason=fallback_reason,
    )


def _book_valid(bid, ask) -> bool:
    """A usable top-of-book: both sides present, positive, not crossed."""
    try:
        return bid is not None and ask is not None and float(bid) > 0 and float(ask) >= float(bid)
    except (TypeError, ValueError):
        return False


async def _quote_from_cache(fyers_symbol: str) -> dict | None:
    """Read {ltp, bid, ask, fresh} from the Redis tick cache; None on miss/error."""
    from app.core.redis import get_cached_price

    try:
        cached = await get_cached_price(fyers_symbol)
    except Exception:
        logger.debug("Redis price lookup failed for %s", fyers_symbol)
        return None
    if not cached or not cached.get("ltp") or float(cached["ltp"]) <= 0:
        return None

    fresh = False
    try:
        ts = datetime.fromisoformat(cached["timestamp"])
        age = (datetime.now(IST) - ts).total_seconds()
        fresh = 0 <= age <= _STALE_THRESHOLD_SECONDS
    except (KeyError, TypeError, ValueError):
        pass

    return {
        "ltp": float(cached["ltp"]),
        "bid": float(cached.get("bid") or 0),
        "ask": float(cached.get("ask") or 0),
        "fresh": fresh,
    }


async def _quote_from_rest(fyers_symbol: str) -> dict | None:
    """Fetch {ltp, bid, ask, fresh=True} via Fyers REST quotes; None on failure."""
    try:
        from app.data_feed.fyers_client import FyersClient
        client = FyersClient()
        data = await client.get_quotes([fyers_symbol])
        for item in data.get("d") or []:
            v = item.get("v", {})
            ltp = float(v.get("lp") or v.get("ltp") or 0)
            if ltp > 0:
                return {
                    "ltp": ltp,
                    "bid": float(v.get("bid") or 0),
                    "ask": float(v.get("ask") or 0),
                    "fresh": True,
                }
    except Exception as exc:
        logger.warning("Fyers REST quote failed for %s: %s", fyers_symbol, exc)
    return None
