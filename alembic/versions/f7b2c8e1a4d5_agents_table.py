"""agents table

New, additive "Agentlar" feature (design_handoff_dukan_ai_agents/): a
merchant-configurable AI persona that auto-replies via its own Telegram
bot connection. Deliberately parallel to Merchant.telegram_bot_token/
telegram_bot_id, not a migration of them - see app/db/models.py's Agent
docstring.

Revision ID: f7b2c8e1a4d5
Revises: ec369edcc0e5
Create Date: 2026-07-20 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID


# revision identifiers, used by Alembic.
revision: str = 'f7b2c8e1a4d5'
down_revision: Union[str, Sequence[str], None] = 'ec369edcc0e5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "agents",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("merchant_id", UUID(as_uuid=True), sa.ForeignKey("merchants.id"), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("tone", sa.String(length=16), nullable=False, server_default="polite"),
        sa.Column("daily_limit", sa.Integer(), nullable=False, server_default="20"),
        sa.Column("knowledge_text", sa.Text(), nullable=False, server_default=""),
        sa.Column("active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("split_messages", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("operator_pause", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("stop_keywords", sa.Text(), nullable=True),
        sa.Column("telegram_bot_token", sa.Text(), nullable=True),
        sa.Column("telegram_bot_id", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("telegram_bot_id", name="uq_agents_telegram_bot_id"),
    )
    op.create_index("ix_agents_merchant_id", "agents", ["merchant_id"])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_agents_merchant_id", table_name="agents")
    op.drop_table("agents")
