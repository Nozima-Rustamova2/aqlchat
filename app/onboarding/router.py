"""The platform bot's webhook - handles self-serve onboarding
(app/onboarding/service.py) and dispatches admin commands (/reply,
/release, see app/handoff/service.py). Distinct from
app/telegram/webhook.py's tenant route: this is a single fixed endpoint
shared by every merchant, resolved by identity (who's texting) rather
than by URL.
"""

import hmac
import logging

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.db.models import Merchant, MerchantAdmin
from app.db.session import get_db
from app.handoff.service import handle_admin_command
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
        chat_id = callback_query.message.chat.id if callback_query.message else callback_query.from_.id
        service.handle_platform_callback(db, callback_query.from_.id, chat_id, callback_query.data or "")
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
