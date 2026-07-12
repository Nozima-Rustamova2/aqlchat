"""Post-link resolution (app/telegram/webhook.py) - customers pasting a
t.me link instead of forwarding, resolved the same deterministic way as
forward-match. Wired into receive_update, which commits for real, so
these use the routed_session/routed_merchant/client fixtures
(tests/conftest.py) rather than the plain db_session fixture.
"""

from app.db.models import Merchant, Message, Product


def _text_update(update_id: int, chat_id: int, user_id: int, text: str) -> dict:
    return {
        "update_id": update_id,
        "message": {
            "message_id": update_id,
            "date": 0,
            "chat": {"id": chat_id, "type": "private"},
            "from": {"id": user_id, "is_bot": False, "first_name": "Customer"},
            "text": text,
        },
    }


def test_post_link_username_form_resolves_known_product(client, routed_session, routed_merchant):
    routed_merchant.source_channel_id = -1002000000020
    routed_merchant.source_channel_username = "myshop"
    routed_session.add(routed_merchant)
    routed_session.add(
        Product(
            merchant_id=routed_merchant.id,
            name="Krossovka",
            price=250000,
            currency="UZS",
            price_status="set",
            source_channel_id=-1002000000020,
            source_message_id=55,
        )
    )
    routed_session.flush()

    response = client.post(
        f"/telegram/webhook/tenant/{routed_merchant.webhook_slug}",
        json=_text_update(1, 900801, 900801, "https://t.me/myshop/55 narxi qancha?"),
        headers={"X-Telegram-Bot-Api-Secret-Token": routed_merchant.webhook_secret},
    )
    assert response.status_code == 200

    outbound = routed_session.query(Message).filter_by(response_source="post_link_match", direction="out").first()
    assert outbound is not None
    assert "Krossovka" in outbound.raw_text


def test_post_link_username_is_case_insensitive(client, routed_session, routed_merchant):
    routed_merchant.source_channel_id = -1002000000021
    routed_merchant.source_channel_username = "MyShop"
    routed_session.add(routed_merchant)
    routed_session.add(
        Product(
            merchant_id=routed_merchant.id,
            name="Ko'ylak",
            price=100000,
            currency="UZS",
            price_status="set",
            source_channel_id=-1002000000021,
            source_message_id=56,
        )
    )
    routed_session.flush()

    response = client.post(
        f"/telegram/webhook/tenant/{routed_merchant.webhook_slug}",
        json=_text_update(2, 900802, 900802, "t.me/myshop/56"),
        headers={"X-Telegram-Bot-Api-Secret-Token": routed_merchant.webhook_secret},
    )
    assert response.status_code == 200
    outbound = routed_session.query(Message).filter_by(response_source="post_link_match", direction="out").first()
    assert outbound is not None


def test_post_link_internal_form_resolves_known_product(client, routed_session, routed_merchant):
    routed_merchant.source_channel_id = -1002000000030
    routed_session.add(routed_merchant)
    routed_session.add(
        Product(
            merchant_id=routed_merchant.id,
            name="Sumka",
            price=150000,
            currency="UZS",
            price_status="set",
            source_channel_id=-1002000000030,
            source_message_id=77,
        )
    )
    routed_session.flush()

    response = client.post(
        f"/telegram/webhook/tenant/{routed_merchant.webhook_slug}",
        json=_text_update(3, 900803, 900803, "https://t.me/c/2000000030/77"),
        headers={"X-Telegram-Bot-Api-Secret-Token": routed_merchant.webhook_secret},
    )
    assert response.status_code == 200
    outbound = routed_session.query(Message).filter_by(response_source="post_link_match", direction="out").first()
    assert outbound is not None
    assert "Sumka" in outbound.raw_text


def test_post_link_wrong_username_does_not_resolve(client, routed_session, routed_merchant):
    routed_merchant.source_channel_id = -1002000000040
    routed_merchant.source_channel_username = "myshop"
    routed_session.add(routed_merchant)
    routed_session.flush()

    response = client.post(
        f"/telegram/webhook/tenant/{routed_merchant.webhook_slug}",
        json=_text_update(4, 900804, 900804, "https://t.me/someothershop/1"),
        headers={"X-Telegram-Bot-Api-Secret-Token": routed_merchant.webhook_secret},
    )
    assert response.status_code == 200
    matched = routed_session.query(Message).filter_by(response_source="post_link_match").first()
    assert matched is None


def test_post_link_wrong_internal_id_does_not_resolve(client, routed_session, routed_merchant):
    routed_merchant.source_channel_id = -1002000000050
    routed_session.add(routed_merchant)
    routed_session.flush()

    response = client.post(
        f"/telegram/webhook/tenant/{routed_merchant.webhook_slug}",
        json=_text_update(5, 900805, 900805, "https://t.me/c/9999999999/1"),
        headers={"X-Telegram-Bot-Api-Secret-Token": routed_merchant.webhook_secret},
    )
    assert response.status_code == 200
    matched = routed_session.query(Message).filter_by(response_source="post_link_match").first()
    assert matched is None


def test_post_link_to_unknown_message_does_not_lazy_ingest(client, routed_session, routed_merchant):
    # This IS the merchant's own channel, but no product exists at this
    # message id, and a bare link carries no caption/photo to ingest
    # from - unlike a forward, this must never create a product.
    routed_merchant.source_channel_id = -1002000000060
    routed_merchant.source_channel_username = "myshop"
    routed_session.add(routed_merchant)
    routed_session.flush()

    response = client.post(
        f"/telegram/webhook/tenant/{routed_merchant.webhook_slug}",
        json=_text_update(6, 900806, 900806, "https://t.me/myshop/999"),
        headers={"X-Telegram-Bot-Api-Secret-Token": routed_merchant.webhook_secret},
    )
    assert response.status_code == 200

    product = (
        routed_session.query(Product)
        .filter_by(source_channel_id=-1002000000060, source_message_id=999)
        .first()
    )
    assert product is None


def test_post_link_is_isolated_per_merchant(client, routed_session, routed_merchant):
    other_merchant = Merchant(
        name="Other Merchant",
        telegram_bot_token=f"other-postlink-{id(object())}",
        webhook_secret="other-postlink-secret",
        source_channel_id=-1002000000070,
        source_channel_username="myshop",
    )
    routed_session.add(other_merchant)
    routed_session.flush()
    routed_session.add(
        Product(
            merchant_id=other_merchant.id,
            name="Boshqa mahsulot",
            price=999999,
            price_status="set",
            source_channel_id=-1002000000070,
            source_message_id=88,
        )
    )
    routed_session.flush()

    # routed_merchant has no channel registered - a link matching the
    # OTHER merchant's channel/username must not resolve here.
    response = client.post(
        f"/telegram/webhook/tenant/{routed_merchant.webhook_slug}",
        json=_text_update(7, 900807, 900807, "https://t.me/myshop/88"),
        headers={"X-Telegram-Bot-Api-Secret-Token": routed_merchant.webhook_secret},
    )
    assert response.status_code == 200
    matched = routed_session.query(Message).filter_by(response_source="post_link_match").first()
    assert matched is None


def test_no_link_falls_through_unchanged(client, routed_session, routed_merchant):
    response = client.post(
        f"/telegram/webhook/tenant/{routed_merchant.webhook_slug}",
        json=_text_update(8, 900808, 900808, "salom, yordam kerak"),
        headers={"X-Telegram-Bot-Api-Secret-Token": routed_merchant.webhook_secret},
    )
    assert response.status_code == 200
    matched = routed_session.query(Message).filter_by(response_source="post_link_match").first()
    assert matched is None
