"""Load a merchant's FAQs from a YAML file, replacing any existing ones.
Embeds each question with BGE-M3 at load time (see app/nlp/embeddings.py).

Usage:
    uv run python -m scripts.load_faqs --merchant-id <id> --file examples/faqs.example.yaml
"""

import argparse
import uuid

import yaml
from sqlalchemy import delete

from app.db.models import Faq
from app.db.session import SessionLocal
from app.faq.schema import FaqFile
from app.nlp.embeddings import embed_texts


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--merchant-id", required=True, type=uuid.UUID)
    parser.add_argument("--file", required=True)
    args = parser.parse_args()

    with open(args.file, encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    faq_file = FaqFile.model_validate(raw)

    embeddings = embed_texts([definition.question for definition in faq_file.faqs])

    db = SessionLocal()
    try:
        db.execute(delete(Faq).where(Faq.merchant_id == args.merchant_id))
        for definition, embedding in zip(faq_file.faqs, embeddings):
            db.add(
                Faq(
                    merchant_id=args.merchant_id,
                    question=definition.question,
                    response_config=definition.responses,
                    embedding=embedding,
                )
            )
        db.commit()
    finally:
        db.close()

    print(f"Loaded {len(faq_file.faqs)} FAQs for merchant {args.merchant_id}")


if __name__ == "__main__":
    main()
