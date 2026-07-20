"""Self-serve merchant onboarding through the shared platform bot -
replaces hand-seeding every merchant via scripts/seed_merchant.py (still
kept for scripted/test fixtures, see that script's docstring).

State machine, dispatch-table shaped: start -> awaiting_token ->
awaiting_shop_name -> choosing_vertical -> choosing_source ->
[awaiting_course_description, course only] -> choosing_faq_topics ->
[awaiting_topic_answer / choosing_payment_methods, looped once per
selected topic] -> awaiting_hours -> choosing_tone -> done. Refactored
from the original 4-state if/elif chain (mode-switch checkpoint 4) for
two reasons a chain doesn't fit: the FAQ-topics step is stateful
multi-select with a variable-length follow-up loop (one question per
selected topic, accumulated in session.data before advancing), and
/sozlamalar needs to jump straight into an arbitrary mid-flow state -
trivial with a {state: prompt-sender} table (_STATE_PROMPTS,
_send_prompt_for_state), awkward with a chain.

A telegram identity that's already a MerchantAdmin anywhere is refused at
/start - one Telegram identity administers exactly one merchant in v1
(see app/db/models.py's MerchantAdmin docstring for why).

/sozlamalar re-entry reuses the exact same per-state prompt senders as
forward progression, tagged via session.data["reentry"] = True so the
step-completion handlers know to return straight to done with a short
"saved" confirmation instead of continuing the chain - re-entry edits one
field, it doesn't restart onboarding from that point.

The "photos" source branch and the course branch's structured-extraction
follow-through are both stubbed (store what's given, "coming soon"
message) rather than built here - matching the pattern already used for
the course branch during planning (see the aqlchat_pivot_onboarding_llm_deferred
memory): the backing feature (a photo+caption product-intake webhook path
outside the channel-ingestion flow) doesn't exist anywhere in the
codebase yet, only channel-based ingestion does (app/products/ingestion.py,
app/telegram/webhook.py's _handle_channel_post). Building that intake
pipeline is a real, separate piece of work, not something to silently
invent inside an onboarding refactor.

All Telegram I/O for this flow happens here, not in the router - this
module owns the exact sequencing (delete the pasted token immediately,
*then* send the next prompt) that a request/response-shaped view in the
router would obscure.
"""

import logging
import secrets
from typing import Callable

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.db.models import Faq, Merchant, MerchantAdmin, PlatformOnboardingSession
from app.nlp.embeddings import embed_text
from app.telegram.client import TelegramClient

logger = logging.getLogger(__name__)

STATE_START = "start"
STATE_AWAITING_TOKEN = "awaiting_token"
STATE_AWAITING_SHOP_NAME = "awaiting_shop_name"
STATE_CHOOSING_VERTICAL = "choosing_vertical"
STATE_CHOOSING_SOURCE = "choosing_source"
STATE_AWAITING_COURSE_DESCRIPTION = "awaiting_course_description"
STATE_CHOOSING_FAQ_TOPICS = "choosing_faq_topics"
STATE_AWAITING_TOPIC_ANSWER = "awaiting_topic_answer"
STATE_CHOOSING_PAYMENT_METHODS = "choosing_payment_methods"
STATE_AWAITING_HOURS = "awaiting_hours"
STATE_CHOOSING_TONE = "choosing_tone"
STATE_DONE = "done"

_VERTICALS = {
    "clothing": {"uz": "Kiyim-kechak", "ru": "Одежда"},
    "cosmetics": {"uz": "Kosmetika", "ru": "Косметика"},
    "silver_jewelry": {"uz": "Kumush zargarlik", "ru": "Серебряные украшения"},
    "course": {"uz": "Kurs", "ru": "Курс"},
    "other": {"uz": "Boshqa", "ru": "Другое"},
}

_SOURCES = {
    "channel": {"uz": "Telegram kanalda", "ru": "В Telegram-канале"},
    "photos": {"uz": "Rasmlarini o'zim yuboraman", "ru": "Сам пришлю фото"},
    "course": {"uz": "Kurs - tavsifini joylayman", "ru": "Курс - вставлю описание"},
}

# Which FAQ-topic follow-ups are offered, filtered by vertical - a
# clothing merchant sees "sizes", a course seller sees "course_schedule"
# instead. "price" and "payment"/"hours_location" apply broadly enough to
# offer everywhere.
_TOPICS = {
    "price": {"uz": "Narx", "ru": "Цена"},
    "delivery": {"uz": "Yetkazib berish", "ru": "Доставка"},
    "sizes": {"uz": "O'lchamlar", "ru": "Размеры"},
    "payment": {"uz": "To'lov va nasiya", "ru": "Оплата и рассрочка"},
    "authenticity": {"uz": "Originallik", "ru": "Подлинность"},
    "course_schedule": {"uz": "Kurs davomiyligi va jadvali", "ru": "Длительность и расписание курса"},
    "hours_location": {"uz": "Manzil va ish vaqti", "ru": "Адрес и время работы"},
}

