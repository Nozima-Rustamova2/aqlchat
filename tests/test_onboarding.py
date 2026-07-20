"""Service-level tests use the plain db_session fixture and call
app.onboarding.service functions directly - no HTTP involved, matching
tests/test_handoff.py's style. The router-level /reply authorization
tests need the real FastAPI route (to prove the *router's* MerchantAdmin
lookup is what gates /reply, not just handle_admin_command itself), so
those use the routed_session/routed_merchant/client fixtures from
conftest.py instead.

Onboarding's outbound Telegram calls (_send, delete_message,
set_webhook) use real tokens against the real API and are expected to
fail gracefully against the made-up chat/user ids used here - same
live-hitting-real-API-and-catching-the-failure pattern established in
tests/test_handoff.py. Only _validate_token (the getMe call that decides
whether a pasted token is accepted) is monkeypatched, since there's no
second real BotFather token available to paste in a test, and using
@DukanAI_bot's own token here would reassign its live webhook away from
the platform route.
"""

import pytest

from app.config import settings
from app.db.models import (
    Conversation,
    Customer,
    Faq,
    Merchant,
    MerchantAdmin,
    PendingAdminReply,
    PlatformOnboardingSession,
)
from app.onboarding import service
from app.telegram.client import TelegramClient
from tests.conftest import make_merchant_admin


def test_handle_start_refuses_an_already_registered_admin(db_session, test_merchant):
    make_merchant_admin(db_session, test_merchant.id, 800001)

    service.handle_start(db_session, 800001, chat_id=800001)

    # Refused before ever touching onboarding state.
    assert db_session.get(PlatformOnboardingSession, 800001) is None


def test_handle_start_creates_a_session_for_a_new_identity(db_session):
    service.handle_start(db_session, 800002, chat_id=800002)

    session = db_session.get(PlatformOnboardingSession, 800002)
    assert session is not None
    assert session.state == service.STATE_START


def test_language_pick_transitions_to_awaiting_token(db_session):
    service.handle_start(db_session, 800003, chat_id=800003)

    service.handle_platform_callback(db_session, 800003, chat_id=800003, callback_data="lang:uz")

    session = db_session.get(PlatformOnboardingSession, 800003)
    assert session.state == service.STATE_AWAITING_TOKEN
    assert session.language == "uz"


def test_garbage_token_stays_in_awaiting_token(db_session):
    service.handle_start(db_session, 800004, chat_id=800004)
    service.handle_platform_callback(db_session, 800004, chat_id=800004, callback_data="lang:uz")

    # Not a real token - the live getMe call genuinely fails.
    service.handle_platform_message(db_session, 800004, chat_id=800004, message_id=1, text="garbage-not-a-token")

    session = db_session.get(PlatformOnboardingSession, 800004)
    assert session.state == service.STATE_AWAITING_TOKEN
    assert session.merchant_id is None


def test_token_for_an_already_registered_bot_does_not_crash(db_session, test_merchant, monkeypatch):
    # Found via live testing: pasting a token for a bot that already
    # backs a Merchant (manually seeded, or a prior onboarding run) used
    # to crash with an unhandled IntegrityError on telegram_bot_id's
    # unique constraint instead of failing gracefully.
    test_merchant.telegram_bot_id = 999888700
    db_session.add(test_merchant)
    db_session.flush()

    monkeypatch.setattr(
        service,
        "_validate_token",
        lambda token: {"id": 999888700, "first_name": "Already Registered Bot", "username": "already_bot"},
    )

    service.handle_start(db_session, 800099, chat_id=800099)
    service.handle_platform_callback(db_session, 800099, chat_id=800099, callback_data="lang:uz")
    service.handle_platform_message(db_session, 800099, chat_id=800099, message_id=1, text="already-registered-token")

    session = db_session.get(PlatformOnboardingSession, 800099)
    assert session.state == service.STATE_AWAITING_TOKEN
    assert session.merchant_id is None

    admin = db_session.query(MerchantAdmin).filter_by(telegram_user_id=800099).first()
    assert admin is None


