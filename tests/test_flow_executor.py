from app.flows.executor import match_flow
from app.nlp.transliteration import normalize
from tests.conftest import make_flow


def test_no_flows_returns_none(db_session, test_merchant):
    assert match_flow(db_session, test_merchant.id, "salom") is None


def test_simple_keyword_match(db_session, test_merchant):
    make_flow(db_session, test_merchant.id, "greeting", ["salom", "assalomu alaykum"], "Salom!")
    result = match_flow(db_session, test_merchant.id, normalize("Salom, narxi qancha?").normalized_text)
    assert result is not None
    assert result.name == "greeting"


def test_no_match_returns_none(db_session, test_merchant):
    make_flow(db_session, test_merchant.id, "greeting", ["salom"], "Salom!")
    result = match_flow(db_session, test_merchant.id, "buyurtmam qachon keladi")
    assert result is None


def test_longest_keyword_wins_over_generic_one(db_session, test_merchant):
    # A merchant might define both a generic "narx" trigger and a more
    # specific "narxi qancha" one with a different response - the more
    # specific match should win regardless of which flow was created first.
    make_flow(db_session, test_merchant.id, "generic_price", ["narx"], "generic price answer")
    make_flow(db_session, test_merchant.id, "specific_price", ["narxi qancha"], "specific price answer")

    result = match_flow(db_session, test_merchant.id, "bu mahsulotning narxi qancha ekan")
    assert result is not None
    assert result.name == "specific_price"


def test_matching_is_case_insensitive(db_session, test_merchant):
    make_flow(db_session, test_merchant.id, "greeting", ["salom"], "Salom!")
    result = match_flow(db_session, test_merchant.id, "SALOM, qalaysiz?")
    assert result is not None
    assert result.name == "greeting"


def test_matches_are_scoped_to_merchant(db_session, test_merchant):
    from app.db.models import Merchant

    other_merchant = Merchant(
        name="other-merchant",
        telegram_bot_token="other-token-xyz",
        webhook_secret="other-secret",
    )
    db_session.add(other_merchant)
    db_session.flush()

    make_flow(db_session, other_merchant.id, "greeting", ["salom"], "Salom from other merchant!")

    result = match_flow(db_session, test_merchant.id, "salom")
    assert result is None


def test_cyrillic_russian_keyword_matches_after_normalization(db_session, test_merchant):
    # normalize() leaves genuine Russian in Cyrillic - flow keywords for
    # Russian-speaking customers must be authored in Cyrillic to match.
    make_flow(db_session, test_merchant.id, "greeting_ru", ["здравствуйте"], "Zdravstvuyte!")
    result = match_flow(db_session, test_merchant.id, normalize("Здравствуйте, сколько стоит?").normalized_text)
    assert result is not None
    assert result.name == "greeting_ru"
