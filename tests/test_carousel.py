from app.db.models import Conversation, Customer, ImageMatchLog
from app.image_search.carousel import handle_callback_query
from tests.conftest import make_product


def _make_conversation(db_session, merchant_id, telegram_user_id: int) -> tuple[Customer, Conversation]:
    customer = Customer(merchant_id=merchant_id, telegram_user_id=telegram_user_id)
    db_session.add(customer)
    db_session.flush()
    conversation = Conversation(merchant_id=merchant_id, customer_id=customer.id)
    db_session.add(conversation)
    db_session.flush()
    return customer, conversation


def _make_log(db_session, merchant_id, conversation_id, top_candidates) -> ImageMatchLog:
    log = ImageMatchLog(
        merchant_id=merchant_id,
        conversation_id=conversation_id,
        top_candidates=top_candidates,
        floor_applied=False,
    )
    db_session.add(log)
    db_session.flush()
    return log


def test_confirm_writes_log_and_context(db_session, test_merchant):
    customer, conversation = _make_conversation(db_session, test_merchant.id, 700001)
    product = make_product(
        db_session, test_merchant.id, "Test Product", [0.0] * 768, price=100000, currency="UZS"
    )
    log = _make_log(db_session, test_merchant.id, conversation.id, [{"product_id": str(product.id), "similarity": 0.9}])

    data = f"confirm:{log.id}:{product.id}"
    answer_text = handle_callback_query(db_session, test_merchant, customer, data)

    assert answer_text == "Rahmat!"
    assert log.confirmed_product_id == product.id
    assert conversation.context["last_matched_product_id"] == str(product.id)


def test_confirm_reply_includes_price(db_session, test_merchant):
    customer, conversation = _make_conversation(db_session, test_merchant.id, 700002)
    product = make_product(
        db_session, test_merchant.id, "Krossovka", [0.0] * 768, price=250000, currency="UZS", description="Sport"
    )
    log = _make_log(db_session, test_merchant.id, conversation.id, [{"product_id": str(product.id), "similarity": 0.9}])

    handle_callback_query(db_session, test_merchant, customer, f"confirm:{log.id}:{product.id}")
    db_session.flush()

    outbound = [m for m in conversation.messages if m.direction == "out"]
    assert len(outbound) == 1
    assert "Krossovka" in outbound[0].raw_text
    assert "250000" in outbound[0].raw_text
    assert outbound[0].response_source == "image"


def test_none_tapped_sets_flag_and_escalates(db_session, test_merchant):
    customer, conversation = _make_conversation(db_session, test_merchant.id, 700003)
    log = _make_log(db_session, test_merchant.id, conversation.id, [])

    answer_text = handle_callback_query(db_session, test_merchant, customer, f"none:{log.id}")

    assert answer_text == "Tushunarli."
    assert log.none_tapped is True
    assert conversation.needs_human is True


def test_unrecognized_data_returns_none(db_session, test_merchant):
    customer, conversation = _make_conversation(db_session, test_merchant.id, 700004)
    assert handle_callback_query(db_session, test_merchant, customer, "garbage_payload") is None


def test_confirm_with_stale_log_id_is_handled_gracefully(db_session, test_merchant):
    import uuid

    customer, _ = _make_conversation(db_session, test_merchant.id, 700005)
    fake_log_id = uuid.uuid4()
    fake_product_id = uuid.uuid4()

    answer_text = handle_callback_query(db_session, test_merchant, customer, f"confirm:{fake_log_id}:{fake_product_id}")
    assert answer_text is not None  # graceful "expired" message, not a crash
