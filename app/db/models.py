import secrets
import uuid
from datetime import datetime, timezone

from pgvector.sqlalchemy import Vector
from sqlalchemy import BigInteger, ForeignKey, Numeric, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.crypto import EncryptedString

# BGE-M3 locked in 2026-07-10 after a cross-lingual retrieval check (see
# app/nlp/embeddings.py docstring). Dimension confirmed at 1024.
EMBEDDING_DIM = 1024

# SigLIP locked in 2026-07-11 after a CLIP-vs-SigLIP retrieval-quality
# spike (see app/image_search/embeddings.py docstring). Dimension
# confirmed at 768 via google/siglip-base-patch16-224's
# vision_config.hidden_size - distinct from EMBEDDING_DIM above, which is
# BGE-M3's text dimension.
IMAGE_EMBEDDING_DIM = 768


def _uuid() -> uuid.UUID:
    return uuid.uuid4()


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Merchant(Base):
    __tablename__ = "merchants"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    # Shop name. Nullable: since the website signup flow (app/auth/) creates
    # the Merchant row before any Telegram bot is connected, this isn't
    # known at creation time anymore - it's collected later, during tenant
    # bot onboarding (app/onboarding/service.py) same as before.
    name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # The signup account holder's own name (Nozima, not "Nozima's Shop") -
    # distinct from `name` above. Collected at signup (app/auth/), never
    # touched by the Telegram onboarding flow.
    owner_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Website signup identity (app/auth/). Unique but nullable: merchants
    # seeded pre-signup (scripts/seed_merchant.py) or created by the old
    # Telegram-first path won't have one.
    email: Mapped[str | None] = mapped_column(String(320), unique=True, nullable=True)
    # Captured as plain data at signup, intentionally unverified (product
    # decision: OTP verification is email-only, phone is just a contact
    # field) - not unique, not used for login.
    phone_number: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # PBKDF2-HMAC-SHA256, stdlib hashlib only (no bcrypt/argon2 dependency
    # needed at this scale) - "$"-joined iterations/salt/hash, see
    # app/auth/passwords.py. Set at signup; the one-time email code
    # (EmailVerificationCode below) proves address ownership, it does not
    # log the merchant in by itself anymore - see app/auth/service.py.
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    email_verified_at: Mapped[datetime | None] = mapped_column(nullable=True)
    # Website UI language ('uz' | 'ru' | 'en') - distinct from
    # Customer.preferred_language (the end customer's chat language) and
    # from the reply-copy language picked at match time
    # (app/nlp/transliteration.py's detected_language). This one only
    # controls which language the merchant's OWN dashboard chrome renders
    # in (app/web/static/i18n.js); defaults to 'uz' since that's this
    # product's home market.
    ui_language: Mapped[str] = mapped_column(String(8), default="uz", server_default="uz")
    # Encrypted at rest (app/db/crypto.py) - the ORM attribute is still a
    # plain Python str everywhere it's read/written, only the DB column
    # holds ciphertext. NOT unique: Fernet is randomized (fresh IV per
    # call), so encrypting the same token twice produces different
    # ciphertext - a DB-level unique constraint here would silently stop
    # enforcing "this bot isn't already registered". telegram_bot_id
    # below is the real uniqueness key. Nullable for the same reason as
    # `name` above - not known until the merchant connects a tenant bot,
    # which now happens after website signup, not instead of it.
    telegram_bot_token: Mapped[str | None] = mapped_column(EncryptedString, nullable=True)
    # The bot's own numeric Telegram ID (from getMe during onboarding
    # token validation - see app/onboarding/service.py). Not sensitive,
    # not encrypted, immutable - the actual uniqueness key for "is this
    # bot already registered as a tenant". Nullable because
    # scripts/seed_merchant.py can seed a merchant with a fake token
    # (intentional, for tests) that never resolves via a real getMe call.
    telegram_bot_id: Mapped[int | None] = mapped_column(BigInteger, unique=True, nullable=True)
    # Random identifier used in the tenant webhook URL
    # (/telegram/webhook/tenant/{webhook_slug}), decoupled from the
    # merchant's real primary key so it can be rotated independently.
    # Always generated up front (even before a bot token exists) since it's
    # also the capability token the website's "connect Instagram"/"connect
    # Telegram" buttons link through - see app/instagram/router.py.
    webhook_slug: Mapped[str] = mapped_column(
        String(64), unique=True, index=True, default=lambda: secrets.token_urlsafe(24)
    )
    # Generated up front alongside webhook_slug (not nullable - costs
    # nothing to generate before a bot token exists, and every tenant
    # webhook route already assumes it's set).
    webhook_secret: Mapped[str] = mapped_column(String(255), default=lambda: secrets.token_urlsafe(32))
    # Set during onboarding (app/onboarding/service.py) - clothing /
    # cosmetics / other / courses for now. Nullable: not collected before
    # that onboarding step completes, and older merchants seeded manually
    # won't have one.
    vertical: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # The merchant's product-catalog channel (app/products/ingestion.py) -
    # set reactively the first time a channel_post arrives on this
    # merchant's tenant bot (see onboarding's channel branch), not via an
    # explicit getChat call. Unique: one channel belongs to one merchant.
    source_channel_id: Mapped[int | None] = mapped_column(BigInteger, unique=True, nullable=True)
    source_channel_title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Populated the same way/time as source_channel_id/title (reactive,
    # from TelegramChat.username on the first channel_post seen) - lets
    # customer-pasted t.me/<username>/<msg_id> links resolve without a
    # separate getChat call. Nullable: not every channel has a public
    # username, and older merchants won't have this backfilled.
    source_channel_username: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # 'llm_first' (demo-phase default - Gemini grounds every answer,
    # keyword/FAQ/intent layers are routed around entirely, see
    # app/telegram/webhook.py) | 'layered' (the original full pipeline,
    # untouched, comes back later). Not DB-enforced, matching the
    # vertical/price_status precedent elsewhere in this file - app-level
    # workflow state, not a fixed domain a constraint should police.
    pipeline_mode: Mapped[str] = mapped_column(String(16), server_default="llm_first")
    # Instagram connection (app/instagram/) - all nullable because the
    # channel is opt-in per merchant, connected via OAuth from the website
    # ("connect Instagram" button) after onboarding. instagram_user_id is
    # the routing key for /instagram/webhook (entry[].id -> merchant) and
    # THE tenant boundary on this channel - unique for the same reason
    # telegram_bot_id is: one IG account belongs to one merchant.
    instagram_user_id: Mapped[int | None] = mapped_column(BigInteger, unique=True, nullable=True)
    # Long-lived Instagram Login access token (~60 days), encrypted at
    # rest exactly like telegram_bot_token above (and like it, NOT unique:
    # Fernet ciphertext is randomized). Refreshed by
    # scripts/refresh_instagram_tokens.py before expiry.
    instagram_access_token: Mapped[str | None] = mapped_column(EncryptedString, nullable=True)
    instagram_token_expires_at: Mapped[datetime | None] = mapped_column(nullable=True)
    # Structured bag for onboarding-collected merchant info (shop name,
    # hours, delivery, payment, greeting tone, course description) that
    # app/llm/answer.py reads wholesale as grounding context. Small,
    # structured state - not a general-purpose dumping ground, matching
    # the Faq.response_config / Conversation.context precedent, since
    # what's collected varies by vertical rather than fitting one fixed
    # relational schema.
    profile: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=_now)

    products: Mapped[list["Product"]] = relationship(back_populates="merchant")
    customers: Mapped[list["Customer"]] = relationship(back_populates="merchant")
    conversations: Mapped[list["Conversation"]] = relationship(back_populates="merchant")
    flows: Mapped[list["Flow"]] = relationship(back_populates="merchant")
    faqs: Mapped[list["Faq"]] = relationship(back_populates="merchant")
    admins: Mapped[list["MerchantAdmin"]] = relationship(back_populates="merchant")


