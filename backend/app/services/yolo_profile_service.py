"""YOLO profile service — CRUD + in-memory cache for profit cap tiers.

Each profile defines a profit_cap. During live trading, each signal creates
one Trade+Position per active uncapped profile. Trade monitor checks caps
per profile independently.

Cache pattern mirrors trading_config.py: in-memory list, refreshed via
Redis pubsub on any write.
"""

import asyncio
import logging
import uuid
from dataclasses import dataclass
from datetime import date

from sqlalchemy import select, func, and_, or_

from app.core.database import async_session_factory
from app.models.yolo_profile import YoloProfile
from app.models.trade import Trade
from app.core.enums import TradeSource, ExitReason

logger = logging.getLogger(__name__)

_PUBSUB_CHANNEL = "config:yolo_profiles:updated"


@dataclass(frozen=True)
class YoloProfileDTO:
    id: uuid.UUID
    name: str
    profit_cap: float
    is_active: bool
    sort_order: int
    # Thesis-invalidation exit (S5/S6 momentum). invalidation_persist=None disables it.
    invalidation_persist: int | None = None
    invalidation_quorum: bool = False
    invalidation_strong_only: bool = True
    # Per-profile YOLO execution-confidence threshold. None = inherit the global default.
    min_confidence_for_execution: float | None = None
    # Per-profile intraday-bias strength gate (None = no gate; "WEAK"/"MODERATE"/"STRONG").
    min_bias_strength: str | None = None
    # Execution-side filters (tuples so the frozen DTO stays hashable). Empty = all.
    strategies: tuple[str, ...] = ()
    setups: tuple[str, ...] = ()


_cache: list[YoloProfileDTO] | None = None


def _row_to_dto(row: YoloProfile) -> YoloProfileDTO:
    return YoloProfileDTO(
        id=row.id,
        name=str(row.name),
        profit_cap=float(row.profit_cap),
        is_active=bool(row.is_active),
        sort_order=int(row.sort_order),
        invalidation_persist=(
            int(row.invalidation_persist) if row.invalidation_persist is not None else None
        ),
        invalidation_quorum=bool(row.invalidation_quorum),
        invalidation_strong_only=bool(row.invalidation_strong_only),
        min_confidence_for_execution=(
            float(row.min_confidence_for_execution)
            if row.min_confidence_for_execution is not None else None
        ),
        min_bias_strength=row.min_bias_strength,
        strategies=tuple(row.strategies or []),
        setups=tuple(row.setups or []),
    )


def effective_execution_threshold(profile: YoloProfileDTO, global_default: float) -> float:
    """The profile's own YOLO execution-confidence threshold, or the global default when
    unset (None) or a negative sentinel. Used by: auto_executor (per-profile gate),
    min_execution_threshold_for."""
    t = profile.min_confidence_for_execution
    return float(t) if (t is not None and t >= 0) else float(global_default)


def min_execution_threshold_for(
    strategy_name: str,
    setup_type: str | None,
    global_default: float,
    bias_strength: str | None = None,
) -> float:
    """Lowest execution-confidence threshold among active profiles that subscribe to this
    signal (strategy+setup+bias) — i.e. the bar for "executable by at least one profile".
    Returns the global default when the profile cache is cold or no active profile subscribes.
    Pass `bias_strength` (via `signal_bias_strength`) so a bias-gated profile is only counted
    for signals it would actually execute. Sync (reads the in-memory cache only). Used by:
    strategy_runner (executable gate), agent_runner (notification gate), auto_executor
    (signal-level early-out)."""
    thresholds = [
        effective_execution_threshold(p, global_default)
        for p in get_active_profiles_sync()
        if profile_accepts_signal(p, strategy_name, setup_type, bias_strength)
    ]
    return min(thresholds) if thresholds else float(global_default)


_BIAS_RANK = {"WEAK": 1, "MODERATE": 2, "STRONG": 3}


def signal_bias_strength(indicators: dict | None) -> str | None:
    """Extract the stored stock intraday-bias strength from a signal's indicators JSONB
    (written at signal time as ctx.intraday_bias.components by compute_intraday_bias).
    Returns "WEAK"/"MODERATE"/"STRONG", or None when absent (non-S5 signals carry no bias).
    Used by: auto_executor, strategy_runner, agent_runner — to feed the per-profile bias gate."""
    ib = (indicators or {}).get("intraday_bias") or {}
    return ib.get("strength") if isinstance(ib, dict) else None


def _normalize_bias_strength(v: str | None) -> str | None:
    """Validate/normalize a bias-strength gate value: falsy/"" -> None (no gate); else
    uppercased and must be WEAK/MODERATE/STRONG. Raises ValueError otherwise."""
    if not v:
        return None
    u = str(v).strip().upper()
    if u not in _BIAS_RANK:
        raise ValueError("min_bias_strength must be WEAK, MODERATE, or STRONG")
    return u


