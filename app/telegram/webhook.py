import hmac
import logging
import uuid

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Conversation, Customer, Merchant, Message
from app.db.session import get_db
from app.faq.retrieval import match_faq
from app.flows.executor import match_flow
from app.handoff.service import escalate, escalation_reply_text, forward_to_admin, handle_admin_command
from app.image_search import ordinal
from app.image_search.carousel import handle_callback_query, handle_photo_message
from app.intent.classifier import classify_intent
from app.intent.router import format_product_reply, route_intent
from app.llm.service import get_fallback_reply
from app.nlp.transliteration import normalize
from app.telegram.client import TelegramClient
from app.telegram.schemas import TelegramUpdate

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/telegram", tags=["telegram"])


def _get_or_create_customer(db: Session, merchant_id: uuid.UUID, telegram_user_id: int) -> Customer:
    customer = db.scalar(
        select(Customer).where(
            Customer.merchant_id == merchant_id,
            Customer.telegram_user_id == telegram_user_id,
        )
    )
    if customer is None:
        customer = Customer(merchant_id=merchant_id, telegram_user_id=telegram_user_id)
        db.add(customer)
        db.flush()
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


@router.post("/webhook/{merchant_id}")
def receive_update(
    merchant_id: uuid.UUID,
    update: TelegramUpdate,
    x_telegram_bot_api_secret_token: str | None = Header(default=None),
    db: Session = Depends(get_db),
):
    merchant = db.get(Merchant, merchant_id)
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
        customer = _get_or_create_customer(db, merchant.id, callback_query.from_.id)
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

    # The merchant's own chat with their bot doubles as their admin
    # channel (see app/handoff/service.py) - never runs through the
    # customer pipeline below.
    if merchant.admin_chat_id is not None and message.chat.id == merchant.admin_chat_id and message.text:
        result = handle_admin_command(db, merchant, message.text)
        db.commit()
        if result is not None:
            try:
                TelegramClient(merchant.telegram_bot_token).send_message(message.chat.id, result.confirmation_text)
            except Exception:
                logger.exception("failed to send admin confirmation for merchant %s", merchant.id)
        return {"ok": True}

    customer = _get_or_create_customer(db, merchant.id, message.from_.id)
    conversation = _get_or_create_conversation(db, merchant.id, customer.id)

    msg_type = "photo" if message.photo else "text"

    # Layer 5 already active on this thread: suppress every automated
    # layer below and let the merchant handle it directly via /reply.
    if conversation.needs_human:
        if message.text:
            forward_to_admin(merchant, customer, message.text)
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

    if msg_type == "text" and message.text:
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

    inbound = Message(
        conversation_id=conversation.id,
        direction="in",
        type=msg_type,
        raw_text=message.text,
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