_TOPICS_BY_VERTICAL = {
    "clothing": ["price", "delivery", "sizes", "payment", "hours_location"],
    "cosmetics": ["price", "delivery", "authenticity", "payment", "hours_location"],
    "silver_jewelry": ["price", "delivery", "authenticity", "payment", "hours_location"],
    "course": ["price", "course_schedule", "payment", "hours_location"],
    "other": ["price", "delivery", "payment", "hours_location"],
}

# The FAQ row created from each topic's follow-up needs a real question
# text (matching drives off Faq.embedding of this, per
# app/faq/retrieval.py) - not just the short button label above.
_TOPIC_QUESTIONS = {
    "price": {"uz": "Narxi qancha?", "ru": "Сколько стоит?"},
    "delivery": {"uz": "Yetkazib berish qanday amalga oshiriladi?", "ru": "Как происходит доставка?"},
    "sizes": {"uz": "Qanday o'lchamlar mavjud?", "ru": "Какие размеры есть?"},
    "payment": {"uz": "To'lov va nasiya sharoitlari qanday?", "ru": "Какие условия оплаты и рассрочки?"},
    "authenticity": {
        "uz": "Mahsulot originalligi qanday tasdiqlanadi?",
        "ru": "Как подтверждается подлинность товара?",
    },
    "course_schedule": {
        "uz": "Kurs qancha davom etadi va jadvali qanday?",
        "ru": "Сколько длится курс и какое расписание?",
    },
    "hours_location": {"uz": "Manzil va ish vaqtingiz qanday?", "ru": "Какой у вас адрес и время работы?"},
}

_PAYMENT_METHODS = {
    "cash": {"uz": "Naqd", "ru": "Наличные"},
    "card": {"uz": "Karta", "ru": "Карта"},
    "click_payme": {"uz": "Click/Payme", "ru": "Click/Payme"},
    "installment": {"uz": "Nasiya", "ru": "Рассрочка"},
}

# Re-entry targets for /sozlamalar - one Telegram identity administers
# exactly one merchant (MerchantAdmin), so re-entry always resumes that
# admin's own onboarding session, not a fresh one.
_REENTRY_STATES = {
    "shop_name": STATE_AWAITING_SHOP_NAME,
    "vertical": STATE_CHOOSING_VERTICAL,
    "source": STATE_CHOOSING_SOURCE,
    "faq_topics": STATE_CHOOSING_FAQ_TOPICS,
    "hours": STATE_AWAITING_HOURS,
    "tone": STATE_CHOOSING_TONE,
}