class MerchantAdmin(Base):
    """Who receives Layer-5 escalations and may issue /reply, /release for
    a merchant - replaces the old merchants.admin_chat_id single-field
    design now that admin interaction moved to the shared platform bot
    (app/onboarding/, app/handoff/service.py), not the tenant bot.

    telegram_user_id is unique across the WHOLE table, not just per
    merchant: one Telegram identity administers exactly one merchant in
    v1. This is what makes `/reply <customer_id> <text>` unambiguous in
    the platform bot - the merchant is resolved purely from who's
    texting, with no merchant id in the command syntax. Relaxing this
    later (multi-business admins) means dropping the constraint, not
    restructuring the command parser.
    """

    __tablename__ = "merchant_admins"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    merchant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("merchants.id"), index=True)
    telegram_user_id: Mapped[int] = mapped_column(BigInteger, unique=True)
    created_at: Mapped[datetime] = mapped_column(default=_now)

    merchant: Mapped["Merchant"] = relationship(back_populates="admins")


class PendingAdminReply(Base):
    """Tracks a thread of platform-bot notifications sent to ONE admin
    about ONE target (a conversation being escalated, or a product
    awaiting a price) so a later Telegram-reply from that admin can be
    resolved back to what it's about (app/handoff/service.py). Scoped per
    merchant_admin_id, not per merchant: message ids are only meaningful
    within the replying admin's own chat with the platform bot, so if a
    merchant has several admins, each gets their own independent row (and
    their own independent thread) for the same target.

    `kind` discriminates what `target_id` points at: "escalation" ->
    conversations.id, "price_query" -> products.id. Deliberately one
    table with a kind column rather than two separate tables - both are
    the same "which admin message resolves to which target" problem.

    `platform_message_ids` grows every time a new notification is sent
    for this target (the original escalation, then every subsequent
    customer follow-up while still open) so the admin can reply to *any*
    message in the thread, not just the first.
    """

    __tablename__ = "pending_admin_replies"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    merchant_admin_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("merchant_admins.id"), index=True)
    kind: Mapped[str] = mapped_column(String(32))
    target_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)
    platform_message_ids: Mapped[list[int]] = mapped_column(ARRAY(BigInteger), default=list)
    status: Mapped[str] = mapped_column(String(16), default="open")
    created_at: Mapped[datetime] = mapped_column(default=_now)
    last_activity_at: Mapped[datetime] = mapped_column(default=_now, onupdate=_now)


