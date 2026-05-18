"""Automated Fyers OAuth2 login using TOTP-based headless authentication.

Fyers requires daily re-authentication. This module automates the full flow:
1. Request login OTP (send_login_otp)
2. Verify TOTP (generated from TOTP secret, replacing SMS OTP)
3. Verify 4-digit PIN
4. Extract auth code from redirect URL
5. Exchange auth code for access token using SessionModel

Uses Fyers' undocumented login API at api-t2.fyers.in, which is the same
flow the web app uses internally.
"""

import asyncio
import base64
import hashlib
import logging
import time
from urllib.parse import parse_qs, urlparse

import httpx
import pyotp

from app.config import settings
from app.core.redis import get_redis

logger = logging.getLogger(__name__)

# Fyers login API base (separate from the data API)
LOGIN_API = "https://api-t2.fyers.in/vagator/v2"
TOKEN_API = "https://api-t1.fyers.in/api/v3/validate-authcode"

# Redis key and TTL (matches auth.py)
FYERS_TOKEN_KEY = "fyers:access_token"
FYERS_TOKEN_TTL_SECONDS = 10 * 60 * 60  # 10 hours

# Reauth guard: prevents stampede when many concurrent calls 401 simultaneously
_reauth_lock: asyncio.Lock | None = None
_last_reauth_at: float | None = None
_REAUTH_COOLDOWN_SECONDS = 60.0


def _get_reauth_lock() -> asyncio.Lock:
    """Lazy-init so the Lock is created in the running event loop."""
    global _reauth_lock
    if _reauth_lock is None:
        _reauth_lock = asyncio.Lock()
    return _reauth_lock


class FyersAutoLoginError(Exception):
    """Raised when automated login fails at any step."""

    pass


def _generate_totp(totp_secret: str) -> str:
    """Generate a time-based OTP from the TOTP secret."""
    totp = pyotp.TOTP(totp_secret)
    return totp.now()


def _compute_app_id_hash(app_id: str, secret_key: str) -> str:
    """Compute SHA-256 hash of app_id:secret_key for token exchange."""
    hash_val = hashlib.sha256(f"{app_id}:{secret_key}".encode())
    return hash_val.hexdigest()



def _auto_login_sync() -> str:
    """Execute the full login flow using sync httpx.

    anyio's async TLS wrapper is broken on some OpenSSL 3.6 + macOS combos
    (BrokenResourceError during TLS handshake) while sync httpx works fine.
    Running this in a thread via asyncio.to_thread avoids the issue.
    """
    with httpx.Client(timeout=30.0) as client:
        # Step 1: Send login OTP
        payload = {"fy_id": base64.b64encode(settings.fyers_username.encode()).decode(), "app_id": "2"}
        response = client.post(f"{LOGIN_API}/send_login_otp_v2", json=payload)
        data = response.json()
        if data.get("s") != "ok" and data.get("code") != 200:
            raise FyersAutoLoginError(f"send_login_otp failed: {data.get('message', data)}")
        request_key = data.get("request_key")
        if not request_key:
            raise FyersAutoLoginError("No request_key in send_login_otp response")

        # Step 2: Generate and verify TOTP.
        # Guard: if < 5s remain in the current 30s window the code may expire
        # in transit — wait for the next fresh window instead.
        window_remaining = 30 - (int(time.time()) % 30)
        if window_remaining < 5:
            time.sleep(window_remaining + 1)
        totp = _generate_totp(settings.fyers_totp_secret)
        payload = {"request_key": request_key, "otp": int(totp)}
        response = client.post(f"{LOGIN_API}/verify_otp", json=payload)
        data = response.json()
        if data.get("s") != "ok" and data.get("code") != 200:
            raise FyersAutoLoginError(f"verify_otp failed: {data.get('message', data)}")
        request_key = data.get("request_key")
        if not request_key:
            raise FyersAutoLoginError("No request_key in verify_otp response")

        # Step 3: Verify PIN
        payload = {
            "request_key": request_key,
            "identity_type": "pin",
            "identifier": base64.b64encode(str(settings.fyers_pin).encode()).decode(),
        }
        response = client.post(f"{LOGIN_API}/verify_pin_v2", json=payload)
        data = response.json()
        if data.get("s") != "ok" and data.get("code") != 200:
            raise FyersAutoLoginError(f"verify_pin failed: {data.get('message', data)}")
        login_token = data.get("data", {}).get("access_token")
        if not login_token:
            raise FyersAutoLoginError("No access_token in verify_pin response")

        # Step 4: Get auth code (or direct access token in newer API response)
        payload = {
            "fyers_id": settings.fyers_username,
            "app_id": settings.fyers_app_id.split("-")[0],
            "redirect_uri": settings.fyers_redirect_uri,
            "appType": "100",
            "code_challenge": "",
            "state": "stocktrading",
            "scope": "",
            "nonce": "",
            "response_type": "code",
            "create_cookie": True,
        }
        headers = {"Authorization": f"Bearer {login_token}"}
        response = client.post("https://api-t1.fyers.in/api/v3/token", json=payload, headers=headers)
        data = response.json()
        if data.get("s") != "ok" and data.get("code") != 200:
            raise FyersAutoLoginError(f"token (auth_code) request failed: {data.get('message', data)}")

        # Fyers v3 API may return the access token directly in data.auth
        direct_token = (data.get("data") or {}).get("auth")
        url_str = data.get("Url")

        if direct_token:
            access_token = direct_token
        elif url_str:
            parsed = urlparse(url_str)
            auth_code_params = parse_qs(parsed.query)
            auth_code = auth_code_params.get("auth_code", [None])[0]
            if not auth_code:
                raise FyersAutoLoginError(f"Could not extract auth_code from URL: {url_str}")

            # Step 5: Exchange auth code for API access token
            app_id_hash = _compute_app_id_hash(settings.fyers_app_id, settings.fyers_secret_key)
            payload = {
                "grant_type": "authorization_code",
                "appIdHash": app_id_hash,
                "code": auth_code,
            }
            response = client.post(TOKEN_API, json=payload)
            data = response.json()
            if data.get("s") != "ok":
                raise FyersAutoLoginError(f"validate-authcode failed: {data.get('message', data)}")
            access_token = data.get("access_token")
            if not access_token:
                raise FyersAutoLoginError("No access_token in validate-authcode response")
        else:
            raise FyersAutoLoginError(f"No Url or auth in token response: {data}")

    logger.info("Successfully obtained Fyers API access token")
    return access_token


