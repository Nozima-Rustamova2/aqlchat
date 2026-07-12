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
