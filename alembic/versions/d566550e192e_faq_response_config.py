"""faq response_config

Revision ID: d566550e192e
Revises: 3a557475ae89
Create Date: 2026-07-11 00:16:24.867787

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


# revision identifiers, used by Alembic.
revision: str = 'd566550e192e'
down_revision: Union[str, Sequence[str], None] = '3a557475ae89'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("faqs", sa.Column("response_config", JSONB, nullable=True))
    op.execute(
        "UPDATE faqs SET response_config = jsonb_build_object('uz', answer)"
    )
    op.alter_column("faqs", "response_config", nullable=False)
    op.drop_column("faqs", "answer")


def downgrade() -> None:
    """Downgrade schema."""
    op.add_column("faqs", sa.Column("answer", sa.Text(), nullable=True))
    op.execute("UPDATE faqs SET answer = response_config ->> 'uz'")
    op.alter_column("faqs", "answer", nullable=False)
    op.drop_column("faqs", "response_config")
