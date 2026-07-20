"""Worker-side comment processing (app/instagram/service.py) with a
stubbed Graph client - the two-tier DM/public-reply rule, staleness,
budget deferral, and idempotency. Uses routed_session (savepoint
containment) because the service commits for real, like the FastAPI
routes do.
"""

import datetime as dt

import pytest

from app.db.models import CommentEvent
from app.instagram.budget import HOURLY_CALL_CAP, _current_hour
from app.instagram.queue import DEFERRED_SET_KEY
from app.instagram.service import process_comment_event
from app.redis_client import get_redis
from tests.conftest import make_flow

IG_RESPONSE_CONFIG = {
    "link": "https://t.me/merchant_bot",
    "private_reply": {"uz": "Mana havola 👉 {link}", "ru": "Вот ссылка 👉 {link}"},
    "public_reply": {"uz": "DM'ga yubordim! 📩", "ru": "Отправили в личку! 📩"},
    "media_ids": [],
}


class StubClient:
    def __init__(self, fail_private: bool = False, fail_public: bool = False):
        self.fail_private = fail_private
        self.fail_public = fail_public
        self.private_calls: list[tuple[str, str]] = []
        self.public_calls: list[tuple[str, str]] = []
        self.message_calls: list[tuple[str, str]] = []

    def send_private_reply(self, comment_id: str, text: str) -> dict:
        if self.fail_private:
            raise RuntimeError("graph api down")
        self.private_calls.append((comment_id, text))
        return {}

    def reply_to_comment(self, comment_id: str, text: str) -> dict:
        if self.fail_public:
            raise RuntimeError("graph api down")
        self.public_calls.append((comment_id, text))
        return {}

    def send_message(self, recipient_id: str, text: str) -> dict:
        if self.fail_private:
            raise RuntimeError("graph api down")
        self.message_calls.append((recipient_id, text))
        return {}


@pytest.fixture
def ig_merchant(routed_session, routed_merchant):
    routed_merchant.instagram_user_id = 17841400000000002
    routed_merchant.instagram_access_token = "test-ig-token"
    routed_session.flush()
    return routed_merchant


def _make_event(db, merchant_id, text: str, comment_id: str = "c-1", created_at: dt.datetime | None = None):
    event = CommentEvent(
        merchant_id=merchant_id,
        channel="instagram_comment",
        external_id=comment_id,
        commenter_id="9001",
        media_id="18001",
        raw_text=text,
        status="pending",
    )
    if created_at is not None:
        event.created_at = created_at
    db.add(event)
    db.flush()
    return event


def _make_price_flow(db, merchant_id):
    return make_flow(
        db,
        merchant_id,
        "price_link",
        ["narx", "narxi", "цена"],
        channel="instagram_comment",
        response_config=IG_RESPONSE_CONFIG,
    )


STORY_RESPONSE_CONFIG = {
    "link": "https://t.me/merchant_bot",
    "private_reply": {"uz": "Mana havola 👉 {link}", "ru": "Вот ссылка 👉 {link}"},
    "media_ids": [],
}


def _make_story_event(db, merchant_id, text: str, message_id: str = "m-1", created_at: dt.datetime | None = None):
    event = CommentEvent(
        merchant_id=merchant_id,
        channel="instagram_story_reply",
        external_id=message_id,
        commenter_id="9001",
        media_id="story-18001",
        raw_text=text,
        status="pending",
    )
    if created_at is not None:
        event.created_at = created_at
    db.add(event)
    db.flush()
    return event


def _make_story_flow(db, merchant_id):
    return make_flow(
        db,
        merchant_id,
        "story_reply_link",
        ["narx", "narxi", "цена"],
        channel="instagram_story_reply",
        response_config=STORY_RESPONSE_CONFIG,
    )


def test_exact_keyword_comment_gets_dm_and_public_reply(routed_session, ig_merchant):
    flow = _make_price_flow(routed_session, ig_merchant.id)
    event = _make_event(routed_session, ig_merchant.id, "Narxi?")
    client = StubClient()

    status = process_comment_event(routed_session, get_redis(), event.id, client=client)

    assert status == "replied"
    assert client.private_calls == [("c-1", "Mana havola 👉 https://t.me/merchant_bot")]
    assert client.public_calls == [("c-1", "DM'ga yubordim! 📩")]
    assert event.status == "replied"
    assert event.matched_flow_id == flow.id
    assert event.replied_at is not None
    assert event.detected_language is not None


def test_substring_match_gets_quiet_dm_without_public_reply(routed_session, ig_merchant):
    # "narxlar juda qimmat ekan!" contains "narx" but is not just the
    # keyword - the customer gets the link privately, and no cheery bot
    # reply appears under what might be a complaint.
    _make_price_flow(routed_session, ig_merchant.id)
    event = _make_event(routed_session, ig_merchant.id, "narxlar juda qimmat ekan!")
    client = StubClient()

    status = process_comment_event(routed_session, get_redis(), event.id, client=client)

    assert status == "replied"
    assert len(client.private_calls) == 1
    assert client.public_calls == []


def test_unmatched_comment_is_silent(routed_session, ig_merchant):
    _make_price_flow(routed_session, ig_merchant.id)
    event = _make_event(routed_session, ig_merchant.id, "qanday chiroyli!")
    client = StubClient()

    status = process_comment_event(routed_session, get_redis(), event.id, client=client)

    assert status == "no_match"
    assert client.private_calls == []
    assert client.public_calls == []


