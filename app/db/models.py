import secrets
import uuid
from datetime import datetime, timezone

from pgvector.sqlalchemy import Vector
from sqlalchemy import BigInteger, ForeignKey, Numeric, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
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
    name: Mapped[str] = mapped_column(String(255))
    # Encrypted at rest (app/db/crypto.py) - the ORM attribute is still a
    # plain Python str everywhere it's read/written, only the DB column
    # holds ciphertext. NOT unique: Fernet is randomized (fresh IV per
    # call), so encrypting the same token twice produces different
    # ciphertext - a DB-level unique constraint here would silently stop
    # enforcing "this bot isn't already registered". telegram_bot_id
    # below is the real uniqueness key.
    telegram_bot_token: Mapped[str] = mapped_column(EncryptedString)
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
    webhook_slug: Mapped[str] = mapped_column(
        String(64), unique=True, index=True, default=lambda: secrets.token_urlsafe(24)
    )
    webhook_secret: Mapped[str] = mapped_column(String(255))
    # Set during onboarding (app/onboarding/service.py) - clothing /
    # cosmetics / other for now. Nullable: not collected before that
    # onboarding step completes, and older merchants seeded manually
    # won't have one.
    vertical: Mapped[str | None] = mapped_column(String(32), nullable=True)
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
    created_at: Mapped[datetime] = mapped_column(default=_now)
    updated_at: Mapped[datetime] = mapped_column(default=_now, onupdate=_now)


class Product(Base):
    __tablename__ = "products"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    merchant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("merchants.id"), index=True)
    name: Mapped[str] = mapped_column(String(255))
    price: Mapped[float | None] = mapped_column(Numeric(12, 2), nullable=True)
    currency: Mapped[str | None] = mapped_column(String(8), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    image_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    image_embedding: Mapped[list[float] | None] = mapped_column(Vector(IMAGE_EMBEDDING_DIM), nullable=True)
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
    # "handoff" (layer 5's own "passed to the seller" message) | "human"
    # (the merchant's own /reply, relayed verbatim) | None (inbound / no match)
    response_source: Mapped[str | None] = mapped_column(String(16), nullable=True)
    raw_update: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=_now)

    conversation: Mapped["Conversation"] = relationship(back_populates="messages")


class LlmFallbackLog(Base):
    """One row per LLM fallback call (not per cache hit) - captures the
    exact context given to the model alongside its answer, specifically so
    the deferred provider bake-off can be run later as a replay against
    real misses instead of a synthetic test set. See app/llm/service.py.
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
    response_config: Mapped[dict] = mapped_column(JSONB)
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
