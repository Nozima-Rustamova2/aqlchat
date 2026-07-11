"""Routes a classified intent (app/intent/classifier.py) to an actual
reply. Canned-reply intents (greeting/thanks/human_handoff/complaint)
deliberately do NOT store their own copy of reply text - they look up the
merchant's existing Flow by a well-known name and reuse its
response_config. This means a merchant only ever edits one place
(their flow config) and the semantic layer automatically stays in sync
with it; if no such flow exists for a merchant, that intent silently
produces no reply rather than inventing hardcoded English/Uzbek text the
merchant never approved.

"complaint" falls back to "human_handoff" when a merchant hasn't defined a
dedicated complaint flow, since escalating to a human is the safest
generic response to a complaint - better than staying silent or (worse)
replying with an unrelated canned message.

"product_inquiry" is the one intent with a real action instead of a
canned reply: a plain substring match of product names against the
normalized text (not embedding-based - that's the Phase 2 catalog job,
not this one). Only replies when exactly one product matches; zero or
multiple matches means genuine ambiguity, and per the project's
established "don't force a guess" rule (see FAQ/photo-match confidence
thresholds), that falls through rather than picking one.
"""

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Flow, Product
from app.intent.classifier import IntentMatch

_CANNED_INTENT_FLOW_NAMES: dict[str, list[str]] = {
    "greeting": ["greeting"],
    "thanks": ["thanks"],
    "human_handoff": ["human_handoff"],
    "complaint": ["complaint", "human_handoff"],
}


@dataclass
class RoutedReply:
    reply_text: str
    similarity: float


def route_intent(
    db: Session, merchant_id: uuid.UUID, intent: IntentMatch, normalized_text: str
) -> RoutedReply | None:
    if intent.label == "product_inquiry":
        return _route_product_inquiry(db, merchant_id, normalized_text, intent.similarity)

    flow_names = _CANNED_INTENT_FLOW_NAMES.get(intent.label)
    if not flow_names:
        return None

    for name in flow_names:
        flow = db.scalar(select(Flow).where(Flow.merchant_id == merchant_id, Flow.name == name))
        if flow is not None:
            return RoutedReply(reply_text=flow.response_config["text"], similarity=intent.similarity)

    return None


def _route_product_inquiry(
    db: Session, merchant_id: uuid.UUID, normalized_text: str, similarity: float
) -> RoutedReply | None:
    products = db.scalars(select(Product).where(Product.merchant_id == merchant_id)).all()

    haystack = normalized_text.lower()
    matches = [p for p in products if p.name.lower() in haystack]
    if len(matches) != 1:
        return None

    product = matches[0]
    price_line = f"{product.price} {product.currency}" if product.price is not None else ""
    reply_text = "\n".join(part for part in (product.name, price_line, product.description) if part)
    return RoutedReply(reply_text=reply_text, similarity=similarity)
