"""Layer 5 - the floor under the pipeline. Every layer above this can
decline to answer (FAQ/intent below their confidence threshold, the LLM
fallback deciding it isn't grounded enough, or a per-customer/per-merchant
budget cap being hit); this is the layer that can't be wrong, because it
doesn't try to answer at all - it hands the conversation to a human. See
the aqlchat-phase1-plan memory for why this was added: without it, a miss
at every automated layer left the customer at a dead end.

Admin interaction goes through the shared platform bot, not the tenant
bot (see app/onboarding/ for how a Telegram identity becomes a
MerchantAdmin). A registered admin handles escalated conversations with
two commands sent to the platform bot:

    /reply <telegram_user_id> <message>   - relay a message to that customer
    /release <telegram_user_id>           - hand the conversation back to the bot

While a conversation's needs_human flag is set, the webhook suppresses all
automated replies on that thread and forwards every customer message to
every registered admin instead - see app/telegram/webhook.py.
"""

import logging
import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.db.models import Conversation, Customer, Merchant, MerchantAdmin, Message
from app.telegram.client import TelegramClient

logger = logging.getLogger(__name__)

# Not merchant-configurable like flows/FAQs are - this is a platform-level
# message about the pipeline's own state, not business content.
_ESCALATION_REPLY = {
    "uz": "Savolingizni sotuvchiga yubordim, tez orada javob beradi.",
    "ru": "Я передал ваш вопрос продавцу, он скоро ответит.",
}
_DEFAULT_LANGUAGE = "uz"


def escalation_reply_text(detected_language: str | None) -> str:
    return _ESCALATION_REPLY.get(detected_language, _ESCALATION_REPLY[_DEFAULT_LANGUAGE])


def _merchant_admins(db: Session, merchant_id: uuid.UUID) -> list[MerchantAdmin]:
    return list(db.scalars(select(MerchantAdmin).where(MerchantAdmin.merchant_id == merchant_id)))


def escalate(
    db: Session,
    merchant: Merchant,
    customer: Customer,
    conversation: Conversation,
    trigger_text: str,
    reason: str,
) -> None:
    """Marks the conversation as needing a human and notifies every
    registered admin via the platform bot. Safe to call with no
    merchant_admins registered - the conversation still gets suppressed,
    just without a real-time nudge to anyone."""
    conversation.needs_human = True
    db.add(conversation)

    admins = _merchant_admins(db, merchant.id)
    if not admins:
        logger.warning("merchant %s has no registered admins - escalation has no notification", merchant.id)
        return

    notification = (
        f"[{reason}] Customer {customer.telegram_user_id}:\n"
        f"{trigger_text}\n\n"
        f"Reply: /reply {customer.telegram_user_id} <message>\n"
        f"Done: /release {customer.telegram_user_id}"
    )
    for admin in admins:
        _notify_admin(admin.telegram_user_id, notification)


def forward_to_admin(db: Session, merchant: Merchant, customer: Customer, text: str) -> None:
    """Relays a further customer message while a conversation is already
    in needs_human state, so admins can actually follow what the customer
    is saying rather than handling it blind."""
    for admin in _merchant_admins(db, merchant.id):
        _notify_admin(admin.telegram_user_id, f"Customer {customer.telegram_user_id}:\n{text}")


def _notify_admin(telegram_user_id: int, text: str) -> None:
    """Never raises - an admin who hasn't /start'd the platform bot yet
    gets a 403 from Telegram on every send, same failure shape as a bad
    tenant token today: logged, swallowed, pipeline keeps going."""
    try:
        TelegramClient(settings.platform_bot_token).send_message(telegram_user_id, text)
    except Exception:
        logger.exception("failed to notify admin %s", telegram_user_id)


@dataclass
class AdminCommandResult:
    confirmation_text: str


def handle_admin_command(db: Session, merchant: Merchant, text: str) -> AdminCommandResult | None:
    """Returns None if `text` isn't a recognized admin command, so the
    caller can fall through to normal handling (e.g. if a merchant ever
    sends a plain message to their own admin chat by mistake)."""
    parts = text.strip().split(maxsplit=2)
    if not parts:
        return None

    command = parts[0].lower()
    if command == "/release" and len(parts) >= 2:
        return _handle_release(db, merchant, parts[1])
    if command == "/reply" and len(parts) >= 3:
        return _handle_reply(db, merchant, parts[1], parts[2])
    return None


def _find_customer_and_conversation(
    db: Session, merchant_id: uuid.UUID, telegram_user_id_str: str
) -> tuple[Customer | None, Conversation | None]:
    try:
        telegram_user_id = int(telegram_user_id_str)
    except ValueError:
        return None, None

    customer = db.scalar(
        select(Customer).where(Customer.merchant_id == merchant_id, Customer.telegram_user_id == telegram_user_id)
    )
    if customer is None:
        return None, None

    conversation = db.scalar(
        select(Conversation)
        .where(Conversation.merchant_id == merchant_id, Conversation.customer_id == customer.id)
        .order_by(Conversation.created_at.desc())
    )
    return customer, conversation


def _handle_release(db: Session, merchant: Merchant, telegram_user_id_str: str) -> AdminCommandResult:
    _, conversation = _find_customer_and_conversation(db, merchant.id, telegram_user_id_str)
    if conversation is None:
        return AdminCommandResult(f"No conversation found for {telegram_user_id_str}.")

    conversation.needs_human = False
    db.add(conversation)
    return AdminCommandResult(f"Released {telegram_user_id_str} back to the bot.")


def _handle_reply(
    db: Session, merchant: Merchant, telegram_user_id_str: str, message_text: str
) -> AdminCommandResult:
    customer, conversation = _find_customer_and_conversation(db, merchant.id, telegram_user_id_str)
    if customer is None:
        return AdminCommandResult(f"No customer found for {telegram_user_id_str}.")

    try:
        TelegramClient(merchant.telegram_bot_token).send_message(customer.telegram_user_id, message_text)
    except Exception:
        logger.exception("failed to relay admin reply to customer %s", customer.telegram_user_id)
        return AdminCommandResult("Failed to send - check the bot token and logs.")

    if conversation is not None:
        db.add(
            Message(
                conversation_id=conversation.id,
                direction="out",
                type="text",
                raw_text=message_text,
                response_source="human",
            )
        )
    return AdminCommandResult(f"Sent to {telegram_user_id_str}.")