_ALREADY_REGISTERED = (
    "Siz allaqachon administrator sifatida ro'yxatdan o'tgansiz.\n"
    "Вы уже зарегистрированы как администратор."
)
# Both languages in one string, like _ALREADY_REGISTERED - these fire
# before the language-pick step, so no language is known yet.
_TELEGRAM_ALREADY_CONNECTED = (
    "Bu akkauntga Telegram bot allaqachon ulangan.\n"
    "К этому аккаунту Telegram-бот уже подключён."
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
_TOKEN_ALREADY_REGISTERED = {
    "uz": "Bu bot allaqachon ro'yxatdan o'tgan. Boshqa token yuboring.",
    "ru": "Этот бот уже зарегистрирован. Отправьте другой токен.",
}
_WEBHOOK_REGISTRATION_FAILED = {
    "uz": "\n\n(Ogohlantirish: webhook avtomatik ro'yxatga olinmadi - administratorga xabar bering.)",
    "ru": "\n\n(Предупреждение: webhook не удалось зарегистрировать автоматически - сообщите администратору.)",
}
_CONFIRM_SHOP_NAME = {
    "uz": "Do'kon nomi: {name}\nTo'g'rimi? Tasdiqlang yoki yangi nomni yozing.",
    "ru": "Название магазина: {name}\nВерно? Подтвердите или напишите новое название.",
}
_CONFIRM_BUTTON = {"uz": "✅ To'g'ri", "ru": "✅ Верно"}
_CHOOSE_VERTICAL = {
    "uz": "Sohangizni tanlang:",
    "ru": "Выберите сферу деятельности:",
}
_CHOOSE_SOURCE = {
    "uz": "Mahsulotlaringiz qayerda?",
    "ru": "Где ваши товары?",
}
_CHANNEL_INSTRUCTIONS = {
    "uz": (
        "Kanalingizga botni administrator qilib qo'shing, so'ng mahsulot postlaringizni "
        "shu kanalga joylang - bot ularni avtomatik o'qib oladi."
    ),
    "ru": (
        "Добавьте бота администратором в ваш канал, затем публикуйте посты о товарах туда - "
        "бот будет читать их автоматически."
    ),
}
_PHOTOS_COMING_SOON = {
    "uz": "Rasm orqali qo'shish tez orada qo'shiladi - hozircha kanal orqali davom eting.",
    "ru": "Добавление через фото появится скоро - пока продолжайте через канал.",
}
_ASK_COURSE_DESCRIPTION = {
    "uz": "Kursingiz haqida qisqacha yozing (mavzu, davomiyligi, narxi):",
    "ru": "Кратко опишите ваш курс (тема, длительность, цена):",
}
_COURSE_SAVED = {
    "uz": "Saqlandi. Kursni avtomatik tahlil qilish tez orada qo'shiladi.",
    "ru": "Сохранено. Автоматический анализ курса появится скоро.",
}
_CHOOSE_TOPICS = {
    "uz": "Mijozlar ko'pincha nima so'raydi? Bir nechtasini tanlashingiz mumkin.",
    "ru": "Что чаще всего спрашивают клиенты? Можно выбрать несколько.",
}
_TOPICS_DONE_BUTTON = {"uz": "Tayyor ✅", "ru": "Готово ✅"}
_ASK_PAYMENT_METHODS = {
    "uz": "Qanday to'lov usullari mavjud? Bir nechtasini tanlang.",
    "ru": "Какие способы оплаты доступны? Выберите несколько.",
}
_PAYMENT_DONE_BUTTON = {"uz": "Tayyor ✅", "ru": "Готово ✅"}
_ASK_HOURS = {
    "uz": "Ish vaqtingiz qanday? (Yozing yoki o'tkazib yuboring)",
    "ru": "Какой у вас режим работы? (Напишите или пропустите)",
}
_SKIP_BUTTON = {"uz": "O'tkazib yuborish", "ru": "Пропустить"}
_CHOOSE_TONE = {
    "uz": "Botingiz mijozlar bilan qanday ohangda gaplashsin?",
    "ru": "В каком тоне бот должен общаться с клиентами?",
}
_TONE_OPTIONS = {
    "formal": {"uz": "Rasmiy", "ru": "Формальный"},
    "friendly": {"uz": "Do'stona", "ru": "Дружелюбный"},
}
_DONE = {
    "uz": "Tayyor! Botingiz ishga tayyor: @{username}",
    "ru": "Готово! Ваш бот готов к работе: @{username}",
}
_REENTRY_SAVED = {
    "uz": "Saqlandi.",
    "ru": "Сохранено.",
}
_NOT_YET_ONBOARDED = {
    "uz": "Avval ro'yxatdan o'ting: /start",
    "ru": "Сначала зарегистрируйтесь: /start",
}
_SETTINGS_MENU_TEXT = {
    "uz": "Nimani o'zgartirmoqchisiz?",
    "ru": "Что вы хотите изменить?",
}
_SETTINGS_LABELS = {
    "shop_name": {"uz": "Do'kon nomi", "ru": "Название магазина"},
    "vertical": {"uz": "Soha", "ru": "Сфера"},
    "source": {"uz": "Mahsulot manbai", "ru": "Источник товаров"},
    "faq_topics": {"uz": "FAQ mavzulari", "ru": "Темы FAQ"},
    "hours": {"uz": "Ish vaqti", "ru": "Режим работы"},
    "tone": {"uz": "Muloqot ohangi", "ru": "Тон общения"},
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


def handle_start(db: Session, telegram_user_id: int, chat_id: int, connect_token: str | None = None) -> None:
    existing_admin = db.scalar(select(MerchantAdmin).where(MerchantAdmin.telegram_user_id == telegram_user_id))
    if existing_admin is not None:
        _send(chat_id, _ALREADY_REGISTERED)
        return

    # Deep-link connect: the website's "connect Telegram" button links to
    # t.me/<bot>?start=<webhook_slug> - the same unguessable capability
    # token the Instagram connect flow keys on (app/instagram/router.py's
    # start_connect). Resolving it here binds this onboarding session to
    # the web-created merchant row, so _handle_token_text attaches the bot
    # to that row instead of creating a second, disconnected Merchant (the
    # dashboard's "Telegram: connected" check reads telegram_bot_id off
    # the web account's own row - see app/auth/router.py's _merchant_out).
    # An unknown or absent payload falls through to plain Telegram-first
    # onboarding unchanged.
    merchant = None
    if connect_token:
        merchant = db.scalar(select(Merchant).where(Merchant.webhook_slug == connect_token))
        if merchant is not None and merchant.telegram_bot_id is not None:
            _send(chat_id, _TELEGRAM_ALREADY_CONNECTED)
            return

    session = _get_or_create_session(db, telegram_user_id)
    session.state = STATE_START
    session.language = None
    session.merchant_id = merchant.id if merchant is not None else None
    session.data = None
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


def handle_settings_command(db: Session, telegram_user_id: int, chat_id: int) -> None:
    """/sozlamalar - re-enter any already-completed onboarding step to
    change it, without redoing the whole flow. Only meaningful for an
    identity that's already finished onboarding at least once (has a
    session with a merchant attached); a no-op otherwise, same as
    unrecognized free text elsewhere in this module."""
    admin = db.scalar(select(MerchantAdmin).where(MerchantAdmin.telegram_user_id == telegram_user_id))
    if admin is None:
        return
    session = db.get(PlatformOnboardingSession, telegram_user_id)
    if session is None or session.merchant_id is None:
        return

    language = session.language or "uz"
    _send(
        chat_id,
        _SETTINGS_MENU_TEXT[language],
        reply_markup={
            "inline_keyboard": [
                [{"text": labels[language], "callback_data": f"settings:{key}"}]
                for key, labels in _SETTINGS_LABELS.items()
            ]
        },
    )


def handle_platform_message(db: Session, telegram_user_id: int, chat_id: int, message_id: int, text: str) -> None:
    session = db.get(PlatformOnboardingSession, telegram_user_id)
    if session is None:
        return
    handler = _TEXT_HANDLERS.get(session.state)
    if handler is not None:
        handler(db, session, chat_id, message_id, text)


def handle_platform_callback(db: Session, telegram_user_id: int, chat_id: int, callback_data: str) -> None:
    session = db.get(PlatformOnboardingSession, telegram_user_id)
    if session is None:
        return

    if callback_data.startswith("settings:"):
        # Re-entry is valid from any state (typically STATE_DONE) - not
        # gated by session.state like every other callback branch below.
        _handle_settings_reentry_pick(db, session, chat_id, callback_data.removeprefix("settings:"))
        return

    if callback_data.startswith("lang:") and session.state == STATE_START:
        _handle_language_pick(db, session, chat_id, callback_data.removeprefix("lang:"))
    elif callback_data.startswith("shop_name:") and session.state == STATE_AWAITING_SHOP_NAME:
        _handle_shop_name_confirm(db, session, chat_id)
    elif callback_data.startswith("vertical:") and session.state == STATE_CHOOSING_VERTICAL:
        _handle_vertical_pick(db, session, chat_id, callback_data.removeprefix("vertical:"))
    elif callback_data.startswith("source:") and session.state == STATE_CHOOSING_SOURCE:
        _handle_source_pick(db, session, chat_id, callback_data.removeprefix("source:"))
    elif session.state == STATE_CHOOSING_FAQ_TOPICS and callback_data.startswith("topic:"):
        _handle_topic_toggle(db, session, chat_id, callback_data.removeprefix("topic:"))
    elif session.state == STATE_CHOOSING_FAQ_TOPICS and callback_data == "topics_done":
        _handle_topics_done(db, session, chat_id)
    elif session.state == STATE_CHOOSING_PAYMENT_METHODS and callback_data.startswith("payment:"):
        _handle_payment_toggle(db, session, chat_id, callback_data.removeprefix("payment:"))
    elif session.state == STATE_CHOOSING_PAYMENT_METHODS and callback_data == "payment_done":
        _handle_payment_done(db, session, chat_id)
    elif callback_data == "hours:skip" and session.state == STATE_AWAITING_HOURS:
        _finish_hours(db, session, chat_id, hours_text=None)
    elif callback_data.startswith("tone:") and session.state == STATE_CHOOSING_TONE:
        _handle_tone_pick(db, session, chat_id, callback_data.removeprefix("tone:"))


# ---- language / token / shop name -----------------------------------


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

    existing = db.scalar(select(Merchant).where(Merchant.telegram_bot_id == bot_info["id"]))
    if existing is not None:
        # Same bot already backs a merchant (manually seeded, or a prior
        # onboarding run) - telegram_bot_id is the real uniqueness key
        # (see app/db/models.py's Merchant docstring), so creating a
        # second Merchant row for it would crash on that constraint.
        # Stay in awaiting_token so a different token can be pasted.
        _send(chat_id, _TOKEN_ALREADY_REGISTERED[language])
        return

    merchant = db.get(Merchant, session.merchant_id) if session.merchant_id else None
    if merchant is not None:
        # Deep-link connect: the session was bound to a web-created
        # merchant at /start (see handle_start) - attach the bot to that
        # existing row rather than creating a new one. Guard against a
        # concurrent connect through the same link having finished first.
        if merchant.telegram_bot_id is not None:
            _send(chat_id, _TELEGRAM_ALREADY_CONNECTED)
            return
        merchant.telegram_bot_token = token
        merchant.telegram_bot_id = bot_info["id"]
        # Website signup leaves name NULL (shop name was never asked
        # there) - prefill from getMe only if empty, so the shop-name
        # confirm prompt below has something to show without clobbering
        # anything a merchant already set.
        if not merchant.name:
            merchant.name = bot_info.get("first_name") or bot_info.get("username") or "Unnamed merchant"
        db.add(merchant)
    else:
        merchant = Merchant(
            name=bot_info.get("first_name") or bot_info.get("username") or "Unnamed merchant",
            telegram_bot_token=token,
            telegram_bot_id=bot_info["id"],
            webhook_secret=secrets.token_urlsafe(32),
        )
        db.add(merchant)
        db.flush()

    # Not a model column - just enough to prefill a t.me/<username> link in
    # the automations install wizard (app/automations/router.py) without a
    # live getMe() call at that point. Best-effort: some bots have no
    # public username set yet.
    if bot_info.get("username"):
        merchant.profile = {**(merchant.profile or {}), "telegram_bot_username": bot_info["username"]}
        db.add(merchant)

    db.add(MerchantAdmin(merchant_id=merchant.id, telegram_user_id=session.telegram_user_id))

    session.state = STATE_AWAITING_SHOP_NAME
    session.merchant_id = merchant.id
    db.add(session)

    webhook_registered = _register_tenant_webhook(merchant, token)
    if not webhook_registered:
        _send(chat_id, _WEBHOOK_REGISTRATION_FAILED[language])

    _send_shop_name_prompt(db, session, chat_id)


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


def _send_shop_name_prompt(db: Session, session: PlatformOnboardingSession, chat_id: int) -> None:
    merchant = db.get(Merchant, session.merchant_id) if session.merchant_id else None
    language = session.language or "uz"
    name = merchant.name if merchant is not None else "?"
    _send(
        chat_id,
        _CONFIRM_SHOP_NAME[language].format(name=name),
        reply_markup={"inline_keyboard": [[{"text": _CONFIRM_BUTTON[language], "callback_data": "shop_name:confirm"}]]},
    )


def _handle_shop_name_text(
    db: Session, session: PlatformOnboardingSession, chat_id: int, message_id: int, text: str
) -> None:
    merchant = db.get(Merchant, session.merchant_id) if session.merchant_id else None
    if merchant is None:
        return
    new_name = text.strip()
    if new_name:
        merchant.name = new_name
        db.add(merchant)
    _advance_from(db, session, chat_id, STATE_CHOOSING_VERTICAL)


def _handle_shop_name_confirm(db: Session, session: PlatformOnboardingSession, chat_id: int) -> None:
    # Prefilled name (from getMe) accepted as-is - confirm-with-editable-
    # default, not a blank prompt (see feedback_design_decisions memory:
    # bot names/first_names are frequently junk, so this still needs an
    # explicit human confirmation step even though a value is prefilled).
    _advance_from(db, session, chat_id, STATE_CHOOSING_VERTICAL)


# ---- vertical / source -------------------------------------------------


def _send_vertical_prompt(db: Session, session: PlatformOnboardingSession, chat_id: int) -> None:
    language = session.language or "uz"
    _send(
        chat_id,
        _CHOOSE_VERTICAL[language],
        reply_markup={
            "inline_keyboard": [
                [{"text": labels[language], "callback_data": f"vertical:{key}"}] for key, labels in _VERTICALS.items()
            ]
        },
    )


def _handle_vertical_pick(db: Session, session: PlatformOnboardingSession, chat_id: int, vertical: str) -> None:
    if vertical not in _VERTICALS or session.merchant_id is None:
        return
    merchant = db.get(Merchant, session.merchant_id)
    if merchant is None:
        return
    merchant.vertical = vertical
    db.add(merchant)
    _advance_from(db, session, chat_id, STATE_CHOOSING_SOURCE)


def _send_source_prompt(db: Session, session: PlatformOnboardingSession, chat_id: int) -> None:
    language = session.language or "uz"
    _send(
        chat_id,
        _CHOOSE_SOURCE[language],
        reply_markup={
            "inline_keyboard": [
                [{"text": labels[language], "callback_data": f"source:{key}"}] for key, labels in _SOURCES.items()
            ]
        },
    )


def _handle_source_pick(db: Session, session: PlatformOnboardingSession, chat_id: int, source: str) -> None:
    if source not in _SOURCES or session.merchant_id is None:
        return
    merchant = db.get(Merchant, session.merchant_id)
    if merchant is None:
        return
    language = session.language or "uz"
    merchant.profile = {**(merchant.profile or {}), "intake_source": source}
    db.add(merchant)

    if source == "channel":
        _send(chat_id, _CHANNEL_INSTRUCTIONS[language])
        _advance_from(db, session, chat_id, STATE_CHOOSING_FAQ_TOPICS)
    elif source == "photos":
        # Stubbed - no photo+caption product-intake path exists outside
        # channel ingestion yet (see this module's docstring). Merchant
        # is steered back toward the channel path for now.
        _send(chat_id, _PHOTOS_COMING_SOON[language])
        _advance_from(db, session, chat_id, STATE_CHOOSING_FAQ_TOPICS)
    elif source == "course":
        # Not routed through _advance_from: picking "course" isn't the
        # completed action for this step, the description text submitted
        # next is - see _handle_course_description_text. Going through
        # _advance_from here would consume a re-entry's reentry=True flag
        # early and skip straight to "saved" without ever prompting for
        # the description.
        session.state = STATE_AWAITING_COURSE_DESCRIPTION
        db.add(session)
        _send_course_description_prompt(db, session, chat_id)


def _send_course_description_prompt(db: Session, session: PlatformOnboardingSession, chat_id: int) -> None:
    language = session.language or "uz"
    _send(chat_id, _ASK_COURSE_DESCRIPTION[language])


def _handle_course_description_text(
    db: Session, session: PlatformOnboardingSession, chat_id: int, message_id: int, text: str
) -> None:
    merchant = db.get(Merchant, session.merchant_id) if session.merchant_id else None
    if merchant is None:
        return
    language = session.language or "uz"
    # Stubbed - stored raw for a human to read later, not run through any
    # extraction pipeline (none exists - see this module's docstring and
    # the aqlchat_pivot_onboarding_llm_deferred memory).
    merchant.profile = {**(merchant.profile or {}), "course_raw_description": text.strip()}
    db.add(merchant)
    _send(chat_id, _COURSE_SAVED[language])
    _advance_from(db, session, chat_id, STATE_CHOOSING_FAQ_TOPICS)


# ---- FAQ topics multi-select + per-topic follow-up loop -----------------


def _topics_for_vertical(vertical: str | None) -> list[str]:
    return _TOPICS_BY_VERTICAL.get(vertical, _TOPICS_BY_VERTICAL["other"])


def _send_faq_topics_prompt(db: Session, session: PlatformOnboardingSession, chat_id: int) -> None:
    merchant = db.get(Merchant, session.merchant_id) if session.merchant_id else None
    language = session.language or "uz"
    selected = set((session.data or {}).get("selected_topics", []))

    rows = []
    for key in _topics_for_vertical(merchant.vertical if merchant else None):
        mark = "☑" if key in selected else "▫"
        rows.append([{"text": f"{mark} {_TOPICS[key][language]}", "callback_data": f"topic:{key}"}])
    rows.append([{"text": _TOPICS_DONE_BUTTON[language], "callback_data": "topics_done"}])

    _send(chat_id, _CHOOSE_TOPICS[language], reply_markup={"inline_keyboard": rows})


def _handle_topic_toggle(db: Session, session: PlatformOnboardingSession, chat_id: int, topic: str) -> None:
    merchant = db.get(Merchant, session.merchant_id) if session.merchant_id else None
    if topic not in _topics_for_vertical(merchant.vertical if merchant else None):
        return
    data = dict(session.data or {})
    selected = list(data.get("selected_topics", []))
    if topic in selected:
        selected.remove(topic)
    else:
        selected.append(topic)
    data["selected_topics"] = selected
    session.data = data
    db.add(session)
    _send_faq_topics_prompt(db, session, chat_id)


def _handle_topics_done(db: Session, session: PlatformOnboardingSession, chat_id: int) -> None:
    selected = list((session.data or {}).get("selected_topics", []))
    reentry = bool((session.data or {}).get("reentry"))
    session.data = {"pending_topics": selected, "collected": {}, "reentry": reentry}
    db.add(session)
    _advance_topic_loop(db, session, chat_id)


def _advance_topic_loop(db: Session, session: PlatformOnboardingSession, chat_id: int) -> None:
    """Pops the next pending topic and prompts for it - payment gets a
    multi-select sub-flow, everything else is free text. Once
    pending_topics is empty, finalizes every collected answer as a Faq
    row (both language keys set to the merchant's own text - matching is
    cross-lingual off the embedded question regardless, see
    app/faq/retrieval.py) and folds them into merchant.profile before
    moving on to the hours step."""
    data = dict(session.data or {})
    pending = list(data.get("pending_topics", []))
    if not pending:
        _finalize_faq_topics(db, session, chat_id)
        return

    topic = pending.pop(0)
    data["pending_topics"] = pending
    data["current_topic"] = topic
    session.data = data
    db.add(session)

    language = session.language or "uz"
    if topic == "payment":
        data["selected_payment_methods"] = []
        session.data = data
        session.state = STATE_CHOOSING_PAYMENT_METHODS
        db.add(session)
        _send_payment_prompt(db, session, chat_id)
        return

    session.state = STATE_AWAITING_TOPIC_ANSWER
    db.add(session)
    _send(chat_id, _TOPIC_QUESTIONS[topic][language])


def _handle_topic_answer_text(
    db: Session, session: PlatformOnboardingSession, chat_id: int, message_id: int, text: str
) -> None:
    data = dict(session.data or {})
    topic = data.get("current_topic")
    if topic is None:
        return
    collected = dict(data.get("collected", {}))
    collected[topic] = text.strip()
    data["collected"] = collected
    session.data = data
    db.add(session)
    _advance_topic_loop(db, session, chat_id)


def _send_payment_prompt(db: Session, session: PlatformOnboardingSession, chat_id: int) -> None:
    language = session.language or "uz"
    selected = set((session.data or {}).get("selected_payment_methods", []))
    rows = []
    for key, labels in _PAYMENT_METHODS.items():
        mark = "☑" if key in selected else "▫"
        rows.append([{"text": f"{mark} {labels[language]}", "callback_data": f"payment:{key}"}])
    rows.append([{"text": _PAYMENT_DONE_BUTTON[language], "callback_data": "payment_done"}])
    _send(chat_id, _ASK_PAYMENT_METHODS[language], reply_markup={"inline_keyboard": rows})


def _handle_payment_toggle(db: Session, session: PlatformOnboardingSession, chat_id: int, method: str) -> None:
    if method not in _PAYMENT_METHODS:
        return
    data = dict(session.data or {})
    selected = list(data.get("selected_payment_methods", []))
    if method in selected:
        selected.remove(method)
    else:
        selected.append(method)
    data["selected_payment_methods"] = selected
    session.data = data
    db.add(session)
    _send_payment_prompt(db, session, chat_id)


def _handle_payment_done(db: Session, session: PlatformOnboardingSession, chat_id: int) -> None:
    language = session.language or "uz"
    data = dict(session.data or {})
    selected = list(data.get("selected_payment_methods", []))
    answer_text = ", ".join(_PAYMENT_METHODS[key][language] for key in selected) or "-"

    collected = dict(data.get("collected", {}))
    collected["payment"] = answer_text
    data["collected"] = collected
    data.pop("selected_payment_methods", None)
    data.pop("current_topic", None)
    session.data = data
    db.add(session)
    _advance_topic_loop(db, session, chat_id)


def _finalize_faq_topics(db: Session, session: PlatformOnboardingSession, chat_id: int) -> None:
    merchant = db.get(Merchant, session.merchant_id) if session.merchant_id else None
    if merchant is None:
        return
    collected: dict[str, str] = dict((session.data or {}).get("collected", {}))

    for topic, answer_text in collected.items():
        question = _TOPIC_QUESTIONS[topic][session.language or "uz"]
        db.add(
            Faq(
                merchant_id=merchant.id,
                question=question,
                response_config={"uz": answer_text, "ru": answer_text},
                embedding=embed_text(question),
            )
        )

    merchant.profile = {**(merchant.profile or {}), "faq_topics": collected}
    db.add(merchant)

    _advance_from(db, session, chat_id, STATE_AWAITING_HOURS)


# ---- hours / tone / done -------------------------------------------------


def _send_hours_prompt(db: Session, session: PlatformOnboardingSession, chat_id: int) -> None:
    language = session.language or "uz"
    _send(
        chat_id,
        _ASK_HOURS[language],
        reply_markup={"inline_keyboard": [[{"text": _SKIP_BUTTON[language], "callback_data": "hours:skip"}]]},
    )


def _handle_hours_text(
    db: Session, session: PlatformOnboardingSession, chat_id: int, message_id: int, text: str
) -> None:
    _finish_hours(db, session, chat_id, hours_text=text.strip())


def _finish_hours(db: Session, session: PlatformOnboardingSession, chat_id: int, hours_text: str | None) -> None:
    merchant = db.get(Merchant, session.merchant_id) if session.merchant_id else None
    if merchant is None:
        return
    if hours_text:
        merchant.profile = {**(merchant.profile or {}), "hours": hours_text}
        db.add(merchant)
    _advance_from(db, session, chat_id, STATE_CHOOSING_TONE)


def _send_tone_prompt(db: Session, session: PlatformOnboardingSession, chat_id: int) -> None:
    language = session.language or "uz"
    _send(
        chat_id,
        _CHOOSE_TONE[language],
        reply_markup={
            "inline_keyboard": [
                [{"text": labels[language], "callback_data": f"tone:{key}"}] for key, labels in _TONE_OPTIONS.items()
            ]
        },
    )


def _handle_tone_pick(db: Session, session: PlatformOnboardingSession, chat_id: int, tone: str) -> None:
    if tone not in _TONE_OPTIONS or session.merchant_id is None:
        return
    merchant = db.get(Merchant, session.merchant_id)
    if merchant is None:
        return
    merchant.profile = {**(merchant.profile or {}), "tone": tone}
    db.add(merchant)
    _finish_onboarding(db, session, chat_id)


def _finish_onboarding(db: Session, session: PlatformOnboardingSession, chat_id: int) -> None:
    merchant = db.get(Merchant, session.merchant_id) if session.merchant_id else None
    session.state = STATE_DONE
    session.data = None
    db.add(session)

    language = session.language or "uz"
    username = None
    if merchant is not None:
        bot_info = _validate_token(merchant.telegram_bot_token)
        if bot_info is not None:
            username = bot_info.get("username")
    _send(chat_id, _DONE[language].format(username=username or "?"))


# ---- shared forward-progression / re-entry plumbing ----------------------

_STATE_PROMPTS: dict[str, Callable[[Session, PlatformOnboardingSession, int], None]] = {
    STATE_AWAITING_SHOP_NAME: _send_shop_name_prompt,
    STATE_CHOOSING_VERTICAL: _send_vertical_prompt,
    STATE_CHOOSING_SOURCE: _send_source_prompt,
    STATE_AWAITING_COURSE_DESCRIPTION: _send_course_description_prompt,
    STATE_CHOOSING_FAQ_TOPICS: _send_faq_topics_prompt,
    STATE_AWAITING_HOURS: _send_hours_prompt,
    STATE_CHOOSING_TONE: _send_tone_prompt,
}

_TEXT_HANDLERS: dict[str, Callable[[Session, PlatformOnboardingSession, int, int, str], None]] = {
    STATE_AWAITING_TOKEN: _handle_token_text,
    STATE_AWAITING_SHOP_NAME: _handle_shop_name_text,
    STATE_AWAITING_COURSE_DESCRIPTION: _handle_course_description_text,
    STATE_AWAITING_TOPIC_ANSWER: _handle_topic_answer_text,
    STATE_AWAITING_HOURS: _handle_hours_text,
}


def _send_prompt_for_state(db: Session, session: PlatformOnboardingSession, chat_id: int) -> None:
    handler = _STATE_PROMPTS.get(session.state)
    if handler is not None:
        handler(db, session, chat_id)


def _advance_from(db: Session, session: PlatformOnboardingSession, chat_id: int, next_state: str) -> None:
    """Shared tail of every step-completion handler: if this step was
    reached via /sozlamalar re-entry (session.data["reentry"]), one field
    was being edited in isolation - go straight back to done with a short
    confirmation instead of continuing the chain. Otherwise advance
    normally and (re-)send the next state's prompt."""
    reentry = bool((session.data or {}).get("reentry"))
    if reentry:
        session.state = STATE_DONE
        session.data = None
        db.add(session)
        _send(chat_id, _REENTRY_SAVED[session.language or "uz"])
        return

    session.state = next_state
    session.data = None
    db.add(session)
    _send_prompt_for_state(db, session, chat_id)


def _handle_settings_reentry_pick(db: Session, session: PlatformOnboardingSession, chat_id: int, target: str) -> None:
    target_state = _REENTRY_STATES.get(target)
    if target_state is None or session.merchant_id is None:
        return
    session.state = target_state
    session.data = {"reentry": True}
    db.add(session)
    _send_prompt_for_state(db, session, chat_id)


def _send(chat_id: int, text: str, reply_markup: dict | None = None) -> None:
    try:
        _platform_client().send_message(chat_id, text, reply_markup=reply_markup)
    except Exception:
        logger.exception("failed to send onboarding message to chat %s", chat_id)
