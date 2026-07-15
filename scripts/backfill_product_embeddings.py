"""Backfills text embeddings for existing rows - Product.embedding (BGE-M3
embedding of name+description) and Faq.embedding (embedding of question).
app/products/ingestion.py and the FAQ creation paths set these on every
create/update going forward, but rows created before a column existed
(or loaded via scripts/load_products.py, which doesn't set it) need a
one-time pass. Products are needed for Gemini grounded-answering retrieval
(app/products/retrieval.py) to find them at all.

Also the re-embedding tool after any change to how text is embedded: e.g.
the 2026-07-15 lowercasing fix in app/nlp/embeddings.py means every
embedding stored before it lives in a different space than lowercased
queries - run with --force after such a change.

Usage:
    uv run python -m scripts.backfill_product_embeddings
    uv run python -m scripts.backfill_product_embeddings --merchant-id <id>
    uv run python -m scripts.backfill_product_embeddings --force  # re-embed rows that already have one
"""

import argparse
import uuid

from sqlalchemy import select

from app.db.models import Faq, Product
from app.db.session import SessionLocal
from app.nlp.embeddings import embed_text


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--merchant-id", type=uuid.UUID, default=None)
    parser.add_argument("--force", action="store_true", help="re-embed rows that already have an embedding")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        product_query = select(Product)
        faq_query = select(Faq)
        if not args.force:
            product_query = product_query.where(Product.embedding.is_(None))
            faq_query = faq_query.where(Faq.embedding.is_(None))
        if args.merchant_id is not None:
            product_query = product_query.where(Product.merchant_id == args.merchant_id)
            faq_query = faq_query.where(Faq.merchant_id == args.merchant_id)

        products = db.scalars(product_query).all()
        for product in products:
            text = f"{product.name}\n{product.description}" if product.description else product.name
            product.embedding = embed_text(text)
            db.add(product)

        faqs = db.scalars(faq_query).all()
        for faq in faqs:
            faq.embedding = embed_text(faq.question)
            db.add(faq)

        db.commit()
    finally:
        db.close()

    print(f"Embedded {len(products)} products, {len(faqs)} FAQs")


if __name__ == "__main__":
    main()
