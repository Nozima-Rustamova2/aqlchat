"""Image-based product search - the top-k half of the confirmation UX
(see app/image_search/embeddings.py and the aqlchat-phase1-plan memory for
why this isn't a confident top-1 auto-reply). Always returns up to
`limit` candidates for logging (app/db/models.py's ImageMatchLog captures
the full top-10) - the caller decides how many to actually show the
customer (3, fixed - see the memory note on why k isn't configurable
higher: more candidates degrades confirmation-tap accuracy, which is the
future calibration/training data this whole design exists to collect).
"""

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Product

# Deliberately NOT a safety threshold - see ImageMatchLog's docstring and
# the aqlchat-phase1-plan memory. The customer's confirm/"none of these"
# tap is the real gate in v1; this floor only exists to skip showing a
# carousel for genuinely dismal photos (screenshots, blur, unrelated
# objects). In the spike, even unrelated real product pairs scored 0.356+
# minimum similarity, so 0.25 should only fire on truly degenerate
# photos, not ordinary photographed-but-wrong items. Needs real
# recalibration once ImageMatchLog has pilot data - see retrieval.py's
# module docstring reasoning, this number is a placeholder, not a result.
FLOOR = 0.25

DEFAULT_LIMIT = 10


@dataclass
class Candidate:
    product: Product
    similarity: float


def find_candidates(
    db: Session, merchant_id: uuid.UUID, image_embedding: list[float], limit: int = DEFAULT_LIMIT
) -> list[Candidate]:
    distance_expr = Product.image_embedding.cosine_distance(image_embedding)
    rows = db.execute(
        select(Product, distance_expr.label("distance"))
        .where(Product.merchant_id == merchant_id, Product.image_embedding.is_not(None))
        .order_by(distance_expr)
        .limit(limit)
    ).all()
    return [Candidate(product=product, similarity=1 - distance) for product, distance in rows]