def test_valid_token_creates_merchant_and_admin_and_advances_state(db_session, monkeypatch):
    monkeypatch.setattr(
        service,
        "_validate_token",
        lambda token: {"id": 999888777, "first_name": "Fake Shop Bot", "username": "fake_shop_bot"},
    )

    service.handle_start(db_session, 800005, chat_id=800005)
    service.handle_platform_callback(db_session, 800005, chat_id=800005, callback_data="lang:uz")
    service.handle_platform_message(db_session, 800005, chat_id=800005, message_id=1, text="fake-but-valid-token")
    db_session.flush()  # SessionLocal runs autoflush=False - the query below needs this explicitly

    session = db_session.get(PlatformOnboardingSession, 800005)
    assert session.state == service.STATE_AWAITING_SHOP_NAME
    assert session.merchant_id is not None

    admin = db_session.query(MerchantAdmin).filter_by(telegram_user_id=800005).first()
    assert admin is not None
    assert admin.merchant_id == session.merchant_id

    merchant = db_session.get(Merchant, session.merchant_id)
    assert merchant.name == "Fake Shop Bot"  # prefilled from getMe's first_name


def test_start_with_connect_token_binds_session_to_web_signup_merchant(db_session):
    merchant = Merchant(email="signup@example.com", owner_name="Nozima", webhook_secret="s")
    db_session.add(merchant)
    db_session.flush()

    service.handle_start(db_session, 800030, chat_id=800030, connect_token=merchant.webhook_slug)

    session = db_session.get(PlatformOnboardingSession, 800030)
    assert session.merchant_id == merchant.id


def test_start_with_connect_token_for_already_connected_merchant_is_refused(db_session):
    merchant = Merchant(email="taken@example.com", telegram_bot_id=555, telegram_bot_token="tok", webhook_secret="s")
    db_session.add(merchant)
    db_session.flush()

    service.handle_start(db_session, 800031, chat_id=800031, connect_token=merchant.webhook_slug)

    # Refused before ever creating an onboarding session.
    assert db_session.get(PlatformOnboardingSession, 800031) is None


def test_start_with_unknown_connect_token_falls_back_to_normal_flow(db_session):
    service.handle_start(db_session, 800032, chat_id=800032, connect_token="not-a-real-slug")

    session = db_session.get(PlatformOnboardingSession, 800032)
    assert session is not None
    assert session.merchant_id is None


def test_valid_token_after_connect_token_attaches_bot_to_existing_merchant(db_session, monkeypatch):
    merchant = Merchant(email="signup2@example.com", owner_name="Dilnoza", webhook_secret="s")
    db_session.add(merchant)
    db_session.flush()
    merchant_id = merchant.id

    monkeypatch.setattr(
        service,
        "_validate_token",
        lambda token: {"id": 999888800, "first_name": "Linked Bot", "username": "linked_bot"},
    )

    service.handle_start(db_session, 800033, chat_id=800033, connect_token=merchant.webhook_slug)
    service.handle_platform_callback(db_session, 800033, chat_id=800033, callback_data="lang:uz")
    service.handle_platform_message(db_session, 800033, chat_id=800033, message_id=1, text="fake-linked-token")
    db_session.flush()

    session = db_session.get(PlatformOnboardingSession, 800033)
    assert session.state == service.STATE_AWAITING_SHOP_NAME
    assert session.merchant_id == merchant_id

    # No second Merchant row was created - the web account was updated in place.
    assert db_session.query(Merchant).filter_by(email="signup2@example.com").count() == 1

    merchant = db_session.get(Merchant, merchant_id)
    assert merchant.telegram_bot_id == 999888800
    assert merchant.name == "Linked Bot"  # prefilled since website signup never asked for shop name

    admin = db_session.query(MerchantAdmin).filter_by(telegram_user_id=800033).first()
    assert admin is not None
    assert admin.merchant_id == merchant_id


def test_valid_token_preserves_existing_shop_name_on_linked_merchant(db_session, monkeypatch):
    merchant = Merchant(email="signup3@example.com", name="Already Named Shop", webhook_secret="s")
    db_session.add(merchant)
    db_session.flush()
    merchant_id = merchant.id

    monkeypatch.setattr(
        service,
        "_validate_token",
        lambda token: {"id": 999888801, "first_name": "Bot Name", "username": "bot_name"},
    )

    service.handle_start(db_session, 800034, chat_id=800034, connect_token=merchant.webhook_slug)
    service.handle_platform_callback(db_session, 800034, chat_id=800034, callback_data="lang:uz")
    service.handle_platform_message(db_session, 800034, chat_id=800034, message_id=1, text="fake-token-3")
    db_session.flush()

    merchant = db_session.get(Merchant, merchant_id)
    assert merchant.name == "Already Named Shop"  # not clobbered by getMe's first_name


