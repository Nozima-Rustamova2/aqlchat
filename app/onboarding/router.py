"""The platform bot's webhook - handles self-serve onboarding
(app/onboarding/service.py) and dispatches admin interaction (Telegram-
replies to an escalation, /reply, /release, see app/handoff/service.py).
Distinct from app/telegram/webhook.py's tenant route: this is a single
fixed endpoint shared by every merchant, resolved by identity (who's
texting) rather than by URL.
"""

import hmac
import logging
import uuid

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.db.models import Conversation, Customer, Merchant, MerchantAdmin, PendingAdminReply
from app.db.session import get_db
from app.handoff.service import handle_admin_command, release_conversation, reply_to_customer
from app.onboarding import service
from app.telegram.client import TelegramClient
from app.telegram.schemas import TelegramUpdate

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/telegram", tags=["platform"])


@router.post("/webhook/platform")
def receive_platform_update(
    update: TelegramUpdate,
    x_telegram_bot_api_secret_token: str | None = Header(default=None),
    db: Session = Depends(get_db),
):
    if not x_telegram_bot_api_secret_token or not hmac.compare_digest(
        x_telegram_bot_api_secret_token, settings.platform_webhook_secret
    ):
        raise HTTPException(status_code=403, detail="invalid secret token")

    if update.callback_query is not None:
        callback_query = update.callback_query
        data = callback_query.data or ""
        if data.startswith("release:"):
            _handle_release_button(db, callback_query.from_.id, data.removeprefix("release:"))
            db.commit()
            return {"ok": True}
        chat_id = callback_query.message.chat.id if callback_query.message else callback_query.from_.id
        service.handle_platform_callback(db, callback_query.from_.id, chat_id, data)
        db.commit()
        return {"ok": True}

    if update.message is None or update.message.from_ is None or update.message.text is None:
        return {"ok": True}

    message = update.message
    telegram_user_id = message.from_.id
    text = message.text

    if text == "/start":
        service.handle_start(db, telegram_user_id, message.chat.id)
        db.commit()
        return {"ok": True}

    # An incoming admin command is resolved by identity, not URL - look up
    # which merchant (if any) this Telegram identity administers before
    # falling through to onboarding's own (session-state-gated) text
    # handling. See app/handoff/service.py and app/db/models.py's
    # MerchantAdmin docstring for why telegram_user_id is enough on its
    # own to resolve this unambiguously.
    merchant_admin = db.scalar(select(MerchantAdmin).where(MerchantAdmin.telegram_user_id == telegram_user_id))
    if merchant_admin is not None:
        if message.reply_to_message is not None:
            handled = _handle_reply_to_message(db, merchant_admin, message.reply_to_message.message_id, text)
            if handled:
                db.commit()
                return {"ok": True}
            # Not a reply to a thread we're tracking - fall through to
            # the explicit /reply, /release typed fallback below.

        merchant = db.get(Merchant, merchant_admin.merchant_id)
        result = handle_admin_command(db, merchant, text)
        db.commit()
        if result is not None:
            try:
                TelegramClient(settings.platform_bot_token).send_message(message.chat.id, result.confirmation_text)
            except Exception:
                logger.exception("failed to send admin confirmation to %s", telegram_user_id)
            return {"ok": True}
        # Not a recognized admin command - an already-registered admin
        # has nothing else onboarding would do with free text, so this
        # just falls through to a no-op below.

    service.handle_platform_message(db, telegram_user_id, message.chat.id, message.message_id, text)
    db.commit()
    return {"ok": True}


def _handle_release_button(db: Session, telegram_user_id: int, pending_id_str: str) -> None:
    try:
        pending_id = uuid.UUID(pending_id_str)
    except ValueError:
        return
    pending = db.get(PendingAdminReply, pending_id)
    if pending is None:
        return
    admin = db.get(MerchantAdmin, pending.merchant_admin_id)
    if admin is None or admin.telegram_user_id != telegram_user_id:
        return  # not this admin's own thread - ignore silently
    conversation = db.get(Conversation, pending.target_id)
    if conversation is None:
        return
    release_conversation(db, conversation)


def _handle_reply_to_message(
    db: Session, merchant_admin: MerchantAdmin, replied_message_id: int, text: str
) -> bool:
    """Resolves a Telegram-reply to whichever PendingAdminReply thread it
    belongs to (matched by this admin's own id + the message being
    replied to) and dispatches by kind. Returns False if this reply
    doesn't match any open thread - not every reply-to-message sent to
    the platform bot is necessarily replying to an escalation."""
    pending = db.scalar(
        select(PendingAdminReply).where(
            PendingAdminReply.merchant_admin_id == merchant_admin.id,
            PendingAdminReply.status == "open",
            PendingAdminReply.platform_message_ids.any(replied_message_id),
        )
    )
    if pending is None:
        return False

    if pending.kind == "escalation":
        conversation = db.get(Conversation, pending.target_id)
        if conversation is None:
            return False
        merchant = db.get(Merchant, merchant_admin.merchant_id)
        customer = db.get(Customer, conversation.customer_id)
        reply_to_customer(db, merchant, customer, conversation, text)
        return True

    # kind == "price_query" lands in checkpoint 4, once
    # app/nlp/price_parsing.py exists to parse the reply text into a
    # price - no price_query rows can be created before then anyway.
    return False
