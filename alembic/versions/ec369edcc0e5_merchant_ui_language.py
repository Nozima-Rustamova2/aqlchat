"""merchant ui language

Revision ID: ec369edcc0e5
Revises: d4a1c9f2e6b7
Create Date: 2026-07-19 16:35:07.478886

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'ec369edcc0e5'
down_revision: Union[str, Sequence[str], None] = 'd4a1c9f2e6b7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "merchants",
        sa.Column("ui_language", sa.String(length=8), server_default="uz", nullable=False),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("merchants", "ui_language")
