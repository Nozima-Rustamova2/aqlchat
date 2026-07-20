"""OAuth connect flow: webhook_slug-keyed start (the slug is the
capability check until real dashboard auth exists), single-use Redis
state, and credential storage on callback. exchange_code is stubbed -
the real Meta exchange is exercised manually with our own IG account
(Standard Access) before App Review.
"""

import datetime as dt
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

import app.instagram.router as instagram_router_module
from app.config import settings
from app.instagram.client import InstagramClient
from app.instagram.oauth import InstagramCredentials
from app.redis_client import get_redis

CONNECTED_IG_USER_ID = 17841400000000003


@pytest.fixture
def oauth_settings(monkeypatch):
    monkeypatch.setattr(settings, "instagram_app_id", "test-app-id")
    monkeypatch.setattr(settings, "public_base_url", "https://aqlchat.example")


@pytest.fixture
def stub_exchange(monkeypatch):
    def _exchange(code: str) -> InstagramCredentials:
        assert code == "auth-code-42"
        return InstagramCredentials(
            user_id=CONNECTED_IG_USER_ID,
            access_token="long-lived-token",
            expires_at=dt.datetime(2026, 9, 14, 12, 0, 0),
        )

    monkeypatch.setattr(instagram_router_module, "exchange_code", _exchange)


def test_connect_redirects_to_authorize_url_with_state(client, oauth_settings, routed_merchant):
    response = client.get(f"/instagram/connect/{routed_merchant.webhook_slug}", follow_redirects=False)
    assert response.status_code == 307

    parsed = urlparse(response.headers["location"])
    assert parsed.netloc == "www.instagram.com"
    query = parse_qs(parsed.query)
    assert query["client_id"] == ["test-app-id"]
    assert query["redirect_uri"] == ["https://aqlchat.example/instagram/oauth/callback"]

    state = query["state"][0]
    try:
        assert get_redis().get(f"ig_oauth_state:{state}") == str(routed_merchant.id)
    finally:
        get_redis().delete(f"ig_oauth_state:{state}")


def test_connect_unknown_slug_is_404(client, oauth_settings, routed_session):
    response = client.get("/instagram/connect/not-a-real-slug", follow_redirects=False)
    assert response.status_code == 404


def test_callback_stores_credentials(client, oauth_settings, stub_exchange, routed_session, routed_merchant):
    get_redis().set("ig_oauth_state:state-abc", str(routed_merchant.id), ex=60)

    response = client.get("/instagram/oauth/callback", params={"code": "auth-code-42", "state": "state-abc"})

    assert response.status_code == 200
    assert routed_merchant.instagram_user_id == CONNECTED_IG_USER_ID
    assert routed_merchant.instagram_access_token == "long-lived-token"
    assert routed_merchant.instagram_token_expires_at == dt.datetime(2026, 9, 14, 12, 0, 0)
    assert get_redis().get("ig_oauth_state:state-abc") is None  # single-use


def test_callback_rejects_unknown_state(client, oauth_settings, stub_exchange, routed_session):
    response = client.get("/instagram/oauth/callback", params={"code": "auth-code-42", "state": "never-issued"})
    assert response.status_code == 400


def test_callback_surfaces_subscribe_failure_but_keeps_the_connection(
    client, oauth_settings, stub_exchange, routed_session, routed_merchant, monkeypatch
):
    # The token exchange succeeding and the account being subscribed to
    # webhooks are two separate Graph calls (app/instagram/client.py's
    # subscribe_webhooks) - a failure in the second must not undo the
    # first, since the connection is still useful for the media picker
    # and manual sends even without live comment events.
    def _fail_subscribe(self, fields="comments"):
        raise httpx.HTTPStatusError("bad token", request=None, response=None)

    monkeypatch.setattr(InstagramClient, "subscribe_webhooks", _fail_subscribe)
    get_redis().set("ig_oauth_state:state-sub-fail", str(routed_merchant.id), ex=60)

    response = client.get("/instagram/oauth/callback", params={"code": "auth-code-42", "state": "state-sub-fail"})

    assert response.status_code == 200
    assert "webhook subscription failed" in response.text
    assert routed_merchant.instagram_user_id == CONNECTED_IG_USER_ID  # connection itself still saved


def test_callback_rejects_ig_account_already_connected_elsewhere(
    client, oauth_settings, stub_exchange, routed_session, routed_merchant
):
    from app.db.models import Merchant

    other = Merchant(
        name="already-connected",
        telegram_bot_token="other-token-ig-oauth",
        webhook_secret="other-secret",
        instagram_user_id=CONNECTED_IG_USER_ID,
    )
    routed_session.add(other)
    routed_session.flush()

    get_redis().set("ig_oauth_state:state-dup", str(routed_merchant.id), ex=60)
    response = client.get("/instagram/oauth/callback", params={"code": "auth-code-42", "state": "state-dup"})

    assert response.status_code == 409
    assert routed_merchant.instagram_user_id is None
