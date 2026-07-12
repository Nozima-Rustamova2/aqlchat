"""Entry point for llm_first pipeline mode's customer-facing answer layer -
mirrors app/llm/service.py::get_fallback_reply's shape (cache check ->
budget check -> context assembly -> provider call -> log -> return)
without importing anything from it: a parallel stack, not a rework, per
the mode-switch plan's Flag 7 decision - this must not touch the
layered-mode Claude path at all.

Two different forms of the customer's text are used deliberately:
`normalized_text` (script-canonicalized - e.g. Uzbek Cyrillic
transliterated to Latin, see app/nlp/transliteration.py) drives retrieval
embeddings and the cache key, matching how match_faq/product retrieval
already embed queries; but the ORIGINAL `raw_text` is what's actually
sent to Gemini as the question, since the prompt asks Gemini to mirror
the customer's script - normalized_text would already have erased that
signal for Cyrillic input.

Called only from app/telegram/webhook.py's llm_first branch - never from
the layered-mode chain.
"""

import logging
import uuid
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Conversation, Customer, LlmFallbackLog, Merchant, Message, Product
from app.faq.retrieval import list_faqs_for_context, select_reply_text
from app.llm.budget import check_and_consume_budget
from app.llm.cache import get_cached_answer, set_cached_answer
from app.llm.gemini_provider import AnswerContext, AnswerResult, GeminiProvider
from app.llm.redis_client import get_redis
from app.nlp.embeddings import embed_text
from app.products.retrieval import find_text_candidates

logger = logging.getLogger(__name__)

PROMPT_VERSION = "grounded_answer_v1"

# Shorter than the Claude fallback's 24h (app/llm/cache.py) - the top-K
# product/FAQ context retrieved for a cached query can shift if a
# merchant edits their catalog mid-demo, which the whole-catalog Claude
# fallback context isn't as exposed to.
_CACHE_TTL_SECONDS = 3600

_HISTORY_LIMIT = 6
_PRODUCT_LIMIT = 8

# Ceiling on the Gemini call, enforced two ways: the SDK-level
# http_options timeout in gemini_provider.py, and this thread-pool wrapper
# - so the guarantee doesn't depend solely on the SDK actually honoring
# its own timeout parameter. A single shared pool, not one per call.
#
# Widened from the originally-specified 8s after a live call: the full
# grounded-answer prompt (system instruction + product/FAQ context + JSON
# schema) measured 6-8s end to end on a real request, so an 8s ceiling
# would discard a meaningful fraction of genuinely-good answers as
# timeouts and escalate them to a human needlessly. 12s trades a slower
# worst-case customer wait for not throwing away real answers.
_TIMEOUT_SECONDS = 12
_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="gemini-answer")

# Constructed lazily, not at import time: google-genai's Client validates
# the API key eagerly and raises ValueError on a blank one (unlike
# anthropic.Anthropic(), which only fails on first call - see
# app/llm/service.py's docstring for the past incident that taught this).
# Building it lazily inside the try/except below means a missing
# gemini_api_key degrades to handoff like any other provider failure,
# instead of crashing on import (which would break every module that
# transitively imports this one, including app startup).
_provider: GeminiProvider | None = None


def _get_provider() -> GeminiProvider:
    global _provider
    if _provider is None:
        _provider = GeminiProvider()
    return _provider


def _product_dict(product: Product) -> dict:
    return {
        "id": str(product.id),
        "name": product.name,
        "price": float(product.price) if product.price is not None else None,
        "currency": product.currency,
        "description": product.description,
        "price_status": product.price_status,
    }


