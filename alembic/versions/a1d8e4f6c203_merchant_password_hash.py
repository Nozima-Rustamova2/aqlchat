"""merchant password hash

Signup (app/auth/) now collects a password at registration instead of
being fully passwordless - the email code still verifies address
ownership, but login is email+password afterward. See
app/auth/passwords.py for the PBKDF2-HMAC-SHA256 hashing (stdlib only).

Revision ID: a1d8e4f6c203
Revises: c3f0a7e5b912
Create Date: 2026-07-18 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a1d8e4f6c203'
down_revision: Union[str, Sequence[str], None] = 'c3f0a7e5b912'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("merchants", sa.Column("password_hash", sa.String(255), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("merchants", "password_hash")
