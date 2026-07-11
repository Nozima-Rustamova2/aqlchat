"""Self-serve merchant onboarding through the shared platform bot -
replaces hand-seeding every merchant via scripts/seed_merchant.py (still
kept for scripted/test fixtures, see that script's docstring).

Crude state machine, no framework, one PlatformOnboardingSession row per
telegram_user_id: start -> awaiting_token -> choosing_vertical -> done.
A telegram identity that's already a MerchantAdmin anywhere is refused at
/start - one Telegram identity administers exactly one merchant in v1
(see app/db/models.py's MerchantAdmin docstring for why).

All Telegram I/O for this flow happens here, not in the router - this
module owns the exact sequencing (delete the pasted token immediately,
*then* send the next prompt) that a request/response-shaped view in the
router would obscure.
"""

import logging
import secrets

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.db.models import Merchant, MerchantAdmin, PlatformOnboardingSession
from app.telegram.client import TelegramClient

logger = logging.getLogger(__name__)

STATE_START = "start"
STATE_AWAITING_TOKEN = "awaiting_token"
STATE_CHOOSING_VERTICAL = "choosing_vertical"
STATE_DONE = "done"

_VERTICALS = {
    "clothing": {"uz": "Kiyim-kechak", "ru": "Одежда"},
    "cosmetics": {"uz": "Kosmetika", "ru": "Косметика"},
    "other": {"uz": "Boshqa", "ru": "Другое"},
}

_ALREADY_REGISTERED = (
    "Siz allaqachon administrator sifatida ro'yxatdan o'tgansiz.\n"
    "Вы уже зарегистрированы как администратор."
)
_CHOOSE_LANGUAGE = "Tilni tanlang / Выберите язык:"
_BOTFATHER_INSTRUCTIONS = {
    "uz": (
        "Telegram botingizni yarating:\n"
        "1. @BotFather ga o'ting, /newbot yuboring\n"
        "2. Bot nomi va username so'raladi\n"
        "3. Sizga beriladigan tokenni shu yerga joylang"
    ),
    "ru": (
        "Создайте своего Telegram-бота:\n"
        "1. Перейдите к @BotFather, отправьте /newbot\n"
        "2. Укажите имя и username бота\n"
        "3. Вставьте сюда полученный токен"
    ),
}
_INVALID_TOKEN = {
    "uz": "Bu token ishlamayapti. Qaytadan urinib ko'ring.",
    "ru": "Этот токен не работает. Попробуйте ещё раз.",
}
_CHOOSE_VERTICAL = {
    "uz": "Sohangizni tanlang:",
    "ru": "Выберите сферу деятельности:",
}
_DONE = {
    "uz": "Tayyor! Botingiz ishga tayyor: @{username}",
    "ru": "Готово! Ваш бот готов к работе: @{username}",
}
_WEBHOOK_REGISTRATION_FAILED = {
    "uz": "\n\n(Ogohlantirish: webhook avtomatik ro'yxatga olinmadi - administratorga xabar bering.)",
    "ru": "\n\n(Предупреждение: webhook не удалось зарегистрировать автоматически - сообщите администратору.)",
}


def _platform_client() -> TelegramClient:
    return TelegramClient(settings.platform_bot_token)


def _get_or_create_session(db: Session, telegram_user_id: int) -> PlatformOnboardingSession:
    session = db.get(PlatformOnboardingSession, telegram_user_id)
    if session is None:
        session = PlatformOnboardingSession(telegram_user_id=telegram_user_id, state=STATE_START)
        db.add(session)
        db.flush()
    return session


def _validate_token(token: str) -> dict | None:
    """A bare httpx call, not TelegramClient - this is validating a
    *candidate* token that isn't known-good yet, not making an
    authenticated call on behalf of an already-registered bot."""
    try:
        response = httpx.get(f"{settings.telegram_api_base}/bot{token}/getMe", timeout=10)
        response.raise_for_status()
        return response.json()["result"]
    except Exception:
        return None


def handle_start(db: Session, telegram_user_id: int, chat_id: int) -> None:
    existing_admin = db.scalar(select(MerchantAdmin).where(MerchantAdmin.telegram_user_id == telegram_user_id))
    if existing_admin is not None:
        _send(chat_id, _ALREADY_REGISTERED)
        return

    session = _get_or_create_session(db, telegram_user_id)
    session.state = STATE_START
    session.language = None
    session.merchant_id = None
    db.add(session)

    _send(
        chat_id,
        _CHOOSE_LANGUAGE,
        reply_markup={
            "inline_keyboard": [
                [
                    {"text": "O'zbekcha", "callback_data": "lang:uz"},
                    {"text": "Русский", "callback_data": "lang:ru"},
                ]
            ]
        },
    )


