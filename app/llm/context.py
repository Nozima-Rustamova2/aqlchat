"""Builds the grounding context handed to the LLM fallback provider. Pulls
every FAQ and product for the merchant - no retrieval/ranking - which is
fine at pilot-scale catalogs but is a known scaling gap: a merchant with a
large catalog would blow the context budget. Revisit if/when that's a
real merchant, not before.
"""

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Faq, Product
from app.faq.retrieval import select_reply_text
from app.llm.fallback import FallbackContext


def build_context(db: Session, merchant_id: uuid.UUID, query: str, detected_language: str) -> FallbackContext:
    faqs = db.scalars(select(Faq).where(Faq.merchant_id == merchant_id)).all()
    products = db.scalars(select(Product).where(Product.merchant_id == merchant_id)).all()

    faq_dicts = [
        {"question": faq.question, "answer": select_reply_text(faq.response_config, detected_language)}
        for faq in faqs
    ]
    product_dicts = [
        {"name": p.name, "price": p.price, "currency": p.currency, "description": p.description} for p in products
    ]

    return FallbackContext(
        query=query, detected_language=detected_language, faqs=faq_dicts, products=product_dicts
    )
