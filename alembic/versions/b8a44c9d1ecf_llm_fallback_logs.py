"""llm fallback logs

Revision ID: b8a44c9d1ecf
Revises: 62a54d7f6d62
Create Date: 2026-07-11 14:09:41.855827

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID


# revision identifiers, used by Alembic.
revision: str = 'b8a44c9d1ecf'
down_revision: Union[str, Sequence[str], None] = '62a54d7f6d62'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "llm_fallback_logs",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("merchant_id", UUID(as_uuid=True), sa.ForeignKey("merchants.id"), nullable=False),
        sa.Column("conversation_id", UUID(as_uuid=True), sa.ForeignKey("conversations.id"), nullable=True),
        sa.Column("query", sa.Text(), nullable=False),
        sa.Column("context_snapshot", JSONB, nullable=False),
        sa.Column("provider_name", sa.String(64), nullable=False),
        sa.Column("answerable", sa.Boolean(), nullable=False),
        sa.Column("answer", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_llm_fallback_logs_merchant_id", "llm_fallback_logs", ["merchant_id"])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_llm_fallback_logs_merchant_id", table_name="llm_fallback_logs")
    op.drop_table("llm_fallback_logs")