def _build_context(
    db: Session,
    merchant: Merchant,
    conversation: Conversation,
    normalized_text: str,
    raw_text: str,
    detected_language: str,
) -> AnswerContext:
    query_embedding = embed_text(normalized_text)

    candidates = find_text_candidates(db, merchant.id, query_embedding, limit=_PRODUCT_LIMIT)
    products = [_product_dict(candidate.product) for candidate in candidates]

    faqs = [
        {"question": faq.question, "answer": select_reply_text(faq.response_config, detected_language)}
        for faq in list_faqs_for_context(db, merchant.id, query_embedding)
    ]

    last_matched_product = None
    last_matched_id = (conversation.context or {}).get("last_matched_product_id")
    if last_matched_id:
        product = db.get(Product, uuid.UUID(last_matched_id))
        if product is not None:
            last_matched_product = _product_dict(product)

    history_rows = db.scalars(
        select(Message)
        .where(Message.conversation_id == conversation.id)
        .order_by(Message.created_at.desc())
        .limit(_HISTORY_LIMIT)
    ).all()
    history = [{"direction": m.direction, "text": m.raw_text or ""} for m in reversed(history_rows)]

    shop_name = (merchant.profile or {}).get("shop_name") or merchant.name

    return AnswerContext(
        query=raw_text,
        detected_language=detected_language,
        shop_name=shop_name,
        profile=merchant.profile or {},
        products=products,
        faqs=faqs,
        last_matched_product=last_matched_product,
        history=history,
    )


def _generate_with_timeout(context: AnswerContext) -> AnswerResult:
    future = _executor.submit(_get_provider().generate, context)
    return future.result(timeout=_TIMEOUT_SECONDS)


def _log(
    db: Session,
    merchant: Merchant,
    conversation: Conversation,
    query: str,
    context: AnswerContext,
    result: AnswerResult,
) -> None:
    matched_product_uuid: uuid.UUID | None = None
    if result.matched_product_ref:
        try:
            matched_product_uuid = uuid.UUID(result.matched_product_ref)
        except ValueError:
            matched_product_uuid = None

    db.add(
        LlmFallbackLog(
            merchant_id=merchant.id,
            conversation_id=conversation.id,
            query=query,
            context_snapshot={"faqs": context.faqs, "products": context.products},
            provider_name=result.provider_name,
            answerable=result.answerable,
            answer=result.reply,
            prompt_version=PROMPT_VERSION,
            matched_product_ref=matched_product_uuid,
        )
    )

    if matched_product_uuid is not None:
        conversation.context = {**(conversation.context or {}), "last_matched_product_id": str(matched_product_uuid)}
        db.add(conversation)


def get_grounded_answer(
    db: Session,
    merchant: Merchant,
    customer: Customer,
    conversation: Conversation,
    normalized_text: str,
    raw_text: str,
    detected_language: str,
) -> AnswerResult | None:
    """Returns None whenever this layer has nothing to say at all (cache
    miss plus exhausted budget, or the provider call failing/timing out
    outright) - the caller (app/telegram/webhook.py's
    _handle_llm_first_text) falls back to the generic escalation reply in
    that case. A non-None result with answerable=False still carries a
    holding reply that should be shown to the customer before escalating -
    that's a real, if partial, answer from this layer, not nothing."""
    redis_client = get_redis()

    cached_reply = get_cached_answer(redis_client, str(merchant.id), normalized_text)
    if cached_reply is not None:
        return AnswerResult(answerable=True, reply=cached_reply, matched_product_ref=None, provider_name="cache")

    if not check_and_consume_budget(redis_client, str(merchant.id), str(customer.id)):
        return None

    context = _build_context(db, merchant, conversation, normalized_text, raw_text, detected_language)

    try:
        result = _generate_with_timeout(context)
    except FutureTimeoutError:
        logger.warning("Gemini grounded-answer call timed out for merchant %s", merchant.id)
        db.add(
            LlmFallbackLog(
                merchant_id=merchant.id,
                conversation_id=conversation.id,
                query=raw_text,
                context_snapshot={"faqs": context.faqs, "products": context.products},
                provider_name="timeout",
                answerable=False,
                answer=None,
                prompt_version=PROMPT_VERSION,
            )
        )
        return None
    except Exception:
        logger.exception("Gemini grounded-answer call failed for merchant %s", merchant.id)
        db.add(
            LlmFallbackLog(
                merchant_id=merchant.id,
                conversation_id=conversation.id,
                query=raw_text,
                context_snapshot={"faqs": context.faqs, "products": context.products},
                provider_name="error",
                answerable=False,
                answer=None,
                prompt_version=PROMPT_VERSION,
            )
        )
        return None

    _log(db, merchant, conversation, raw_text, context, result)

    if result.answerable and result.reply:
        set_cached_answer(redis_client, str(merchant.id), normalized_text, result.reply, ttl_seconds=_CACHE_TTL_SECONDS)

    return result