def test_stale_comment_is_dropped_without_api_calls(routed_session, ig_merchant):
    _make_price_flow(routed_session, ig_merchant.id)
    eight_days_ago = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None) - dt.timedelta(days=8)
    event = _make_event(routed_session, ig_merchant.id, "narx", created_at=eight_days_ago)
    client = StubClient()

    status = process_comment_event(routed_session, get_redis(), event.id, client=client)

    assert status == "dropped_stale"
    assert client.private_calls == []


def test_budget_exhaustion_defers_instead_of_dropping(routed_session, ig_merchant):
    _make_price_flow(routed_session, ig_merchant.id)
    event = _make_event(routed_session, ig_merchant.id, "narx")
    client = StubClient()

    redis_client = get_redis()
    budget_key = f"ig_budget:{ig_merchant.id}:{_current_hour()}"
    redis_client.set(budget_key, HOURLY_CALL_CAP)
    try:
        status = process_comment_event(routed_session, redis_client, event.id, client=client)

        assert status == "deferred"
        assert client.private_calls == []
        assert redis_client.zscore(DEFERRED_SET_KEY, str(event.id)) is not None
    finally:
        redis_client.delete(budget_key)
        redis_client.zrem(DEFERRED_SET_KEY, str(event.id))


def test_already_replied_event_is_not_reprocessed(routed_session, ig_merchant):
    _make_price_flow(routed_session, ig_merchant.id)
    event = _make_event(routed_session, ig_merchant.id, "narx")
    event.status = "replied"
    routed_session.flush()
    client = StubClient()

    status = process_comment_event(routed_session, get_redis(), event.id, client=client)

    assert status == "replied"
    assert client.private_calls == []


def test_failed_dm_marks_event_failed(routed_session, ig_merchant):
    _make_price_flow(routed_session, ig_merchant.id)
    event = _make_event(routed_session, ig_merchant.id, "narx")
    client = StubClient(fail_private=True)

    status = process_comment_event(routed_session, get_redis(), event.id, client=client)

    assert status == "failed"
    assert event.status == "failed"


def test_failed_public_reply_still_counts_as_replied(routed_session, ig_merchant):
    # The DM is the deliverable; the public reply is decoration.
    _make_price_flow(routed_session, ig_merchant.id)
    event = _make_event(routed_session, ig_merchant.id, "narx")
    client = StubClient(fail_public=True)

    status = process_comment_event(routed_session, get_redis(), event.id, client=client)

    assert status == "replied"
    assert len(client.private_calls) == 1


def test_media_scoped_flow_ignores_other_posts(routed_session, ig_merchant):
    make_flow(
        routed_session,
        ig_merchant.id,
        "reel_promo",
        ["narx"],
        channel="instagram_comment",
        response_config={**IG_RESPONSE_CONFIG, "media_ids": ["some-other-media"]},
    )
    event = _make_event(routed_session, ig_merchant.id, "narx")  # media_id="18001"
    client = StubClient()

    status = process_comment_event(routed_session, get_redis(), event.id, client=client)

    assert status == "no_match"
    assert client.private_calls == []


def test_story_reply_gets_dm_via_send_message_not_private_reply(routed_session, ig_merchant):
    flow = _make_story_flow(routed_session, ig_merchant.id)
    event = _make_story_event(routed_session, ig_merchant.id, "narxi qancha?")
    client = StubClient()

    status = process_comment_event(routed_session, get_redis(), event.id, client=client)

    assert status == "replied"
    assert client.message_calls == [("9001", "Mana havola 👉 https://t.me/merchant_bot")]
    assert client.private_calls == []
    assert client.public_calls == []
    assert event.matched_flow_id == flow.id


def test_story_reply_never_attempts_public_reply(routed_session, ig_merchant):
    # Even if a story flow's config somehow carried a public_reply, the
    # channel gate must still suppress it - there's no comment to post
    # a public reply under.
    _make_story_flow(routed_session, ig_merchant.id)
    event = _make_story_event(routed_session, ig_merchant.id, "narx")
    event.matched_flow_id = None  # not asserted here; just confirming no public call path
    client = StubClient()

    process_comment_event(routed_session, get_redis(), event.id, client=client)

    assert client.public_calls == []


def test_story_reply_uses_24h_window_not_7_day(routed_session, ig_merchant):
    _make_story_flow(routed_session, ig_merchant.id)
    thirty_hours_ago = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None) - dt.timedelta(hours=30)
    event = _make_story_event(routed_session, ig_merchant.id, "narx", created_at=thirty_hours_ago)
    client = StubClient()

    status = process_comment_event(routed_session, get_redis(), event.id, client=client)

    assert status == "dropped_stale"
    assert client.message_calls == []


def test_comment_channel_flow_does_not_match_story_reply_event(routed_session, ig_merchant):
    # Channel-scoped matching (app/flows/executor.py) must keep the two
    # event kinds from cross-triggering each other's flows.
    _make_price_flow(routed_session, ig_merchant.id)  # channel="instagram_comment"
    event = _make_story_event(routed_session, ig_merchant.id, "narx")
    client = StubClient()

    status = process_comment_event(routed_session, get_redis(), event.id, client=client)

    assert status == "no_match"
    assert client.message_calls == []
