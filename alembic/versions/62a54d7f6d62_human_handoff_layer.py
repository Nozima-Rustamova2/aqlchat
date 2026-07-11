"""human handoff layer

Revision ID: 62a54d7f6d62
Revises: d566550e192e
Create Date: 2026-07-11 13:19:23.310447

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '62a54d7f6d62'
down_revision: Union[str, Sequence[str], None] = 'd566550e192e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("merchants", sa.Column("admin_chat_id", sa.BigInteger(), nullable=True))
    op.add_column(
        "conversations",
        sa.Column("needs_human", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.alter_column("conversations", "needs_human", server_default=None)

    # customers.telegram_user_id was a 32-bit Integer, which real Telegram
    # user ids for newer accounts already exceed. Fixed here alongside the
    # related admin_chat_id addition above, which would have had the same
    # bug if added as a plain Integer.
    op.alter_column("customers", "telegram_user_id", type_=sa.BigInteger())


def downgrade() -> None:
    """Downgrade schema."""
    op.alter_column("customers", "telegram_user_id", type_=sa.Integer())
    op.drop_column("conversations", "needs_human")
    op.drop_column("merchants", "admin_chat_id")
