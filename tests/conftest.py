import json
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlalchemy.orm import Session as SqlaSession

from app.db.models import Faq, Flow, Merchant, MerchantAdmin, Product
from app.db.session import SessionLocal, engine, get_db
from app.main import app
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


@pytest.fixture
def routed_session():
    """For tests that go through a real FastAPI route (via TestClient),
    not a service function called directly. Those routes call db.commit()
    for real, which the plain db_session fixture's rollback-only teardown
    can't contain - this wraps the whole test in a connection + SAVEPOINT
    so a commit() inside the route only releases the savepoint, and the
    real rollback at teardown undoes everything. Standard SQLAlchemy
    "join a session into an external transaction" pattern - see
    https://docs.sqlalchemy.org/en/20/orm/session_transaction.html#joining-a-session-into-an-external-transaction-such-as-for-test-suites
    """
    connection = engine.connect()
    trans = connection.begin()
    session = SqlaSession(bind=connection)
    session.begin_nested()

    @event.listens_for(session, "after_transaction_end")
    def _restart_savepoint(sess, transaction):
        if transaction.nested and not transaction._parent.nested:
            sess.begin_nested()

    def _override_get_db():
        yield session

    app.dependency_overrides[get_db] = _override_get_db
    try:
        yield session
    finally:
        app.dependency_overrides.pop(get_db, None)
        session.close()
        trans.rollback()
        connection.close()


@pytest.fixture
def routed_merchant(routed_session) -> Merchant:
    merchant = Merchant(
        name="routed-test-merchant",
        telegram_bot_token=f"pytest-routed-{id(object())}",
        webhook_secret="pytest-routed-secret",
    )
    routed_session.add(merchant)
    routed_session.flush()
    return merchant


@pytest.fixture
def client(routed_session) -> TestClient:
    return TestClient(app)


def make_merchant_admin(db_session, merchant_id, telegram_user_id: int) -> MerchantAdmin:
    admin = MerchantAdmin(merchant_id=merchant_id, telegram_user_id=telegram_user_id)
    db_session.add(admin)
    db_session.flush()
    return admin


def make_flow(
    db_session,
    merchant_id,
    name: str,
    keywords: list[str],
    reply_text: str = "",
    channel: str = "telegram",
    response_config: dict | None = None,
) -> Flow:
    flow = Flow(
        merchant_id=merchant_id,
        name=name,
        trigger_type="keyword",
        trigger_value=json.dumps(keywords),
        channel=channel,
        response_config=response_config if response_config is not None else {"type": "text", "text": reply_text},
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


def make_product(
    db_session, merchant_id, name: str, image_embedding: list[float], price=None, currency=None, description=None
) -> Product:
    product = Product(
        merchant_id=merchant_id,
        name=name,
        price=price,
        currency=currency,
        description=description,
        image_embedding=image_embedding,
    )
    db_session.add(product)
    db_session.flush()
    return product
