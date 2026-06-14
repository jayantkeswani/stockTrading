"""Tests for yolo_profile_service default-profile helpers.

Covers get_default_profile() and default_profile_trade_filter(), which scope
reports + per-trade notifications to a single profile so multi-profile fan-out
doesn't multiply output.
"""

import uuid

import pytest

import app.services.yolo_profile_service as svc
from app.services.yolo_profile_service import (
    YoloProfileDTO,
    effective_execution_threshold,
    min_execution_threshold_for,
    profile_accepts_signal,
)


def _dto(
    name: str, cap: float, active: bool = True, sort_order: int = 0,
    strategies: tuple = (), setups: tuple = (), min_bias_strength: str | None = None,
) -> YoloProfileDTO:
    return YoloProfileDTO(
        id=uuid.uuid4(),
        name=name,
        profit_cap=cap,
        is_active=active,
        sort_order=sort_order,
        strategies=strategies,
        setups=setups,
        min_bias_strength=min_bias_strength,
    )


@pytest.fixture(autouse=True)
def _reset_cache():
    """Reset the module-level cache before/after each test."""
    original = svc._cache
    svc._cache = None
    yield
    svc._cache = original


@pytest.mark.asyncio
async def test_get_default_profile_is_lowest_sort_order_not_lowest_cap():
    # Default is the lowest sort_order, even when another profile has a lower cap.
    svc._cache = [
        _dto("10K", 10000, sort_order=0),
        _dto("5K", 5000, sort_order=1),
        _dto("15K", 15000, sort_order=2),
    ]
    default = await svc.get_default_profile()
    assert default.name == "10K"


@pytest.mark.asyncio
async def test_get_default_profile_skips_inactive():
    # Lowest-sort_order profile is inactive → next active one wins.
    svc._cache = [
        _dto("5K", 5000, active=False, sort_order=0),
        _dto("10K", 10000, sort_order=1),
    ]
    default = await svc.get_default_profile()
    assert default.name == "10K"


@pytest.mark.asyncio
async def test_get_default_profile_none_when_no_active():
    svc._cache = [_dto("5K", 5000, active=False)]
    assert await svc.get_default_profile() is None


def test_profile_accepts_signal_empty_filters_accept_all():
    """Empty strategies/setups = act on everything (backward compatible)."""
    p = _dto("full", 0)
    assert profile_accepts_signal(p, "breakout_retest", "ORB_RETEST")
    assert profile_accepts_signal(p, "intraday_futures", "PDH_PDL")
    assert profile_accepts_signal(p, "vwap_pullback", None)


def test_profile_accepts_signal_strategy_filter():
    p = _dto("s6-only", 0, strategies=("breakout_retest",))
    assert profile_accepts_signal(p, "breakout_retest", "SWING_RETEST")
    assert not profile_accepts_signal(p, "intraday_futures", "ORB")
    assert not profile_accepts_signal(p, "vwap_pullback", None)


def test_profile_accepts_signal_setup_filter():
    """Setup filter is independent; a non-empty setups rejects signals lacking the tag."""
    p = _dto("orb-only", 0, strategies=("breakout_retest",), setups=("ORB_RETEST",))
    assert profile_accepts_signal(p, "breakout_retest", "ORB_RETEST")
    assert not profile_accepts_signal(p, "breakout_retest", "SWING_RETEST")
    assert not profile_accepts_signal(p, "breakout_retest", None)
    # right setup but wrong strategy still rejected (AND of both filters)
    assert not profile_accepts_signal(p, "intraday_futures", "ORB_RETEST")


def test_default_profile_trade_filter_none_when_no_active():
    svc._cache = []
    assert svc.default_profile_trade_filter() is None


def test_default_profile_trade_filter_scopes_to_default_and_manual():
    # default = lowest sort_order (10K), not lowest cap (5K).
    default = _dto("10K", 10000, sort_order=0)
    other = _dto("5K", 5000, sort_order=1)
    svc._cache = [default, other]
    clause = svc.default_profile_trade_filter()
    assert clause is not None
    rendered = str(clause.compile(compile_kwargs={"literal_binds": True}))
    # OR over the default profile id and MANUAL trades; other tiers excluded.
    # SQLAlchemy renders UUID literals without dashes (hex form).
    assert default.id.hex in rendered
    assert "MANUAL" in rendered
    assert other.id.hex not in rendered


# ── Per-profile execution-confidence threshold ────────────────────────────

def test_effective_execution_threshold_inherits_or_overrides():
    g = 70.0
    inherit = _dto("def", 5000)                       # min_confidence_for_execution=None
    override = YoloProfileDTO(id=uuid.uuid4(), name="s7", profit_cap=5000, is_active=True,
                              sort_order=1, min_confidence_for_execution=40.0)
    sentinel = YoloProfileDTO(id=uuid.uuid4(), name="x", profit_cap=5000, is_active=True,
                              sort_order=2, min_confidence_for_execution=-1.0)
    assert effective_execution_threshold(inherit, g) == 70.0   # None → global
    assert effective_execution_threshold(override, g) == 40.0  # own value
    assert effective_execution_threshold(sentinel, g) == 70.0  # negative sentinel → global


