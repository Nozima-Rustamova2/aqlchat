"""Forward-match resolution - the highest-priority product-resolution
layer in the customer pipeline (app/telegram/webhook.py). Wired directly
into receive_update, which commits for real, so these use the
routed_session/routed_merchant/client fixtures (tests/conftest.py)
rather than the plain db_session fixture. Text-only forwards throughout
(no photo) to keep these fast and network-free - photo download/embed is
already covered by the existing image-search test suite.
"""

from app.db.models import Merchant, MerchantAdmin, Message, PendingAdminReply, Product


def _forward_update(update_id: int, user_id: int, forward_chat_id: int, forward_message_id: int, text=None) -> dict:
    return {
        "update_id": update_id,
        "message": {
            "message_id": update_id,
            "date": 0,
            "chat": {"id": user_id, "type": "private"},
            "from": {"id": user_id, "is_bot": False, "first_name": "Customer"},
            "text": text,
            "forward_origin": {
                "type": "channel",
                "chat": {"id": forward_chat_id, "type": "channel"},
                "message_id": forward_message_id,
            },
        },
    }


def test_forward_match_resolves_known_product(client, routed_session, routed_merchant):
    product = Product(
        merchant_id=routed_merchant.id,
        name="Krossovka",
        price=250000,
        currency="UZS",
        price_status="set",
        source_channel_id=-1001111111111,
        source_message_id=42,
    )
    routed_session.add(product)
    routed_session.flush()

    response = client.post(
        f"/telegram/webhook/tenant/{routed_merchant.webhook_slug}",
        json=_forward_update(1, 900101, -1001111111111, 42),
        headers={"X-Telegram-Bot-Api-Secret-Token": routed_merchant.webhook_secret},
    )
    assert response.status_code == 200

    # Both the inbound (customer's forward) and outbound (our reply) rows
    # carry response_source="forward_match" - same convention as every
    # other response path in this pipeline.
    outbound = routed_session.query(Message).filter_by(response_source="forward_match", direction="out").all()
    assert len(outbound) == 1
    assert "Krossovka" in outbound[0].raw_text
    assert "250000" in outbound[0].raw_text


def test_forward_match_legacy_fields_also_work(client, routed_session, routed_merchant):
    # Bot API 7.0 deprecated forward_from_chat/forward_from_message_id in
    # favor of forward_origin, but both may still be populated depending
    # on API version - this confirms the fallback path.
    product = Product(
        merchant_id=routed_merchant.id,
        name="Ko'ylak",
        price=100000,
        currency="UZS",
        price_status="set",
        source_channel_id=-1001111111112,
        source_message_id=43,
    )
    routed_session.add(product)
    routed_session.flush()

    payload = {
        "update_id": 2,
        "message": {
            "message_id": 2,
            "date": 0,
            "chat": {"id": 900102, "type": "private"},
            "from": {"id": 900102, "is_bot": False, "first_name": "Customer"},
            "forward_from_chat": {"id": -1001111111112, "type": "channel"},
            "forward_from_message_id": 43,
        },
    }
    response = client.post(
        f"/telegram/webhook/tenant/{routed_merchant.webhook_slug}",
        json=payload,
        headers={"X-Telegram-Bot-Api-Secret-Token": routed_merchant.webhook_secret},
    )
    assert response.status_code == 200

    outbound = routed_session.query(Message).filter_by(response_source="forward_match", direction="out").all()
    assert len(outbound) == 1


def test_forward_match_price_pending_queues_price_query(client, routed_session, routed_merchant):
    admin = MerchantAdmin(merchant_id=routed_merchant.id, telegram_user_id=900201)
    routed_session.add(admin)
    product = Product(
        merchant_id=routed_merchant.id,
        name="Sumka",
        price_status="missing",
        source_channel_id=-1001111111113,
        source_message_id=44,
    )
    routed_session.add(product)
    routed_session.flush()

    response = client.post(
        f"/telegram/webhook/tenant/{routed_merchant.webhook_slug}",
        json=_forward_update(3, 900202, -1001111111113, 44),
        headers={"X-Telegram-Bot-Api-Secret-Token": routed_merchant.webhook_secret},
    )
    assert response.status_code == 200

    pending = (
        routed_session.query(PendingAdminReply)
        .filter_by(merchant_admin_id=admin.id, kind="price_query", target_id=product.id)
        .first()
    )
    assert pending is not None


def test_forward_match_lazy_ingests_from_own_channel(client, routed_session, routed_merchant):
    routed_merchant.source_channel_id = -1001111111114
    routed_session.add(routed_merchant)
    routed_session.flush()

    response = client.post(
        f"/telegram/webhook/tenant/{routed_merchant.webhook_slug}",
        json=_forward_update(4, 900301, -1001111111114, 55, text="Kepka\nnarxi: 80000 so'm"),
        headers={"X-Telegram-Bot-Api-Secret-Token": routed_merchant.webhook_secret},
    )
    assert response.status_code == 200

    product = (
        routed_session.query(Product)
        .filter_by(merchant_id=routed_merchant.id, source_channel_id=-1001111111114, source_message_id=55)
        .first()
    )
    assert product is not None
    assert product.name == "Kepka"
    assert product.price == 80000
    assert product.price_status == "set"


def test_forward_from_unrelated_channel_falls_through(client, routed_session, routed_merchant):
    # Not the merchant's own registered channel, and no matching product
    # already exists - forward-match must not create anything here, it
    # should fall through to the normal pipeline (image search etc.).
    routed_merchant.source_channel_id = -1001111111115
    routed_session.add(routed_merchant)
    routed_session.flush()

    response = client.post(
        f"/telegram/webhook/tenant/{routed_merchant.webhook_slug}",
        json=_forward_update(5, 900401, -1009999999999, 99, text="hello"),
        headers={"X-Telegram-Bot-Api-Secret-Token": routed_merchant.webhook_secret},
    )
    assert response.status_code == 200

    product = routed_session.query(Product).filter_by(source_channel_id=-1009999999999).first()
    assert product is None

    forward_matched = routed_session.query(Message).filter_by(response_source="forward_match").all()
    assert len(forward_matched) == 0


def test_forward_match_is_isolated_per_merchant(client, routed_session, routed_merchant):
    # A second merchant has a product at the exact same (channel,
    # message) coordinates - resolving for routed_merchant must not leak
    # into that other merchant's catalog.
    other_merchant = Merchant(
        name="Other Merchant",
        telegram_bot_token=f"other-{id(object())}",
        webhook_secret="other-secret",
    )
    routed_session.add(other_merchant)
    routed_session.flush()
    routed_session.add(
        Product(
            merchant_id=other_merchant.id,
            name="Boshqa mahsulot",
            price=999999,
            price_status="set",
            source_channel_id=-1001111111116,
            source_message_id=77,
        )
    )
    routed_session.flush()

    response = client.post(
        f"/telegram/webhook/tenant/{routed_merchant.webhook_slug}",
        json=_forward_update(6, 900501, -1001111111116, 77, text="salom"),
        headers={"X-Telegram-Bot-Api-Secret-Token": routed_merchant.webhook_secret},
    )
    assert response.status_code == 200

    forward_matched = routed_session.query(Message).filter_by(response_source="forward_match").all()
    assert len(forward_matched) == 0