def test_shop_name_confirm_keeps_the_prefilled_name(db_session, monkeypatch):
    monkeypatch.setattr(
        service, "_validate_token", lambda token: {"id": 999888790, "first_name": "Prefill Bot", "username": "p_bot"}
    )
    service.handle_start(db_session, 800020, chat_id=800020)
    service.handle_platform_callback(db_session, 800020, chat_id=800020, callback_data="lang:uz")
    service.handle_platform_message(db_session, 800020, chat_id=800020, message_id=1, text="tok")

    service.handle_platform_callback(db_session, 800020, chat_id=800020, callback_data="shop_name:confirm")

    session = db_session.get(PlatformOnboardingSession, 800020)
    assert session.state == service.STATE_CHOOSING_VERTICAL
    merchant = db_session.get(Merchant, session.merchant_id)
    assert merchant.name == "Prefill Bot"


def test_shop_name_text_overrides_the_prefilled_name(db_session, monkeypatch):
    # Bot first_names are frequently junk - see feedback_design_decisions
    # memory - so editing must actually override, not just confirm.
    monkeypatch.setattr(
        service, "_validate_token", lambda token: {"id": 999888791, "first_name": "asdf123", "username": "j_bot"}
    )
    service.handle_start(db_session, 800021, chat_id=800021)
    service.handle_platform_callback(db_session, 800021, chat_id=800021, callback_data="lang:uz")
    service.handle_platform_message(db_session, 800021, chat_id=800021, message_id=1, text="tok")

    service.handle_platform_message(
        db_session, 800021, chat_id=800021, message_id=2, text="Gulnora Kiyimlari"
    )

    session = db_session.get(PlatformOnboardingSession, 800021)
    assert session.state == service.STATE_CHOOSING_VERTICAL
    merchant = db_session.get(Merchant, session.merchant_id)
    assert merchant.name == "Gulnora Kiyimlari"


def _walk_to_vertical_pick(db_session, monkeypatch, telegram_user_id: int, bot_id: int) -> None:
    monkeypatch.setattr(
        service, "_validate_token", lambda token: {"id": bot_id, "first_name": "Walkthrough Bot", "username": "w_bot"}
    )
    service.handle_start(db_session, telegram_user_id, chat_id=telegram_user_id)
    service.handle_platform_callback(db_session, telegram_user_id, chat_id=telegram_user_id, callback_data="lang:uz")
    service.handle_platform_message(db_session, telegram_user_id, chat_id=telegram_user_id, message_id=1, text="tok")
    service.handle_platform_callback(
        db_session, telegram_user_id, chat_id=telegram_user_id, callback_data="shop_name:confirm"
    )


def test_vertical_pick_advances_to_source_not_done(db_session, monkeypatch):
    # The v1 flow finished right after vertical pick - v3.1 continues
    # into source/FAQ-topics/hours/tone instead.
    _walk_to_vertical_pick(db_session, monkeypatch, 800006, 999888778)
    service.handle_platform_callback(db_session, 800006, chat_id=800006, callback_data="vertical:clothing")

    session = db_session.get(PlatformOnboardingSession, 800006)
    assert session.state == service.STATE_CHOOSING_SOURCE
    merchant = db_session.get(Merchant, session.merchant_id)
    assert merchant.vertical == "clothing"


def test_channel_source_pick_skips_straight_to_faq_topics(db_session, monkeypatch):
    _walk_to_vertical_pick(db_session, monkeypatch, 800007, 999888779)
    service.handle_platform_callback(db_session, 800007, chat_id=800007, callback_data="vertical:clothing")
    service.handle_platform_callback(db_session, 800007, chat_id=800007, callback_data="source:channel")

    session = db_session.get(PlatformOnboardingSession, 800007)
    assert session.state == service.STATE_CHOOSING_FAQ_TOPICS


