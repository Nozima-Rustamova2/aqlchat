"""The Instagram comment worker - a standalone sync process that drains
the Redis queue the webhook feeds (app/instagram/queue.py) and does all
Graph API work (app/instagram/service.py), keeping the webhook's
200-immediately guarantee honest.

Run with:
    uv run python -m app.instagram.worker

Single process, no framework: Meta's 7-day reply window means latency
tolerance is enormous, and one worker drains far more than pilot-scale
traffic. Each job gets a fresh DB session; a job that raises is marked
failed by the service layer, and the loop itself never dies with it.
"""

import logging
import uuid

import redis

from app.db.session import SessionLocal
from app.instagram.queue import COMMENTS_QUEUE_KEY, pop_due_deferred
from app.instagram.service import process_comment_event
from app.redis_client import get_redis

logger = logging.getLogger(__name__)

_BRPOP_TIMEOUT_SECONDS = 5  # doubles as the deferred-set poll interval


def _process(redis_client, event_id: str) -> None:
    db = SessionLocal()
    try:
        status = process_comment_event(db, redis_client, uuid.UUID(event_id))
        logger.info("comment event %s -> %s", event_id, status)
    except Exception:
        # Belt over the service layer's own handling: a bug there must
        # not kill the worker loop.
        logger.exception("unhandled error processing comment event %s", event_id)
    finally:
        db.close()


def run() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    redis_client = get_redis()
    logger.info("instagram comment worker started (queue=%s)", COMMENTS_QUEUE_KEY)

    while True:
        for event_id in pop_due_deferred(redis_client):
            _process(redis_client, event_id)

        try:
            popped = redis_client.brpop([COMMENTS_QUEUE_KEY], timeout=_BRPOP_TIMEOUT_SECONDS)
        except redis.exceptions.TimeoutError:
            # redis-py's client-side socket read has ~zero headroom over
            # BRPOP's own server-side block timeout, so an empty queue
            # reliably races this exception instead of returning None -
            # that's the expected "nothing to do this cycle" case, not a
            # real failure.
            popped = None
        if popped is not None:
            _, event_id = popped
            _process(redis_client, event_id)


if __name__ == "__main__":
    run()
