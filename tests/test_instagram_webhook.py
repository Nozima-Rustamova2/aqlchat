"""Instagram webhook: Meta's GET verification handshake, HMAC signature
enforcement over raw bytes, entry[].id -> merchant routing (THE tenant
boundary on this channel - mirrors the webhook_slug isolation tests), the
(merchant_id, external_id) dedupe insert, and post-commit enqueueing.
"""

import hashlib
import hmac
import json

import pytest
from sqlalchemy import select

import app.instagram.router as instagram_router_module
from app.config import settings
from app.db.models import CommentEvent

TEST_APP_SECRET = "test-ig-app-secret"
TEST_VERIFY_TOKEN = "test-ig-verify-token"
IG_USER_ID = 17841400000000001


class FakeRedis:
    def __init__(self):
        self.pushed = []

    def lpush(self, key, value):
        self.pushed.append((key, value))


@pytest.fixture
def ig_settings(monkeypatch):
    monkeypatch.setattr(settings, "instagram_app_secret", TEST_APP_SECRET)
    monkeypatch.setattr(settings, "instagram_verify_token", TEST_VERIFY_TOKEN)


@pytest.fixture
def fake_redis(monkeypatch):
    fake = FakeRedis()
    monkeypatch.setattr(instagram_router_module, "get_redis", lambda: fake)
    return fake


@pytest.fixture
def ig_merchant(routed_session, routed_merchant):
    routed_merchant.instagram_user_id = IG_USER_ID
    routed_session.flush()
    return routed_merchant


def _sign(body: bytes) -> str:
    return "sha256=" + hmac.new(TEST_APP_SECRET.encode(), body, hashlib.sha256).hexdigest()


def _comment_payload(
    entry_id: int = IG_USER_ID,
    comment_id: str = "comment-1",
    commenter_id: str = "9001",
    media_id: str = "18001",
    text: str = "narxi qancha?",
) -> dict:
    return {
        "object": "instagram",
        "entry": [
            {
                "id": str(entry_id),
                "time": 1752600000,
                "changes": [
                    {
                        "field": "comments",
                        "value": {
                            "id": comment_id,
                            "from": {"id": commenter_id, "username": "customer"},
                            "media": {"id": media_id, "media_product_type": "REELS"},
                            "text": text,
                        },
                    }
                ],
            }
        ],
    }


def _story_reply_payload(
    entry_id: int = IG_USER_ID,
    message_id: str = "message-1",
    sender_id: str = "9001",
    story_id: str = "story-18001",
    text: str = "narxi qancha?",
) -> dict:
    return {
        "object": "instagram",
        "entry": [
            {
                "id": str(entry_id),
                "time": 1752600000,
                "changes": [
                    {
                        "field": "messages",
                        "value": {
                            "sender": {"id": sender_id},
                            "recipient": {"id": str(entry_id)},
                            "timestamp": 1752600000,
                            "message": {
                                "mid": message_id,
                                "text": text,
                                "reply_to": {"story": {"id": story_id, "url": "https://example.com/story"}},
                            },
                        },
                    }
                ],
            }
        ],
    }


def _plain_dm_payload(entry_id: int = IG_USER_ID, sender_id: str = "9001", text: str = "salom") -> dict:
    payload = _story_reply_payload(entry_id=entry_id, sender_id=sender_id, text=text)
    del payload["entry"][0]["changes"][0]["value"]["message"]["reply_to"]
    return payload


def _post(client, payload: dict, signature: str | None = None):
    body = json.dumps(payload).encode()
    headers = {"X-Hub-Signature-256": signature if signature is not None else _sign(body)}
    return client.post("/instagram/webhook", content=body, headers=headers)


def test_get_verification_echoes_challenge(client, ig_settings):
    response = client.get(
        "/instagram/webhook",
        params={"hub.mode": "subscribe", "hub.verify_token": TEST_VERIFY_TOKEN, "hub.challenge": "challenge-42"},
    )
    assert response.status_code == 200
    assert response.text == "challenge-42"


def test_get_verification_rejects_wrong_token(client, ig_settings):
    response = client.get(
        "/instagram/webhook",
        params={"hub.mode": "subscribe", "hub.verify_token": "wrong", "hub.challenge": "challenge-42"},
    )
    assert response.status_code == 403


def test_post_rejects_invalid_signature(client, ig_settings, fake_redis, ig_merchant, routed_session):
    response = _post(client, _comment_payload(), signature="sha256=" + "0" * 64)
    assert response.status_code == 403
    assert routed_session.scalars(select(CommentEvent)).all() == []
    assert fake_redis.pushed == []


