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

import app.instagram.oauth as instagram_oauth_module
import app.instagram.router as instagram_router_module
from app.config import settings
from app.instagram.client import InstagramClient
from app.instagram.oauth import InstagramCredentials, exchange_code
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


# --- exchange_code's own id-namespace bug -----------------------------
#
# Regression coverage for a live bug: a merchant connected successfully,
# but every webhook for their account logged "unknown ig user id ...
# dropped" and comment-to-DM never fired. Root cause was here, not in
# webhook routing - api.instagram.com/oauth/access_token's "user_id"
# field is an app-scoped id, a different numeric namespace than the id
# Meta stamps into entry[].id on webhook deliveries (confirmed against
# Meta's Instagram Platform docs: graph.instagram.com/me's "id" field is
# documented as "the app user's app-scoped ID", while its "user_id"
# field - "the Instagram professional account ID" - is what shows up as
# entry.id). exchange_code must use the latter.


class _FakeHttpResponse:
    def __init__(self, payload: dict):
        self._payload = payload

    def raise_for_status(self) -> None:
        pass

    def json(self) -> dict:
        return self._payload


TOKEN_EXCHANGE_APP_SCOPED_ID = "999900000000001"  # wrong namespace - must NOT end up stored
WEBHOOK_ROUTING_ID = "17841457205391437"  # right namespace - must end up stored


@pytest.fixture
def stub_graph_calls(monkeypatch):
    calls = []

    def fake_post(url, data=None, timeout=None):
        calls.append(("post", url, data))
        assert url == instagram_oauth_module.SHORT_LIVED_TOKEN_URL
        return _FakeHttpResponse({"access_token": "short-lived-token", "user_id": TOKEN_EXCHANGE_APP_SCOPED_ID})

    def fake_get(url, params=None, timeout=None):
        calls.append(("get", url, params))
        if url == "https://graph.instagram.com/access_token":
            assert params["access_token"] == "short-lived-token"
            return _FakeHttpResponse({"access_token": "long-lived-token", "expires_in": 5183944})
        if url.endswith("/me"):
            assert params["access_token"] == "long-lived-token"  # fetched with the fresh token
            return _FakeHttpResponse({"user_id": WEBHOOK_ROUTING_ID})
        raise AssertionError(f"unexpected GET {url}")

    monkeypatch.setattr(instagram_oauth_module.httpx, "post", fake_post)
    monkeypatch.setattr(instagram_oauth_module.httpx, "get", fake_get)
    return calls


def test_exchange_code_uses_me_endpoint_id_not_token_response_user_id(monkeypatch, stub_graph_calls):
    monkeypatch.setattr(settings, "instagram_app_id", "test-app-id")
    monkeypatch.setattr(settings, "instagram_app_secret", "test-app-secret")
    monkeypatch.setattr(settings, "instagram_graph_api_version", "v23.0")
    monkeypatch.setattr(settings, "public_base_url", "https://aqlchat.example")

    credentials = exchange_code("auth-code-42")

    assert credentials.user_id == int(WEBHOOK_ROUTING_ID)
    assert credentials.user_id != int(TOKEN_EXCHANGE_APP_SCOPED_ID)
    assert credentials.access_token == "long-lived-token"

    me_calls = [c for c in stub_graph_calls if c[1].endswith("/me")]
    assert len(me_calls) == 1
    assert me_calls[0][2] == {"fields": "user_id", "access_token": "long-lived-token"}
