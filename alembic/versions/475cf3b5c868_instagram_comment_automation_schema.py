"""instagram comment automation schema

Instagram comment-to-DM automation (app/instagram/): merchants gain the
IG connection triple (instagram_user_id routing key + encrypted
long-lived token + its expiry), flows gain a channel discriminator so
comment rules and Telegram keyword rules share the table without ever
cross-firing, and comment_events is the per-comment dedupe/processing
log (see the CommentEvent model docstring for the status lifecycle and
why this is not a Message).

Revision ID: 475cf3b5c868
Revises: b036e70a3fbb
Create Date: 2026-07-16 05:06:40.016503

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID


# revision identifiers, used by Alembic.
revision: str = '475cf3b5c868'
down_revision: Union[str, Sequence[str], None] = 'b036e70a3fbb'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("merchants", sa.Column("instagram_user_id", sa.BigInteger(), nullable=True))
    op.create_unique_constraint("uq_merchants_instagram_user_id", "merchants", ["instagram_user_id"])
    # Text, not String: EncryptedString's Fernet ciphertext runs longer
    # than the plaintext (same as telegram_bot_token in the initial schema).
    op.add_column("merchants", sa.Column("instagram_access_token", sa.Text(), nullable=True))
    op.add_column("merchants", sa.Column("instagram_token_expires_at", sa.DateTime(), nullable=True))

    op.add_column(
        "flows",
        sa.Column("channel", sa.String(32), nullable=False, server_default="telegram"),
    )

    op.create_table(
        "comment_events",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("merchant_id", UUID(as_uuid=True), sa.ForeignKey("merchants.id"), nullable=False),
        sa.Column("channel", sa.String(32), nullable=False),
        sa.Column("external_id", sa.String(64), nullable=False),
        sa.Column("commenter_id", sa.String(64), nullable=False),
        sa.Column("media_id", sa.String(64), nullable=True),
        sa.Column("raw_text", sa.Text(), nullable=True),
        sa.Column("detected_language", sa.String(8), nullable=True),
        sa.Column("matched_flow_id", UUID(as_uuid=True), sa.ForeignKey("flows.id"), nullable=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("replied_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("merchant_id", "external_id", name="uq_comment_events_merchant_external"),
    )
    op.create_index("ix_comment_events_merchant_id", "comment_events", ["merchant_id"])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_comment_events_merchant_id", table_name="comment_events")
    op.drop_table("comment_events")
    op.drop_column("flows", "channel")
    op.drop_constraint("uq_merchants_instagram_user_id", "merchants", type_="unique")
    op.drop_column("merchants", "instagram_token_expires_at")
    op.drop_column("merchants", "instagram_access_token")
    op.drop_column("merchants", "instagram_user_id")
