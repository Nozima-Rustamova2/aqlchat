import hmac
import logging
import uuid

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Conversation, Customer, Merchant, Message, Product
from app.db.session import get_db
from app.faq.retrieval import match_faq
from app.flows.executor import match_flow
from app.handoff.service import (
    escalate,
    escalation_reply_text,
    forward_to_admin,
    price_pending_reply_text,
    queue_price_query,
    release_if_stale,
)
from app.image_search import ordinal
from app.image_search.carousel import handle_callback_query, handle_photo_message
from app.intent.classifier import classify_intent
from app.intent.router import format_product_reply, route_intent
from app.llm.service import get_fallback_reply
from app.nlp.transliteration import normalize
from app.products.ingestion import ingest_post
from app.telegram.client import TelegramClient
from app.telegram.schemas import TelegramMessage, TelegramUpdate

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/telegram", tags=["telegram"])


def _get_or_create_customer(
    db: Session, merchant_id: uuid.UUID, telegram_user_id: int, first_name: str | None = None
) -> Customer:
    customer = db.scalar(
        select(Customer).where(
            Customer.merchant_id == merchant_id,
            Customer.telegram_user_id == telegram_user_id,
        )
    )
    if customer is None:
        customer = Customer(merchant_id=merchant_id, telegram_user_id=telegram_user_id, first_name=first_name)
        db.add(customer)
        db.flush()
    elif first_name and customer.first_name != first_name:
        customer.first_name = first_name
        db.add(customer)
    return customer


def _get_or_create_conversation(db: Session, merchant_id: uuid.UUID, customer_id: uuid.UUID) -> Conversation:
    conversation = db.scalar(
        select(Conversation)
        .where(Conversation.merchant_id == merchant_id, Conversation.customer_id == customer_id)
        .order_by(Conversation.created_at.desc())
    )
    if conversation is None:
        conversation = Conversation(merchant_id=merchant_id, customer_id=customer_id)
        db.add(conversation)
        db.flush()
    return conversation


def _resolve_forward_origin(message: TelegramMessage) -> tuple[int, int] | None:
    """Returns (chat_id, message_id) a message was forwarded from, or
    None if it wasn't forwarded from a channel. Telegram deprecated
    forward_from_chat/forward_from_message_id in Bot API 7.0 in favor of
    the unified forward_origin, but both may still be populated
    depending on API version - forward_origin is preferred, legacy
    fields are the fallback. NOT yet independently live-verified against
    a real forwarded channel post (see the pivot plan's Flag 6) - do
    that check before fully trusting this in production."""
    origin = message.forward_origin
    if origin is not None and origin.get("type") == "channel":
        chat = origin.get("chat") or {}
        chat_id = chat.get("id")
        origin_message_id = origin.get("message_id")
        if chat_id is not None and origin_message_id is not None:
            return chat_id, origin_message_id

    if message.forward_from_chat is not None and message.forward_from_message_id is not None:
        return message.forward_from_chat.id, message.forward_from_message_id

    return None


