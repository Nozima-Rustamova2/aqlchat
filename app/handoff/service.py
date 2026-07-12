"""Layer 5 - the floor under the pipeline. Every layer above this can
decline to answer (FAQ/intent below their confidence threshold, the LLM
fallback deciding it isn't grounded enough, or a per-customer/per-merchant
budget cap being hit); this is the layer that can't be wrong, because it
doesn't try to answer at all - it hands the conversation to a human. See
the aqlchat-phase1-plan memory for why this was added: without it, a miss
at every automated layer left the customer at a dead end.

Admin interaction goes through the shared platform bot, not the tenant
bot (see app/onboarding/ for how a Telegram identity becomes a
MerchantAdmin). A registered admin handles an escalation by replying
directly to the platform-bot message it arrived as (resolved via
PendingAdminReply - see app/onboarding/router.py); `/reply
<telegram_user_id> <message>` / `/release <telegram_user_id>` remain as
an explicit typed fallback.

While a conversation's needs_human flag is set, the webhook suppresses all
automated replies on that thread and forwards every customer message to
every registered admin instead - see app/telegram/webhook.py. The flag
lazily releases itself after 30 minutes of inactivity (release_if_stale) -
checked on the one code path that already runs on every inbound message
during an active handoff, not via a background sweep: nothing observable
happens between the timeout and the next customer message anyway, so a
proactive sweep would solve a problem with no consequence.
"""

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.db.models import Conversation, Customer, Merchant, MerchantAdmin, Message, PendingAdminReply, Product
from app.telegram.client import TelegramClient

logger = logging.getLogger(__name__)

# Not merchant-configurable like flows/FAQs are - this is a platform-level
# message about the pipeline's own state, not business content.
_ESCALATION_REPLY = {
    "uz": "Savolingizni sotuvchiga yubordim, tez orada javob beradi.",
    "ru": "Я передал ваш вопрос продавцу, он скоро ответит.",
}
_PRICE_PENDING_REPLY = {
    "uz": "Narxini aniqlashtirmoqdamiz, tez orada javob beramiz.",
    "ru": "Уточняем цену, скоро ответим.",
}
_DEFAULT_LANGUAGE = "uz"
_RELEASE_BUTTON_TEXT = "✅ Botga qaytarish"
_HANDOFF_TIMEOUT = timedelta(minutes=30)


def escalation_reply_text(detected_language: str | None) -> str:
    return _ESCALATION_REPLY.get(detected_language, _ESCALATION_REPLY[_DEFAULT_LANGUAGE])


def price_pending_reply_text(detected_language: str | None) -> str:
    return _PRICE_PENDING_REPLY.get(detected_language, _PRICE_PENDING_REPLY[_DEFAULT_LANGUAGE])


def _merchant_admins(db: Session, merchant_id: uuid.UUID) -> list[MerchantAdmin]:
    return list(db.scalars(select(MerchantAdmin).where(MerchantAdmin.merchant_id == merchant_id)))


def _customer_label(customer: Customer) -> str:
    return customer.first_name or str(customer.telegram_user_id)


def _get_or_create_pending_reply(
    db: Session, merchant_admin_id: uuid.UUID, kind: str, target_id: uuid.UUID
) -> PendingAdminReply:
    """One open thread per (admin, kind, target) - a repeat escalation on
    the same conversation reuses the still-open row (so a Telegram-reply
    to any earlier message in the thread still resolves), while a fresh
    escalation after a previous release starts a new one (the old row's
    status is no longer "open", so it's not found here)."""
    row = db.scalar(
        select(PendingAdminReply).where(
            PendingAdminReply.merchant_admin_id == merchant_admin_id,
            PendingAdminReply.kind == kind,
            PendingAdminReply.target_id == target_id,
            PendingAdminReply.status == "open",
        )
    )
    if row is None:
        row = PendingAdminReply(merchant_admin_id=merchant_admin_id, kind=kind, target_id=target_id)
        db.add(row)
        db.flush()
    return row


def _notify_admin(telegram_user_id: int, text: str, reply_markup: dict | None = None) -> dict | None:
    """Never raises - an admin who hasn't /start'd the platform bot yet
    gets a 403 from Telegram on every send, same failure shape as a bad
    tenant token today: logged, swallowed, pipeline keeps going. Returns
    the raw API result (to capture the sent message's id) or None on
    failure."""
    try:
        return TelegramClient(settings.platform_bot_token).send_message(telegram_user_id, text, reply_markup=reply_markup)
    except Exception:
        logger.exception("failed to notify admin %s", telegram_user_id)
        return None


def _record_notification(db: Session, pending: PendingAdminReply, result: dict | None) -> None:
    if result is None:
        return
    message_id = result.get("result", {}).get("message_id")
    if message_id is None:
        return
    pending.platform_message_ids = [*pending.platform_message_ids, message_id]
    pending.last_activity_at = datetime.now(timezone.utc)
    db.add(pending)


def escalate(
    db: Session,
    merchant: Merchant,
    customer: Customer,
    conversation: Conversation,
    trigger_text: str,
    reason: str,
) -> None:
    """Marks the conversation as needing a human and notifies every
    registered admin via the platform bot, each in their own thread
    (PendingAdminReply). Safe to call with no merchant_admins registered -
    the conversation still gets suppressed, just without a real-time
    nudge to anyone. `reason` is caller-facing documentation only (not
    shown in the notification text) - bake anything that should be
    visible to the admin directly into `trigger_text`."""
    conversation.needs_human = True
    db.add(conversation)

    admins = _merchant_admins(db, merchant.id)
    if not admins:
        logger.warning("merchant %s has no registered admins - escalation has no notification", merchant.id)
        return

    notification = f'{_customer_label(customer)}: "{trigger_text}"'
    reply_markup = None  # attached per-admin below, once we know the pending row's id

    for admin in admins:
        pending = _get_or_create_pending_reply(db, admin.id, "escalation", conversation.id)
        reply_markup = {"inline_keyboard": [[{"text": _RELEASE_BUTTON_TEXT, "callback_data": f"release:{pending.id}"}]]}
        result = _notify_admin(admin.telegram_user_id, notification, reply_markup=reply_markup)
        _record_notification(db, pending, result)


