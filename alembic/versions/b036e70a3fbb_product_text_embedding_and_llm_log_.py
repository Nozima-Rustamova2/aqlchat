"""product text embedding and llm log widening

Adds Product.embedding (BGE-M3 text embedding, EMBEDDING_DIM=1024 -
distinct from image_embedding's SigLIP 768-dim) for the Gemini
grounded-answering context assembly's top-K product retrieval (see
app/products/retrieval.py, app/llm/answer.py). Widens llm_fallback_logs
with prompt_version/matched_product_ref, shared by both the layered-mode
Claude fallback and llm_first-mode Gemini answering - see the mode-switch
plan.

Revision ID: b036e70a3fbb
Revises: 60a83d687dcc
Create Date: 2026-07-12 15:59:40.192576

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects.postgresql import UUID


# revision identifiers, used by Alembic.
revision: str = 'b036e70a3fbb'
down_revision: Union[str, Sequence[str], None] = '60a83d687dcc'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("products", sa.Column("embedding", Vector(1024), nullable=True))

    op.add_column("llm_fallback_logs", sa.Column("prompt_version", sa.String(32), nullable=True))
    op.add_column(
        "llm_fallback_logs",
        sa.Column("matched_product_ref", UUID(as_uuid=True), sa.ForeignKey("products.id"), nullable=True),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("llm_fallback_logs", "matched_product_ref")
    op.drop_column("llm_fallback_logs", "prompt_version")
    op.drop_column("products", "embedding")
