"""Gemini grounded answering (app/llm/answer.py) - the llm_first pipeline
mode's answer layer. Provider calls are stubbed (monkeypatched
answer._provider, same pattern as tests/test_llm_service.py for the
Claude fallback) - a live-Gemini smoke test is separate and opt-in, not
run here (language mirroring and follow-up coherence aren't
unit-testable against a stub).
"""

import uuid

from sqlalchemy import select

import app.llm.answer as answer_module
from app.db.models import Conversation, Customer, LlmFallbackLog, Product
from app.llm.gemini_provider import AnswerContext, AnswerResult
from app.nlp.embeddings import embed_text


class _StubProvider:
    def __init__(self, result: AnswerResult, delay: float = 0.0):
        self.result = result
        self.delay = delay
        self.calls: list[AnswerContext] = []

    def generate(self, context: AnswerContext) -> AnswerResult:
        if self.delay:
            import time

            time.sleep(self.delay)
        self.calls.append(context)
        return self.result


def _use_stub_provider(monkeypatch, result: AnswerResult, delay: float = 0.0) -> _StubProvider:
    stub = _StubProvider(result, delay=delay)
    monkeypatch.setattr(answer_module, "_provider", stub)
    return stub


def _make_conversation(db_session, merchant_id, telegram_user_id: int) -> tuple[Customer, Conversation]:
    customer = Customer(merchant_id=merchant_id, telegram_user_id=telegram_user_id)
    db_session.add(customer)
    db_session.flush()
    conversation = Conversation(merchant_id=merchant_id, customer_id=customer.id)
    db_session.add(conversation)
    db_session.flush()
    return customer, conversation


def test_answerable_result_is_returned_and_cached(monkeypatch, db_session, test_merchant):
    stub = _use_stub_provider(
        monkeypatch,
        AnswerResult(answerable=True, reply="Yetkazib berish bepul", matched_product_ref=None, provider_name="stub"),
    )
    customer, conversation = _make_conversation(db_session, test_merchant.id, 700001)

    result = answer_module.get_grounded_answer(
        db_session, test_merchant, customer, conversation, "yetkazib berish qancha", "yetkazib berish qancha", "uz"
    )
    assert result is not None
    assert result.answerable is True
    assert result.reply == "Yetkazib berish bepul"
    assert len(stub.calls) == 1

    # Same normalized text should hit the cache and skip the provider.
    result2 = answer_module.get_grounded_answer(
        db_session, test_merchant, customer, conversation, "yetkazib berish qancha", "yetkazib berish qancha", "uz"
    )
    assert result2 is not None
    assert result2.reply == "Yetkazib berish bepul"
    assert len(stub.calls) == 1


def test_not_answerable_returns_holding_reply_not_none(monkeypatch, db_session, test_merchant):
    # answerable=false still carries a holding reply - app/telegram/
    # webhook.py's _handle_llm_first_text shows it to the customer before
    # escalating, instead of the generic canned line. Only a hard failure
    # (error/timeout/budget) should return None.
    _use_stub_provider(
        monkeypatch,
        AnswerResult(
            answerable=False, reply="Hozir aniqlab beraman.", matched_product_ref=None, provider_name="stub"
        ),
    )
    customer, conversation = _make_conversation(db_session, test_merchant.id, 700002)

    result = answer_module.get_grounded_answer(
        db_session, test_merchant, customer, conversation, "noma'lum savol", "noma'lum savol", "uz"
    )
    assert result is not None
    assert result.answerable is False
    assert result.reply == "Hozir aniqlab beraman."


def test_not_answerable_result_is_not_cached(monkeypatch, db_session, test_merchant):
    # A holding reply cached under the exact-text key would be replayed
    # as answerable=true on a repeat of the same question - defeating
    # re-escalation. Only real answers get cached.
    stub = _use_stub_provider(
        monkeypatch,
        AnswerResult(answerable=False, reply="Hozir aniqlab beraman.", matched_product_ref=None, provider_name="stub"),
    )
    customer, conversation = _make_conversation(db_session, test_merchant.id, 700003)

    answer_module.get_grounded_answer(
        db_session, test_merchant, customer, conversation, "cache test query", "cache test query", "uz"
    )
    answer_module.get_grounded_answer(
        db_session, test_merchant, customer, conversation, "cache test query", "cache test query", "uz"
    )
    assert len(stub.calls) == 2


def test_provider_exception_falls_through_instead_of_raising(monkeypatch, db_session, test_merchant):
    class _RaisingProvider:
        def generate(self, context):
            raise RuntimeError("simulated provider failure")

    monkeypatch.setattr(answer_module, "_provider", _RaisingProvider())
    customer, conversation = _make_conversation(db_session, test_merchant.id, 700004)

    result = answer_module.get_grounded_answer(
        db_session, test_merchant, customer, conversation, "provider failure test", "provider failure test", "uz"
    )
    assert result is None

    db_session.flush()
    log = db_session.scalar(
        select(LlmFallbackLog).where(
            LlmFallbackLog.merchant_id == test_merchant.id, LlmFallbackLog.query == "provider failure test"
        )
    )
    assert log is not None
    assert log.answerable is False
    assert log.provider_name == "error"
    assert log.prompt_version == "grounded_answer_v1"


