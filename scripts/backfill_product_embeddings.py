"""Backfills Product.embedding (BGE-M3 text embedding of name+description)
for existing rows - app/products/ingestion.py sets this on every
create/update going forward, but rows created before that column existed
(or loaded via scripts/load_products.py, which doesn't set it) need a
one-time pass. Needed for Gemini grounded-answering retrieval
(app/products/retrieval.py) to find them at all.

Usage:
    uv run python -m scripts.backfill_product_embeddings
    uv run python -m scripts.backfill_product_embeddings --merchant-id <id>
    uv run python -m scripts.backfill_product_embeddings --force  # re-embed rows that already have one
"""

import argparse
import uuid

from sqlalchemy import select

from app.db.models import Product
from app.db.session import SessionLocal
from app.nlp.embeddings import embed_text


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--merchant-id", type=uuid.UUID, default=None)
    parser.add_argument("--force", action="store_true", help="re-embed rows that already have an embedding")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        query = select(Product)
        if not args.force:
            query = query.where(Product.embedding.is_(None))
        if args.merchant_id is not None:
            query = query.where(Product.merchant_id == args.merchant_id)

        products = db.scalars(query).all()
        for product in products:
            text = f"{product.name}\n{product.description}" if product.description else product.name
            product.embedding = embed_text(text)
            db.add(product)
        db.commit()
    finally:
        db.close()

    print(f"Embedded {len(products)} products")


if __name__ == "__main__":
    main()
