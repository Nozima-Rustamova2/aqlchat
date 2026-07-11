"""Load a merchant's product catalog from a YAML file, replacing any
existing products. Embeds each product's photo with SigLIP at load time
(see app/image_search/embeddings.py) so image search has something to
match against.

Usage:
    uv run python -m scripts.load_products --merchant-id <id> --file examples/products.example.yaml
"""

import argparse
import uuid
from pathlib import Path

import yaml
from PIL import Image
from pydantic import BaseModel
from sqlalchemy import delete

from app.db.models import Product
from app.db.session import SessionLocal
from app.image_search.embeddings import embed_image


class ProductDefinition(BaseModel):
    name: str
    price: float | None = None
    currency: str | None = None
    description: str | None = None
    image_path: str


class ProductFile(BaseModel):
    products: list[ProductDefinition]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--merchant-id", required=True, type=uuid.UUID)
    parser.add_argument("--file", required=True)
    args = parser.parse_args()

    file_path = Path(args.file)
    with open(file_path, encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    product_file = ProductFile.model_validate(raw)

    db = SessionLocal()
    try:
        db.execute(delete(Product).where(Product.merchant_id == args.merchant_id))
        for definition in product_file.products:
            image_path = (file_path.parent / definition.image_path).resolve()
            embedding = embed_image(Image.open(image_path))
            db.add(
                Product(
                    merchant_id=args.merchant_id,
                    name=definition.name,
                    price=definition.price,
                    currency=definition.currency,
                    description=definition.description,
                    image_url=str(image_path),
                    image_embedding=embedding,
                )
            )
        db.commit()
    finally:
        db.close()

    print(f"Loaded {len(product_file.products)} products for merchant {args.merchant_id}")


if __name__ == "__main__":
    main()
