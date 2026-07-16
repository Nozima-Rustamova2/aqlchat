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

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.config import settings
from app.db.models import CommentEvent, Merchant
from app.db.session import get_db
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


@router.post("/instagram/webhook")
async def receive_webhook(request: Request, db: Session = Depends(get_db)) -> dict:
    raw_body = await request.body()
    if not _signature_is_valid(raw_body, request.headers.get("X-Hub-Signature-256")):
        raise HTTPException(status_code=403, detail="invalid signature")

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
            if change.get("field") != "comments":
                continue
            value = change.get("value", {})
            comment_id = value.get("id")
            commenter_id = str((value.get("from") or {}).get("id", ""))
            if not comment_id or not commenter_id:
                logger.warning("instagram comment change missing id/from for merchant %s", merchant.id)
                continue
            if commenter_id == entry_id:
                # Loop guard: our own public replies arrive back on this
                # webhook. Dropped at ingress so they never occupy a
                # comment_events row or a queue slot.
                continue

            inserted_id = db.execute(
                insert(CommentEvent)
                .values(
                    merchant_id=merchant.id,
                    channel="instagram_comment",
                    external_id=str(comment_id),
                    commenter_id=commenter_id,
                    media_id=str((value.get("media") or {}).get("id", "")) or None,
                    raw_text=value.get("text"),
                    status="pending",
                )
                .on_conflict_do_nothing(constraint="uq_comment_events_merchant_external")
                .returning(CommentEvent.id)
            ).scalar()
            if inserted_id is not None:  # None = duplicate delivery, already recorded
                enqueued_ids.append(str(inserted_id))

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

    return PlainTextResponse("Instagram account connected. You can close this page.")
