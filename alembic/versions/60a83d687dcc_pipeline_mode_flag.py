"""pipeline mode flag

Schema-only checkpoint of the LLM-first mode-switch: lets a merchant's
customer pipeline route entirely around the layered NLP pipeline
(keyword/FAQ/intent) to Gemini-grounded answering instead, without
deleting or dropping anything the layered pipeline needs - see the
mode-switch plan. merchants.pipeline_mode defaults every merchant
(existing and new) to 'llm_first' for the demo phase; flipping a
merchant back to 'layered' is a one-column update, not a re-migration.

Revision ID: 60a83d687dcc
Revises: baecf8befc6b
Create Date: 2026-07-12 15:41:52.155364

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


# revision identifiers, used by Alembic.
revision: str = '60a83d687dcc'
down_revision: Union[str, Sequence[str], None] = 'baecf8befc6b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "merchants",
        sa.Column("pipeline_mode", sa.String(16), nullable=False, server_default="llm_first"),
    )
    op.add_column("merchants", sa.Column("source_channel_username", sa.String(64), nullable=True))
    op.add_column("merchants", sa.Column("profile", JSONB(), nullable=True))

    op.add_column("messages", sa.Column("resolution_path", sa.String(16), nullable=True))

    op.add_column("platform_onboarding_sessions", sa.Column("data", JSONB(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("platform_onboarding_sessions", "data")
    op.drop_column("messages", "resolution_path")
    op.drop_column("merchants", "profile")
    op.drop_column("merchants", "source_channel_username")
    op.drop_column("merchants", "pipeline_mode")
