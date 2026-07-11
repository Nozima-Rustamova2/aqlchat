"""Pure ranking-logic tests using hand-set embedding vectors - no need to
call the real SigLIP model here, that's covered by the live verification
in app/image_search/embeddings.py's docstring/memory (real embed+search
against seeded products, confirmed live 2026-07-11)."""

import math

from app.image_search.retrieval import find_candidates
from tests.conftest import make_product

DIM = 768


def _vec(x: float, y: float) -> list[float]:
    v = [0.0] * DIM
    v[0] = x
    v[1] = y
    return v


def test_nearest_neighbor_ranked_first(db_session, test_merchant):
    close = make_product(db_session, test_merchant.id, "close product", _vec(1.0, 0.0))
    far = make_product(db_session, test_merchant.id, "far product", _vec(0.0, 1.0))

    query = _vec(0.9, math.sqrt(1 - 0.9**2))  # cosine sim ~0.9 to `close`, ~0.44 to `far`
    candidates = find_candidates(db_session, test_merchant.id, query)

    assert candidates[0].product.id == close.id
    assert candidates[1].product.id == far.id
    assert candidates[0].similarity > candidates[1].similarity


def test_limit_truncates_results(db_session, test_merchant):
    for i in range(5):
        angle = i * 0.1
        make_product(db_session, test_merchant.id, f"product {i}", _vec(math.cos(angle), math.sin(angle)))

    candidates = find_candidates(db_session, test_merchant.id, _vec(1.0, 0.0), limit=3)
    assert len(candidates) == 3


def test_products_without_embedding_are_excluded(db_session, test_merchant):
    from app.db.models import Product

    no_embedding = Product(merchant_id=test_merchant.id, name="no photo yet", image_embedding=None)
    db_session.add(no_embedding)
    db_session.flush()
    make_product(db_session, test_merchant.id, "has photo", _vec(1.0, 0.0))

    candidates = find_candidates(db_session, test_merchant.id, _vec(1.0, 0.0))
    assert all(c.product.id != no_embedding.id for c in candidates)


def test_results_are_scoped_to_merchant(db_session, test_merchant):
    from app.db.models import Merchant

    other_merchant = Merchant(
        name="other-merchant-image",
        telegram_bot_token="other-token-image-xyz",
        webhook_secret="other-secret",
    )
    db_session.add(other_merchant)
    db_session.flush()
    make_product(db_session, other_merchant.id, "other merchant's product", _vec(1.0, 0.0))

    candidates = find_candidates(db_session, test_merchant.id, _vec(1.0, 0.0))
    assert candidates == []


def test_no_products_returns_empty_list(db_session, test_merchant):
    assert find_candidates(db_session, test_merchant.id, _vec(1.0, 0.0)) == []