def forward_to_admin(db: Session, merchant: Merchant, customer: Customer, conversation: Conversation, text: str) -> None:
    """Relays a further customer message while a conversation is already
    in needs_human state, threaded onto the same PendingAdminReply so
    admins can reply to it too, not just the original escalation."""
    for admin in _merchant_admins(db, merchant.id):
        pending = _get_or_create_pending_reply(db, admin.id, "escalation", conversation.id)
        result = _notify_admin(admin.telegram_user_id, f"{_customer_label(customer)}: {text}")
        _record_notification(db, pending, result)


def queue_price_query(db: Session, merchant: Merchant, product: Product, trigger_text: str) -> None:
    """Notifies every registered admin that a product needs a price,
    threaded per-admin like escalate() - a merchant's Telegram-reply to
    this resolves via app/onboarding/router.py's reply-to-message
    dispatch, the same mechanism as escalation replies (kind="price_query"
    instead of "escalation", target_id is the product, not a conversation
    - doesn't touch needs_human at all, since a customer waiting on a
    price isn't otherwise stuck).

    Reusable across forward-match (a customer asked about a still-unpriced
    product - app/telegram/webhook.py) and passive channel ingestion (a
    new post came in with no price - checkpoint 4). get-or-create plus
    the already-notified check means repeated triggers for the same
    product don't spam a fresh notification every time."""
    admins = _merchant_admins(db, merchant.id)
    if not admins:
        logger.warning("merchant %s has no registered admins - price query has no notification", merchant.id)
        return

    for admin in admins:
        pending = _get_or_create_pending_reply(db, admin.id, "price_query", product.id)
        if pending.platform_message_ids:
            continue  # already notified this admin about this product, thread still open
        result = _notify_admin(admin.telegram_user_id, trigger_text)
        _record_notification(db, pending, result)


def release_conversation(db: Session, conversation: Conversation) -> None:
    """Shared by the /release text command, the inline release button,
    and the lazy timeout below - one place that flips needs_human and
    closes out every admin's open thread for this conversation."""
    conversation.needs_human = False
    db.add(conversation)
    open_rows = db.scalars(
        select(PendingAdminReply).where(
            PendingAdminReply.kind == "escalation",
            PendingAdminReply.target_id == conversation.id,
            PendingAdminReply.status == "open",
        )
    )
    for row in open_rows:
        row.status = "released"
        db.add(row)


def release_if_stale(db: Session, conversation: Conversation) -> bool:
    """Lazy expiry, checked inline on the needs_human path in
    app/telegram/webhook.py rather than via a background sweep - the
    flag's only observable effect is what happens on the *next* customer
    message, and that's exactly when this runs. Returns True if it just
    released (caller should treat the message as arriving on a
    fresh/non-suppressed conversation)."""
    if not conversation.needs_human:
        return False

    open_rows = list(
        db.scalars(
            select(PendingAdminReply).where(
                PendingAdminReply.kind == "escalation",
                PendingAdminReply.target_id == conversation.id,
                PendingAdminReply.status == "open",
            )
        )
    )
    if not open_rows:
        return False

    # last_activity_at is stored as TIMESTAMP WITHOUT TIME ZONE (matching
    # every other datetime column in this schema), always written from an
    # aware UTC value - but whether the in-memory value is aware or naive
    # depends on whether it's a fresh Python-side default (aware, from
    # _now()) or came back from a DB round-trip (naive - Postgres strips
    # tzinfo on the way in for this column type). Normalize before
    # comparing so both cases work.
    most_recent = max(row.last_activity_at for row in open_rows)
    if most_recent.tzinfo is not None:
        most_recent = most_recent.astimezone(timezone.utc).replace(tzinfo=None)
    now_naive = datetime.now(timezone.utc).replace(tzinfo=None)
    if now_naive - most_recent < _HANDOFF_TIMEOUT:
        return False

    release_conversation(db, conversation)
    return True


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

    release_conversation(db, conversation)
    return AdminCommandResult(f"Released {telegram_user_id_str} back to the bot.")


def _handle_reply(
    db: Session, merchant: Merchant, telegram_user_id_str: str, message_text: str
) -> AdminCommandResult:
    customer, conversation = _find_customer_and_conversation(db, merchant.id, telegram_user_id_str)
    if customer is None:
        return AdminCommandResult(f"No customer found for {telegram_user_id_str}.")

    if not reply_to_customer(db, merchant, customer, conversation, message_text):
        return AdminCommandResult("Failed to send - check the bot token and logs.")
    return AdminCommandResult(f"Sent to {telegram_user_id_str}.")


def reply_to_customer(
    db: Session, merchant: Merchant, customer: Customer, conversation: Conversation | None, message_text: str
) -> bool:
    """Relays an admin's reply to the customer via the TENANT bot (not
    the platform bot - the customer only ever talks to their merchant's
    own bot). Shared by the typed /reply fallback and the
    reply-to-message dispatch (app/onboarding/router.py). Returns False
    on send failure, so callers can report it without raising."""
    try:
        TelegramClient(merchant.telegram_bot_token).send_message(customer.telegram_user_id, message_text)
    except Exception:
        logger.exception("failed to relay admin reply to customer %s", customer.telegram_user_id)
        return False

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
    return True
