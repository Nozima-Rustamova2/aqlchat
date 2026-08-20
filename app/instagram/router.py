"""Instagram webhook - single endpoint for ALL merchants, routed by
entry[].id -> merchants.instagram_user_id (the tenant boundary on this
channel, mirroring webhook_slug's role for Telegram tenant bots).

The handler does the minimum Meta's 200-immediately expectation allows:
verify the signature, dedupe-insert a comment_events row, enqueue the row
id for the worker (app/instagram/worker.py). Every Graph API call happens
worker-side - nothing here blocks on Meta.

Signature verification is HMAC-SHA256 of the RAW request bytes with the
app secret - the body must be read before any JSON parsing, and never
re-serialized (key order/whitespace changes break the digest).
"""

import hashlib
import hmac
import json
import logging
import secrets

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.config import settings
from app.db.models import CommentEvent, Merchant
from app.db.session import get_db
from app.instagram.client import InstagramClient
from app.instagram.oauth import build_authorize_url, exchange_code
from app.instagram.queue import enqueue_comment
from app.redis_client import get_redis

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/instagram/webhook")
def verify_webhook(
    hub_mode: str = Query(default="", alias="hub.mode"),
    hub_verify_token: str = Query(default="", alias="hub.verify_token"),
    hub_challenge: str = Query(default="", alias="hub.challenge"),
) -> PlainTextResponse:
    """Meta's one-time subscription handshake: echo hub.challenge back as
    plain text iff the verify token matches ours."""
    if (
        hub_mode == "subscribe"
        and settings.instagram_verify_token
        and hmac.compare_digest(hub_verify_token, settings.instagram_verify_token)
    ):
        return PlainTextResponse(hub_challenge)
    raise HTTPException(status_code=403, detail="verification failed")