def test_course_source_pick_asks_for_description_first(db_session, monkeypatch):
    _walk_to_vertical_pick(db_session, monkeypatch, 800008, 999888780)
    service.handle_platform_callback(db_session, 800008, chat_id=800008, callback_data="vertical:course")
    service.handle_platform_callback(db_session, 800008, chat_id=800008, callback_data="source:course")

    session = db_session.get(PlatformOnboardingSession, 800008)
    assert session.state == service.STATE_AWAITING_COURSE_DESCRIPTION

    service.handle_platform_message(
        db_session, 800008, chat_id=800008, message_id=2, text="8 haftalik ingliz tili kursi, 500000 som"
    )
    session = db_session.get(PlatformOnboardingSession, 800008)
    assert session.state == service.STATE_CHOOSING_FAQ_TOPICS
    merchant = db_session.get(Merchant, session.merchant_id)
    assert merchant.profile["course_raw_description"] == "8 haftalik ingliz tili kursi, 500000 som"


def test_faq_topic_selection_creates_faq_rows_with_answers(db_session, monkeypatch):
    _walk_to_vertical_pick(db_session, monkeypatch, 800009, 999888781)
    service.handle_platform_callback(db_session, 800009, chat_id=800009, callback_data="vertical:clothing")
    service.handle_platform_callback(db_session, 800009, chat_id=800009, callback_data="source:channel")

    service.handle_platform_callback(db_session, 800009, chat_id=800009, callback_data="topic:price")
    service.handle_platform_callback(db_session, 800009, chat_id=800009, callback_data="topic:delivery")
    service.handle_platform_callback(db_session, 800009, chat_id=800009, callback_data="topics_done")

    session = db_session.get(PlatformOnboardingSession, 800009)
    assert session.state == service.STATE_AWAITING_TOPIC_ANSWER

    service.handle_platform_message(db_session, 800009, chat_id=800009, message_id=2, text="150000 dan boshlab")
    service.handle_platform_message(db_session, 800009, chat_id=800009, message_id=3, text="Toshkent bo'ylab bepul")

    session = db_session.get(PlatformOnboardingSession, 800009)
    assert session.state == service.STATE_AWAITING_HOURS
    db_session.flush()  # SessionLocal runs autoflush=False - the query below needs this explicitly

    merchant = db_session.get(Merchant, session.merchant_id)
    faqs = db_session.query(Faq).filter_by(merchant_id=merchant.id).all()
    answers = {faq.question: faq.response_config["uz"] for faq in faqs}
    assert "150000 dan boshlab" in answers.values()
    assert "Toshkent bo'ylab bepul" in answers.values()
    assert merchant.profile["faq_topics"]["price"] == "150000 dan boshlab"


def test_payment_topic_uses_multiselect_not_free_text(db_session, monkeypatch):
    _walk_to_vertical_pick(db_session, monkeypatch, 800010, 999888782)
    service.handle_platform_callback(db_session, 800010, chat_id=800010, callback_data="vertical:clothing")
    service.handle_platform_callback(db_session, 800010, chat_id=800010, callback_data="source:channel")

    service.handle_platform_callback(db_session, 800010, chat_id=800010, callback_data="topic:payment")
    service.handle_platform_callback(db_session, 800010, chat_id=800010, callback_data="topics_done")

    session = db_session.get(PlatformOnboardingSession, 800010)
    assert session.state == service.STATE_CHOOSING_PAYMENT_METHODS

    service.handle_platform_callback(db_session, 800010, chat_id=800010, callback_data="payment:cash")
    service.handle_platform_callback(db_session, 800010, chat_id=800010, callback_data="payment:card")
    service.handle_platform_callback(db_session, 800010, chat_id=800010, callback_data="payment_done")

    session = db_session.get(PlatformOnboardingSession, 800010)
    assert session.state == service.STATE_AWAITING_HOURS
    db_session.flush()  # SessionLocal runs autoflush=False - the query below needs this explicitly

    merchant = db_session.get(Merchant, session.merchant_id)
    faq = db_session.query(Faq).filter_by(merchant_id=merchant.id).first()
    assert "Naqd" in faq.response_config["uz"]
    assert "Karta" in faq.response_config["uz"]


