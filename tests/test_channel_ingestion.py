"""Passive channel_post/edited_channel_post ingestion and admin-backfill
forwarding (app/telegram/webhook.py) - part of the channel-as-catalog
pivot. Wired directly into receive_update, which commits for real, so
these use the routed_session/routed_merchant/client fixtures (see
tests/conftest.py) rather than the plain db_session fixture.
"""

from app.db.models import MerchantAdmin, PendingAdminReply, Product


def _channel_post_update(update_id: int, chat_id: int, message_id: int, text=None, chat_title=None) -> dict:
    return {
        "update_id": update_id,
        "channel_post": {
            "message_id": message_id,
            "date": 0,
            "chat": {"id": chat_id, "type": "channel", "title": chat_title},
            "text": text,
        },
    }


def test_first_channel_post_registers_the_channel_reactively(client, routed_session, routed_merchant):
    assert routed_merchant.source_channel_id is None

    response = client.post(
        f"/telegram/webhook/tenant/{routed_merchant.webhook_slug}",
        json=_channel_post_update(1, -1002000000001, 10, text="Krossovka\nnarxi: 250000 so'm", chat_title="Do'kon"),
        headers={"X-Telegram-Bot-Api-Secret-Token": routed_merchant.webhook_secret},
    )
    assert response.status_code == 200
    assert routed_merchant.source_channel_id == -1002000000001
    assert routed_merchant.source_channel_title == "Do'kon"

    product = (
        routed_session.query(Product)
        .filter_by(merchant_id=routed_merchant.id, source_channel_id=-1002000000001, source_message_id=10)
        .first()
    )
    assert product is not None
    assert product.name == "Krossovka"
    assert product.price == 250000
    assert product.price_status == "set"


def test_channel_post_from_a_different_channel_is_ignored(client, routed_session, routed_merchant):
    routed_merchant.source_channel_id = -1002000000002
    routed_session.add(routed_merchant)
    routed_session.flush()

    response = client.post(
        f"/telegram/webhook/tenant/{routed_merchant.webhook_slug}",
        json=_channel_post_update(2, -1002000000099, 11, text="Boshqa kanal posti"),
        headers={"X-Telegram-Bot-Api-Secret-Token": routed_merchant.webhook_secret},
    )
    assert response.status_code == 200

    product = routed_session.query(Product).filter_by(source_channel_id=-1002000000099).first()
    assert product is None


def test_channel_post_without_price_queues_a_price_query(client, routed_session, routed_merchant):
    admin = MerchantAdmin(merchant_id=routed_merchant.id, telegram_user_id=900601)
    routed_session.add(admin)
    routed_session.flush()

    response = client.post(
        f"/telegram/webhook/tenant/{routed_merchant.webhook_slug}",
        json=_channel_post_update(3, -1002000000003, 12, text="Yangi kurtka, narxi shaxsiyda"),
        headers={"X-Telegram-Bot-Api-Secret-Token": routed_merchant.webhook_secret},
    )
    assert response.status_code == 200

    product = (
        routed_session.query(Product)
        .filter_by(merchant_id=routed_merchant.id, source_channel_id=-1002000000003, source_message_id=12)
        .first()
    )
    assert product is not None
    assert product.price_status == "missing"

    pending = (
        routed_session.query(PendingAdminReply)
        .filter_by(merchant_admin_id=admin.id, kind="price_query", target_id=product.id)
        .first()
    )
    assert pending is not None


def test_edited_channel_post_updates_the_existing_product(client, routed_session, routed_merchant):
    routed_merchant.source_channel_id = -1002000000004
    routed_session.add(routed_merchant)
    routed_session.flush()

    first = client.post(
        f"/telegram/webhook/tenant/{routed_merchant.webhook_slug}",
        json=_channel_post_update(4, -1002000000004, 13, text="Ko'ylak, narxi shaxsiyda"),
        headers={"X-Telegram-Bot-Api-Secret-Token": routed_merchant.webhook_secret},
    )
    assert first.status_code == 200

    edited = {
        "update_id": 5,
        "edited_channel_post": {
            "message_id": 13,
            "date": 0,
            "chat": {"id": -1002000000004, "type": "channel"},
            "text": "Ko'ylak, narxi: 120000 so'm",
        },
    }
    second = client.post(
        f"/telegram/webhook/tenant/{routed_merchant.webhook_slug}",
        json=edited,
        headers={"X-Telegram-Bot-Api-Secret-Token": routed_merchant.webhook_secret},
    )
    assert second.status_code == 200

    products = (
        routed_session.query(Product)
        .filter_by(merchant_id=routed_merchant.id, source_channel_id=-1002000000004, source_message_id=13)
        .all()
    )
    assert len(products) == 1
    assert products[0].price == 120000
    assert products[0].price_status == "set"


def _admin_forward_update(update_id: int, admin_user_id: int, forward_chat_id: int, forward_message_id: int, text=None) -> dict:
    return {
        "update_id": update_id,
        "message": {
            "message_id": update_id,
            "date": 0,
            "chat": {"id": admin_user_id, "type": "private"},
            "from": {"id": admin_user_id, "is_bot": False, "first_name": "Admin"},
            "text": text,
            "forward_origin": {
                "type": "channel",
                "chat": {"id": forward_chat_id, "type": "channel", "title": "Eski kanal"},
                "message_id": forward_message_id,
            },
        },
    }


def test_admin_backfill_forward_ingests_silently_without_creating_a_customer(client, routed_session, routed_merchant):
    admin = MerchantAdmin(merchant_id=routed_merchant.id, telegram_user_id=900701)
    routed_session.add(admin)
    routed_session.flush()

    response = client.post(
        f"/telegram/webhook/tenant/{routed_merchant.webhook_slug}",
        json=_admin_forward_update(6, 900701, -1002000000005, 20, text="Eski mahsulot, narxi: 90000 so'm"),
        headers={"X-Telegram-Bot-Api-Secret-Token": routed_merchant.webhook_secret},
    )
    assert response.status_code == 200

    product = (
        routed_session.query(Product)
        .filter_by(merchant_id=routed_merchant.id, source_channel_id=-1002000000005, source_message_id=20)
        .first()
    )
    assert product is not None
    assert product.price == 90000

    from app.db.models import Customer

    customer = routed_session.query(Customer).filter_by(telegram_user_id=900701).first()
    assert customer is None

    assert routed_merchant.source_channel_id == -1002000000005
    assert routed_merchant.source_channel_title == "Eski kanal"


def test_non_admin_forward_is_not_treated_as_backfill(client, routed_session, routed_merchant):
    # Same forward, but from a telegram_user_id that isn't a
    # MerchantAdmin - this must go through the normal customer
    # forward-match path (or fall through), never the silent backfill
    # path, and DOES create a Customer.
    response = client.post(
        f"/telegram/webhook/tenant/{routed_merchant.webhook_slug}",
        json=_admin_forward_update(7, 900702, -1002000000006, 21, text="hello"),
        headers={"X-Telegram-Bot-Api-Secret-Token": routed_merchant.webhook_secret},
    )
    assert response.status_code == 200

    from app.db.models import Customer

    customer = routed_session.query(Customer).filter_by(telegram_user_id=900702).first()
    assert customer is not None
