import datetime as dt
import uuid

from sqlalchemy import select

import app.llm.service as llm_service
from app.db.models import LlmFallbackLog
from app.llm.budget import DAILY_CUSTOMER_CALL_LIMIT
from app.llm.fallback import FallbackContext, FallbackResult
from app.llm.redis_client import get_redis


class _StubProvider:
    def __init__(self, result: FallbackResult):
        self.result = result
        self.calls: list[FallbackContext] = []

    def generate(self, context: FallbackContext) -> FallbackResult:
        self.calls.append(context)
        return self.result


def _use_stub_provider(monkeypatch, result: FallbackResult) -> _StubProvider:
    stub = _StubProvider(result)
    monkeypatch.setattr(llm_service, "_provider", stub)
    return stub


def test_answerable_result_is_returned_and_cached(monkeypatch, db_session, test_merchant):
    stub = _use_stub_provider(
        monkeypatch, FallbackResult(answerable=True, answer="Yetkazib berish bepul", provider_name="stub")
    )
    customer_id = uuid.uuid4()
    conversation_id = uuid.uuid4()

    reply = llm_service.get_fallback_reply(
        db_session, test_merchant.id, customer_id, conversation_id, "yetkazib berish qancha turadi", "uz"
    )

    assert reply is not None
    assert reply.answer == "Yetkazib berish bepul"
    assert len(stub.calls) == 1

    # Second call with the same normalized text should hit the cache and
    # not call the provider again.
    reply2 = llm_service.get_fallback_reply(
        db_session, test_merchant.id, customer_id, conversation_id, "yetkazib berish qancha turadi", "uz"
    )
    assert reply2 is not None
    assert reply2.answer == "Yetkazib berish bepul"
    assert len(stub.calls) == 1


def test_not_answerable_result_returns_none(monkeypatch, db_session, test_merchant):
    _use_stub_provider(monkeypatch, FallbackResult(answerable=False, answer=None, provider_name="stub"))

    reply = llm_service.get_fallback_reply(
        db_session, test_merchant.id, uuid.uuid4(), uuid.uuid4(), "sizning maskotingiz bormi", "uz"
    )

    assert reply is None


def test_not_answerable_result_is_logged_for_replay(monkeypatch, db_session, test_merchant):
    from app.db.models import Conversation, Customer

    _use_stub_provider(monkeypatch, FallbackResult(answerable=False, answer=None, provider_name="stub"))

    customer = Customer(merchant_id=test_merchant.id, telegram_user_id=424242)
    db_session.add(customer)
    db_session.flush()
    conversation = Conversation(merchant_id=test_merchant.id, customer_id=customer.id)
    db_session.add(conversation)
    db_session.flush()

    llm_service.get_fallback_reply(
        db_session, test_merchant.id, customer.id, conversation.id, "sizning maskotingiz bormi", "uz"
    )
    db_session.flush()

    log = db_session.scalar(
        select(LlmFallbackLog).where(
            LlmFallbackLog.merchant_id == test_merchant.id, LlmFallbackLog.query == "sizning maskotingiz bormi"
        )
    )
    assert log is not None
    assert log.answerable is False
    assert log.provider_name == "stub"


def test_budget_exhausted_returns_none_without_calling_provider(monkeypatch, db_session, test_merchant):
    stub = _use_stub_provider(
        monkeypatch, FallbackResult(answerable=True, answer="should not be reached", provider_name="stub")
    )
    customer_id = uuid.uuid4()

    redis_client = get_redis()
    today = dt.datetime.now(dt.timezone.utc).date().isoformat()
    redis_client.set(f"llm_budget:customer:{test_merchant.id}:{customer_id}:{today}", DAILY_CUSTOMER_CALL_LIMIT)

    reply = llm_service.get_fallback_reply(
        db_session, test_merchant.id, customer_id, uuid.uuid4(), "unique query for budget test", "uz"
    )

    assert reply is None
    assert len(stub.calls) == 0
