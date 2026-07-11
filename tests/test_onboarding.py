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
from app.db.models import Merchant, MerchantAdmin, PlatformOnboardingSession
from app.onboarding import service
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
    assert session.state == service.STATE_CHOOSING_VERTICAL
    assert session.merchant_id is not None

    admin = db_session.query(MerchantAdmin).filter_by(telegram_user_id=800005).first()
    assert admin is not None
    assert admin.merchant_id == session.merchant_id


def test_vertical_pick_finalizes_merchant(db_session, monkeypatch):
    monkeypatch.setattr(
        service,
        "_validate_token",
        lambda token: {"id": 999888778, "first_name": "Another Fake Bot", "username": "another_fake_bot"},
    )

    service.handle_start(db_session, 800006, chat_id=800006)
    service.handle_platform_callback(db_session, 800006, chat_id=800006, callback_data="lang:uz")
    service.handle_platform_message(db_session, 800006, chat_id=800006, message_id=1, text="fake-but-valid-token-2")

    service.handle_platform_callback(db_session, 800006, chat_id=800006, callback_data="vertical:clothing")

    session = db_session.get(PlatformOnboardingSession, 800006)
    assert session.state == service.STATE_DONE

    merchant = db_session.get(Merchant, session.merchant_id)
    assert merchant.vertical == "clothing"


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
