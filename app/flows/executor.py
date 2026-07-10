"""Rule-based flow matching: the cheapest, free layer in the cost-minimizing
pipeline (rule triggers -> FAQ/intent embedding retrieval -> LLM fallback).
Keyword matching only for Phase 1 - no NLP, just substring checks against
already-normalized text.
"""

import json
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Flow


def match_flow(db: Session, merchant_id: uuid.UUID, normalized_text: str) -> Flow | None:
    """Return the flow whose longest matching keyword is found in normalized_text.

    Longest-match-wins so a merchant can define both a generic keyword
    ("narx") and a more specific one ("narxi qancha") with different
    responses, and the more specific one takes priority when both match.
    """
    flows = db.scalars(select(Flow).where(Flow.merchant_id == merchant_id, Flow.trigger_type == "keyword")).all()

    haystack = normalized_text.lower()
    best_flow: Flow | None = None
    best_length = -1

    for flow in flows:
        keywords = json.loads(flow.trigger_value)
        for keyword in keywords:
            if keyword.lower() in haystack and len(keyword) > best_length:
                best_flow = flow
                best_length = len(keyword)

    return best_flow