def handle_platform_message(db: Session, telegram_user_id: int, chat_id: int, message_id: int, text: str) -> None:
    """Text outside the awaiting_token state is a no-op - onboarding only
    reads free text at the "paste your bot token" step; every other step
    is button-driven."""
    session = db.get(PlatformOnboardingSession, telegram_user_id)
    if session is None or session.state != STATE_AWAITING_TOKEN:
        return
    _handle_token_text(db, session, chat_id, message_id, text)


def handle_platform_callback(db: Session, telegram_user_id: int, chat_id: int, callback_data: str) -> None:
    session = db.get(PlatformOnboardingSession, telegram_user_id)
    if session is None:
        return

    if callback_data.startswith("lang:") and session.state == STATE_START:
        _handle_language_pick(db, session, chat_id, callback_data.removeprefix("lang:"))
    elif callback_data.startswith("vertical:") and session.state == STATE_CHOOSING_VERTICAL:
        _handle_vertical_pick(db, session, chat_id, callback_data.removeprefix("vertical:"))


def _handle_language_pick(db: Session, session: PlatformOnboardingSession, chat_id: int, language: str) -> None:
    if language not in ("uz", "ru"):
        return
    session.language = language
    session.state = STATE_AWAITING_TOKEN
    db.add(session)
    _send(chat_id, _BOTFATHER_INSTRUCTIONS[language])


def _handle_token_text(
    db: Session, session: PlatformOnboardingSession, chat_id: int, message_id: int, text: str
) -> None:
    token = text.strip()
    language = session.language or "uz"

    bot_info = _validate_token(token)

    # Never leave the pasted token sitting in the chat, valid or not.
    try:
        _platform_client().delete_message(chat_id, message_id)
    except Exception:
        logger.exception("failed to delete pasted token message for chat %s", chat_id)

    if bot_info is None:
        _send(chat_id, _INVALID_TOKEN[language])
        return

    merchant = Merchant(
        name=bot_info.get("first_name") or bot_info.get("username") or "Unnamed merchant",
        telegram_bot_token=token,
        telegram_bot_id=bot_info["id"],
        webhook_secret=secrets.token_urlsafe(32),
    )
    db.add(merchant)
    db.flush()

    db.add(MerchantAdmin(merchant_id=merchant.id, telegram_user_id=session.telegram_user_id))

    session.state = STATE_CHOOSING_VERTICAL
    session.merchant_id = merchant.id
    db.add(session)

    webhook_registered = _register_tenant_webhook(merchant, token)

    text_out = _CHOOSE_VERTICAL[language]
    if not webhook_registered:
        text_out += _WEBHOOK_REGISTRATION_FAILED[language]

    _send(
        chat_id,
        text_out,
        reply_markup={
            "inline_keyboard": [
                [{"text": labels[language], "callback_data": f"vertical:{key}"}]
                for key, labels in _VERTICALS.items()
            ]
        },
    )


def _register_tenant_webhook(merchant: Merchant, token: str) -> bool:
    if not settings.public_base_url:
        logger.warning("PUBLIC_BASE_URL not configured - skipping tenant webhook registration for %s", merchant.id)
        return False
    try:
        url = f"{settings.public_base_url}/telegram/webhook/tenant/{merchant.webhook_slug}"
        TelegramClient(token).set_webhook(url, merchant.webhook_secret)
        return True
    except Exception:
        logger.exception("failed to register tenant webhook for merchant %s", merchant.id)
        return False


def _handle_vertical_pick(db: Session, session: PlatformOnboardingSession, chat_id: int, vertical: str) -> None:
    if vertical not in _VERTICALS or session.merchant_id is None:
        return

    merchant = db.get(Merchant, session.merchant_id)
    if merchant is None:
        return
    merchant.vertical = vertical
    db.add(merchant)

    session.state = STATE_DONE
    db.add(session)

    language = session.language or "uz"
    username = None
    bot_info = _validate_token(merchant.telegram_bot_token)
    if bot_info is not None:
        username = bot_info.get("username")

    _send(chat_id, _DONE[language].format(username=username or "?"))


def _send(chat_id: int, text: str, reply_markup: dict | None = None) -> None:
    try:
        _platform_client().send_message(chat_id, text, reply_markup=reply_markup)
    except Exception:
        logger.exception("failed to send onboarding message to chat %s", chat_id)
