"""Merchant-facing Instagram automation dashboard API
(app/automations/router.py) - goes through real FastAPI routes that
commit for real, so this uses the routed_session/routed_merchant/client
fixtures (see tests/conftest.py), plus a hand-built session cookie
(app/auth/'s signup/verify flow is exercised separately in
tests/test_auth.py - reusing it here would just be indirection).
"""

import datetime as dt
import json
import secrets
import uuid

import pytest

from app.auth import service as auth_service
from app.db.models import CommentEvent, Flow, WebSession
from app.flows.executor import match_flow
from tests.conftest import make_flow


def _now_naive() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)


@pytest.fixture
def auth_cookie(routed_session, routed_merchant):
    token = secrets.token_urlsafe(16)
    routed_session.add(
        WebSession(
            merchant_id=routed_merchant.id,
            token_hash=auth_service._hash_token(token),
            expires_at=_now_naive() + dt.timedelta(days=1),
        )
    )
    routed_session.flush()
    return token


@pytest.fixture
def auth_client(client, auth_cookie):
    client.cookies.set("dukan_session", auth_cookie)
    return client


# ---- match_flow respects is_active -----------------------------------


def test_inactive_flow_is_not_matched(db_session, test_merchant):
    flow = make_flow(
        db_session,
        test_merchant.id,
        "price",
        ["narx"],
        channel="instagram_comment",
        response_config={"link": "https://t.me/shop", "private_reply": {"uz": "narx: {link}"}},
    )
    flow.is_active = False
    db_session.add(flow)
    db_session.flush()

    result = match_flow(db_session, test_merchant.id, "narxi qancha", channel="instagram_comment")
    assert result is None


def test_active_flow_is_matched(db_session, test_merchant):
    make_flow(
        db_session,
        test_merchant.id,
        "price",
        ["narx"],
        channel="instagram_comment",
        response_config={"link": "https://t.me/shop", "private_reply": {"uz": "narx: {link}"}},
    )
    result = match_flow(db_session, test_merchant.id, "narxi qancha", channel="instagram_comment")
    assert result is not None


# ---- auth gating -------------------------------------------------------


def test_templates_requires_auth(client, routed_session):
    response = client.get("/automations/templates")
    assert response.status_code == 401


def test_automations_list_requires_auth(client, routed_session):
    response = client.get("/automations")
    assert response.status_code == 401


# ---- GET /automations/templates ----------------------------------------


def test_templates_filtered_by_vertical(auth_client, routed_session, routed_merchant):
    routed_merchant.vertical = "clothing"
    routed_session.add(routed_merchant)
    routed_session.flush()

    response = auth_client.get("/automations/templates")
    assert response.status_code == 200
    keys = {t["key"] for t in response.json()}
    assert "course_enroll" not in keys
    assert "giveaway_keyword" in keys


def test_templates_include_course_only_for_course_vertical(auth_client, routed_session, routed_merchant):
    routed_merchant.vertical = "course"
    routed_session.add(routed_merchant)
    routed_session.flush()

    response = auth_client.get("/automations/templates")
    keys = {t["key"] for t in response.json()}
    assert "course_enroll" in keys


# ---- POST /automations --------------------------------------------------