class PlatformOnboardingSession(Base):
    """Crude per-chat state machine for self-serve onboarding through the
    platform bot (app/onboarding/service.py) - no framework, just a state
    string. States: start -> awaiting_token -> choosing_vertical -> done.
    Keyed by telegram_user_id since onboarding always happens in a private
    DM with the platform bot (chat.id == user.id there).
    """

    __tablename__ = "platform_onboarding_sessions"

    telegram_user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    state: Mapped[str] = mapped_column(String(32), default="start")
    language: Mapped[str | None] = mapped_column(String(8), nullable=True)
    merchant_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("merchants.id"), nullable=True)
    # Transient scratch state for multi-select/follow-up-loop steps (e.g.
    # which FAQ topics were picked, which one is being answered right
    # now) - distinct from Merchant.profile, where FINALIZED answers
    # land once a step completes.
    data: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=_now)
    updated_at: Mapped[datetime] = mapped_column(default=_now, onupdate=_now)


class Product(Base):
    __tablename__ = "products"
    __table_args__ = (UniqueConstraint("source_channel_id", "source_message_id", name="uq_products_source"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    merchant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("merchants.id"), index=True)
    name: Mapped[str] = mapped_column(String(255))
    price: Mapped[float | None] = mapped_column(Numeric(12, 2), nullable=True)
    currency: Mapped[str | None] = mapped_column(String(8), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    image_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    image_embedding: Mapped[list[float] | None] = mapped_column(Vector(IMAGE_EMBEDDING_DIM), nullable=True)
    # BGE-M3 text embedding of name+description (app/products/ingestion.py
    # sets this on every create/update, scripts/backfill_product_embeddings.py
    # backfills pre-existing rows) - used by app/products/retrieval.py's
    # top-K similarity search for Gemini grounded answering
    # (app/llm/answer.py). Distinct from image_embedding above (SigLIP,
    # for photo search) - same EMBEDDING_DIM constant Faq.embedding uses.
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBEDDING_DIM), nullable=True)
    # Set together when a product came from channel ingestion
    # (app/products/ingestion.py) - a forwarded post's forward_origin
    # resolves against this pair (see app/telegram/webhook.py's
    # forward-match branch). NULL/NULL for manually-uploaded products;
    # Postgres treats NULL != NULL in unique constraints, so the
    # constraint below only actually enforces uniqueness when both are
    # present (an edited_channel_post reuses the same source_message_id,
    # which is how ingestion tells create from update apart).
    source_channel_id: Mapped[int | None] = mapped_column(BigInteger, index=True, nullable=True)
    source_message_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    # "set" / "missing" / "pending_merchant" - not DB-enforced (see the
    # vertical/provider_name precedent elsewhere in this file), since it's
    # app-level workflow state, not a fixed domain of values a constraint
    # should police. Missing price is a first-class product state, not
    # bad data - see app/products/ingestion.py and the missing-price
    # queue in app/handoff/service.py.
    price_status: Mapped[str | None] = mapped_column(String(16), nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=_now)

    merchant: Mapped["Merchant"] = relationship(back_populates="products")


