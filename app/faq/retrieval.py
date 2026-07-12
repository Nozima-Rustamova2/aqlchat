"""Semantic FAQ retrieval - the second layer in the cost-minimizing pipeline
(rule triggers -> FAQ/intent embedding retrieval -> LLM fallback). Runs only
when the rule-based flow executor (app/flows/executor.py) doesn't match.

Matching is cross-lingual by design: a merchant writes one FAQ question in
whichever language they prefer, and BGE-M3 places semantically-equivalent
Uzbek Latin/Cyrillic/Russian queries close together in the same embedding
space (see app/nlp/embeddings.py and the aqlchat-phase1-plan memory for the
check that established this - 100% top-1 cross-lingual retrieval accuracy
on a hand-built paraphrase set). So each Faq row has exactly one embedding
of its `question`, not one per language.

The *reply text* is still per-language (`response_config`), since matching
working across languages doesn't mean the customer wants the answer back in
whatever language the merchant happened to author the question in - see
select_reply_text below.
"""

import uuid
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import Faq
from app.nlp.embeddings import embed_text

# Calibrated 2026-07-11 against the same 6-topic cross-lingual paraphrase
# set used for the BGE-M3 model choice (18 sentences, 30 same-topic pairs,
# 276 cross-topic pairs): true-match similarities ranged 0.630-0.971 (mean
# 0.816), unrelated-pair similarities ranged 0.389-0.752 (mean 0.550) - the
# two distributions overlap between 0.630 and 0.752. 0.72 sits in that
# overlap, biased toward precision: a customer-facing bot confidently
# returning the wrong FAQ answer is worse than falling through to the next
# pipeline layer. This is calibrated on only 6 topics - needs re-tuning
# once real merchant FAQ sets and real traffic are available.
MATCH_THRESHOLD = 0.72

# Fallback order when the customer's detected language has no key in
# response_config (e.g. detected_language is "mixed"/"unknown", or the
# merchant only ever filled in one language's reply).
_LANGUAGE_FALLBACK_ORDER = ("uz", "ru")


@dataclass
class FaqMatch:
    faq: Faq
    similarity: float
    reply_text: str


def select_reply_text(response_config: dict[str, str], detected_language: str) -> str:
    if detected_language in response_config:
        return response_config[detected_language]
    for lang in _LANGUAGE_FALLBACK_ORDER:
        if lang in response_config:
            return response_config[lang]
    return next(iter(response_config.values()))


def match_faq(
    db: Session, merchant_id: uuid.UUID, normalized_text: str, detected_language: str
) -> FaqMatch | None:
    """Return the best-matching FAQ for this merchant, or None if nothing
    clears MATCH_THRESHOLD (including when the merchant has no FAQs yet)."""
    query_embedding = embed_text(normalized_text)

    distance_expr = Faq.embedding.cosine_distance(query_embedding)
    row = db.execute(
        select(Faq, distance_expr.label("distance"))
        .where(Faq.merchant_id == merchant_id, Faq.embedding.is_not(None))
        .order_by(distance_expr)
        .limit(1)
    ).first()

    if row is None:
        return None

    faq, distance = row
    similarity = 1 - distance
    if similarity < MATCH_THRESHOLD:
        return None

    reply_text = select_reply_text(faq.response_config, detected_language)
    return FaqMatch(faq=faq, similarity=similarity, reply_text=reply_text)


# Above this many FAQs, sending every row to Gemini on every message would
# blow the context budget - switch to top-K by similarity instead. Below
# it, sending the merchant's whole FAQ set costs little and means a
# loosely-worded question can still land on a relevant FAQ Gemini judges
# useful, without depending on match_faq's precision-biased threshold.
CONTEXT_ALL_THRESHOLD = 30
CONTEXT_LIMIT = 8


def list_faqs_for_context(db: Session, merchant_id: uuid.UUID, query_embedding: list[float]) -> list[Faq]:
    """FAQ rows to hand Gemini as grounding context (app/llm/answer.py) -
    all of them if the merchant has few, else the top-K by the same
    cosine-distance ranking match_faq uses, but with no MATCH_THRESHOLD
    cutoff: unlike match_faq's single confident auto-reply, this is a
    context list the LLM itself judges relevance from."""
    total = db.scalar(select(func.count()).select_from(Faq).where(Faq.merchant_id == merchant_id)) or 0
    if total <= CONTEXT_ALL_THRESHOLD:
        return list(db.scalars(select(Faq).where(Faq.merchant_id == merchant_id)))

    distance_expr = Faq.embedding.cosine_distance(query_embedding)
    rows = db.scalars(
        select(Faq)
        .where(Faq.merchant_id == merchant_id, Faq.embedding.is_not(None))
        .order_by(distance_expr)
        .limit(CONTEXT_LIMIT)
    ).all()
    return list(rows)
