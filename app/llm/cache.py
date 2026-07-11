"""Semantic-free response cache: hash of normalized text -> cached answer,
per merchant. Deliberately not embedding-based (unlike FAQ retrieval) -
this only catches exact repeats of the same normalized query, which is
enough to stop identical spam/repeat questions from re-hitting the paid
API, without the complexity of a similarity threshold for cached LLM
answers (a wrong cache hit here would serve a stale/mismatched answer
with no confidence signal, unlike FAQ retrieval's calibrated threshold).
"""

import hashlib

import redis

_CACHE_TTL_SECONDS = 86400  # 24h - answers are about a specific merchant's FAQs/products, which can change


def _cache_key(merchant_id: str, normalized_text: str) -> str:
    digest = hashlib.sha256(normalized_text.strip().lower().encode("utf-8")).hexdigest()
    return f"llm_cache:{merchant_id}:{digest}"


def get_cached_answer(redis_client: redis.Redis, merchant_id: str, normalized_text: str) -> str | None:
    return redis_client.get(_cache_key(merchant_id, normalized_text))


def set_cached_answer(redis_client: redis.Redis, merchant_id: str, normalized_text: str, answer: str) -> None:
    redis_client.set(_cache_key(merchant_id, normalized_text), answer, ex=_CACHE_TTL_SECONDS)