def profile_accepts_signal(
    profile: YoloProfileDTO,
    strategy_name: str,
    setup_type: str | None,
    bias_strength: str | None = None,
) -> bool:
    """True if the profile's strategy/setup/bias filters admit this signal.

    Empty strategy/setup filter = accept all (backward compatible); the filters are
    independent AND conditions (a profile with `setups=["ORB_RETEST"]` only executes that
    setup; a signal with no setup_type is rejected by a non-empty setups filter). When
    `min_bias_strength` is set, the signal's stock intraday-bias `bias_strength` must be
    >= it (ordinal WEAK<MODERATE<STRONG); a signal with no bias (None) is rejected. Pass
    `bias_strength` via `signal_bias_strength(signal.indicators)`. Used by: auto_executor
    (per-profile fan-out gate), min_execution_threshold_for (executable/notify bar).
    """
    if profile.strategies and strategy_name not in profile.strategies:
        return False
    if profile.setups and (setup_type is None or setup_type not in profile.setups):
        return False
    if profile.min_bias_strength:
        if _BIAS_RANK.get(bias_strength or "", 0) < _BIAS_RANK.get(profile.min_bias_strength, 0):
            return False
    return True


async def _load_from_db() -> list[YoloProfileDTO]:
    async with async_session_factory() as session:
        result = await session.execute(
            select(YoloProfile).order_by(YoloProfile.profit_cap.asc(), YoloProfile.sort_order.asc())
        )
        return [_row_to_dto(r) for r in result.scalars().all()]


async def get_active_profiles() -> list[YoloProfileDTO]:
    """Return active profiles sorted by profit_cap ASC. Cached after first load."""
    global _cache
    if _cache is None:
        _cache = await _load_from_db()
    return [p for p in _cache if p.is_active]


def get_active_profiles_sync() -> list[YoloProfileDTO]:
    """Return cached active profiles or empty list (no DB call)."""
    if _cache is None:
        return []
    return [p for p in _cache if p.is_active]


async def get_all_profiles() -> list[YoloProfileDTO]:
    """Return all profiles (active + inactive) sorted by profit_cap ASC."""
    global _cache
    if _cache is None:
        _cache = await _load_from_db()
    return list(_cache)


async def get_uncapped_profile_ids(today: date) -> set[uuid.UUID]:
    """Return IDs of active profiles that haven't been profit-capped today."""
    active = await get_active_profiles()
    if not active:
        return set()

    async with async_session_factory() as session:
        # A profile is "capped" if it has any trade closed with PROFIT_CAP today
        result = await session.execute(
            select(Trade.yolo_profile_id)
            .where(
                and_(
                    Trade.source == TradeSource.YOLO.value,
                    Trade.exit_reason == ExitReason.PROFIT_CAP.value,
                    func.date(Trade.exit_time) == today,
                    Trade.yolo_profile_id.isnot(None),
                )
            )
            .distinct()
        )
        capped_ids = {row[0] for row in result.all()}

    return {p.id for p in active} - capped_ids


async def get_profile_by_id(profile_id: uuid.UUID) -> YoloProfileDTO | None:
    """Return a single profile by ID from cache."""
    profiles = await get_all_profiles()
    for p in profiles:
        if p.id == profile_id:
            return p
    return None


async def get_default_profile() -> YoloProfileDTO | None:
    """Return the default YOLO profile, or None if no active profiles exist.

    The default is the lowest-`sort_order` active profile — the same definition
    `update_profile`/`delete_profile` use to protect the un-deletable profile.
    Reports and per-trade notifications scope to this single profile so multi-profile
    fan-out doesn't multiply output. Async; loads the cache from DB on first use.
    """
    active = await get_active_profiles()
    return min(active, key=lambda p: p.sort_order) if active else None


def get_default_profile_sync() -> YoloProfileDTO | None:
    """Sync variant of `get_default_profile` — reads the in-memory cache only.

    Returns None when the cache is empty/unpopulated (no DB I/O). Safe to call from
    hot paths (trade_monitor close) and report tasks; the cache is kept warm by the
    500ms trade-monitor loop. Used by: trade_monitor (`_close_position`),
    default_profile_trade_filter.
    """
    active = get_active_profiles_sync()
    return min(active, key=lambda p: p.sort_order) if active else None


def default_profile_trade_filter():
    """Return a SQLAlchemy condition selecting trades for the default YOLO profile
    plus all MANUAL trades (which carry no profile), or None if the cache is empty.

    Sync (reads cache only, no DB I/O). Used by EOD/morning reports to avoid summing
    the same signal across every profile tier. Callers append the result to their
    WHERE clause only when non-None; None means "no scoping" (graceful degradation).
    """
    default = get_default_profile_sync()
    if default is None:
        return None
    return or_(
        Trade.yolo_profile_id == default.id,
        Trade.source == TradeSource.MANUAL.value,
    )