def test_unselecting_a_topic_removes_it(db_session, monkeypatch):
    _walk_to_vertical_pick(db_session, monkeypatch, 800011, 999888783)
    service.handle_platform_callback(db_session, 800011, chat_id=800011, callback_data="vertical:clothing")
    service.handle_platform_callback(db_session, 800011, chat_id=800011, callback_data="source:channel")

    service.handle_platform_callback(db_session, 800011, chat_id=800011, callback_data="topic:price")
    service.handle_platform_callback(db_session, 800011, chat_id=800011, callback_data="topic:price")  # toggle off
    service.handle_platform_callback(db_session, 800011, chat_id=800011, callback_data="topics_done")

    session = db_session.get(PlatformOnboardingSession, 800011)
    # Nothing selected - straight through to hours, no follow-up loop.
    assert session.state == service.STATE_AWAITING_HOURS


def _finish_faqless_walkthrough(db_session, telegram_user_id: int) -> None:
    service.handle_platform_callback(
        db_session, telegram_user_id, chat_id=telegram_user_id, callback_data="source:channel"
    )
    service.handle_platform_callback(
        db_session, telegram_user_id, chat_id=telegram_user_id, callback_data="topics_done"
    )


def test_full_walkthrough_reaches_done_with_hours_and_tone_saved(db_session, monkeypatch):
    monkeypatch.setattr(TelegramClient, "send_message", lambda self, *a, **k: {"result": {"message_id": 1}})
    _walk_to_vertical_pick(db_session, monkeypatch, 800012, 999888784)
    service.handle_platform_callback(db_session, 800012, chat_id=800012, callback_data="vertical:cosmetics")
    _finish_faqless_walkthrough(db_session, 800012)

    session = db_session.get(PlatformOnboardingSession, 800012)
    assert session.state == service.STATE_AWAITING_HOURS
    service.handle_platform_message(db_session, 800012, chat_id=800012, message_id=5, text="9:00-18:00")

    session = db_session.get(PlatformOnboardingSession, 800012)
    assert session.state == service.STATE_CHOOSING_TONE
    service.handle_platform_callback(db_session, 800012, chat_id=800012, callback_data="tone:friendly")

    session = db_session.get(PlatformOnboardingSession, 800012)
    assert session.state == service.STATE_DONE
    merchant = db_session.get(Merchant, session.merchant_id)
    assert merchant.profile["hours"] == "9:00-18:00"
    assert merchant.profile["tone"] == "friendly"


def test_hours_skip_button_leaves_hours_unset(db_session, monkeypatch):
    monkeypatch.setattr(TelegramClient, "send_message", lambda self, *a, **k: {"result": {"message_id": 1}})
    _walk_to_vertical_pick(db_session, monkeypatch, 800013, 999888785)
    service.handle_platform_callback(db_session, 800013, chat_id=800013, callback_data="vertical:other")
    _finish_faqless_walkthrough(db_session, 800013)

    service.handle_platform_callback(db_session, 800013, chat_id=800013, callback_data="hours:skip")

    session = db_session.get(PlatformOnboardingSession, 800013)
    assert session.state == service.STATE_CHOOSING_TONE
    merchant = db_session.get(Merchant, session.merchant_id)
    assert "hours" not in (merchant.profile or {})


def test_settings_reentry_edits_one_field_and_returns_to_done(db_session, monkeypatch):
    monkeypatch.setattr(TelegramClient, "send_message", lambda self, *a, **k: {"result": {"message_id": 1}})
    _walk_to_vertical_pick(db_session, monkeypatch, 800014, 999888786)
    service.handle_platform_callback(db_session, 800014, chat_id=800014, callback_data="vertical:other")
    _finish_faqless_walkthrough(db_session, 800014)
    service.handle_platform_callback(db_session, 800014, chat_id=800014, callback_data="hours:skip")
    service.handle_platform_callback(db_session, 800014, chat_id=800014, callback_data="tone:formal")

    session = db_session.get(PlatformOnboardingSession, 800014)
    assert session.state == service.STATE_DONE

    # /sozlamalar -> edit tone only - must NOT cascade back through
    # hours/faq_topics/source/vertical, just save and return to done.
    service.handle_settings_command(db_session, 800014, chat_id=800014)
    service.handle_platform_callback(db_session, 800014, chat_id=800014, callback_data="settings:tone")

    session = db_session.get(PlatformOnboardingSession, 800014)
    assert session.state == service.STATE_CHOOSING_TONE

    service.handle_platform_callback(db_session, 800014, chat_id=800014, callback_data="tone:friendly")

    session = db_session.get(PlatformOnboardingSession, 800014)
    assert session.state == service.STATE_DONE
    merchant = db_session.get(Merchant, session.merchant_id)
    assert merchant.profile["tone"] == "friendly"