class Customer(Base):
    __tablename__ = "customers"
    __table_args__ = (UniqueConstraint("merchant_id", "telegram_user_id", name="uq_customer_merchant_tguser"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    merchant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("merchants.id"), index=True)
    # BigInteger, not the default 32-bit Integer: real Telegram user ids
    # already exceed Postgres's int32 range (2,147,483,647) for newer
    # accounts. Fixed alongside the related admin_chat_id addition below,
    # which would have had the same bug if left as a plain Integer.
    telegram_user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    preferred_language: Mapped[str | None] = mapped_column(String(8), nullable=True)
    # Refreshed from TelegramUser.first_name on every inbound message
    # (app/telegram/webhook.py) - needed so Layer-5 escalation
    # notifications can show a human name instead of a raw numeric id
    # (see app/handoff/service.py's escalate()).
    first_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=_now)

    merchant: Mapped["Merchant"] = relationship(back_populates="customers")
    conversations: Mapped[list["Conversation"]] = relationship(back_populates="customer")


class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    merchant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("merchants.id"), index=True)
    customer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("customers.id"), index=True)
    # Layer 5 (app/handoff/): while True, the pipeline suppresses all
    # automated replies on this thread - the merchant is handling it
    # directly via /reply in their admin chat. Cleared by /release.
    needs_human: Mapped[bool] = mapped_column(default=False)
    # Small, structured state - not a general-purpose dumping ground.
    # {"last_candidates": [{"product_id": ..., "rank": 1}, ...],
    #  "last_matched_product_id": ...} - written by the image-search
    # carousel (app/image_search/) so a later text follow-up ("ikkinchisi
    # narxi qancha?") can resolve an ordinal reference, and by a
    # confirmed carousel tap. See app/image_search/ordinal.py.
    context: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=_now)

    merchant: Mapped["Merchant"] = relationship(back_populates="conversations")
    customer: Mapped["Customer"] = relationship(back_populates="conversations")
    messages: Mapped[list["Message"]] = relationship(back_populates="conversation")


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    conversation_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("conversations.id"), index=True)
    direction: Mapped[str] = mapped_column(String(8))  # "in" | "out"
    type: Mapped[str] = mapped_column(String(16))  # "text" | "photo"
    raw_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    detected_language: Mapped[str | None] = mapped_column(String(8), nullable=True)
    normalized_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    intent: Mapped[str | None] = mapped_column(String(64), nullable=True)
    matched_product_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("products.id"), nullable=True)
    match_confidence: Mapped[float | None] = mapped_column(nullable=True)
    # which layer produced the reply: "rule" | "faq" | "intent" | "llm" |
    # "image" (image-search carousel) | "forward_match" (a forwarded
    # channel post resolved directly to a known product) | "post_link_match"
    # (a customer-pasted t.me link resolved the same way, no forward
    # needed - see app/telegram/webhook.py) | "handoff" (layer 5's own
    # "passed to the seller" message) | "human" (the merchant's own
    # reply, relayed verbatim) | None (inbound / no match)
    response_source: Mapped[str | None] = mapped_column(String(16), nullable=True)
    # Coarse resolution-path label, uniform across both pipeline modes:
    # "deterministic" (forward-match, post-link match) | "llm" (Gemini
    # grounded answer in llm_first mode, or the Claude fallback in
    # layered mode) | "handoff". For layered-mode messages this is
    # derived from response_source (rule/faq/intent/image/forward_match
    # -> deterministic, llm -> llm, handoff/human -> handoff) rather than
    # stored redundantly by each layer.
    resolution_path: Mapped[str | None] = mapped_column(String(16), nullable=True)
    raw_update: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=_now)

    conversation: Mapped["Conversation"] = relationship(back_populates="messages")


