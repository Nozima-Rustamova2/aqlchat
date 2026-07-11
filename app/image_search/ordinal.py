"""Resolves ordinal references to the carousel just shown (e.g.
"ikkinchisi narxi qancha?" - "how much is the second one?") against
conversation.context["last_candidates"], written by
app/image_search/carousel.py. Small, closed lexicon - not
embedding-based, since ordinal words are a fixed, tiny vocabulary in each
language, not open-ended text needing semantic matching.

Deliberately does NOT match bare digits ("1", "2", "3") - a message like
"2 dona kerak" (need 2 pieces) is a quantity, not a reference to the
second carousel item, and there's no similarity score here to gate a
wrong guess the way FAQ/intent matching can. Only explicit ordinal word
forms count as a match.

Known simplification, not handled: no expiry on last_candidates - a
reference to "the second one" days after the carousel was shown (and no
newer photo search has overwritten it) would still resolve. Acceptable
for v1; revisit if pilot data shows this misfires in practice.
"""

import re
import uuid
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.db.models import Conversation, Product

_ORDINAL_WORDS: dict[str, int] = {
    # Uzbek Latin
    "birinchi": 1,
    "birinchisi": 1,
    "1-chi": 1,
    "ikkinchi": 2,
    "ikkinchisi": 2,
    "2-chi": 2,
    "uchinchi": 3,
    "uchinchisi": 3,
    "3-chi": 3,
    # Russian
    "первый": 1,
    "первая": 1,
    "первое": 1,
    "второй": 2,
    "вторая": 2,
    "второе": 2,
    "третий": 3,
    "третья": 3,
    "третье": 3,
    # English - customers do sometimes type it
    "first": 1,
    "second": 2,
    "third": 3,
}


@dataclass
class OrdinalMatch:
    product: Product
    rank: int


def resolve(db: Session, conversation: Conversation, normalized_text: str) -> OrdinalMatch | None:
    context = conversation.context or {}
    last_candidates = context.get("last_candidates")
    if not last_candidates:
        return None

    rank = _extract_rank(normalized_text)
    if rank is None:
        return None

    entry = next((c for c in last_candidates if c["rank"] == rank), None)
    if entry is None:
        return None

    product = db.get(Product, uuid.UUID(entry["product_id"]))
    if product is None:
        return None

    return OrdinalMatch(product=product, rank=rank)


def _extract_rank(normalized_text: str) -> int | None:
    tokens = re.findall(r"[\w'-]+", normalized_text.lower())
    for token in tokens:
        if token in _ORDINAL_WORDS:
            return _ORDINAL_WORDS[token]
    return None
