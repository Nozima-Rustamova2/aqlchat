"""Rule-based flow matching: the cheapest, free layer in the cost-minimizing
pipeline (rule triggers -> FAQ/intent embedding retrieval -> LLM fallback).
Keyword matching only for Phase 1 - no NLP, just substring checks against
already-normalized text.

Shared by two channels (flows.channel): 'telegram' keyword rules (the
original Layer-1 pipeline) and 'instagram_comment' comment-to-DM rules
(app/instagram/). A rule only ever fires on its own channel.
"""

import json
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Flow


def match_flow(
    db: Session,
    merchant_id: uuid.UUID,
    normalized_text: str,
    channel: str = "telegram",
    media_id: str | None = None,
) -> Flow | None:
    """Return the flow whose longest matching keyword is found in normalized_text.

    Longest-match-wins so a merchant can define both a generic keyword
    ("narx") and a more specific one ("narxi qancha") with different
    responses, and the more specific one takes priority when both match.

    Instagram rules can additionally be scoped to specific posts via
    response_config["media_ids"] (absent/empty = account-wide); pass the
    comment's media_id to apply the scope. A post-scoped rule beats an
    account-wide one when both match - the merchant pinned it to this
    post on purpose - and keyword length only breaks ties within the
    same scope specificity.
    """
    flows = db.scalars(
        select(Flow).where(
            Flow.merchant_id == merchant_id,
            Flow.trigger_type == "keyword",
            Flow.channel == channel,
        )
    ).all()

    haystack = normalized_text.lower()
    best_flow: Flow | None = None
    best_key = (-1, -1)  # (scoped: 0|1, keyword length)

    for flow in flows:
        media_ids = flow.response_config.get("media_ids") or []
        if media_ids and media_id not in media_ids:
            continue
        scoped = 1 if media_ids else 0
        keywords = json.loads(flow.trigger_value)
        for keyword in keywords:
            key = (scoped, len(keyword))
            if keyword.lower() in haystack and key > best_key:
                best_flow = flow
                best_key = key

    return best_flow