class LlmFallbackLog(Base):
    """One row per LLM call (not per cache hit) - captures the exact
    context given to the model alongside its answer, specifically so a
    provider comparison can be run later as a replay against real misses
    instead of a synthetic test set. Shared by both the layered-mode
    Claude fallback (app/llm/service.py) and llm_first-mode Gemini
    grounded answering (app/llm/answer.py) - provider_name already
    discriminates rows from either, and this table's stated purpose
    applies identically to both.
    """

    __tablename__ = "llm_fallback_logs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    merchant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("merchants.id"), index=True)
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("conversations.id"), nullable=True)
    query: Mapped[str] = mapped_column(Text)
    context_snapshot: Mapped[dict] = mapped_column(JSONB)
    provider_name: Mapped[str] = mapped_column(String(64))
    answerable: Mapped[bool] = mapped_column()
    answer: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Which prompt file version produced this answer (e.g.
    # "grounded_answer_v1") - lets replay data stay interpretable across
    # prompt contract changes. NULL for Claude fallback rows (no
    # versioned prompt file there).
    prompt_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # The product Gemini said its answer was about, if any - written to
    # conversations.context.last_matched_product_id the same way
    # forward-match already does. NULL when the answer wasn't about a
    # specific product (e.g. a delivery/hours FAQ).
    matched_product_ref: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("products.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=_now)


class ImageMatchLog(Base):
    """One row per photo query - captures the full top-10 similarity
    scores (not just the top-3 shown to the customer) plus the
    confirmation outcome, so a real per-merchant match floor can be
    calibrated from pilot data instead of guessed. The floor used at
    query time (app/image_search/retrieval.py FLOOR) is deliberately not
    safety-critical in v1 - the customer's confirm/"none of these" tap is
    the real gate. See app/image_search/ and the aqlchat-phase1-plan
    memory for the full reasoning.
    """

    __tablename__ = "image_match_logs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    merchant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("merchants.id"), index=True)
    conversation_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("conversations.id"))
    top_candidates: Mapped[list] = mapped_column(JSONB)  # [{"product_id": ..., "similarity": ...}, ...] up to 10
    floor_applied: Mapped[bool] = mapped_column(default=False)
    confirmed_product_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("products.id"), nullable=True)
    none_tapped: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(default=_now)


class Flow(Base):
    __tablename__ = "flows"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    merchant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("merchants.id"), index=True)
    name: Mapped[str] = mapped_column(String(255))
    trigger_type: Mapped[str] = mapped_column(String(32))  # e.g. "keyword", "command"
    trigger_value: Mapped[str] = mapped_column(String(255))
    # Which inbound channel this rule listens on: 'telegram' (customer
    # messages to the tenant bot, the original pipeline's Layer-1 rules)
    # | 'instagram_comment' (comment-to-DM automation, app/instagram/).
    # Not DB-enforced, per the pipeline_mode/vertical precedent above.
    # match_flow filters on this so an IG comment rule can never fire on
    # a Telegram message or vice versa.
    channel: Mapped[str] = mapped_column(String(32), server_default="telegram")
    # Shape differs by channel. telegram: {"type": "text", "text": ...}.
    # instagram_comment: {"link": ..., "private_reply": {uz/ru with
    # {link} placeholder}, "public_reply": {uz/ru} (optional),
    # "media_ids": [...] (optional - absent/empty = all posts; else the
    # rule only fires on comments under those IG media ids)}.
    response_config: Mapped[dict] = mapped_column(JSONB)
    # On/off toggle for merchant-installed automations (app/automations/) -
    # match_flow filters on this so a deactivated rule stops firing without
    # deleting it (keeps its stats/history). Rows from scripts/load_flows
    # (hand-written YAML, no installer UI) default to active.
    is_active: Mapped[bool] = mapped_column(default=True, server_default="true")
    # Which app/flows/templates.py preset this row was installed from, or
    # None for hand-written rows (scripts/load_flows) - drives the
    # dashboard's "installed" badge and (later) preset-upgrade tooling.
    # Not a FK: presets are code, not a DB table (see templates.py).
    template_key: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=_now)

    merchant: Mapped["Merchant"] = relationship(back_populates="flows")


