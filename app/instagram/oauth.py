"""Instagram Login OAuth for connecting a merchant's IG account -
authorize URL construction, code -> long-lived token exchange, and the
periodic refresh (scripts/refresh_instagram_tokens.py).

Instagram API with Instagram Login (no Facebook Page in the loop):
short-lived token from api.instagram.com, immediately exchanged for a
~60-day long-lived token on graph.instagram.com. Long-lived tokens are
refreshable any time they're older than 24h and not yet expired - a
token allowed to expire forces the merchant through the connect flow
again, which is why the refresh cron exists.
"""

import datetime as dt
from dataclasses import dataclass
from urllib.parse import urlencode

import httpx

from app.config import settings

AUTHORIZE_URL = "https://www.instagram.com/oauth/authorize"
SHORT_LIVED_TOKEN_URL = "https://api.instagram.com/oauth/access_token"

# Everything comment-to-DM automation needs, nothing more (App Review
# scrutinizes each scope).
SCOPES = "instagram_business_basic,instagram_business_manage_comments,instagram_business_manage_messages"


@dataclass
class InstagramCredentials:
    user_id: int
    access_token: str
    expires_at: dt.datetime  # naive UTC, like every timestamp we store


def redirect_uri() -> str:
    return f"{settings.public_base_url}/instagram/oauth/callback"


def build_authorize_url(state: str) -> str:
    params = urlencode(
        {
            "client_id": settings.instagram_app_id,
            "redirect_uri": redirect_uri(),
            "scope": SCOPES,
            "response_type": "code",
            "state": state,
        }
    )
    return f"{AUTHORIZE_URL}?{params}"


def exchange_code(code: str) -> InstagramCredentials:
    """Authorization code -> short-lived token -> long-lived token -> the
    account's webhook-routing id.

    The short-lived token response's own "user_id" field is NOT the id
    Meta stamps into entry[].id on every subsequent webhook delivery for
    this account - it's an app-scoped id, a different numeric namespace
    for the same account. (Confirmed against Meta's Instagram Platform
    docs: graph.instagram.com/me's "id" field is documented as "the app
    user's app-scoped ID", while its "user_id" field - "the Instagram
    professional account ID" - is the one that matches entry.id on
    webhooks. Real-world symptom this fixes: a merchant connected fine,
    but every webhook for their account logged "unknown ig user id" and
    was silently dropped, because the wrong-namespace id had been stored
    as merchants.instagram_user_id.) So an explicit /me call, using the
    fresh token, replaces trusting the token-exchange response's
    same-named-but-different field.
    """
    short_response = httpx.post(
        SHORT_LIVED_TOKEN_URL,
        data={
            "client_id": settings.instagram_app_id,
            "client_secret": settings.instagram_app_secret,
            "grant_type": "authorization_code",
            "redirect_uri": redirect_uri(),
            "code": code,
        },
        timeout=15,
    )
    short_response.raise_for_status()
    short = short_response.json()

    long_response = httpx.get(
        f"https://graph.instagram.com/access_token",
        params={
            "grant_type": "ig_exchange_token",
            "client_secret": settings.instagram_app_secret,
            "access_token": short["access_token"],
        },
        timeout=15,
    )
    long_response.raise_for_status()
    long = long_response.json()

    webhook_user_id = _fetch_webhook_user_id(long["access_token"])

    return InstagramCredentials(
        user_id=webhook_user_id,
        access_token=long["access_token"],
        expires_at=_expires_at(long["expires_in"]),
    )


def _fetch_webhook_user_id(access_token: str) -> int:
    """The id that will show up as entry[].id on this account's webhook
    deliveries - see exchange_code's docstring for why this can't just be
    read off the token-exchange response."""
    response = httpx.get(
        f"https://graph.instagram.com/{settings.instagram_graph_api_version}/me",
        params={"fields": "user_id", "access_token": access_token},
        timeout=15,
    )
    response.raise_for_status()
    return int(response.json()["user_id"])


def refresh_long_lived_token(access_token: str) -> tuple[str, dt.datetime]:
    response = httpx.get(
        "https://graph.instagram.com/refresh_access_token",
        params={"grant_type": "ig_refresh_token", "access_token": access_token},
        timeout=15,
    )
    response.raise_for_status()
    payload = response.json()
    return payload["access_token"], _expires_at(payload["expires_in"])


def _expires_at(expires_in_seconds: int) -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc).replace(tzinfo=None) + dt.timedelta(seconds=int(expires_in_seconds))
