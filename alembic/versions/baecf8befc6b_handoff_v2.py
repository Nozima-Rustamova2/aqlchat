"""handoff v2

Adds customers.first_name (so escalations can show a human name) and
pending_admin_replies (tracks which platform-bot message threads
resolve to which target - a conversation being escalated, or a product
awaiting a price - so an admin's Telegram-reply resolves back to what
it's about). See app/handoff/service.py and app/db/models.py's
PendingAdminReply docstring.

Revision ID: baecf8befc6b
Revises: 0ff2ed7ab500
Create Date: 2026-07-12 12:39:30.523893

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY, UUID


# revision identifiers, used by Alembic.
revision: str = 'baecf8befc6b'
down_revision: Union[str, Sequence[str], None] = '0ff2ed7ab500'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("customers", sa.Column("first_name", sa.String(255), nullable=True))

    op.create_table(
        "pending_admin_replies",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("merchant_admin_id", UUID(as_uuid=True), sa.ForeignKey("merchant_admins.id"), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("target_id", UUID(as_uuid=True), nullable=False),
        sa.Column("platform_message_ids", ARRAY(sa.BigInteger()), nullable=False, server_default="{}"),
        sa.Column("status", sa.String(16), nullable=False, server_default="open"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("last_activity_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_pending_admin_replies_merchant_admin_id", "pending_admin_replies", ["merchant_admin_id"])
    op.create_index("ix_pending_admin_replies_target_id", "pending_admin_replies", ["target_id"])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("pending_admin_replies")
    op.drop_column("customers", "first_name")