def test_provider_timeout_falls_through_instead_of_hanging(monkeypatch, db_session, test_merchant):
    monkeypatch.setattr(answer_module, "_TIMEOUT_SECONDS", 0.05)
    _use_stub_provider(
        monkeypatch,
        AnswerResult(answerable=True, reply="too slow", matched_product_ref=None, provider_name="stub"),
        delay=0.3,
    )
    customer, conversation = _make_conversation(db_session, test_merchant.id, 700005)

    result = answer_module.get_grounded_answer(
        db_session, test_merchant, customer, conversation, "timeout test query", "timeout test query", "uz"
    )
    assert result is None

    db_session.flush()
    log = db_session.scalar(
        select(LlmFallbackLog).where(
            LlmFallbackLog.merchant_id == test_merchant.id, LlmFallbackLog.query == "timeout test query"
        )
    )
    assert log is not None
    assert log.provider_name == "timeout"


def test_budget_exhausted_returns_none_without_calling_provider(monkeypatch, db_session, test_merchant):
    from app.llm.budget import DAILY_CUSTOMER_CALL_LIMIT
    from app.llm.redis_client import get_redis
    import datetime as dt

    stub = _use_stub_provider(
        monkeypatch,
        AnswerResult(answerable=True, reply="should not be reached", matched_product_ref=None, provider_name="stub"),
    )
    customer, conversation = _make_conversation(db_session, test_merchant.id, 700006)

    redis_client = get_redis()
    today = dt.datetime.now(dt.timezone.utc).date().isoformat()
    redis_client.set(f"llm_budget:customer:{test_merchant.id}:{customer.id}:{today}", DAILY_CUSTOMER_CALL_LIMIT)

    result = answer_module.get_grounded_answer(
        db_session, test_merchant, customer, conversation, "unique budget test query", "unique budget test query", "uz"
    )
    assert result is None
    assert len(stub.calls) == 0


def test_matched_product_ref_written_to_conversation_context(monkeypatch, db_session, test_merchant):
    product = Product(
        merchant_id=test_merchant.id,
        name="Qora sumka",
        price=150000,
        currency="UZS",
        price_status="set",
        embedding=embed_text("Qora sumka"),
    )
    db_session.add(product)
    db_session.flush()

    _use_stub_provider(
        monkeypatch,
        AnswerResult(
            answerable=True, reply="Ha, bor.", matched_product_ref=str(product.id), provider_name="stub"
        ),
    )
    customer, conversation = _make_conversation(db_session, test_merchant.id, 700007)

    answer_module.get_grounded_answer(
        db_session, test_merchant, customer, conversation, "qora sumka bormi", "qora sumka bormi", "uz"
    )

    assert conversation.context["last_matched_product_id"] == str(product.id)


def test_price_status_missing_is_reflected_in_assembled_context(monkeypatch, db_session, test_merchant):
    product = Product(
        merchant_id=test_merchant.id,
        name="Kumush uzuk",
        price_status="missing",
        embedding=embed_text("Kumush uzuk"),
    )
    db_session.add(product)
    db_session.flush()

    stub = _use_stub_provider(
        monkeypatch,
        AnswerResult(answerable=False, reply="Hozir aniqlab beraman.", matched_product_ref=None, provider_name="stub"),
    )
    customer, conversation = _make_conversation(db_session, test_merchant.id, 700008)

    answer_module.get_grounded_answer(
        db_session, test_merchant, customer, conversation, "kumush uzuk narxi", "kumush uzuk narxi", "uz"
    )

    assert len(stub.calls) == 1
    context = stub.calls[0]
    matched = next(p for p in context.products if p["id"] == str(product.id))
    assert matched["price_status"] == "missing"
    assert matched["price"] is None


def test_raw_text_not_normalized_text_is_sent_as_the_query(monkeypatch, db_session, test_merchant):
    # normalized_text is script-canonicalized (Cyrillic Uzbek -> Latin,
    # see app/nlp/transliteration.py) and drives retrieval/cache, but
    # Gemini needs the ORIGINAL text to mirror the customer's script.
    stub = _use_stub_provider(
        monkeypatch, AnswerResult(answerable=True, reply="ok", matched_product_ref=None, provider_name="stub")
    )
    customer, conversation = _make_conversation(db_session, test_merchant.id, 700009)

    answer_module.get_grounded_answer(
        db_session, test_merchant, customer, conversation, "narxi qancha", "нархи қанча", "uz"
    )

    assert stub.calls[0].query == "нархи қанча"
