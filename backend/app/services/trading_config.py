"""DB-backed trading configuration service.

Single source of truth for user-editable trading parameters:
  capital, max_daily_drawdown_pct, max_risk_per_trade_pct,
  max_trades_per_day, paper_trading, autonomy_level.

Read path: in-memory cache (O(1) after first load) — zero DB hits on hot reads.
Write path: DB → update cache → publish Redis pubsub event.
Reload path: pubsub listener reloads cache from DB on any write.

Seeding: on startup, if the trading_config row is missing, it is inserted
         with values from .env (settings.*) as a one-time bootstrap.
"""

import asyncio
import logging
from dataclasses import dataclass

from sqlalchemy import select

from app.core.database import async_session_factory
from app.models.trading_config import TradingConfig

logger = logging.getLogger(__name__)

_PUBSUB_CHANNEL = "config:trading:updated"


@dataclass(frozen=True)
class TradingConfigDTO:
    capital: int
    max_daily_drawdown_pct: float
    max_risk_per_trade_pct: float
    max_trades_per_day: int
    paper_trading: bool
    autonomy_level: str  # "MANUAL" | "SEMI" | "YOLO"
    min_confidence_to_persist: float
    min_confidence_for_shadow: float
    min_confidence_for_execution: float
    shadow_skip_permanent_watchlist: bool
    yolo_skip_permanent_watchlist: bool

    @property
    def yolo_mode(self) -> bool:
        return self.autonomy_level == "YOLO"

    @property
    def max_drawdown_amount(self) -> float:
        return self.capital * (self.max_daily_drawdown_pct / 100.0)


# Module-level in-memory cache — populated on first call or startup
_cache: TradingConfigDTO | None = None


def _row_to_dto(row: TradingConfig) -> TradingConfigDTO:
    return TradingConfigDTO(
        capital=int(row.capital),
        max_daily_drawdown_pct=float(row.max_daily_drawdown_pct),
        max_risk_per_trade_pct=float(row.max_risk_per_trade_pct),
        max_trades_per_day=int(row.max_trades_per_day),
        paper_trading=bool(row.paper_trading),
        autonomy_level=str(row.autonomy_level),
        min_confidence_to_persist=float(row.min_confidence_to_persist),
        min_confidence_for_shadow=float(row.min_confidence_for_shadow),
        min_confidence_for_execution=float(row.min_confidence_for_execution),
        shadow_skip_permanent_watchlist=bool(row.shadow_skip_permanent_watchlist),
        yolo_skip_permanent_watchlist=bool(row.yolo_skip_permanent_watchlist),
    )


async def _load_from_db() -> TradingConfigDTO:
    async with async_session_factory() as session:
        result = await session.execute(select(TradingConfig).where(TradingConfig.id == 1))
        row = result.scalar_one()
        return _row_to_dto(row)


async def get_trading_config() -> TradingConfigDTO:
    """Return the current trading configuration.

    After the initial load, this is a pure in-memory operation.
    """
    global _cache
    if _cache is None:
        _cache = await _load_from_db()
    return _cache


def get_trading_config_sync() -> TradingConfigDTO | None:
    """Return cached config or None (no DB call). For use in sync code paths."""
    return _cache


