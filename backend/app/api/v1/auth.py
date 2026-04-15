"""Fyers OAuth2 authentication endpoints.

Provides browser-based login flow:
1. GET /api/v1/auth/fyers/login — redirects to Fyers OAuth page
2. GET /api/v1/auth/fyers/callback — receives auth code, exchanges for token
3. GET /api/v1/auth/fyers/status — checks if we have a valid token
"""

import logging

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import RedirectResponse

from app.config import settings
from app.core.redis import get_redis
from app.data_feed.fyers_auth import exchange_auth_code, get_auth_url

logger = logging.getLogger(__name__)

router = APIRouter()

# Redis key for storing the Fyers access token
FYERS_TOKEN_KEY = "fyers:access_token"
# Token is valid for one trading day; expire after 10 hours to be safe
FYERS_TOKEN_TTL_SECONDS = 10 * 60 * 60


@router.get("/fyers/login")
async def fyers_login():
    """Redirect the browser to the Fyers OAuth2 authorization page."""
    if not settings.fyers_app_id:
        raise HTTPException(
            status_code=503,
            detail="Fyers API credentials not configured",
        )

    auth_url = get_auth_url()
    return RedirectResponse(url=auth_url)


@router.get("/fyers/callback")
async def fyers_callback(
    auth_code: str = Query(default=None),
    code: str = Query(default=None),
    s: str = Query(default=None, description="State parameter from Fyers"),
):
    """Handle the OAuth2 callback from Fyers.

    Fyers redirects back with either `auth_code` or `code` query parameter.
    We exchange it for an access token, store in Redis, and redirect to frontend.
    """
    # Fyers may send the code as `auth_code` or `code`
    received_code = auth_code or code
    if not received_code:
        raise HTTPException(
            status_code=400,
            detail="No authorization code received from Fyers",
        )

    # Exchange auth code for access token
    access_token = await exchange_auth_code(received_code)
    if access_token is None:
        raise HTTPException(
            status_code=502,
            detail="Failed to exchange auth code for access token",
        )

    # Store token in Redis with TTL
    r = get_redis()
    await r.set(FYERS_TOKEN_KEY, access_token, ex=FYERS_TOKEN_TTL_SECONDS)
    logger.info("Fyers access token stored in Redis (TTL=%ds)", FYERS_TOKEN_TTL_SECONDS)

    # Redirect to frontend with success indicator
    frontend_url = settings.frontend_url
    return RedirectResponse(url=f"{frontend_url}?fyers_auth=success")


@router.get("/fyers/status")
async def fyers_status():
    """Check whether we have a valid Fyers access token."""
    r = get_redis()
    token = await r.get(FYERS_TOKEN_KEY)

    if token:
        ttl = await r.ttl(FYERS_TOKEN_KEY)
        return {
            "authenticated": True,
            "ttl_seconds": ttl if ttl > 0 else None,
        }

    return {
        "authenticated": False,
        "ttl_seconds": None,
    }