def _handle_forward_match(
    db: Session, merchant: Merchant, customer: Customer, conversation: Conversation, message: TelegramMessage
) -> tuple[str, str, str | None] | None:
    """The highest-priority product-resolution layer: a customer
    forwarding a channel post carries a deterministic pointer to the
    exact product (forward chat + message id) - free, instant, precision
    1.0, beating image search outright for this case (which stays for
    screenshots/own-photos - see the fallthrough below). Cheap and
    deterministic, not an LLM call, same invariant as every other layer
    above Layer 5.

    Returns (reply_text, response_source, detected_language) if this
    message resolved here, or None to fall through to the normal
    pipeline - either it wasn't a forward at all, or it was a forward
    from some OTHER channel entirely (a competitor screenshot etc.),
    which the existing image-search path already handles."""
    origin = _resolve_forward_origin(message)
    if origin is None:
        return None
    forward_chat_id, forward_message_id = origin

    caption_or_text = message.caption or message.text
    detected_language = normalize(caption_or_text).detected_language if caption_or_text else None

    product = db.scalar(
        select(Product).where(
            Product.merchant_id == merchant.id,
            Product.source_channel_id == forward_chat_id,
            Product.source_message_id == forward_message_id,
        )
    )

    if product is None:
        if merchant.source_channel_id is None or forward_chat_id != merchant.source_channel_id:
            return None
        # Not a known product yet, but it came from this merchant's own
        # registered channel - lazy-ingest it now instead of dead-ending.
        photo_file_id = message.photo[-1].file_id if message.photo else None
        product = ingest_post(db, merchant, forward_chat_id, forward_message_id, photo_file_id, caption_or_text)

    conversation.context = {**(conversation.context or {}), "last_matched_product_id": str(product.id)}
    db.add(conversation)

    if product.price_status != "set":
        queue_price_query(
            db,
            merchant,
            product,
            trigger_text=f"\U0001f4b0 Mijoz so'radi: {product.name} - narxi hali kiritilmagan.",
        )
        reply_text = price_pending_reply_text(detected_language)
    else:
        reply_text = format_product_reply(product)

    return reply_text, "forward_match", detected_language


