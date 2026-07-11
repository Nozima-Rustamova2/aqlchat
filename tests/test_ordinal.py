from app.db.models import Conversation, Customer
from app.image_search import ordinal
from tests.conftest import make_product


def _make_conversation_with_candidates(db_session, merchant_id, products) -> Conversation:
    customer = Customer(merchant_id=merchant_id, telegram_user_id=900000 + len(products))
    db_session.add(customer)
    db_session.flush()
    conversation = Conversation(
        merchant_id=merchant_id,
        customer_id=customer.id,
        context={"last_candidates": [{"product_id": str(p.id), "rank": i} for i, p in enumerate(products, start=1)]},
    )
    db_session.add(conversation)
    db_session.flush()
    return conversation


def _three_products(db_session, merchant_id):
    dim = [0.0] * 768
    return [
        make_product(db_session, merchant_id, f"product {i}", dim.copy())
        for i in range(3)
    ]


def test_resolves_uzbek_ordinal(db_session, test_merchant):
    products = _three_products(db_session, test_merchant.id)
    conversation = _make_conversation_with_candidates(db_session, test_merchant.id, products)

    match = ordinal.resolve(db_session, conversation, "ikkinchisi narxi qancha")
    assert match is not None
    assert match.rank == 2
    assert match.product.id == products[1].id


def test_resolves_russian_ordinal(db_session, test_merchant):
    products = _three_products(db_session, test_merchant.id)
    conversation = _make_conversation_with_candidates(db_session, test_merchant.id, products)

    match = ordinal.resolve(db_session, conversation, "сколько стоит первый")
    assert match is not None
    assert match.rank == 1
    assert match.product.id == products[0].id


def test_resolves_english_ordinal(db_session, test_merchant):
    products = _three_products(db_session, test_merchant.id)
    conversation = _make_conversation_with_candidates(db_session, test_merchant.id, products)

    match = ordinal.resolve(db_session, conversation, "how much is the third one")
    assert match is not None
    assert match.rank == 3


def test_no_candidates_in_context_returns_none(db_session, test_merchant):
    customer = Customer(merchant_id=test_merchant.id, telegram_user_id=123456)
    db_session.add(customer)
    db_session.flush()
    conversation = Conversation(merchant_id=test_merchant.id, customer_id=customer.id, context=None)
    db_session.add(conversation)
    db_session.flush()

    assert ordinal.resolve(db_session, conversation, "ikkinchisi narxi qancha") is None


def test_non_ordinal_text_returns_none(db_session, test_merchant):
    products = _three_products(db_session, test_merchant.id)
    conversation = _make_conversation_with_candidates(db_session, test_merchant.id, products)

    assert ordinal.resolve(db_session, conversation, "yetkazib berish qancha turadi") is None


def test_bare_digit_does_not_match():
    # Deliberately not matched - "2 dona kerak" is a quantity, not an
    # ordinal reference, and there's no confidence score here to gate a
    # wrong guess.
    assert ordinal._extract_rank("2 dona kerak") is None