def test_min_execution_threshold_for_cold_cache_returns_global():
    # _reset_cache fixture leaves the cache at None.
    assert min_execution_threshold_for("vwap_reclaim", None, 70.0) == 70.0


def test_min_execution_threshold_for_is_strategy_aware():
    # A vwap_reclaim-only profile at 40 lowers the bar for vwap_reclaim signals only;
    # the default (inherit-global) profile keeps vwap_pullback at the global 70.
    default = _dto("def", 5000, sort_order=0)          # inherits → 70
    s7 = YoloProfileDTO(id=uuid.uuid4(), name="s7", profit_cap=5000, is_active=True,
                        sort_order=1, min_confidence_for_execution=40.0,
                        strategies=("vwap_reclaim",))
    svc._cache = [default, s7]
    assert min_execution_threshold_for("vwap_reclaim", None, 70.0) == 40.0
    assert min_execution_threshold_for("vwap_pullback", None, 70.0) == 70.0


@pytest.mark.asyncio
async def test_update_profile_rejects_out_of_range_threshold():
    # The 0-100 range check raises before any DB access.
    with pytest.raises(ValueError, match="between 0 and 100"):
        await svc.update_profile(uuid.uuid4(), min_confidence_for_execution=150)


# ── Per-profile intraday-bias strength gate ───────────────────────────────

def test_signal_bias_strength_extraction():
    """Reads the stored stock intraday_bias.strength out of a signal's indicators JSONB."""
    from app.services.yolo_profile_service import signal_bias_strength
    assert signal_bias_strength({"intraday_bias": {"strength": "STRONG"}}) == "STRONG"
    assert signal_bias_strength({"intraday_bias": {"score": 0.5}}) is None  # no strength key
    assert signal_bias_strength({"intraday_bias": None}) is None
    assert signal_bias_strength({}) is None
    assert signal_bias_strength(None) is None


def test_profile_accepts_signal_no_bias_gate_accepts_any():
    """min_bias_strength=None (default) = no gate → any bias passes (backward compatible)."""
    p = _dto("full", 0)
    assert profile_accepts_signal(p, "intraday_futures", "PDH_PDL", "STRONG")
    assert profile_accepts_signal(p, "intraday_futures", "PDH_PDL", "WEAK")
    assert profile_accepts_signal(p, "intraday_futures", "PDH_PDL", None)


def test_profile_accepts_signal_strong_bias_gate():
    """STRONG gate: only a STRONG-bias signal passes; a missing bias is rejected."""
    p = _dto("s5-strong", 10000, strategies=("intraday_futures",),
             setups=("PDH_PDL", "ORB"), min_bias_strength="STRONG")
    assert profile_accepts_signal(p, "intraday_futures", "PDH_PDL", "STRONG")
    assert not profile_accepts_signal(p, "intraday_futures", "PDH_PDL", "MODERATE")
    assert not profile_accepts_signal(p, "intraday_futures", "PDH_PDL", "WEAK")
    assert not profile_accepts_signal(p, "intraday_futures", "PDH_PDL", None)
    # the bias gate is AND-ed with the setup filter
    assert not profile_accepts_signal(p, "intraday_futures", "GAP_CONTINUATION", "STRONG")


def test_profile_accepts_signal_bias_gate_is_ordinal():
    """MODERATE gate accepts MODERATE and STRONG, rejects WEAK and missing."""
    p = _dto("mod", 0, strategies=("intraday_futures",), min_bias_strength="MODERATE")
    assert profile_accepts_signal(p, "intraday_futures", "ORB", "STRONG")
    assert profile_accepts_signal(p, "intraday_futures", "ORB", "MODERATE")
    assert not profile_accepts_signal(p, "intraday_futures", "ORB", "WEAK")
    assert not profile_accepts_signal(p, "intraday_futures", "ORB", None)


def test_min_execution_threshold_for_is_bias_aware():
    """A STRONG-gated profile at conf 40 lowers the executable bar ONLY for STRONG signals;
    a MODERATE-bias signal keeps the global bar (the gated profile won't execute it)."""
    default = _dto("def", 5000, sort_order=0)              # inherits global 70, no bias gate
    s5strong = YoloProfileDTO(
        id=uuid.uuid4(), name="S5-STRONG", profit_cap=10000, is_active=True, sort_order=1,
        min_confidence_for_execution=40.0, strategies=("intraday_futures",),
        setups=("PDH_PDL", "ORB"), min_bias_strength="STRONG",
    )
    svc._cache = [default, s5strong]
    assert min_execution_threshold_for("intraday_futures", "PDH_PDL", 70.0, "STRONG") == 40.0
    assert min_execution_threshold_for("intraday_futures", "PDH_PDL", 70.0, "MODERATE") == 70.0
    # legacy callers that don't pass a bias → the gated profile is excluded → global default
    assert min_execution_threshold_for("intraday_futures", "PDH_PDL", 70.0) == 70.0


@pytest.mark.asyncio
async def test_update_profile_rejects_invalid_bias_strength():
    with pytest.raises(ValueError, match="WEAK, MODERATE, or STRONG"):
        await svc.update_profile(uuid.uuid4(), min_bias_strength="HUGE")