@router.post("/webhook/tenant/{webhook_slug}")
def receive_update(
    webhook_slug: str,
    update: TelegramUpdate,
    x_telegram_bot_api_secret_token: str | None = Header(default=None),
    db: Session = Depends(get_db),
):
    merchant = db.scalar(select(Merchant).where(Merchant.webhook_slug == webhook_slug))
    if merchant is None:
        raise HTTPException(status_code=404, detail="unknown merchant")

    if not x_telegram_bot_api_secret_token or not hmac.compare_digest(
        x_telegram_bot_api_secret_token, merchant.webhook_secret
    ):
        raise HTTPException(status_code=403, detail="invalid secret token")

    # Inline-button taps on the image-search carousel (app/image_search/)
    # arrive as a separate update type, not a message.
    if update.callback_query is not None:
        callback_query = update.callback_query
        customer = _get_or_create_customer(
            db, merchant.id, callback_query.from_.id, callback_query.from_.first_name
        )
        answer_text = handle_callback_query(db, merchant, customer, callback_query.data or "")
        db.commit()
        try:
            TelegramClient(merchant.telegram_bot_token).answer_callback_query(callback_query.id, text=answer_text)
        except Exception:
            logger.exception("failed to answer callback query for merchant %s", merchant.id)
        return {"ok": True}

    if update.message is None or update.message.from_ is None:
        # Update types we don't handle yet (edited messages, channel posts, etc.)
        return {"ok": True}

    message = update.message

    customer = _get_or_create_customer(db, merchant.id, message.from_.id, message.from_.first_name)
    conversation = _get_or_create_conversation(db, merchant.id, customer.id)

    msg_type = "photo" if message.photo else "text"

    # Layer 5 already active on this thread: suppress every automated
    # layer below and let the merchant handle it directly. release_if_stale
    # lazily expires a conversation nobody's touched in 30 minutes - if it
    # just released, fall through to normal handling below instead of
    # suppressing this message too.
    if conversation.needs_human and not release_if_stale(db, conversation):
        if message.text:
            forward_to_admin(db, merchant, customer, conversation, message.text)
        db.add(
            Message(
                conversation_id=conversation.id,
                direction="in",
                type=msg_type,
                raw_text=message.text,
                raw_update=update.model_dump(mode="json", by_alias=True),
            )
        )
        db.commit()
        return {"ok": True}

    normalized_text: str | None = None
    detected_language: str | None = None
    reply_text: str | None = None
    response_source: str | None = None
    match_confidence: float | None = None
    carousel_sent = False

    forward_result = _handle_forward_match(db, merchant, customer, conversation, message)

    if forward_result is not None:
        reply_text, response_source, detected_language = forward_result
    elif msg_type == "text" and message.text:
        result = normalize(message.text)
        normalized_text = result.normalized_text
        detected_language = result.detected_language

        # A carousel was just shown - "ikkinchisi narxi qancha?" ("how
        # much is the second one?") should resolve against it before
        # trying the generic chain below, which has no way to know what
        # "the second one" refers to.
        ordinal_match = ordinal.resolve(db, conversation, normalized_text)
        if ordinal_match is not None:
            reply_text = format_product_reply(ordinal_match.product)
            response_source = "intent"
        else:
            matched_flow = match_flow(db, merchant.id, normalized_text)
            if matched_flow is not None:
                reply_text = matched_flow.response_config["text"]
                response_source = "rule"
            else:
                faq_match = match_faq(db, merchant.id, normalized_text, detected_language)
                if faq_match is not None:
                    reply_text = faq_match.reply_text
                    response_source = "faq"
                    match_confidence = faq_match.similarity
                else:
                    intent_match = classify_intent(normalized_text)
                    routed = route_intent(db, merchant.id, intent_match, normalized_text) if intent_match else None
                    if routed is not None:
                        reply_text = routed.reply_text
                        response_source = "intent"
                        match_confidence = routed.similarity
                    else:
                        fallback = get_fallback_reply(
                            db, merchant.id, customer.id, conversation.id, normalized_text, detected_language
                        )
                        if fallback is not None:
                            reply_text = fallback.answer
                            response_source = "llm"
    elif msg_type == "photo" and message.photo:
        # The largest PhotoSize is last in Telegram's array.
        carousel_sent = handle_photo_message(db, merchant, conversation, message.chat.id, message.photo[-1].file_id)
        if carousel_sent:
            response_source = "image"
        elif conversation.context and conversation.context.get("last_matched_product_id"):
            # No confident image match, but this thread was just
            # discussing a specific product - likely a payment receipt,
            # not another product photo. Route to the merchant instead of
            # falling into the generic "unhandled" escalation below (no
            # payment processing, no orders table - routing only).
            product = db.get(Product, uuid.UUID(conversation.context["last_matched_product_id"]))
            product_label = product.name if product else "mahsulot"
            escalate(
                db,
                merchant,
                customer,
                conversation,
                trigger_text=f"\U0001f4b0 To'lov cheki bo'lishi mumkin - {product_label}",
                reason="payment",
            )
            reply_text = escalation_reply_text(detected_language)
            response_source = "handoff"

    inbound = Message(
        conversation_id=conversation.id,
        direction="in",
        type=msg_type,
        # Channel-post forwards carry their content in `caption`, not
        # `text` (see TelegramMessage) - fall back to it so a forwarded
        # photo's original caption still gets logged.
        raw_text=message.text or message.caption,
        detected_language=detected_language,
        normalized_text=normalized_text,
        response_source=response_source,
        match_confidence=match_confidence,
        raw_update=update.model_dump(mode="json", by_alias=True),
    )
    db.add(inbound)

    # Nothing above answered - this is the pipeline floor (Layer 5), not
    # a dead end. A sent carousel already handled the reply itself (its
    # own Telegram messages + outbound Message row), so it skips this.
    if reply_text is None and not carousel_sent:
        escalate(
            db,
            merchant,
            customer,
            conversation,
            trigger_text=normalized_text or (message.text if message.text else "[photo]"),
            reason="unhandled",
        )
        reply_text = escalation_reply_text(detected_language)
        response_source = "handoff"

    if reply_text is not None:
        try:
            TelegramClient(merchant.telegram_bot_token).send_message(message.chat.id, reply_text)
        except Exception:
            logger.exception("failed to send Telegram reply for merchant %s", merchant.id)
        db.add(
            Message(
                conversation_id=conversation.id,
                direction="out",
                type="text",
                raw_text=reply_text,
                response_source=response_source,
            )
        )

    db.commit()

    return {"ok": True}