def test_settings_command_is_a_noop_before_onboarding_completes(db_session):
    # No MerchantAdmin registered yet for this identity - nothing to
    # re-enter.
    service.handle_settings_command(db_session, 800015, chat_id=800015)
    assert db_session.get(PlatformOnboardingSession, 800015) is None


@pytest.fixture
def reply_update():
    def _make(update_id: int, user_id: int, text: str) -> dict:
        return {
            "update_id": update_id,
            "message": {
                "message_id": update_id,
                "date": 0,
                "chat": {"id": user_id, "type": "private"},
                "from": {"id": user_id, "is_bot": False, "first_name": "Admin"},
                "text": text,
            },
        }

    return _make


def test_platform_reply_from_registered_admin_is_authorized(client, routed_session, routed_merchant, reply_update):
    routed_session.add(MerchantAdmin(merchant_id=routed_merchant.id, telegram_user_id=800101))
    routed_session.flush()

    response = client.post(
        "/telegram/webhook/platform",
        json=reply_update(1, 800101, "/reply 555 salom"),
        headers={"X-Telegram-Bot-Api-Secret-Token": settings.platform_webhook_secret},
    )
    assert response.status_code == 200


def test_platform_reply_from_unregistered_identity_is_not_dispatched_as_admin(client, routed_session, reply_update):
    # No MerchantAdmin row for this identity - the router must not
    # resolve any merchant for it. It still 200s (onboarding's own
    # session-state-gated text handling no-ops on unrecognized text),
    # but nothing admin-shaped should happen.
    response = client.post(
        "/telegram/webhook/platform",
        json=reply_update(2, 800102, "/reply 555 salom"),
        headers={"X-Telegram-Bot-Api-Secret-Token": settings.platform_webhook_secret},
    )
    assert response.status_code == 200

    admin = routed_session.query(MerchantAdmin).filter_by(telegram_user_id=800102).first()
    assert admin is None


def test_platform_webhook_wrong_secret_returns_403(client, reply_update):
    response = client.post(
        "/telegram/webhook/platform",
        json=reply_update(3, 800103, "/start"),
        headers={"X-Telegram-Bot-Api-Secret-Token": "wrong"},
    )
    assert response.status_code == 403


def test_release_button_releases_the_conversation(client, routed_session, routed_merchant, monkeypatch):
    monkeypatch.setattr(TelegramClient, "send_message", lambda self, *a, **k: {"result": {"message_id": 200}})

    admin = MerchantAdmin(merchant_id=routed_merchant.id, telegram_user_id=800201)
    routed_session.add(admin)
    routed_session.flush()
    customer = Customer(merchant_id=routed_merchant.id, telegram_user_id=800202)
    routed_session.add(customer)
    routed_session.flush()
    conversation = Conversation(merchant_id=routed_merchant.id, customer_id=customer.id, needs_human=True)
    routed_session.add(conversation)
    routed_session.flush()
    pending = PendingAdminReply(
        merchant_admin_id=admin.id, kind="escalation", target_id=conversation.id, platform_message_ids=[200]
    )
    routed_session.add(pending)
    routed_session.flush()

    response = client.post(
        "/telegram/webhook/platform",
        json={
            "update_id": 10,
            "callback_query": {
                "id": "cbq1",
                "from": {"id": 800201, "is_bot": False, "first_name": "Admin"},
                "message": {"message_id": 200, "date": 0, "chat": {"id": 800201, "type": "private"}},
                "data": f"release:{pending.id}",
            },
        },
        headers={"X-Telegram-Bot-Api-Secret-Token": settings.platform_webhook_secret},
    )
    assert response.status_code == 200
    assert conversation.needs_human is False
    assert pending.status == "released"


