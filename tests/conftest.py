import json
from pathlib import Path

import pytest
import yaml

from app.db.models import Faq, Flow, Merchant
from app.db.session import SessionLocal
from app.nlp.embeddings import embed_text

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "mixed_language_examples.yaml"


@pytest.fixture(scope="session")
def mixed_language_examples() -> list[dict]:
    with open(FIXTURE_PATH, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return data["examples"]


@pytest.fixture
def db_session():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


@pytest.fixture
def test_merchant(db_session):
    merchant = Merchant(
        name="pytest-merchant",
        telegram_bot_token=f"pytest-{id(object())}",
        webhook_secret="pytest-secret",
    )
    db_session.add(merchant)
    db_session.flush()
    yield merchant
    db_session.rollback()


def make_flow(db_session, merchant_id, name: str, keywords: list[str], reply_text: str) -> Flow:
    flow = Flow(
        merchant_id=merchant_id,
        name=name,
        trigger_type="keyword",
        trigger_value=json.dumps(keywords),
        response_config={"type": "text", "text": reply_text},
    )
    db_session.add(flow)
    db_session.flush()
    return flow


def make_faq(db_session, merchant_id, question: str, responses: dict[str, str]) -> Faq:
    faq = Faq(
        merchant_id=merchant_id,
        question=question,
        response_config=responses,
        embedding=embed_text(question),
    )
    db_session.add(faq)
    db_session.flush()
    return faq
