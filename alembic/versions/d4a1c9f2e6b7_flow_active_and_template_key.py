"""flow active and template key

Merchant-facing Instagram automation (app/automations/): flows gain
is_active (the installed-list on/off toggle - match_flow filters on it)
and template_key (provenance from app/flows/templates.py's presets, None
for hand-written scripts/load_flows rows).

Revision ID: d4a1c9f2e6b7
Revises: a1d8e4f6c203
Create Date: 2026-07-18 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd4a1c9f2e6b7'
down_revision: Union[str, Sequence[str], None] = 'a1d8e4f6c203'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("flows", sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"))
    op.add_column("flows", sa.Column("template_key", sa.String(64), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("flows", "template_key")
    op.drop_column("flows", "is_active")
