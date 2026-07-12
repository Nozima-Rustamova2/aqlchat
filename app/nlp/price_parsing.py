"""Deterministic price extraction from Telegram channel post captions -
part of the channel-as-catalog pivot's forward-match/ingestion layer (see
app/products/ingestion.py). Pure regex, not an LLM call - this needs to
stay a cheap deterministic step, same invariant as forward-match itself
(see the pivot plan). Distinct from app/llm/extraction.py (checkpoint 7),
which is scoped to course descriptions/FAQ text, not channel captions.

Real merchant captions often deliberately omit price ("narxi shaxsiyda")
or bury it among unrelated numbers (a phone number, a size, a quantity) -
parse_price() returns None rather than guessing in that case; missing
price is a first-class product state (Product.price_status), not
something this module should paper over.
"""

import re

# Uzbek phone numbers, with or without a country code, in any of the
# common spacing/dash styles ("+998 90 123 45 67", "998901234567",
# "90 123 45 67") - masked out before price patterns run, so a caption
# like "buyurtma uchun: +998 90 123 45 67" doesn't false-positive a
# fragment of the phone number as a price.
_PHONE_LIKE = re.compile(r"(?:\+?998[\s\-]?)?\d{2}[\s\-]?\d{3}[\s\-]?\d{2}[\s\-]?\d{2}\b")

_PRICE_LABEL = r"(?:narx(?:i)?|цена|price)\s*[:\-]?\s*"
_CURRENCY_WORD = r"(?:so'm|so‘m|som|sum|сум|uzs)"

# An explicit label first - "narxi: 250000" is unambiguous even as a bare
# unformatted number, since nothing else in a caption would be labeled
# this way.
_PATTERN_LABELED = re.compile(rf"{_PRICE_LABEL}(?P<number>[\d\s.,']+)", re.IGNORECASE)
# A thousands-grouped number (with or without a trailing currency word) -
# requires at least one group separator, so a bare unlabeled number
# ("6 random digits") is never mistaken for a price.
_PATTERN_GROUPED = re.compile(
    rf"(?P<number>\d{{1,3}}(?:[\s.,']\d{{3}})+)\s*(?:{_CURRENCY_WORD})?", re.IGNORECASE
)
_PATTERN_K = re.compile(r"(?P<number>\d+(?:[.,]\d+)?)\s*k\b", re.IGNORECASE)
_PATTERN_MING = re.compile(r"(?P<number>\d+(?:[.,]\d+)?)\s*ming\b", re.IGNORECASE)


def _digits_only(number_str: str) -> str:
    return re.sub(r"[^\d]", "", number_str)


def parse_price(text: str) -> float | None:
    """Returns the first price found in `text`, or None if nothing looks
    like a price. Tried in priority order: an explicit "narxi:"-style
    label, then a thousands-grouped number, then "250k" / "250 ming"
    shorthand."""
    text = _PHONE_LIKE.sub(" ", text)

    match = _PATTERN_LABELED.search(text)
    if match is not None:
        digits = _digits_only(match.group("number"))
        if digits:
            value = float(digits)
            if value > 0:
                return value

    match = _PATTERN_GROUPED.search(text)
    if match is not None:
        digits = _digits_only(match.group("number"))
        if digits:
            value = float(digits)
            if value > 0:
                return value

    for pattern in (_PATTERN_K, _PATTERN_MING):
        match = pattern.search(text)
        if match is not None:
            try:
                value = float(match.group("number").replace(",", ".")) * 1000
            except ValueError:
                continue
            if value > 0:
                return value

    return None
