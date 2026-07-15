from app.faq.retrieval import match_faq, select_reply_text
from app.nlp.transliteration import normalize
from tests.conftest import make_faq


def test_no_faqs_returns_none(db_session, test_merchant):
    assert match_faq(db_session, test_merchant.id, "narxi qancha", "uz") is None


def test_cross_lingual_match_russian_query_finds_uzbek_authored_faq(db_session, test_merchant):
    # The FAQ is authored in Uzbek; a Russian-language customer query about
    # the same topic should still match it - this is the whole point of
    # using BGE-M3 (see aqlchat-phase1-plan memory for the model check).
    make_faq(
        db_session,
        test_merchant.id,
        question="Yetkazib berish qancha vaqt oladi?",
        responses={"uz": "1-3 ish kuni.", "ru": "1-3 рабочих дня."},
    )
    result = normalize("Сколько времени занимает доставка?")
    match = match_faq(db_session, test_merchant.id, result.normalized_text, result.detected_language)

    assert match is not None
    assert match.reply_text == "1-3 рабочих дня."


def test_cross_script_match_cyrillic_uzbek_query(db_session, test_merchant):
    make_faq(
        db_session,
        test_merchant.id,
        question="Toʻlovni qanday amalga oshiraman?",
        responses={"uz": "Payme yoki Click orqali.", "ru": "Через Payme или Click."},
    )
    result = normalize("Тўловни қандай амалга оширaман?")
    match = match_faq(db_session, test_merchant.id, result.normalized_text, result.detected_language)

    assert match is not None
    assert match.reply_text == "Payme yoki Click orqali."


def test_short_capitalized_fragment_matches_longer_faq(db_session, test_merchant):
    # Phone keyboards auto-capitalize the first letter, and normalize()
    # preserves case through transliteration ("Нархи қанча?" -> "Narxi
    # qancha?"). Pre-lowercasing (see app/nlp/embeddings.py) this exact
    # pair scored 0.583 - far below MATCH_THRESHOLD - purely because of
    # the leading capital; post-fix it scores 0.893. Guards the most
    # common real-traffic shape there is: a terse capitalized price
    # question.
    make_faq(
        db_session,
        test_merchant.id,
        question="Bu mahsulotning narxi qancha?",
        responses={"uz": "Narxi 10 ming so'm.", "ru": "Цена 10 тысяч сум."},
    )
    result = normalize("Нархи қанча?")
    match = match_faq(db_session, test_merchant.id, result.normalized_text, result.detected_language)

    assert match is not None
    assert match.reply_text == "Narxi 10 ming so'm."


def test_unrelated_query_does_not_match(db_session, test_merchant):
    make_faq(
        db_session,
        test_merchant.id,
        question="Yetkazib berish qancha vaqt oladi?",
        responses={"uz": "1-3 ish kuni.", "ru": "1-3 рабочих дня."},
    )
    result = normalize("Ish soatlaringiz qanday?")  # "what are your working hours" - unrelated topic
    match = match_faq(db_session, test_merchant.id, result.normalized_text, result.detected_language)

    assert match is None


def test_matches_are_scoped_to_merchant(db_session, test_merchant):
    from app.db.models import Merchant

    other_merchant = Merchant(
        name="other-merchant-faq",
        telegram_bot_token="other-token-faq-xyz",
        webhook_secret="other-secret",
    )
    db_session.add(other_merchant)
    db_session.flush()

    make_faq(
        db_session,
        other_merchant.id,
        question="Yetkazib berish qancha vaqt oladi?",
        responses={"uz": "Boshqa merchant javobi.", "ru": "..."},
    )

    match = match_faq(db_session, test_merchant.id, "yetkazib berish qancha vaqt oladi", "uz")
    assert match is None


def test_select_reply_text_exact_language_key():
    assert select_reply_text({"uz": "salom", "ru": "privet"}, "ru") == "privet"


def test_select_reply_text_falls_back_when_language_missing():
    # detected_language "mixed"/"unknown" has no matching key - falls back
    # to "uz" first per _LANGUAGE_FALLBACK_ORDER.
    assert select_reply_text({"uz": "salom", "ru": "privet"}, "mixed") == "salom"


def test_select_reply_text_falls_back_to_only_available_language():
    assert select_reply_text({"ru": "privet"}, "uz") == "privet"
