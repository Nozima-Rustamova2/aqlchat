from app.db.models import Product
from app.intent.classifier import IntentMatch
from app.intent.router import route_intent
from tests.conftest import make_flow


def _make_product(db_session, merchant_id, name: str, price=None, currency=None, description=None) -> Product:
    product = Product(
        merchant_id=merchant_id, name=name, price=price, currency=currency, description=description
    )
    db_session.add(product)
    db_session.flush()
    return product


def test_greeting_reuses_merchants_flow_reply(db_session, test_merchant):
    make_flow(db_session, test_merchant.id, "greeting", ["salom"], "Assalomu alaykum! Xush kelibsiz.")
    routed = route_intent(db_session, test_merchant.id, IntentMatch("greeting", 0.9), "xayrli kun")
    assert routed is not None
    assert routed.reply_text == "Assalomu alaykum! Xush kelibsiz."


def test_canned_intent_with_no_matching_flow_returns_none(db_session, test_merchant):
    routed = route_intent(db_session, test_merchant.id, IntentMatch("thanks", 0.9), "rahmat sizga")
    assert routed is None


def test_complaint_falls_back_to_human_handoff_flow(db_session, test_merchant):
    make_flow(db_session, test_merchant.id, "human_handoff", ["operator"], "Operatorga ulanmoqdamiz.")
    routed = route_intent(db_session, test_merchant.id, IntentMatch("complaint", 0.9), "mahsulot singan keldi")
    assert routed is not None
    assert routed.reply_text == "Operatorga ulanmoqdamiz."


def test_complaint_prefers_its_own_flow_over_human_handoff(db_session, test_merchant):
    make_flow(db_session, test_merchant.id, "human_handoff", ["operator"], "Operatorga ulanmoqdamiz.")
    make_flow(db_session, test_merchant.id, "complaint", ["shikoyat"], "Uzr so'raymiz, tez orada javob beramiz.")
    routed = route_intent(db_session, test_merchant.id, IntentMatch("complaint", 0.9), "mahsulot singan keldi")
    assert routed is not None
    assert routed.reply_text == "Uzr so'raymiz, tez orada javob beramiz."


def test_product_inquiry_single_match_returns_structured_reply(db_session, test_merchant):
    _make_product(db_session, test_merchant.id, "krossovka", price=250000, currency="UZS", description="Sport krossovkasi")
    routed = route_intent(
        db_session, test_merchant.id, IntentMatch("product_inquiry", 0.9), "krossovka haqida gapirib bering"
    )
    assert routed is not None
    assert "krossovka" in routed.reply_text
    assert "250000" in routed.reply_text


def test_product_inquiry_no_match_returns_none(db_session, test_merchant):
    _make_product(db_session, test_merchant.id, "krossovka", price=250000, currency="UZS")
    routed = route_intent(db_session, test_merchant.id, IntentMatch("product_inquiry", 0.9), "sumka bormi")
    assert routed is None


def test_product_inquiry_ambiguous_multiple_matches_returns_none(db_session, test_merchant):
    # Two distinct products both named as substrings of the same message -
    # genuinely ambiguous, should not guess (same "don't force it" rule as
    # the FAQ/photo-match confidence thresholds).
    _make_product(db_session, test_merchant.id, "krossovka", price=250000, currency="UZS")
    _make_product(db_session, test_merchant.id, "sumka", price=150000, currency="UZS")
    routed = route_intent(
        db_session, test_merchant.id, IntentMatch("product_inquiry", 0.9), "krossovka va sumka narxi qancha"
    )
    assert routed is None


def test_routing_scoped_to_merchant(db_session, test_merchant):
    from app.db.models import Merchant

    other_merchant = Merchant(
        name="other-merchant-intent",
        telegram_bot_token="other-token-intent-xyz",
        webhook_secret="other-secret",
    )
    db_session.add(other_merchant)
    db_session.flush()

    make_flow(db_session, other_merchant.id, "greeting", ["salom"], "Boshqa merchant javobi")

    routed = route_intent(db_session, test_merchant.id, IntentMatch("greeting", 0.9), "xayrli kun")
    assert routed is None
