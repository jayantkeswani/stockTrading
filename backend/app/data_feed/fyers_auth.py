"""Fyers OAuth2 authentication flow.

Uses the fyers_apiv3 SDK's SessionModel for correct token exchange.
"""

import logging

from fyers_apiv3.fyersModel import SessionModel

from app.config import settings

logger = logging.getLogger(__name__)


def get_auth_url() -> str:
    """Generate the OAuth2 authorization URL for Fyers login."""
    session = SessionModel(
        client_id=settings.fyers_app_id,
        redirect_uri=settings.fyers_redirect_uri,
        response_type="code",
        state="stocktrading",
        secret_key=settings.fyers_secret_key,
        grant_type="authorization_code",
    )
    return session.generate_authcode()


async def exchange_auth_code(auth_code: str) -> str | None:
    """Exchange auth code for access token using the Fyers SDK.

    Returns access_token string or None on failure.
    """
    try:
        session = SessionModel(
            client_id=settings.fyers_app_id,
            redirect_uri=settings.fyers_redirect_uri,
            response_type="code",
            state="stocktrading",
            secret_key=settings.fyers_secret_key,
            grant_type="authorization_code",
        )
        session.set_token(auth_code)
        response = session.generate_token()

        logger.info("Fyers token response status: %s", response.get("s"))

        if response.get("s") == "ok":
            return response.get("access_token")

        logger.error("Fyers auth failed: %s (code: %s)", response.get("message"), response.get("code"))
        return None
    except Exception as e:
        logger.error("Failed to exchange auth code: %s", e)
        return None