def test_post_rejects_missing_signature_even_with_blank_secret(client, fake_redis, monkeypatch):
    # An unconfigured deploy (blank app secret) must reject everything,
    # not fall open to accepting unsigned traffic.
    monkeypatch.setattr(settings, "instagram_app_secret", "")
    response = client.post("/instagram/webhook", content=b"{}")
    assert response.status_code == 403


def test_valid_comment_creates_pending_event_and_enqueues(client, ig_settings, fake_redis, ig_merchant, routed_session):
    response = _post(client, _comment_payload())
    assert response.status_code == 200

    event = routed_session.scalars(select(CommentEvent)).one()
    assert event.merchant_id == ig_merchant.id
    assert event.channel == "instagram_comment"
    assert event.external_id == "comment-1"
    assert event.commenter_id == "9001"
    assert event.media_id == "18001"
    assert event.raw_text == "narxi qancha?"
    assert event.status == "pending"

    assert fake_redis.pushed == [("ig:comments", str(event.id))]


def test_duplicate_delivery_is_deduped(client, ig_settings, fake_redis, ig_merchant, routed_session):
    _post(client, _comment_payload())
    response = _post(client, _comment_payload())  # Meta redelivers the same comment id
    assert response.status_code == 200

    events = routed_session.scalars(select(CommentEvent)).all()
    assert len(events) == 1
    assert len(fake_redis.pushed) == 1


def test_unknown_instagram_user_id_is_dropped_with_200(client, ig_settings, fake_redis, routed_session):
    response = _post(client, _comment_payload(entry_id=999999999))
    assert response.status_code == 200
    assert routed_session.scalars(select(CommentEvent)).all() == []
    assert fake_redis.pushed == []


def test_own_reply_loop_guard_drops_at_ingress(client, ig_settings, fake_redis, ig_merchant, routed_session):
    # Our own public reply fires this same webhook, with from.id equal to
    # the merchant's own IG user id - it must never enter the queue.
    response = _post(client, _comment_payload(commenter_id=str(IG_USER_ID)))
    assert response.status_code == 200
    assert routed_session.scalars(select(CommentEvent)).all() == []
    assert fake_redis.pushed == []


def test_non_comment_changes_are_ignored(client, ig_settings, fake_redis, ig_merchant, routed_session):
    payload = _comment_payload()
    payload["entry"][0]["changes"][0]["field"] = "mentions"
    response = _post(client, payload)
    assert response.status_code == 200
    assert routed_session.scalars(select(CommentEvent)).all() == []
    assert fake_redis.pushed == []


def test_valid_story_reply_creates_pending_event_and_enqueues(client, ig_settings, fake_redis, ig_merchant, routed_session):
    response = _post(client, _story_reply_payload())
    assert response.status_code == 200

    event = routed_session.scalars(select(CommentEvent)).one()
    assert event.merchant_id == ig_merchant.id
    assert event.channel == "instagram_story_reply"
    assert event.external_id == "message-1"
    assert event.commenter_id == "9001"
    assert event.media_id == "story-18001"
    assert event.raw_text == "narxi qancha?"
    assert event.status == "pending"

    assert fake_redis.pushed == [("ig:comments", str(event.id))]


def test_plain_dm_without_story_reply_is_dropped(client, ig_settings, fake_redis, ig_merchant, routed_session):
    # Conversation-state DM automation isn't built yet - only story
    # replies (message.reply_to.story present) are handled today.
    response = _post(client, _plain_dm_payload())
    assert response.status_code == 200
    assert routed_session.scalars(select(CommentEvent)).all() == []
    assert fake_redis.pushed == []


def test_story_reply_duplicate_delivery_is_deduped(client, ig_settings, fake_redis, ig_merchant, routed_session):
    _post(client, _story_reply_payload())
    response = _post(client, _story_reply_payload())
    assert response.status_code == 200

    events = routed_session.scalars(select(CommentEvent)).all()
    assert len(events) == 1
    assert len(fake_redis.pushed) == 1


def test_story_reply_own_message_loop_guard_drops_at_ingress(client, ig_settings, fake_redis, ig_merchant, routed_session):
    response = _post(client, _story_reply_payload(sender_id=str(IG_USER_ID)))
    assert response.status_code == 200
    assert routed_session.scalars(select(CommentEvent)).all() == []
    assert fake_redis.pushed == []
