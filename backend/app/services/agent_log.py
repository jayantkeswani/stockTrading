"""Shared agent log utility — prefix-parameterized Redis list for strategy diagnostics."""

import json
import time
from datetime import date

from app.core.redis import get_redis

REDIS_TTL = 90 * 86400  # 90 days


async def append_agent_log(prefix: str, today: date, category: str, message: str) -> None:
    """Append an entry to today's agent log in Redis."""
    r = get_redis()
    key = f"{prefix}:agent_log:{today}"
    entry = {
        "timestamp": time.time(),
        "category": category,
        "message": message,
    }
    await r.rpush(key, json.dumps(entry))
    await r.expire(key, REDIS_TTL)


async def get_agent_log(
    prefix: str, date_str: str, *, offset: int = 0, limit: int = 0
) -> list[dict] | tuple[list[dict], int]:
    """Read agent log entries from Redis.

    limit=0: returns all entries oldest-first (bare list).
    limit>0: returns (entries_newest_first, total_count).
    """
    r = get_redis()
    key = f"{prefix}:agent_log:{date_str}"

    if limit <= 0:
        raw_list = await r.lrange(key, 0, -1)
        return [json.loads(item) for item in raw_list]

    total = await r.llen(key)
    if total == 0:
        return [], 0

    start = max(0, total - offset - limit)
    end = total - offset - 1
    if end < 0 or start > end:
        return [], total

    raw_list = await r.lrange(key, start, end)
    entries = [json.loads(item) for item in reversed(raw_list)]
    return entries, total
