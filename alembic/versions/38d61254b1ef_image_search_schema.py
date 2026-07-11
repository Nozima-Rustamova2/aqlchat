"""image search schema

Revision ID: 38d61254b1ef
Revises: b8a44c9d1ecf
Create Date: 2026-07-11 15:54:32.120131

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects.postgresql import JSONB, UUID


# revision identifiers, used by Alembic.
revision: str = '38d61254b1ef'
down_revision: Union[str, Sequence[str], None] = 'b8a44c9d1ecf'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # products.image_embedding was provisioned at BGE-M3's dimension
    # (1024) before the image model was chosen, and has never been
    # populated - safe to drop and re-add at SigLIP's actual dimension
    # (768) rather than carry a wrong-dimension dead column.
    op.drop_column("products", "image_embedding")
    op.add_column("products", sa.Column("image_embedding", Vector(768), nullable=True))

    op.add_column("conversations", sa.Column("context", JSONB, nullable=True))

    op.create_table(
        "image_match_logs",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("merchant_id", UUID(as_uuid=True), sa.ForeignKey("merchants.id"), nullable=False),
        sa.Column("conversation_id", UUID(as_uuid=True), sa.ForeignKey("conversations.id"), nullable=False),
        sa.Column("top_candidates", JSONB, nullable=False),
        sa.Column("floor_applied", sa.Boolean(), nullable=False),
        sa.Column("confirmed_product_id", UUID(as_uuid=True), sa.ForeignKey("products.id"), nullable=True),
        sa.Column("none_tapped", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_image_match_logs_merchant_id", "image_match_logs", ["merchant_id"])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_image_match_logs_merchant_id", table_name="image_match_logs")
    op.drop_table("image_match_logs")
    op.drop_column("conversations", "context")
    op.drop_column("products", "image_embedding")
    op.add_column("products", sa.Column("image_embedding", Vector(1024), nullable=True))
