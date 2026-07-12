import pytest

from app.nlp.price_parsing import parse_price


@pytest.mark.parametrize(
    "text,expected",
    [
        ("narxi: 250000", (250000.0, "UZS")),
        ("Narxi: 250 000 so'm", (250000.0, "UZS")),
        ("цена: 150000", (150000.0, "UZS")),
        ("price: 99000", (99000.0, "UZS")),
        ("Krossovka, 250 000 so'm, 42-razmer", (250000.0, "UZS")),
        ("Krossovka 150,000 sum", (150000.0, "UZS")),
        ("250k narxida", (250000.0, "UZS")),
        ("Kurtka - 1.5k", (1500.0, "UZS")),
        ("300 ming so'mga sotiladi", (300000.0, "UZS")),
        ("boots\nprice: 40$", (40.0, "USD")),
        ("Jacket - 40 USD", None),  # no label, no thousands separator - no pattern matches "40" at all
        ("price: 40 usd", (40.0, "USD")),
        ("narxi: 40 (narx USD da ko'rsatilgan)", (40.0, "USD")),  # USD mentioned later in the caption, not right after the number
        ("Kurtka. Narxi shaxsiyda.", None),
        ("Yangi kolleksiya keldi!", None),
        ("Buyurtma uchun: +998 90 123 45 67", None),
        ("Tel: 998901234567, savol bering", None),
        ("Bog'lanish uchun 90 123 45 67", None),
    ],
)
def test_parse_price(text, expected):
    assert parse_price(text) == expected


def test_labeled_price_wins_over_an_unrelated_number_elsewhere():
    # A size ("42-razmer") shouldn't be picked up as a price when an
    # explicit label is present.
    assert parse_price("42-razmer, narxi: 180000 so'm") == (180000.0, "UZS")


def test_dollar_sign_immediately_after_the_number_is_detected():
    assert parse_price("narxi: 100$") == (100.0, "USD")


def test_empty_string_returns_none():
    assert parse_price("") is None