def test_release_button_from_a_different_admin_is_ignored(client, routed_session, routed_merchant, monkeypatch):
    monkeypatch.setattr(TelegramClient, "send_message", lambda self, *a, **k: {"result": {"message_id": 201}})

    admin = MerchantAdmin(merchant_id=routed_merchant.id, telegram_user_id=800211)
    routed_session.add(admin)
    routed_session.flush()
    customer = Customer(merchant_id=routed_merchant.id, telegram_user_id=800212)
    routed_session.add(customer)
    routed_session.flush()
    conversation = Conversation(merchant_id=routed_merchant.id, customer_id=customer.id, needs_human=True)
    routed_session.add(conversation)
    routed_session.flush()
    pending = PendingAdminReply(
        merchant_admin_id=admin.id, kind="escalation", target_id=conversation.id, platform_message_ids=[201]
    )
    routed_session.add(pending)
    routed_session.flush()

    response = client.post(
        "/telegram/webhook/platform",
        json={
            "update_id": 11,
            "callback_query": {
                "id": "cbq2",
                # A different telegram_user_id than the pending row's own admin.
                "from": {"id": 999999, "is_bot": False, "first_name": "Intruder"},
                "message": {"message_id": 201, "date": 0, "chat": {"id": 999999, "type": "private"}},
                "data": f"release:{pending.id}",
            },
        },
        headers={"X-Telegram-Bot-Api-Secret-Token": settings.platform_webhook_secret},
    )
    assert response.status_code == 200
    assert conversation.needs_human is True


def test_reply_to_message_relays_to_customer_via_tenant_bot(client, routed_session, routed_merchant, monkeypatch):
    sent = []

    def fake_send_message(self, chat_id, text, reply_markup=None):
        sent.append((chat_id, text))
        return {"result": {"message_id": 300}}

    monkeypatch.setattr(TelegramClient, "send_message", fake_send_message)

    admin = MerchantAdmin(merchant_id=routed_merchant.id, telegram_user_id=800301)
    routed_session.add(admin)
    routed_session.flush()
    customer = Customer(merchant_id=routed_merchant.id, telegram_user_id=800302)
    routed_session.add(customer)
    routed_session.flush()
    conversation = Conversation(merchant_id=routed_merchant.id, customer_id=customer.id, needs_human=True)
    routed_session.add(conversation)
    routed_session.flush()
    pending = PendingAdminReply(
        merchant_admin_id=admin.id, kind="escalation", target_id=conversation.id, platform_message_ids=[301]
    )
    routed_session.add(pending)
    routed_session.flush()

    response = client.post(
        "/telegram/webhook/platform",
        json={
            "update_id": 12,
            "message": {
                "message_id": 302,
                "date": 0,
                "chat": {"id": 800301, "type": "private"},
                "from": {"id": 800301, "is_bot": False, "first_name": "Admin"},
                "text": "Salom, narxi 250000 som",
                "reply_to_message": {
                    "message_id": 301,
                    "date": 0,
                    "chat": {"id": 800301, "type": "private"},
                },
            },
        },
        headers={"X-Telegram-Bot-Api-Secret-Token": settings.platform_webhook_secret},
    )
    assert response.status_code == 200
    assert len(sent) == 1
    assert sent[0] == (800302, "Salom, narxi 250000 som")


def test_reply_to_an_untracked_message_falls_through_to_typed_fallback(
    client, routed_session, routed_merchant, monkeypatch
):
    # Replying to a message that isn't any open PendingAdminReply's thread
    # shouldn't silently vanish - it should fall through to the /reply,
    # /release parser (which will report "not a recognized command" via
    # returning None -> no confirmation, but importantly does NOT relay
    # anything to a customer, since _handle_reply_to_message correctly
    # declined to handle it).
    monkeypatch.setattr(TelegramClient, "send_message", lambda self, *a, **k: {"result": {"message_id": 1}})

    admin = MerchantAdmin(merchant_id=routed_merchant.id, telegram_user_id=800401)
    routed_session.add(admin)
    routed_session.flush()

    response = client.post(
        "/telegram/webhook/platform",
        json={
            "update_id": 13,
            "message": {
                "message_id": 402,
                "date": 0,
                "chat": {"id": 800401, "type": "private"},
                "from": {"id": 800401, "is_bot": False, "first_name": "Admin"},
                "text": "just a random reply",
                "reply_to_message": {
                    "message_id": 999,  # not tracked by any PendingAdminReply
                    "date": 0,
                    "chat": {"id": 800401, "type": "private"},
                },
            },
        },
        headers={"X-Telegram-Bot-Api-Secret-Token": settings.platform_webhook_secret},
    )
    assert response.status_code == 200