class Faq(Base):
    __tablename__ = "faqs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    merchant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("merchants.id"), index=True)
    question: Mapped[str] = mapped_column(Text)
    # per-language reply text, e.g. {"uz": "...", "ru": "..."} - matching is
    # cross-lingual (one embedding of `question` regardless of its
    # language), but the reply shown to the customer should be in their
    # own language. See app/faq/retrieval.py for the language fallback.
    response_config: Mapped[dict] = mapped_column(JSONB)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBEDDING_DIM), nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=_now)

    merchant: Mapped["Merchant"] = relationship(back_populates="faqs")


class CommentEvent(Base):
    """One row per inbound Instagram comment webhook event - the dedupe
    record AND the processing log for comment-to-DM automation
    (app/instagram/). Deliberately NOT a Message: messages require a
    Conversation (non-nullable FK) and this feature creates no
    conversations - a comment is a one-shot public event, not a thread.
    Follows the LlmFallbackLog/ImageMatchLog purpose-built-log precedent.

    The UNIQUE (merchant_id, external_id) constraint is the dedupe:
    the webhook handler inserts (status='pending') before enqueueing, so
    Meta's redelivery of the same comment id no-ops at the DB instead of
    double-DMing a customer.

    status lifecycle: pending (webhook accepted, queued) -> matched ->
    replied | no_match (silence, by design) | deferred (rate-limit cap
    hit, parked in the Redis deferred set) | dropped_stale (older than
    Meta's 7-day private-reply window at processing time) | failed.
    """

    __tablename__ = "comment_events"
    __table_args__ = (UniqueConstraint("merchant_id", "external_id", name="uq_comment_events_merchant_external"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    merchant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("merchants.id"), index=True)
    # 'instagram_comment' today; a column (not implied) so a future
    # comment-bearing channel reuses this table instead of cloning it.
    channel: Mapped[str] = mapped_column(String(32))
    # IG comment id - Meta-issued, string-typed (their ids overflow int32
    # and are documented as strings; never arithmetic on them).
    external_id: Mapped[str] = mapped_column(String(64))
    # from.id of the commenter - kept for the loop-guard audit trail
    # (our own public replies arrive back on this webhook with
    # from.id == the merchant's own instagram_user_id).
    commenter_id: Mapped[str] = mapped_column(String(64))
    media_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    raw_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    detected_language: Mapped[str | None] = mapped_column(String(8), nullable=True)
    matched_flow_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("flows.id"), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="pending")
    created_at: Mapped[datetime] = mapped_column(default=_now)
    replied_at: Mapped[datetime | None] = mapped_column(nullable=True)


class EmailVerificationCode(Base):
    """A one-time 6-digit code for the website's passwordless signup/login
    (app/auth/) - sent to `email` via Resend, proves the address AND logs
    the merchant in, so there's no separate password to manage.

    Only `code_hash` (sha256) is stored, never the plaintext code - same
    reasoning as not storing plaintext bot tokens, just cheaper (no need
    for reversible decryption; verification only ever compares hashes).
    `email` is denormalized here rather than requiring merchant_id up
    front: request-code creates this row (and a not-yet-verified Merchant,
    via get-or-create) in the same step, so both are set together.
    """

    __tablename__ = "email_verification_codes"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    merchant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("merchants.id"), index=True)
    email: Mapped[str] = mapped_column(String(320), index=True)
    code_hash: Mapped[str] = mapped_column(String(64))
    # Wrong-code guesses against this row - checked against a small cap
    # (app/auth/service.py) so a code isn't brute-forceable within its
    # short expiry window.
    attempts: Mapped[int] = mapped_column(default=0)
    expires_at: Mapped[datetime] = mapped_column()
    consumed_at: Mapped[datetime | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=_now)


class WebSession(Base):
    """A logged-in website session for a merchant (app/auth/) - opaque
    bearer token in an httponly cookie, DB-backed (not a signed JWT) so a
    session can be revoked (logout) by deleting/marking the row instead of
    needing a blocklist. Only `token_hash` (sha256) is stored, matching
    EmailVerificationCode - the raw token lives only in the cookie.
    """

    __tablename__ = "web_sessions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    merchant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("merchants.id"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column()
    revoked_at: Mapped[datetime | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=_now)
