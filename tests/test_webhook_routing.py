"""Webhook requests go through the real FastAPI route, which calls
db.commit() for real inside app/telegram/webhook.py - unlike every other
test in this suite, which calls service functions directly against a
session that's only ever flushed and rolled back. The plain db_session
fixture's rollback-only teardown can't contain a real commit, so this
file wraps each test in its own connection + SAVEPOINT: the webhook's
internal commit() only releases the savepoint, and the outer transaction
rollback at teardown undoes everything for real. Standard SQLAlchemy
"join a session into an external transaction" pattern - see
https://docs.sqlalchemy.org/en/20/orm/session_transaction.html#joining-a-session-into-an-external-transaction-such-as-for-test-suites
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlalchemy.orm import Session

from app.db.models import Customer, Merchant, MerchantAdmin
from app.db.session import engine, get_db
from app.main import app


@pytest.fixture
def routed_session():
    connection = engine.connect()
    trans = connection.begin()
    session = Session(bind=connection)
    session.begin_nested()

    @event.listens_for(session, "after_transaction_end")
    def _restart_savepoint(sess, transaction):
        if transaction.nested and not transaction._parent.nested:
            sess.begin_nested()

    def _override_get_db():
        yield session

    app.dependency_overrides[get_db] = _override_get_db
    try:
        yield session
    finally:
        app.dependency_overrides.pop(get_db, None)
        session.close()
        trans.rollback()
        connection.close()


@pytest.fixture
def routed_merchant(routed_session) -> Merchant:
    merchant = Merchant(
        name="webhook-routing-test-merchant",
        telegram_bot_token=f"pytest-webhook-{id(object())}",
        webhook_secret="pytest-webhook-secret",
    )
    routed_session.add(merchant)
    routed_session.flush()
    return merchant


@pytest.fixture
def client(routed_session) -> TestClient:
    return TestClient(app)


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
