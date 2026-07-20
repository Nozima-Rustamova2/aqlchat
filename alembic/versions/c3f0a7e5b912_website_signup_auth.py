"""website signup auth

Website signup (app/auth/): the merchant row can now be created before any
Telegram bot is connected, so `name` (shop name) and `telegram_bot_token`
drop their NOT NULL - both are collected later, during tenant bot
onboarding, same as before. Adds the signup identity fields (owner_name,
email, phone_number, email_verified_at) plus two new tables:
email_verification_codes (the passwordless 6-digit email OTP - doubles as
signup verification and login) and web_sessions (DB-backed opaque session
tokens for the website, revocable on logout unlike a signed JWT).

Revision ID: c3f0a7e5b912
Revises: 475cf3b5c868
Create Date: 2026-07-18 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID


# revision identifiers, used by Alembic.
revision: str = 'c3f0a7e5b912'
down_revision: Union[str, Sequence[str], None] = '475cf3b5c868'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.alter_column("merchants", "name", existing_type=sa.String(255), nullable=True)
    op.alter_column("merchants", "telegram_bot_token", existing_type=sa.Text(), nullable=True)

    op.add_column("merchants", sa.Column("owner_name", sa.String(255), nullable=True))
    op.add_column("merchants", sa.Column("email", sa.String(320), nullable=True))
    op.create_unique_constraint("uq_merchants_email", "merchants", ["email"])
    op.add_column("merchants", sa.Column("phone_number", sa.String(32), nullable=True))
    op.add_column("merchants", sa.Column("email_verified_at", sa.DateTime(), nullable=True))

    op.create_table(
        "email_verification_codes",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("merchant_id", UUID(as_uuid=True), sa.ForeignKey("merchants.id"), nullable=False),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("code_hash", sa.String(64), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("consumed_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_email_verification_codes_merchant_id", "email_verification_codes", ["merchant_id"])
    op.create_index("ix_email_verification_codes_email", "email_verification_codes", ["email"])

    op.create_table(
        "web_sessions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("merchant_id", UUID(as_uuid=True), sa.ForeignKey("merchants.id"), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("revoked_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("token_hash", name="uq_web_sessions_token_hash"),
    )
    op.create_index("ix_web_sessions_merchant_id", "web_sessions", ["merchant_id"])
    op.create_index("ix_web_sessions_token_hash", "web_sessions", ["token_hash"])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_web_sessions_token_hash", table_name="web_sessions")
    op.drop_index("ix_web_sessions_merchant_id", table_name="web_sessions")
    op.drop_table("web_sessions")

    op.drop_index("ix_email_verification_codes_email", table_name="email_verification_codes")
    op.drop_index("ix_email_verification_codes_merchant_id", table_name="email_verification_codes")
    op.drop_table("email_verification_codes")

    op.drop_column("merchants", "email_verified_at")
    op.drop_column("merchants", "phone_number")
    op.drop_constraint("uq_merchants_email", "merchants", type_="unique")
    op.drop_column("merchants", "email")
    op.drop_column("merchants", "owner_name")

    op.alter_column("merchants", "telegram_bot_token", existing_type=sa.Text(), nullable=False)
    op.alter_column("merchants", "name", existing_type=sa.String(255), nullable=False)