def _signature_is_valid(raw_body: bytes, signature_header: str | None) -> bool:
    if not settings.instagram_app_secret or not signature_header:
        return False
    if not signature_header.startswith("sha256="):
        return False
    expected = hmac.new(settings.instagram_app_secret.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(signature_header.removeprefix("sha256="), expected)


def _insert_event(
    db: Session,
    merchant: Merchant,
    channel: str,
    external_id: str,
    commenter_id: str,
    media_id: str | None,
    raw_text: str | None,
) -> str | None:
    """Shared dedupe-insert for both comment and story-reply events -
    same table, same unique constraint, same queue (app/instagram/queue.py),
    just a different channel tag (see CommentEvent's docstring)."""
    inserted_id = db.execute(
        insert(CommentEvent)
        .values(
            merchant_id=merchant.id,
            channel=channel,
            external_id=external_id,
            commenter_id=commenter_id,
            media_id=media_id,
            raw_text=raw_text,
            status="pending",
        )
        .on_conflict_do_nothing(constraint="uq_comment_events_merchant_external")
        .returning(CommentEvent.id)
    ).scalar()
    return str(inserted_id) if inserted_id is not None else None  # None = duplicate delivery, already recorded


@router.post("/instagram/webhook")
async def receive_webhook(request: Request, db: Session = Depends(get_db)) -> dict:
    raw_body = await request.body()
    if not _signature_is_valid(raw_body, request.headers.get("X-Hub-Signature-256")):
        raise HTTPException(status_code=403, detail="invalid signature")

    # TEMP DEBUG - remove once live comment-webhook delivery is confirmed working end to end.
    logger.info("RAW WEBHOOK PAYLOAD: %s", raw_body.decode("utf-8", errors="replace"))

    payload = json.loads(raw_body)

    enqueued_ids: list[str] = []
    for entry in payload.get("entry", []):
        entry_id = str(entry.get("id", ""))
        merchant = _resolve_merchant(db, entry_id)
        if merchant is None:
            # Not a tenant (or a stale subscription) - acknowledge and
            # drop; a non-200 would just make Meta hammer us with retries.
            logger.warning("instagram webhook for unknown ig user id %s - dropped", entry_id)
            continue

        for change in entry.get("changes", []):
            field = change.get("field")
            value = change.get("value", {})

            if field == "comments":
                comment_id = value.get("id")
                commenter_id = str((value.get("from") or {}).get("id", ""))
                if not comment_id or not commenter_id:
                    logger.warning("instagram comment change missing id/from for merchant %s", merchant.id)
                    continue
                if commenter_id == entry_id:
                    # Loop guard: our own public replies arrive back on
                    # this webhook. Dropped at ingress so they never
                    # occupy a comment_events row or a queue slot.
                    continue
                inserted_id = _insert_event(
                    db,
                    merchant,
                    channel="instagram_comment",
                    external_id=str(comment_id),
                    commenter_id=commenter_id,
                    media_id=str((value.get("media") or {}).get("id", "")) or None,
                    raw_text=value.get("text"),
                )

            elif field == "messages":
                message = value.get("message") or {}
                story = (message.get("reply_to") or {}).get("story")
                if story is None:
                    # A plain DM, not a story reply - conversation-state
                    # DM automation is a separate, not-yet-built feature
                    # (needs thread tracking; see the MVP plan). Drop it
                    # here rather than half-processing it.
                    continue
                message_id = message.get("mid") or message.get("id")
                sender_id = str((value.get("sender") or {}).get("id", ""))
                if not message_id or not sender_id:
                    logger.warning("instagram story-reply change missing id/sender for merchant %s", merchant.id)
                    continue
                if sender_id == entry_id:
                    # Loop guard, mirrors the comments branch above - our
                    # own sent messages must never re-trigger a reply.
                    continue
                inserted_id = _insert_event(
                    db,
                    merchant,
                    channel="instagram_story_reply",
                    external_id=str(message_id),
                    commenter_id=sender_id,
                    media_id=story.get("id"),
                    raw_text=message.get("text"),
                )

            else:
                continue

            if inserted_id is not None:
                enqueued_ids.append(inserted_id)

    db.commit()
    # Enqueue only after the rows are durably committed - a worker that
    # BRPOPs a job whose row isn't visible yet would drop it as unknown.
    redis_client = get_redis()
    for event_id in enqueued_ids:
        enqueue_comment(redis_client, event_id)

    return {"ok": True}


def _resolve_merchant(db: Session, entry_id: str) -> Merchant | None:
    if not entry_id.isdigit():
        return None
    return db.scalar(select(Merchant).where(Merchant.instagram_user_id == int(entry_id)))


# --- OAuth connect flow (the website's "connect Instagram" button) -------

_OAUTH_STATE_TTL_SECONDS = 600


@router.get("/instagram/connect/{webhook_slug}")
def start_connect(webhook_slug: str, db: Session = Depends(get_db)) -> RedirectResponse:
    """Entry point the website button links to. Keyed by webhook_slug, not
    the merchant's primary key: the slug is an unguessable random token
    (and independently rotatable), so it doubles as the capability check
    until the dashboard grows real merchant auth."""
    merchant = db.scalar(select(Merchant).where(Merchant.webhook_slug == webhook_slug))
    if merchant is None:
        raise HTTPException(status_code=404, detail="unknown merchant")

    state = secrets.token_urlsafe(24)
    # The state token is the CSRF guard Meta echoes back to the callback -
    # it maps back to which merchant initiated the flow.
    get_redis().set(f"ig_oauth_state:{state}", str(merchant.id), ex=_OAUTH_STATE_TTL_SECONDS)
    return RedirectResponse(build_authorize_url(state))


@router.get("/instagram/oauth/callback")
def oauth_callback(
    code: str = Query(default=""),
    state: str = Query(default=""),
    db: Session = Depends(get_db),
) -> PlainTextResponse:
    redis_client = get_redis()
    merchant_id = redis_client.get(f"ig_oauth_state:{state}") if state else None
    if not code or merchant_id is None:
        raise HTTPException(status_code=400, detail="invalid or expired oauth state")
    redis_client.delete(f"ig_oauth_state:{state}")  # single-use

    merchant = db.get(Merchant, merchant_id)
    if merchant is None:
        raise HTTPException(status_code=400, detail="merchant no longer exists")

    credentials = exchange_code(code)

    already_connected = db.scalar(
        select(Merchant).where(Merchant.instagram_user_id == credentials.user_id, Merchant.id != merchant.id)
    )
    if already_connected is not None:
        raise HTTPException(status_code=409, detail="this Instagram account is already connected to another merchant")

    merchant.instagram_user_id = credentials.user_id
    merchant.instagram_access_token = credentials.access_token
    merchant.instagram_token_expires_at = credentials.expires_at
    db.commit()

    # The app-level webhook URL (Meta App Dashboard, verified by
    # verify_webhook above) only proves Meta CAN reach us - nothing is
    # actually sent for THIS account until it's individually subscribed.
    # Best-effort: the token connection itself already succeeded and is
    # still useful (media picker, manual sends) even if this one call
    # fails, so a Graph hiccup here shouldn't undo the whole connect.
    try:
        InstagramClient(credentials.access_token).subscribe_webhooks(fields="comments,messages")
    except httpx.HTTPError:
        logger.exception("failed to subscribe merchant %s to Instagram webhooks", merchant.id)
        return PlainTextResponse(
            "Instagram account connected, but webhook subscription failed - comment automation "
            "will not receive events yet. Try reconnecting, or contact support."
        )

    return PlainTextResponse("Instagram account connected. You can close this page.")
