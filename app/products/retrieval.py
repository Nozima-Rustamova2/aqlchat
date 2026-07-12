"""Text-based product retrieval for Gemini grounded answering
(app/llm/answer.py) - top-K by BGE-M3 similarity between the customer's
message and each product's name+description embedding
(Product.embedding, set at ingestion time - see app/products/ingestion.py
and scripts/backfill_product_embeddings.py). Same shape as
app/image_search/retrieval.py's find_candidates, deliberately with no
similarity floor at query time: the LLM's own groundedness judgment
(answerable=false when nothing in context actually answers the question)
is the real gate here, not a retrieval-time cutoff.
"""

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Product

DEFAULT_LIMIT = 8


@dataclass
class TextCandidate:
    product: Product
    similarity: float


def find_text_candidates(
    db: Session, merchant_id: uuid.UUID, query_embedding: list[float], limit: int = DEFAULT_LIMIT
) -> list[TextCandidate]:
    distance_expr = Product.embedding.cosine_distance(query_embedding)
    rows = db.execute(
        select(Product, distance_expr.label("distance"))
        .where(Product.merchant_id == merchant_id, Product.embedding.is_not(None))
        .order_by(distance_expr)
        .limit(limit)
    ).all()
    return [TextCandidate(product=product, similarity=1 - distance) for product, distance in rows]