def test_install_creates_flow_with_expected_shape(auth_client, routed_session, routed_merchant):
    response = auth_client.post(
        "/automations",
        json={"template_key": "course_enroll", "fields": {"link": "https://t.me/shop"}, "media_ids": [], "public_reply_enabled": True},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["template_key"] == "course_enroll"
    assert body["link"] == "https://t.me/shop"
    assert body["public_reply_enabled"] is True
    assert body["is_active"] is True

    flow = routed_session.query(Flow).filter_by(merchant_id=routed_merchant.id, channel="instagram_comment").one()
    assert flow.template_key == "course_enroll"
    assert flow.response_config["link"] == "https://t.me/shop"
    assert "public_reply" in flow.response_config


def test_install_rejects_bad_link(auth_client, routed_session):
    response = auth_client.post(
        "/automations",
        json={"template_key": "course_enroll", "fields": {"link": "not-a-url"}, "media_ids": [], "public_reply_enabled": True},
    )
    assert response.status_code == 400


def test_install_public_reply_disabled_omits_it(auth_client, routed_session):
    response = auth_client.post(
        "/automations",
        json={"template_key": "course_enroll", "fields": {"link": "https://t.me/shop"}, "media_ids": [], "public_reply_enabled": False},
    )
    assert response.status_code == 200
    assert response.json()["public_reply_enabled"] is False


def test_giveaway_requires_custom_keyword(auth_client, routed_session):
    response = auth_client.post(
        "/automations",
        json={"template_key": "giveaway_keyword", "fields": {"link": "https://t.me/shop"}, "media_ids": [], "public_reply_enabled": True},
    )
    assert response.status_code == 400


def test_giveaway_uses_custom_keyword_as_trigger(auth_client, routed_session, routed_merchant):
    response = auth_client.post(
        "/automations",
        json={
            "template_key": "giveaway_keyword",
            "fields": {"link": "https://t.me/shop", "keyword": "SOVGA2026"},
            "media_ids": [],
            "public_reply_enabled": True,
        },
    )
    assert response.status_code == 200
    assert response.json()["keywords"] == ["SOVGA2026"]


def test_giveaway_custom_keyword_is_transliterated_to_latin(auth_client, routed_session):
    # A merchant typing their keyword in Uzbek-Cyrillic must still match a
    # customer's Latin-transliterated comment, since match_flow compares
    # against normalize()'d inbound text - see app/nlp/transliteration.py.
    response = auth_client.post(
        "/automations",
        json={
            "template_key": "giveaway_keyword",
            "fields": {"link": "https://t.me/shop", "keyword": "Совға"},
            "media_ids": [],
            "public_reply_enabled": True,
        },
    )
    assert response.status_code == 200
    assert response.json()["keywords"] == ["Sovg‘a"]


def test_patch_custom_keyword_is_transliterated_to_latin(auth_client, routed_session):
    # PATCH's keyword-edit path must canonicalize the same way POST's
    # install path does (_resolve_keywords) - otherwise editing an
    # existing automation's keyword to Cyrillic-Uzbek text would silently
    # stop matching, since inbound comments are normalized to Latin before
    # match_flow compares them.
    install = auth_client.post(
        "/automations",
        json={
            "template_key": "giveaway_keyword",
            "fields": {"link": "https://t.me/shop", "keyword": "old"},
            "media_ids": [],
            "public_reply_enabled": True,
        },
    )
    automation_id = install.json()["id"]
    response = auth_client.patch(f"/automations/{automation_id}", json={"fields": {"keyword": "Совға"}})
    assert response.status_code == 200
    assert response.json()["keywords"] == ["Sovg‘a"]


def test_reinstall_same_template_updates_instead_of_duplicating(auth_client, routed_session, routed_merchant):
    auth_client.post(
        "/automations",
        json={"template_key": "course_enroll", "fields": {"link": "https://t.me/old"}, "media_ids": [], "public_reply_enabled": True},
    )
    auth_client.post(
        "/automations",
        json={"template_key": "course_enroll", "fields": {"link": "https://t.me/new"}, "media_ids": [], "public_reply_enabled": True},
    )
    rows = routed_session.query(Flow).filter_by(merchant_id=routed_merchant.id, template_key="course_enroll").all()
    assert len(rows) == 1
    assert rows[0].response_config["link"] == "https://t.me/new"


def test_reinstall_giveaway_updates_keyword_not_just_link(auth_client, routed_session, routed_merchant):
    # Reinstalling with a different custom keyword must overwrite the old
    # trigger_value, not just response_config fields - otherwise a merchant
    # "editing" their giveaway keyword via reinstall would leave the old
    # (now-wrong) keyword active alongside/instead of the new one.
    auth_client.post(
        "/automations",
        json={
            "template_key": "giveaway_keyword",
            "fields": {"link": "https://t.me/shop", "keyword": "OLDWORD"},
            "media_ids": [],
            "public_reply_enabled": True,
        },
    )
    response = auth_client.post(
        "/automations",
        json={
            "template_key": "giveaway_keyword",
            "fields": {"link": "https://t.me/shop", "keyword": "NEWWORD"},
            "media_ids": [],
            "public_reply_enabled": True,
        },
    )
    assert response.status_code == 200
    assert response.json()["keywords"] == ["NEWWORD"]

    rows = routed_session.query(Flow).filter_by(merchant_id=routed_merchant.id, template_key="giveaway_keyword").all()
    assert len(rows) == 1
    assert json.loads(rows[0].trigger_value) == ["NEWWORD"]


def test_install_two_templates_with_overlapping_keywords_both_succeed(auth_client, routed_session, routed_merchant):
    # The collision warning (dashboard_automations.html's collidingAutomation)
    # is a client-side "activate anyway" confirm step only - the server must
    # NOT itself block two different, same-channel automations from ending
    # up with an overlapping trigger keyword.
    first = auth_client.post(
        "/automations",
        json={
            "template_key": "giveaway_keyword",
            "fields": {"link": "https://t.me/giveaway", "keyword": "sovga"},
            "media_ids": [],
            "public_reply_enabled": True,
        },
    )
    second = auth_client.post(
        "/automations",
        json={
            "template_key": "keyword_to_dm",
            "fields": {"link": "https://t.me/shop", "keyword": "sovga", "message": "Salom!"},
            "media_ids": [],
            "public_reply_enabled": True,
        },
    )
    assert first.status_code == 200
    assert second.status_code == 200

    rows = routed_session.query(Flow).filter_by(merchant_id=routed_merchant.id, channel="instagram_comment").all()
    assert len(rows) == 2
    assert all(row.is_active for row in rows)
    assert all(json.loads(row.trigger_value) == ["sovga"] for row in rows)


def test_giveaway_custom_keyword_mixed_script_is_normalized(auth_client, routed_session):
    # A keyword mixing a Latin word and a Cyrillic-Uzbek word in the same
    # string (e.g. typed with an accidentally-left keyboard layout switch
    # mid-word) must still end up in the router's canonical Latin form -
    # only the Cyrillic run gets transliterated, the Latin run passes
    # through untouched (app/nlp/transliteration.py's per-run behavior).
    response = auth_client.post(
        "/automations",
        json={
            "template_key": "giveaway_keyword",
            "fields": {"link": "https://t.me/shop", "keyword": "VIP Совға"},
            "media_ids": [],
            "public_reply_enabled": True,
        },
    )
    assert response.status_code == 200
    assert response.json()["keywords"] == ["VIP Sovg‘a"]


def test_giveaway_custom_keyword_whitespace_only_is_400(auth_client, routed_session):
    response = auth_client.post(
        "/automations",
        json={
            "template_key": "giveaway_keyword",
            "fields": {"link": "https://t.me/shop", "keyword": "   "},
            "media_ids": [],
            "public_reply_enabled": True,
        },
    )
    assert response.status_code == 400


def test_story_reply_link_uses_story_channel_and_has_no_public_reply(auth_client, routed_session, routed_merchant):
    response = auth_client.post(
        "/automations",
        json={
            "template_key": "story_reply_link",
            "fields": {"link": "https://t.me/shop", "keyword": "STORY2026"},
            "media_ids": [],
            "public_reply_enabled": True,  # merchant asks for it, but the channel has no such surface
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["channel"] == "instagram_story_reply"
    assert body["keywords"] == ["STORY2026"]
    # public_reply_enabled must stay False regardless of the request flag -
    # _build_response_config only turns it on for channel == instagram_comment.
    assert body["public_reply_enabled"] is False

    flow = routed_session.query(Flow).filter_by(merchant_id=routed_merchant.id, channel="instagram_story_reply").one()
    assert "public_reply" not in flow.response_config


def test_story_reply_link_requires_custom_keyword(auth_client, routed_session):
    response = auth_client.post(
        "/automations",
        json={"template_key": "story_reply_link", "fields": {"link": "https://t.me/shop"}, "media_ids": [], "public_reply_enabled": True},
    )
    assert response.status_code == 400


def test_install_rejects_template_not_available_for_vertical(auth_client, routed_session, routed_merchant):
    routed_merchant.vertical = "clothing"
    routed_session.add(routed_merchant)
    routed_session.flush()

    response = auth_client.post(
        "/automations",
        json={"template_key": "course_enroll", "fields": {"link": "https://t.me/course"}, "media_ids": [], "public_reply_enabled": True},
    )
    assert response.status_code == 400


# ---- GET /automations (with stats) --------------------------------------


def test_list_includes_7_day_stats(auth_client, routed_session, routed_merchant):
    install = auth_client.post(
        "/automations",
        json={"template_key": "course_enroll", "fields": {"link": "https://t.me/shop"}, "media_ids": [], "public_reply_enabled": True},
    )
    flow_id = uuid.UUID(install.json()["id"])

    now = _now_naive()
    routed_session.add_all(
        [
            CommentEvent(
                merchant_id=routed_merchant.id,
                channel="instagram_comment",
                external_id="c1",
                commenter_id="u1",
                matched_flow_id=flow_id,
                status="replied",
                created_at=now,
            ),
            CommentEvent(
                merchant_id=routed_merchant.id,
                channel="instagram_comment",
                external_id="c2",
                commenter_id="u2",
                matched_flow_id=flow_id,
                status="no_match",
                created_at=now,
            ),
            CommentEvent(
                merchant_id=routed_merchant.id,
                channel="instagram_comment",
                external_id="c3",
                commenter_id="u3",
                matched_flow_id=flow_id,
                status="replied",
                created_at=now - dt.timedelta(days=10),
            ),
        ]
    )
    routed_session.flush()

    response = auth_client.get("/automations")
    body = response.json()
    assert len(body) == 1
    assert body[0]["comments_7d"] == 2  # the 10-day-old event is outside the window
    assert body[0]["dms_sent_7d"] == 1


# ---- PATCH / DELETE ------------------------------------------------------


def test_patch_toggles_active(auth_client, routed_session):
    install = auth_client.post(
        "/automations",
        json={"template_key": "course_enroll", "fields": {"link": "https://t.me/shop"}, "media_ids": [], "public_reply_enabled": True},
    )
    automation_id = install.json()["id"]

    response = auth_client.patch(f"/automations/{automation_id}", json={"is_active": False})
    assert response.status_code == 200
    assert response.json()["is_active"] is False


def test_delete_removes_flow(auth_client, routed_session, routed_merchant):
    install = auth_client.post(
        "/automations",
        json={"template_key": "course_enroll", "fields": {"link": "https://t.me/shop"}, "media_ids": [], "public_reply_enabled": True},
    )
    automation_id = install.json()["id"]

    response = auth_client.delete(f"/automations/{automation_id}")
    assert response.status_code == 200
    assert routed_session.query(Flow).filter_by(id=automation_id).first() is None


def test_patch_on_another_merchants_automation_is_404(client, routed_session, routed_merchant):
    # Install as routed_merchant, then try to patch it while authenticated
    # as a second, unrelated merchant.
    token = secrets.token_urlsafe(16)
    routed_session.add(
        WebSession(
            merchant_id=routed_merchant.id,
            token_hash=auth_service._hash_token(token),
            expires_at=_now_naive() + dt.timedelta(days=1),
        )
    )
    routed_session.flush()
    client.cookies.set("dukan_session", token)
    install = client.post(
        "/automations",
        json={"template_key": "course_enroll", "fields": {"link": "https://t.me/shop"}, "media_ids": [], "public_reply_enabled": True},
    )
    automation_id = install.json()["id"]

    from app.db.models import Merchant

    other = Merchant(name="other", telegram_bot_token="other-token", webhook_secret="other-secret")
    routed_session.add(other)
    routed_session.flush()
    other_token = secrets.token_urlsafe(16)
    routed_session.add(
        WebSession(
            merchant_id=other.id,
            token_hash=auth_service._hash_token(other_token),
            expires_at=_now_naive() + dt.timedelta(days=1),
        )
    )
    routed_session.flush()

    client.cookies.set("dukan_session", other_token)
    response = client.patch(f"/automations/{automation_id}", json={"is_active": False})
    assert response.status_code == 404


# ---- GET /instagram/media -------------------------------------------------


def test_media_requires_instagram_connection(auth_client, routed_session):
    response = auth_client.get("/instagram/media")
    assert response.status_code == 409


def test_media_returns_cached_page_on_second_call(auth_client, routed_session, routed_merchant, monkeypatch):
    routed_merchant.instagram_access_token = "fake-ig-token"
    routed_session.add(routed_merchant)
    routed_session.flush()

    call_count = {"n": 0}

    def fake_get_media(self, cursor=None, limit=25):
        call_count["n"] += 1
        return {"data": [{"id": "m1", "media_type": "IMAGE"}], "paging": {}}

    from app.instagram.client import InstagramClient

    monkeypatch.setattr(InstagramClient, "get_media", fake_get_media)

    first = auth_client.get("/instagram/media")
    second = auth_client.get("/instagram/media")
    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["items"][0]["id"] == "m1"
    assert call_count["n"] == 1  # second call served from the 5-minute Redis cache
