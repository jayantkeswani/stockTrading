"""Tests for yolo_profile_service default-profile helpers.

Covers get_default_profile() and default_profile_trade_filter(), which scope
reports + per-trade notifications to a single profile so multi-profile fan-out
doesn't multiply output.
"""

import uuid

import pytest

import app.services.yolo_profile_service as svc
from app.services.yolo_profile_service import YoloProfileDTO


def _dto(name: str, cap: float, active: bool = True, sort_order: int = 0) -> YoloProfileDTO:
    return YoloProfileDTO(
        id=uuid.uuid4(),
        name=name,
        profit_cap=cap,
        is_active=active,
        sort_order=sort_order,
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
