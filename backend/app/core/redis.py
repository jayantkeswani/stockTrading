import redis.asyncio as redis

from app.config import settings

redis_pool = redis.ConnectionPool.from_url(
    settings.redis_url, decode_responses=True, max_connections=50,
)


def get_redis() -> redis.Redis:
    return redis.Redis(connection_pool=redis_pool)


async def publish_event(channel: str, message: str) -> None:
    """Publish a message to a Redis pub/sub channel."""
    r = get_redis()
    await r.publish(channel, message)


async def cache_price(symbol: str, price_data: dict) -> None:
    """Cache latest price in Redis with 24h TTL.

    Long TTL ensures prices survive page refreshes and feed restarts.
    The ``timestamp`` field inside the data indicates when the price was last updated.
    """
    import json

    r = get_redis()
    await r.setex(f"price:{symbol}", 86400, json.dumps(price_data))


async def get_cached_price(symbol: str) -> dict | None:
    """Get cached price from Redis."""
    import json

    r = get_redis()
    data = await r.get(f"price:{symbol}")
    if data:
        return json.loads(data)
    return None