async def create_profile(
    name: str,
    profit_cap: float,
    strategies: list[str] | None = None,
    setups: list[str] | None = None,
    min_confidence_for_execution: float | None = None,
    min_bias_strength: str | None = None,
) -> YoloProfileDTO:
    """Create a new YOLO profile (optionally with strategy/setup execution filters, a
    per-profile execution-confidence threshold, and an intraday-bias strength gate;
    None/negative confidence = inherit the global default; falsy bias = no gate)."""
    if min_confidence_for_execution is not None and min_confidence_for_execution < 0:
        min_confidence_for_execution = None
    min_bias_strength = _normalize_bias_strength(min_bias_strength)
    async with async_session_factory() as session:
        # Auto-assign sort_order as max+1
        result = await session.execute(
            select(func.coalesce(func.max(YoloProfile.sort_order), -1))
        )
        max_order = result.scalar()
        row = YoloProfile(
            name=name,
            profit_cap=profit_cap,
            is_active=True,
            sort_order=max_order + 1,
            min_confidence_for_execution=min_confidence_for_execution,
            min_bias_strength=min_bias_strength,
            strategies=strategies or [],
            setups=setups or [],
        )
        session.add(row)
        await session.commit()
        await session.refresh(row)
        dto = _row_to_dto(row)

    await _invalidate_cache()
    return dto


async def update_profile(profile_id: uuid.UUID, **fields) -> YoloProfileDTO:
    """Partially update a YOLO profile."""
    allowed = {
        "name", "profit_cap", "is_active", "sort_order",
        "invalidation_persist", "invalidation_quorum", "invalidation_strong_only",
        "min_confidence_for_execution", "min_bias_strength", "strategies", "setups",
    }
    invalid = set(fields) - allowed
    if invalid:
        raise ValueError(f"Unknown profile fields: {invalid}")

    # Per-profile execution threshold: a negative value is the "inherit global" sentinel
    # (the PATCH endpoint drops None via exclude_none, so the client sends e.g. -1 to clear).
    if "min_confidence_for_execution" in fields:
        v = fields["min_confidence_for_execution"]
        if v is None or float(v) < 0:
            fields["min_confidence_for_execution"] = None
        elif not (0 <= float(v) <= 100):
            raise ValueError("min_confidence_for_execution must be between 0 and 100")

    # Bias-strength gate: the PATCH endpoint drops None via exclude_none, so the client
    # sends an empty string "" to CLEAR the gate back to "no gate" (any bias).
    if "min_bias_strength" in fields:
        fields["min_bias_strength"] = _normalize_bias_strength(fields["min_bias_strength"])

    async with async_session_factory() as session:
        result = await session.execute(
            select(YoloProfile).order_by(YoloProfile.sort_order.asc())
        )
        rows = result.scalars().all()
        row = next((r for r in rows if r.id == profile_id), None)
        if row is None:
            raise ValueError(f"Profile {profile_id} not found")
        if len(rows) > 0 and rows[0].id == profile_id and fields.get("is_active") is False:
            raise ValueError("Cannot deactivate the default profile")
        for key, value in fields.items():
            setattr(row, key, value)
        await session.commit()
        await session.refresh(row)
        dto = _row_to_dto(row)

    await _invalidate_cache()
    return dto


async def delete_profile(profile_id: uuid.UUID) -> None:
    """Delete a YOLO profile. The lowest sort_order profile cannot be deleted."""
    async with async_session_factory() as session:
        result = await session.execute(
            select(YoloProfile).order_by(YoloProfile.sort_order.asc())
        )
        rows = result.scalars().all()
        target = next((r for r in rows if r.id == profile_id), None)
        if target is None:
            raise ValueError(f"Profile {profile_id} not found")
        if len(rows) > 0 and rows[0].id == profile_id:
            raise ValueError("Cannot delete the default profile")
        await session.delete(target)
        await session.commit()

    await _invalidate_cache()


async def _invalidate_cache() -> None:
    """Reload cache and publish pubsub event."""
    global _cache
    _cache = await _load_from_db()

    try:
        from app.core.redis import get_redis
        r = get_redis()
        await r.publish(_PUBSUB_CHANNEL, "updated")
    except Exception:
        logger.warning("Could not publish %s — listeners may lag", _PUBSUB_CHANNEL)


async def start_profile_listener() -> None:
    """Subscribe to profile pubsub channel. Reloads cache on any write."""
    global _cache
    from app.core.redis import get_redis

    r = get_redis()
    pubsub = r.pubsub()
    await pubsub.subscribe(_PUBSUB_CHANNEL)
    logger.info("YOLO profile listener subscribed to %s", _PUBSUB_CHANNEL)

    # Poll with a finite timeout rather than `pubsub.listen()`. listen() reads with
    # block=True, which redis-py maps to the connection's socket_timeout (5s in
    # redis-py 8.x) and raises TimeoutError on an idle channel — killing this task.
    # get_message(timeout=...) returns None on idle instead, so the loop survives.
    try:
        while True:
            message = await pubsub.get_message(
                ignore_subscribe_messages=True, timeout=1.0
            )
            if message is None or message.get("type") != "message":
                continue
            try:
                _cache = await _load_from_db()
                logger.info("YOLO profiles reloaded: %d profiles", len(_cache))
            except Exception:
                logger.exception("Failed to reload YOLO profiles from DB")
    except asyncio.CancelledError:
        await pubsub.unsubscribe(_PUBSUB_CHANNEL)
        logger.info("YOLO profile listener stopped")
