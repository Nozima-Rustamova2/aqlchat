"""Worker-side processing of one Instagram comment/story-reply event -
everything that happens after the webhook's dedupe-insert
(app/instagram/router.py): staleness check, normalization, channel-scoped
flow matching, rate-budget consumption, and the Graph API replies. Both
event kinds share this table and pipeline (see CommentEvent's docstring);
they only diverge at the reply step (comment_id-authorized private reply
vs a plain message send) and the public-reply tier (comments only).

Two-tier matching for comments, per the product decision: the private DM
fires on any keyword substring match (catches agglutinative forms -
"narxi qancha?"), but the VISIBLE public reply only fires when the
comment is essentially just the keyword - so a complaint that merely
contains "narx" gets a quiet, relevant DM instead of a cheery public bot
reply under it. Story replies have no public-reply tier at all.
"""

import datetime as dt
import json
import logging
import re
import uuid

import redis
from sqlalchemy.orm import Session

from app.db.models import CommentEvent, Merchant
from app.faq.retrieval import select_reply_text
from app.flows.executor import match_flow
from app.instagram.budget import next_hour_epoch, try_consume_calls
from app.instagram.client import InstagramClient
from app.instagram.queue import defer_comment
from app.nlp.transliteration import normalize

logger = logging.getLogger(__name__)

# Meta's private-reply window. Measured from our webhook receipt time
# (comment_events.created_at), which trails the comment itself by seconds -
# close enough for a boundary whose failure mode is one rejected API call.
REPLY_WINDOW = dt.timedelta(days=7)
# Story replies aren't the special comment-private-reply grant - they're
# an ordinary message thread, so the standard 24h messaging window
# applies instead of the 7-day comment one.
STORY_REPLY_WINDOW = dt.timedelta(hours=24)


def process_comment_event(
    db: Session,
    redis_client: redis.Redis,
    event_id: uuid.UUID,
    client: InstagramClient | None = None,
) -> str:
    """Process one queued comment; returns the final status written to the
    row. Safe to call again on redelivery/retry - anything not pending or
    deferred is skipped."""
    event = db.get(CommentEvent, event_id)
    if event is None:
        logger.warning("comment event %s not found - dropped", event_id)
        return "missing"
    if event.status not in ("pending", "deferred"):
        return event.status

    merchant = db.get(Merchant, event.merchant_id)
    if merchant is None or not merchant.instagram_access_token:
        return _finish(db, event, "failed")

    reply_window = REPLY_WINDOW if event.channel == "instagram_comment" else STORY_REPLY_WINDOW
    if _now() - _as_utc(event.created_at) > reply_window:
        # Older than the private-reply window - the API would reject the
        # DM anyway. Counted so it can be surfaced to the merchant later.
        return _finish(db, event, "dropped_stale")

    if not event.raw_text:
        return _finish(db, event, "no_match")

    result = normalize(event.raw_text)
    event.detected_language = result.detected_language

    flow = match_flow(
        db,
        merchant.id,
        result.normalized_text,
        channel=event.channel,
        media_id=event.media_id,
    )
    if flow is None:
        return _finish(db, event, "no_match")
    event.matched_flow_id = flow.id
    event.status = "matched"
    db.commit()

    if not try_consume_calls(redis_client, str(merchant.id)):
        # Hourly Graph API cap hit - park in the deferred set for the next
        # hour instead of dropping. Late is fine, missing is not.
        defer_comment(redis_client, str(event.id), next_hour_epoch())
        return _finish(db, event, "deferred")

    config = flow.response_config
    language = event.detected_language or "uz"
    dm_text = select_reply_text(config["private_reply"], language).replace("{link}", config["link"])

    if client is None:
        client = InstagramClient(merchant.instagram_access_token)
    try:
        if event.channel == "instagram_comment":
            # Authorized via comment_id - works even though the commenter
            # never messaged us first (Meta's private-reply grant).
            client.send_private_reply(event.external_id, dm_text)
        else:
            # A story reply already opened an ordinary message thread -
            # reply to the sender directly, same as any inbound DM.
            client.send_message(event.commenter_id, dm_text)
    except Exception:
        logger.exception("private reply failed for comment event %s", event.id)
        return _finish(db, event, "failed")

    # Public replies only exist on the comment channel - a story reply
    # has no "under the comment" surface to post to.
    public_reply = config.get("public_reply")
    if event.channel == "instagram_comment" and public_reply and _is_exact_keyword_match(result.normalized_text, flow.trigger_value):
        public_text = select_reply_text(public_reply, language)
        try:
            client.reply_to_comment(event.external_id, public_text)
        except Exception:
            # The DM (the actual deliverable) went out - a failed public
            # reply downgrades to a logged warning, not a failed event.
            logger.exception("public reply failed for comment event %s (DM already sent)", event.id)

    event.replied_at = _now_naive()
    return _finish(db, event, "replied")


def _is_exact_keyword_match(normalized_text: str, trigger_value: str) -> bool:
    """True when the comment is essentially just one of the keywords -
    punctuation/emoji/whitespace-insensitive equality, not substring."""
    stripped = _strip_to_words(normalized_text)
    return any(stripped == _strip_to_words(keyword) for keyword in json.loads(trigger_value))


def _strip_to_words(text: str) -> str:
    return re.sub(r"[^\w\s]", "", text.lower()).strip()


def _finish(db: Session, event: CommentEvent, status: str) -> str:
    event.status = status
    db.commit()
    return status


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _now_naive() -> dt.datetime:
    return _now().replace(tzinfo=None)


def _as_utc(value: dt.datetime) -> dt.datetime:
    # comment_events timestamps are stored naive-UTC (DateTime without
    # timezone, like every other table here).
    return value.replace(tzinfo=dt.timezone.utc) if value.tzinfo is None else value
