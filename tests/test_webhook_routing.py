"""Webhook requests go through the real FastAPI route, which calls
db.commit() for real - see the routed_session/routed_merchant/client
fixtures in tests/conftest.py for why these tests need their own
SAVEPOINT-based session instead of the plain db_session fixture.
"""

from app.db.models import Customer, MerchantAdmin


def _text_update(update_id: int, chat_id: int, user_id: int, text: str) -> dict:
    return {
        "update_id": update_id,
        "message": {
            "message_id": update_id,
            "date": 0,
            "chat": {"id": chat_id, "type": "private"},
            "from": {"id": user_id, "is_bot": False, "first_name": "Test"},
            "text": text,
        },
    }


def test_tenant_webhook_resolves_merchant_by_slug(client, routed_merchant):
    response = client.post(
        f"/telegram/webhook/tenant/{routed_merchant.webhook_slug}",
        json=_text_update(1, 900001, 900001, "salom"),
        headers={"X-Telegram-Bot-Api-Secret-Token": routed_merchant.webhook_secret},
    )
    assert response.status_code == 200


def test_tenant_webhook_unknown_slug_returns_404(client):
    response = client.post(
        "/telegram/webhook/tenant/this-slug-does-not-exist",
        json=_text_update(2, 900002, 900002, "salom"),
        headers={"X-Telegram-Bot-Api-Secret-Token": "whatever"},
    )
    assert response.status_code == 404


def test_tenant_webhook_wrong_secret_returns_403(client, routed_merchant):
    response = client.post(
        f"/telegram/webhook/tenant/{routed_merchant.webhook_slug}",
        json=_text_update(3, 900003, 900003, "salom"),
        headers={"X-Telegram-Bot-Api-Secret-Token": "definitely-wrong"},
    )
    assert response.status_code == 403


def test_tenant_webhook_missing_secret_header_returns_403(client, routed_merchant):
    response = client.post(
        f"/telegram/webhook/tenant/{routed_merchant.webhook_slug}",
        json=_text_update(4, 900004, 900004, "salom"),
    )
    assert response.status_code == 403


def test_merchant_messaging_own_tenant_bot_is_treated_as_a_customer(client, routed_session, routed_merchant):
    # The core behavior change this checkpoint makes: previously a
    # merchant's own admin_chat_id would hijack this exact message as an
    # admin command (see the old webhook.py admin branch, now removed).
    # Now there is no such branch - messaging the tenant bot is just being
    # a customer, even for a telegram_user_id that is also a registered
    # MerchantAdmin (admin interaction now only happens via the platform
    # bot, a different Telegram bot entirely).
    routed_session.add(MerchantAdmin(merchant_id=routed_merchant.id, telegram_user_id=900005))
    routed_session.flush()

    response = client.post(
        f"/telegram/webhook/tenant/{routed_merchant.webhook_slug}",
        json=_text_update(5, 900005, 900005, "/release 12345"),
        headers={"X-Telegram-Bot-Api-Secret-Token": routed_merchant.webhook_secret},
    )
    assert response.status_code == 200

    customer = (
        routed_session.query(Customer)
        .filter_by(merchant_id=routed_merchant.id, telegram_user_id=900005)
        .first()
    )
    # Got created as a customer record - proof the message went through
    # the normal pipeline, not handle_admin_command.
    assert customer is not None
