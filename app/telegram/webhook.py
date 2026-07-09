import hmac
import uuid

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Conversation, Customer, Merchant, Message
from app.db.session import get_db
from app.telegram.schemas import TelegramUpdate

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

    if update.message is None or update.message.from_ is None:
        # Update types we don't handle yet (edited messages, channel posts, etc.)
        return {"ok": True}

    message = update.message
    customer = _get_or_create_customer(db, merchant.id, message.from_.id)
    conversation = _get_or_create_conversation(db, merchant.id, customer.id)

    msg_type = "photo" if message.photo else "text"
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
