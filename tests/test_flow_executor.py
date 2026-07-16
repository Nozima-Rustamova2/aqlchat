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


def _make_ig_flow(db_session, merchant_id, name, keywords, media_ids=None):
    return make_flow(
        db_session,
        merchant_id,
        name,
        keywords,
        channel="instagram_comment",
        response_config={
            "link": "https://t.me/merchant_bot",
            "private_reply": {"uz": "Mana havola 👉 {link}", "ru": "Вот ссылка 👉 {link}"},
            "media_ids": media_ids or [],
        },
    )


def test_channels_are_isolated_instagram_rule_never_fires_on_telegram(db_session, test_merchant):
    # The channel filter is the tenant-behavior boundary between the two
    # rule sets: an IG comment rule for "narx" must not hijack Telegram
    # Layer-1, and a Telegram greeting must not fire on IG comments.
    _make_ig_flow(db_session, test_merchant.id, "ig_price", ["narx"])
    make_flow(db_session, test_merchant.id, "tg_greeting", ["salom"], "Salom!")

    assert match_flow(db_session, test_merchant.id, "narxi qancha") is None  # default channel = telegram
    assert match_flow(db_session, test_merchant.id, "salom", channel="instagram_comment") is None

    ig_match = match_flow(db_session, test_merchant.id, "narxi qancha", channel="instagram_comment")
    assert ig_match is not None
    assert ig_match.name == "ig_price"


def test_media_scoped_rule_only_fires_on_its_own_post(db_session, test_merchant):
    _make_ig_flow(db_session, test_merchant.id, "reel_promo", ["narx"], media_ids=["18001"])

    on_the_reel = match_flow(db_session, test_merchant.id, "narx", channel="instagram_comment", media_id="18001")
    assert on_the_reel is not None
    assert on_the_reel.name == "reel_promo"

    on_another_post = match_flow(db_session, test_merchant.id, "narx", channel="instagram_comment", media_id="18999")
    assert on_another_post is None


def test_post_scoped_rule_beats_account_wide_rule(db_session, test_merchant):
    # Even when the account-wide rule has the LONGER keyword: the merchant
    # pinned a rule to this post on purpose, so scope specificity
    # outranks keyword length.
    _make_ig_flow(db_session, test_merchant.id, "generic_link", ["narxi qancha"])
    _make_ig_flow(db_session, test_merchant.id, "reel_specific_link", ["narx"], media_ids=["18001"])

    result = match_flow(db_session, test_merchant.id, "narxi qancha?", channel="instagram_comment", media_id="18001")
    assert result is not None
    assert result.name == "reel_specific_link"


def test_account_wide_rule_covers_posts_no_scoped_rule_names(db_session, test_merchant):
    _make_ig_flow(db_session, test_merchant.id, "generic_link", ["narx"])
    _make_ig_flow(db_session, test_merchant.id, "reel_specific_link", ["narx"], media_ids=["18001"])

    result = match_flow(db_session, test_merchant.id, "narx", channel="instagram_comment", media_id="55555")
    assert result is not None
    assert result.name == "generic_link"