async def auto_login() -> str:
    """Execute the full automated login flow and return the access token.

    Raises FyersAutoLoginError if any step fails.
    """
    # Validate required settings
    if not settings.fyers_app_id:
        raise FyersAutoLoginError("FYERS_APP_ID not configured")
    if not settings.fyers_secret_key:
        raise FyersAutoLoginError("FYERS_SECRET_KEY not configured")
    if not settings.fyers_username:
        raise FyersAutoLoginError("FYERS_USERNAME not configured")
    if not settings.fyers_pin:
        raise FyersAutoLoginError("FYERS_PIN not configured")
    if not settings.fyers_totp_secret:
        raise FyersAutoLoginError("FYERS_TOTP_SECRET not configured")

    logger.info("Starting Fyers auto-login for user %s", settings.fyers_username)

    return await asyncio.to_thread(_auto_login_sync)


async def trigger_reauth() -> str:
    """Lock-guarded reauth wrapper — call on 401 or auth-failure WS errors.

    Uses a 60s cooldown and asyncio.Lock so that N concurrent 401s collapse
    into one real TOTP login. Returns the (possibly cached) access token.
    Raises FyersAutoLoginError if the login itself fails.
    """
    global _last_reauth_at

    lock = _get_reauth_lock()
    async with lock:
        now = time.monotonic()
        if _last_reauth_at is not None and (now - _last_reauth_at) < _REAUTH_COOLDOWN_SECONDS:
            # Recent reauth — read the already-stored token from Redis
            r = get_redis()
            token = await r.get(FYERS_TOKEN_KEY)
            if token:
                logger.info("Reauth cooldown active — reusing recently refreshed token")
                return token

        logger.info("Triggering full Fyers reauth (TOTP flow)")
        token = await auto_login_and_store()
        _last_reauth_at = time.monotonic()
        return token


async def auto_login_and_store() -> str:
    """Run auto-login and store the token in Redis.

    Returns the access token on success.
    Raises FyersAutoLoginError on failure.
    """
    access_token = await auto_login()

    # Store in Redis with 10-hour TTL
    r = get_redis()
    await r.set(FYERS_TOKEN_KEY, access_token, ex=FYERS_TOKEN_TTL_SECONDS)
    logger.info(
        "Fyers access token stored in Redis (key=%s, TTL=%ds)",
        FYERS_TOKEN_KEY,
        FYERS_TOKEN_TTL_SECONDS,
    )

    return access_token
