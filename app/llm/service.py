"""Orchestrates the LLM fallback layer: cache check -> budget check ->
provider call -> replay logging. Returns None whenever this layer has
nothing safe to say - a cache miss plus exhausted budget, or the provider
itself saying "not answerable" - so the caller (app/telegram/webhook.py)
falls through to layer 5 human handoff. This layer never invents a reply;
it either has a grounded one or it doesn't.
"""

import uuid
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.db.models import LlmFallbackLog
from app.llm.budget import check_and_consume_budget
from app.llm.cache import get_cached_answer, set_cached_answer
from app.llm.claude_provider import ClaudeProvider
from app.llm.context import build_context
from app.llm.fallback import LLMProvider
from app.llm.redis_client import get_redis

_provider: LLMProvider = ClaudeProvider()


@dataclass
class FallbackReply:
    answer: str


def get_fallback_reply(
    db: Session,
    merchant_id: uuid.UUID,
    customer_id: uuid.UUID,
    conversation_id: uuid.UUID,
    normalized_text: str,
    detected_language: str,
) -> FallbackReply | None:
    redis_client = get_redis()

    cached_answer = get_cached_answer(redis_client, str(merchant_id), normalized_text)
    if cached_answer is not None:
        return FallbackReply(answer=cached_answer)

    if not check_and_consume_budget(redis_client, str(merchant_id), str(customer_id)):
        return None

    context = build_context(db, merchant_id, normalized_text, detected_language)
    result = _provider.generate(context)

    db.add(
        LlmFallbackLog(
            merchant_id=merchant_id,
            conversation_id=conversation_id,
            query=normalized_text,
            context_snapshot={"faqs": context.faqs, "products": context.products},
            provider_name=result.provider_name,
            answerable=result.answerable,
            answer=result.answer,
        )
    )

    if not result.answerable or not result.answer:
        return None

    set_cached_answer(redis_client, str(merchant_id), normalized_text, result.answer)
    return FallbackReply(answer=result.answer)