async def update_trading_config(**fields) -> TradingConfigDTO:
    """Partially update the trading configuration.

    Valid field names match TradingConfigDTO attributes.
    Writes to DB, updates in-memory cache, publishes pubsub event.
    """
    global _cache

    allowed = {
        "capital", "max_daily_drawdown_pct", "max_risk_per_trade_pct",
        "max_trades_per_day", "paper_trading", "autonomy_level",
        "min_confidence_to_persist", "min_confidence_for_shadow", "min_confidence_for_execution",
        "shadow_skip_permanent_watchlist", "yolo_skip_permanent_watchlist",
    }
    invalid = set(fields) - allowed
    if invalid:
        raise ValueError(f"Unknown trading config fields: {invalid}")

    if "autonomy_level" in fields:
        level = fields["autonomy_level"]
        if level not in ("MANUAL", "SEMI", "YOLO"):
            raise ValueError(f"Invalid autonomy_level: {level!r}. Must be MANUAL, SEMI, or YOLO.")

    persist_val = fields.get("min_confidence_to_persist")
    shadow_val = fields.get("min_confidence_for_shadow")
    exec_val = fields.get("min_confidence_for_execution")
    if persist_val is not None or shadow_val is not None or exec_val is not None:
        effective_persist = persist_val if persist_val is not None else (_cache.min_confidence_to_persist if _cache else 30.0)
        effective_shadow = shadow_val if shadow_val is not None else (_cache.min_confidence_for_shadow if _cache else 70.0)
        effective_exec = exec_val if exec_val is not None else (_cache.min_confidence_for_execution if _cache else 70.0)
        if not (effective_persist < effective_shadow <= effective_exec):
            raise ValueError(
                f"Confidence tiers must satisfy persist < shadow <= execution "
                f"(got {effective_persist} < {effective_shadow} <= {effective_exec})"
            )

    async with async_session_factory() as session:
        result = await session.execute(select(TradingConfig).where(TradingConfig.id == 1))
        row = result.scalar_one()
        for key, value in fields.items():
            setattr(row, key, value)
        await session.commit()
        await session.refresh(row)
        new_dto = _row_to_dto(row)

    _cache = new_dto

    # Notify any other processes / listeners that config changed
    try:
        from app.core.redis import get_redis
        r = get_redis()
        await r.publish(_PUBSUB_CHANNEL, "updated")
    except Exception:
        logger.warning("Could not publish config:trading:updated — listeners may lag")

    logger.info("Trading config updated: %s", fields)
    return new_dto


async def ensure_seeded() -> None:
    """Insert the singleton row from .env defaults if it doesn't exist yet.

    Called once per startup, before any other code reads the config.
    After the first boot the row always exists and .env values are ignored.
    """
    from app.config import settings

    async with async_session_factory() as session:
        result = await session.execute(select(TradingConfig).where(TradingConfig.id == 1))
        row = result.scalar_one_or_none()
        if row is not None:
            logger.info("Trading config row already exists — skipping seed")
            return

        autonomy_level = "YOLO" if settings.yolo_mode else "SEMI"
        row = TradingConfig(
            id=1,
            capital=settings.trading_capital,
            max_daily_drawdown_pct=settings.max_daily_drawdown_pct,
            max_risk_per_trade_pct=settings.max_risk_per_trade_pct,
            max_trades_per_day=settings.max_trades_per_day,
            paper_trading=settings.paper_trading,
            autonomy_level=autonomy_level,
            min_confidence_to_persist=30.0,
            min_confidence_for_shadow=70.0,
            min_confidence_for_execution=70.0,
            shadow_skip_permanent_watchlist=True,
            yolo_skip_permanent_watchlist=True,
        )
        session.add(row)
        await session.commit()
        logger.info(
            "Trading config seeded from .env: capital=%s, drawdown=%.1f%%, autonomy=%s",
            settings.trading_capital, settings.max_daily_drawdown_pct, autonomy_level,
        )


async def start_config_listener() -> None:
    """Subscribe to the config:trading:updated pubsub channel.

    Reloads the in-memory cache from DB whenever any process writes a new config.
    Runs as a background asyncio task — registered in task_registry by main.py.
    """
    global _cache
    from app.core.redis import get_redis

    r = get_redis()
    pubsub = r.pubsub()
    await pubsub.subscribe(_PUBSUB_CHANNEL)
    logger.info("Trading config listener subscribed to %s", _PUBSUB_CHANNEL)

    try:
        async for message in pubsub.listen():
            if message.get("type") != "message":
                continue
            try:
                _cache = await _load_from_db()
                logger.info(
                    "Trading config reloaded from DB: capital=%s autonomy=%s",
                    _cache.capital, _cache.autonomy_level,
                )
            except Exception:
                logger.exception("Failed to reload trading config from DB")
    except asyncio.CancelledError:
        await pubsub.unsubscribe(_PUBSUB_CHANNEL)
        logger.info("Trading config listener stopped")
