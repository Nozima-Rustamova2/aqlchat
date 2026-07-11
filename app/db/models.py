import uuid
from datetime import datetime, timezone

from pgvector.sqlalchemy import Vector
from sqlalchemy import BigInteger, ForeignKey, Numeric, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

# BGE-M3 locked in 2026-07-10 after a cross-lingual retrieval check (see
# app/nlp/embeddings.py docstring). Dimension confirmed at 1024.
EMBEDDING_DIM = 1024


def _uuid() -> uuid.UUID:
    return uuid.uuid4()


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Merchant(Base):
    __tablename__ = "merchants"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(255))
    telegram_bot_token: Mapped[str] = mapped_column(String(255), unique=True)
    webhook_secret: Mapped[str] = mapped_column(String(255))
    # The merchant's own Telegram chat with their bot, used for the layer-5
    # human handoff (app/handoff/) - notifications and /reply, /release
    # commands go here. Nullable: a merchant hasn't necessarily registered
    # as their own admin yet.
    admin_chat_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=_now)

    products: Mapped[list["Product"]] = relationship(back_populates="merchant")
    customers: Mapped[list["Customer"]] = relationship(back_populates="merchant")
    conversations: Mapped[list["Conversation"]] = relationship(back_populates="merchant")
    flows: Mapped[list["Flow"]] = relationship(back_populates="merchant")
    faqs: Mapped[list["Faq"]] = relationship(back_populates="merchant")


class Product(Base):
    __tablename__ = "products"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    merchant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("merchants.id"), index=True)
    name: Mapped[str] = mapped_column(String(255))
    price: Mapped[float | None] = mapped_column(Numeric(12, 2), nullable=True)
    currency: Mapped[str | None] = mapped_column(String(8), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    image_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    image_embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBEDDING_DIM), nullable=True)
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
