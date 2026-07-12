"""channel forward match schema

Schema-only checkpoint of the channel-as-catalog pivot (see the pivot
plan). Adds the columns forward-match resolution and channel ingestion
need: products gain a (source_channel_id, source_message_id) pointer
back to the channel post that created them, plus price_status as a
first-class workflow state (missing price is not bad data - see
app/products/ingestion.py, not yet written as of this migration).
merchants gain their own registered catalog channel.

Revision ID: 0ff2ed7ab500
Revises: 4cea323fc768
Create Date: 2026-07-12 12:20:25.918048

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0ff2ed7ab500'
down_revision: Union[str, Sequence[str], None] = '4cea323fc768'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("products", sa.Column("source_channel_id", sa.BigInteger(), nullable=True))
    op.add_column("products", sa.Column("source_message_id", sa.BigInteger(), nullable=True))
    op.create_index("ix_products_source_channel_id", "products", ["source_channel_id"])
    op.create_unique_constraint("uq_products_source", "products", ["source_channel_id", "source_message_id"])

    op.add_column("products", sa.Column("price_status", sa.String(16), nullable=True))
    op.execute(
        "UPDATE products SET price_status = CASE WHEN price IS NOT NULL THEN 'set' ELSE 'missing' END"
    )

    op.add_column("merchants", sa.Column("source_channel_id", sa.BigInteger(), nullable=True))
    op.create_unique_constraint("uq_merchants_source_channel_id", "merchants", ["source_channel_id"])
    op.add_column("merchants", sa.Column("source_channel_title", sa.String(255), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("merchants", "source_channel_title")
    op.drop_constraint("uq_merchants_source_channel_id", "merchants", type_="unique")
    op.drop_column("merchants", "source_channel_id")

    op.drop_column("products", "price_status")

    op.drop_constraint("uq_products_source", "products", type_="unique")
    op.drop_index("ix_products_source_channel_id", table_name="products")
    op.drop_column("products", "source_message_id")
    op.drop_column("products", "source_channel_id")
