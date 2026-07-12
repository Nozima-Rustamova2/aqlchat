"""Seeds a merchant's demo catalog for a live walkthrough - loads
fixtures/demo/{vertical}.json (channel-post-shaped products: a caption
whose first line becomes the name and the rest becomes the description,
same split app/products/ingestion.py does for real channel posts, plus a
price - two products per vertical deliberately have no price, to exercise
the missing-price queue same as a real merchant's catalog would) and FAQ
rows, matching scripts/load_products.py's SessionLocal + argparse
pattern.

Unlike load_products.py/a real channel post, these products don't carry
a source_channel_id/source_message_id - they weren't ingested from an
actual channel, so both stay NULL (see app/db/models.py's Product
docstring: "NULL/NULL for manually-uploaded products"). Product.embedding
is still set (BGE-M3, same as ingestion.py) so Gemini grounded answering
(app/llm/answer.py) has something to retrieve against immediately;
Product.image_embedding is set too when a fixture supplies an image file
that actually exists on disk - best-effort, not required, since demo
images are supplied by the user separately from this fixture skeleton.

Fixture content is a structural skeleton ONLY - every caption/FAQ answer
is marked TODO for the user to hand-author real Uz/Ru copy afterward, not
generated here (see fixtures/demo/*.json).

Usage:
    uv run python -m scripts.seed_demo --merchant-id <id> --vertical clothing
"""

import argparse
import json
import logging
import uuid
from pathlib import Path

from PIL import Image
from pydantic import BaseModel
from sqlalchemy import delete

from app.db.models import Faq, Product
from app.db.session import SessionLocal
from app.image_search.embeddings import embed_image
from app.nlp.embeddings import embed_text

logger = logging.getLogger(__name__)

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures" / "demo"


class ProductFixture(BaseModel):
    caption: str
    price: float | None = None
    currency: str | None = None
    image_path: str | None = None


class FaqFixture(BaseModel):
    question: str
    answers: dict[str, str]


class DemoFixture(BaseModel):
    products: list[ProductFixture]
    faqs: list[FaqFixture]


def _derive_name(caption: str) -> str:
    first_line = caption.strip().splitlines()[0].strip()
    return first_line[:255] or "Mahsulot"


def _derive_description(caption: str) -> str | None:
    rest = "\n".join(caption.strip().splitlines()[1:]).strip()
    return rest or None


def _available_verticals() -> list[str]:
    return sorted(p.stem for p in FIXTURES_DIR.glob("*.json"))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--merchant-id", required=True, type=uuid.UUID)
    parser.add_argument("--vertical", required=True, choices=_available_verticals())
    args = parser.parse_args()

    with open(FIXTURES_DIR / f"{args.vertical}.json", encoding="utf-8") as f:
        fixture = DemoFixture.model_validate(json.load(f))

    db = SessionLocal()
    try:
        db.execute(delete(Product).where(Product.merchant_id == args.merchant_id))
        db.execute(delete(Faq).where(Faq.merchant_id == args.merchant_id))

        for item in fixture.products:
            name = _derive_name(item.caption)
            description = _derive_description(item.caption)
            product = Product(
                merchant_id=args.merchant_id,
                name=name,
                description=description,
                price=item.price,
                currency=item.currency,
                price_status="set" if item.price is not None else "missing",
                embedding=embed_text(f"{name}\n{description}" if description else name),
            )
            if item.image_path:
                image_file = (FIXTURES_DIR / item.image_path).resolve()
                if image_file.exists():
                    product.image_embedding = embed_image(Image.open(image_file))
                else:
                    logger.warning("demo image not found, skipping image embedding: %s", image_file)
            db.add(product)

        for faq_item in fixture.faqs:
            db.add(
                Faq(
                    merchant_id=args.merchant_id,
                    question=faq_item.question,
                    response_config=faq_item.answers,
                    embedding=embed_text(faq_item.question),
                )
            )

        db.commit()
    finally:
        db.close()

    print(f"Seeded {len(fixture.products)} products and {len(fixture.faqs)} FAQs for merchant {args.merchant_id}")


if __name__ == "__main__":
    main()
