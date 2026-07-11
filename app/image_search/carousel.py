"""Sends and resolves the top-k confirmation carousel (see
app/image_search/retrieval.py and the aqlchat-phase1-plan memory for why
this is a customer-confirmed carousel, not a top-1 auto-reply).

Each candidate is sent as its own photo message with its own inline
button, since Telegram media groups (albums) can't carry per-item inline
keyboards - the button's callback_data directly encodes the log id and
product id, so confirmation is unambiguous and writes straight into
conversations.context without needing to guess which photo the customer
meant.
"""

import io
import logging
import uuid

from PIL import Image
from sqlalchemy.orm import Session

from app.db.models import Conversation, Customer, ImageMatchLog, Merchant, Message, Product
from app.handoff.service import escalate, escalation_reply_text
from app.image_search.embeddings import embed_image
from app.image_search.retrieval import FLOOR, find_candidates
from app.intent.router import format_product_reply
from app.telegram.client import TelegramClient

logger = logging.getLogger(__name__)

DISPLAY_K = 3


def _confirm_button(log_id: uuid.UUID, product_id: uuid.UUID) -> dict:
    return {"inline_keyboard": [[{"text": "Bu shu ✓ / Это оно", "callback_data": f"confirm:{log_id}:{product_id}"}]]}


def _none_button(log_id: uuid.UUID) -> dict:
    return {"inline_keyboard": [[{"text": "Hech biri emas / Ни один", "callback_data": f"none:{log_id}"}]]}


def handle_photo_message(
    db: Session,
    merchant: Merchant,
    conversation: Conversation,
    chat_id: int,
    file_id: str,
) -> bool:
    """Downloads, embeds, searches, and either sends a confirmation
    carousel (returns True) or leaves it for the caller to fall through
    to the generic handoff floor (returns False, e.g. below FLOOR or on a
    download/embedding failure). Sends its own Telegram messages and
    writes its own outbound Message rows when it returns True - the
    caller (app/telegram/webhook.py) treats this as a complete,
    self-contained branch, the same pattern as app/handoff/service.py's
    escalate()."""
    client = TelegramClient(merchant.telegram_bot_token)

    try:
        photo_bytes = client.download_photo(file_id)
        image = Image.open(io.BytesIO(photo_bytes))
        embedding = embed_image(image)
    except Exception:
        logger.exception("failed to download/embed photo for merchant %s", merchant.id)
        return False

    candidates = find_candidates(db, merchant.id, embedding)
    top_score = candidates[0].similarity if candidates else 0.0
    floor_applied = top_score < FLOOR

    log = ImageMatchLog(
        merchant_id=merchant.id,
        conversation_id=conversation.id,
        top_candidates=[{"product_id": str(c.product.id), "similarity": c.similarity} for c in candidates],
        floor_applied=floor_applied,
    )
    db.add(log)
    db.flush()

    if floor_applied or not candidates:
        return False

    shown = candidates[:DISPLAY_K]
    for rank, candidate in enumerate(shown, start=1):
        try:
            client.send_photo(
                chat_id,
                candidate.product.image_url,
                caption=f"{rank}. {candidate.product.name}",
                reply_markup=_confirm_button(log.id, candidate.product.id),
            )
        except Exception:
            logger.exception("failed to send carousel photo for merchant %s", merchant.id)
    try:
        client.send_message(chat_id, "Bularning hech biri to'g'ri emasmi?", reply_markup=_none_button(log.id))
    except Exception:
        logger.exception("failed to send carousel none-button prompt for merchant %s", merchant.id)

    conversation.context = {
        **(conversation.context or {}),
        "last_candidates": [{"product_id": str(c.product.id), "rank": i} for i, c in enumerate(shown, start=1)],
        "last_image_match_log_id": str(log.id),
    }
    db.add(conversation)

    db.add(
        Message(
            conversation_id=conversation.id,
            direction="out",
            type="photo",
            raw_text=f"[carousel: {', '.join(c.product.name for c in shown)}]",
            response_source="image",
        )
    )
    return True


def handle_callback_query(db: Session, merchant: Merchant, customer: Customer, data: str) -> str | None:
    """Parses `confirm:<log_id>:<product_id>` / `none:<log_id>` and
    returns the text to answer the callback query with, or None if `data`
    doesn't match either shape (caller still needs to answer the callback
    query so Telegram stops showing a loading spinner on the button)."""
    parts = data.split(":")
    if len(parts) == 3 and parts[0] == "confirm":
        return _handle_confirm(db, merchant, customer, log_id_str=parts[1], product_id_str=parts[2])
    if len(parts) == 2 and parts[0] == "none":
        return _handle_none(db, merchant, customer, log_id_str=parts[1])
    return None


def _handle_confirm(db: Session, merchant: Merchant, customer: Customer, log_id_str: str, product_id_str: str) -> str:
    log = db.get(ImageMatchLog, uuid.UUID(log_id_str))
    if log is None or log.merchant_id != merchant.id:
        return "Kechirasiz, bu so'rov eskirgan."

    product = db.get(Product, uuid.UUID(product_id_str))
    if product is None:
        return "Kechirasiz, bu mahsulot topilmadi."

    log.confirmed_product_id = product.id
    db.add(log)

    conversation = db.get(Conversation, log.conversation_id)
    conversation.context = {**(conversation.context or {}), "last_matched_product_id": str(product.id)}
    db.add(conversation)

    reply_text = format_product_reply(product)
    db.add(
        Message(
            conversation_id=conversation.id,
            direction="out",
            type="text",
            raw_text=reply_text,
            response_source="image",
        )
    )

    client = TelegramClient(merchant.telegram_bot_token)
    try:
        client.send_message(customer.telegram_user_id, reply_text)
    except Exception:
        logger.exception("failed to send confirmed-product reply for merchant %s", merchant.id)

    return "Rahmat!"


def _handle_none(db: Session, merchant: Merchant, customer: Customer, log_id_str: str) -> str:
    log = db.get(ImageMatchLog, uuid.UUID(log_id_str))
    if log is None or log.merchant_id != merchant.id:
        return "Kechirasiz, bu so'rov eskirgan."

    log.none_tapped = True
    db.add(log)

    conversation = db.get(Conversation, log.conversation_id)
    reply_text = escalation_reply_text(None)
    escalate(db, merchant, customer, conversation, trigger_text="[image carousel: none matched]", reason="image_none_matched")

    db.add(
        Message(
            conversation_id=conversation.id,
            direction="out",
            type="text",
            raw_text=reply_text,
            response_source="handoff",
        )
    )

    client = TelegramClient(merchant.telegram_bot_token)
    try:
        client.send_message(customer.telegram_user_id, reply_text)
    except Exception:
        logger.exception("failed to send handoff reply for merchant %s", merchant.id)

    return "Tushunarli."
