"""platform bot architecture

Splits the single tenant-bot-doubles-as-admin-channel design into a
shared platform bot (merchant-facing: onboarding, escalation inbox,
/reply /release) + tenant bots (purely customer-facing). See the
platform-bot-refactor plan and aqlchat-phase1-plan memory for the full
reasoning.

Data migrations performed here, in order:
  1. Widen telegram_bot_token to Text and re-encrypt existing plaintext
     values with Fernet (app/db/crypto.py) - must happen BEFORE the ORM's
     new EncryptedString type is what any future code sees, since after
     this migration every read of that column goes through Fernet.decrypt.
  2. Backfill webhook_slug from each row's own id (already random and
     URL-safe) - avoids a per-row Python loop for existing rows; new rows
     get a fresh secrets.token_urlsafe() value via the model's default.
  3. Backfill merchant_admins from any existing admin_chat_id values, so
     the already-configured live test setup isn't silently lost.
  4. Drop admin_chat_id and the old telegram_bot_token uniqueness
     constraint (superseded by telegram_bot_id - see app/db/models.py).

Revision ID: 4cea323fc768
Revises: 38d61254b1ef
Create Date: 2026-07-12 01:30:18.713636

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID
from cryptography.fernet import Fernet

from app.config import settings


# revision identifiers, used by Alembic.
revision: str = '4cea323fc768'
down_revision: Union[str, Sequence[str], None] = '38d61254b1ef'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    conn = op.get_bind()

    # --- 1. widen + encrypt telegram_bot_token in place ---
    op.alter_column("merchants", "telegram_bot_token", type_=sa.Text())

    fernet = Fernet(settings.token_encryption_key.encode())
    rows = conn.execute(sa.text("SELECT id, telegram_bot_token FROM merchants")).fetchall()
    for row_id, plaintext_token in rows:
        ciphertext = fernet.encrypt(plaintext_token.encode()).decode()
        conn.execute(
            sa.text("UPDATE merchants SET telegram_bot_token = :ciphertext WHERE id = :id"),
            {"ciphertext": ciphertext, "id": row_id},
        )

    op.drop_constraint("merchants_telegram_bot_token_key", "merchants", type_="unique")

    # --- 2. new merchant columns ---
    op.add_column("merchants", sa.Column("telegram_bot_id", sa.BigInteger(), nullable=True))
    op.create_unique_constraint("uq_merchants_telegram_bot_id", "merchants", ["telegram_bot_id"])

    op.add_column("merchants", sa.Column("webhook_slug", sa.String(64), nullable=True))
    op.execute("UPDATE merchants SET webhook_slug = id::text WHERE webhook_slug IS NULL")
    op.alter_column("merchants", "webhook_slug", nullable=False)
    op.create_unique_constraint("uq_merchants_webhook_slug", "merchants", ["webhook_slug"])
    op.create_index("ix_merchants_webhook_slug", "merchants", ["webhook_slug"])

    op.add_column("merchants", sa.Column("vertical", sa.String(32), nullable=True))

    # --- 3. merchant_admins, backfilled from admin_chat_id ---
    op.create_table(
        "merchant_admins",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("merchant_id", UUID(as_uuid=True), sa.ForeignKey("merchants.id"), nullable=False),
        sa.Column("telegram_user_id", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_merchant_admins_merchant_id", "merchant_admins", ["merchant_id"])
    op.create_unique_constraint("uq_merchant_admins_telegram_user_id", "merchant_admins", ["telegram_user_id"])

    op.execute(
        """
        INSERT INTO merchant_admins (id, merchant_id, telegram_user_id, created_at)
        SELECT gen_random_uuid(), id, admin_chat_id, now()
        FROM merchants
        WHERE admin_chat_id IS NOT NULL
        """
    )

    # --- 4. platform_onboarding_sessions ---
    op.create_table(
        "platform_onboarding_sessions",
        sa.Column("telegram_user_id", sa.BigInteger(), primary_key=True),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("language", sa.String(8), nullable=True),
        sa.Column("merchant_id", UUID(as_uuid=True), sa.ForeignKey("merchants.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )

    # --- 5. drop admin_chat_id, now superseded by merchant_admins ---
    op.drop_column("merchants", "admin_chat_id")


def downgrade() -> None:
    """Downgrade schema."""
    op.add_column("merchants", sa.Column("admin_chat_id", sa.BigInteger(), nullable=True))
    op.execute(
        """
        UPDATE merchants m
        SET admin_chat_id = ma.telegram_user_id
        FROM merchant_admins ma
        WHERE ma.merchant_id = m.id
        """
    )

    op.drop_table("platform_onboarding_sessions")
    op.drop_table("merchant_admins")

    op.drop_column("merchants", "vertical")

    op.drop_index("ix_merchants_webhook_slug", table_name="merchants")
    op.drop_constraint("uq_merchants_webhook_slug", "merchants", type_="unique")
    op.drop_column("merchants", "webhook_slug")

    op.drop_constraint("uq_merchants_telegram_bot_id", "merchants", type_="unique")
    op.drop_column("merchants", "telegram_bot_id")

    conn = op.get_bind()
    fernet = Fernet(settings.token_encryption_key.encode())
    rows = conn.execute(sa.text("SELECT id, telegram_bot_token FROM merchants")).fetchall()
    for row_id, ciphertext in rows:
        plaintext = fernet.decrypt(ciphertext.encode()).decode()
        conn.execute(
            sa.text("UPDATE merchants SET telegram_bot_token = :plaintext WHERE id = :id"),
            {"plaintext": plaintext, "id": row_id},
        )
    op.alter_column("merchants", "telegram_bot_token", type_=sa.String(255))
    op.create_unique_constraint("merchants_telegram_bot_token_key", "merchants", ["telegram_bot_token"])
