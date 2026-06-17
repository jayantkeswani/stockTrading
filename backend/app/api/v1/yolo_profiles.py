"""YOLO profiles API — CRUD for profit cap tiers."""

import uuid

from fastapi import APIRouter, HTTPException

from app.core.utils import now_ist
from app.schemas.yolo_profile import YoloProfileCreate, YoloProfileResponse, YoloProfileUpdate
from app.services import yolo_profile_service as svc

router = APIRouter()


@router.get("", response_model=list[YoloProfileResponse])
async def list_profiles():
    """Return all YOLO profiles with today's capped status."""
    profiles = await svc.get_all_profiles()
    today = now_ist().date()
    uncapped_ids = await svc.get_uncapped_profile_ids(today)
    return [
        YoloProfileResponse(
            id=p.id,
            name=p.name,
            profit_cap=p.profit_cap,
            is_active=p.is_active,
            sort_order=p.sort_order,
            is_capped_today=p.id not in uncapped_ids and p.is_active,
            invalidation_persist=p.invalidation_persist,
            invalidation_quorum=p.invalidation_quorum,
            invalidation_strong_only=p.invalidation_strong_only,
            min_confidence_for_execution=p.min_confidence_for_execution,
            min_bias_strength=p.min_bias_strength,
            min_adr=p.min_adr,
            loss_cap=p.loss_cap,
            per_lot_loss_stop=p.per_lot_loss_stop,
            strategies=list(p.strategies),
            setups=list(p.setups),
        )
        for p in profiles
    ]


@router.post("", response_model=YoloProfileResponse, status_code=201)
async def create_profile(body: YoloProfileCreate):
    """Create a new YOLO profile."""
    profile = await svc.create_profile(
        name=body.name, profit_cap=body.profit_cap,
        strategies=body.strategies, setups=body.setups,
        min_confidence_for_execution=body.min_confidence_for_execution,
        min_bias_strength=body.min_bias_strength,
        min_adr=body.min_adr, loss_cap=body.loss_cap,
        per_lot_loss_stop=body.per_lot_loss_stop,
    )
    return YoloProfileResponse(
        id=profile.id,
        name=profile.name,
        profit_cap=profile.profit_cap,
        is_active=profile.is_active,
        sort_order=profile.sort_order,
        is_capped_today=False,
        invalidation_persist=profile.invalidation_persist,
        invalidation_quorum=profile.invalidation_quorum,
        invalidation_strong_only=profile.invalidation_strong_only,
        min_confidence_for_execution=profile.min_confidence_for_execution,
        min_bias_strength=profile.min_bias_strength,
        min_adr=profile.min_adr,
        loss_cap=profile.loss_cap,
        per_lot_loss_stop=profile.per_lot_loss_stop,
        strategies=list(profile.strategies),
        setups=list(profile.setups),
    )


@router.patch("/{profile_id}", response_model=YoloProfileResponse)
async def update_profile(profile_id: uuid.UUID, body: YoloProfileUpdate):
    """Partially update a YOLO profile."""
    fields = body.model_dump(exclude_none=True)
    if not fields:
        raise HTTPException(status_code=400, detail="No fields to update")
    try:
        profile = await svc.update_profile(profile_id, **fields)
    except ValueError as e:
        code = 400 if "default" in str(e) else 404
        raise HTTPException(status_code=code, detail=str(e))
    today = now_ist().date()
    uncapped_ids = await svc.get_uncapped_profile_ids(today)
    return YoloProfileResponse(
        id=profile.id,
        name=profile.name,
        profit_cap=profile.profit_cap,
        is_active=profile.is_active,
        sort_order=profile.sort_order,
        is_capped_today=profile.id not in uncapped_ids and profile.is_active,
        invalidation_persist=profile.invalidation_persist,
        invalidation_quorum=profile.invalidation_quorum,
        invalidation_strong_only=profile.invalidation_strong_only,
        min_confidence_for_execution=profile.min_confidence_for_execution,
        min_bias_strength=profile.min_bias_strength,
        min_adr=profile.min_adr,
        loss_cap=profile.loss_cap,
        per_lot_loss_stop=profile.per_lot_loss_stop,
        strategies=list(profile.strategies),
        setups=list(profile.setups),
    )


@router.delete("/{profile_id}", status_code=204)
async def delete_profile(profile_id: uuid.UUID):
    """Delete a YOLO profile. The default (first) profile cannot be deleted."""
    try:
        await svc.delete_profile(profile_id)
    except ValueError as e:
        code = 400 if "default" in str(e) else 404
        raise HTTPException(status_code=code, detail=str(e))
