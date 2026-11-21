"""mask cards and ibans in existing documents

Маскування наявних рядків `documents` без повторного видобутку.
Щоб відобразити зміни тексту в Qdrant, потрібна повна переіндексація.

Revision ID: 954e908904d3
Revises: 06f5ec3f6365
Create Date: 2026-11-08 21:07:29.457845

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from src.ingestion import privacy

# revision identifiers, used by Alembic.
revision: str = '954e908904d3'
down_revision: Union[str, Sequence[str], None] = '06f5ec3f6365'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    rows = conn.execute(sa.text("SELECT source_id, text FROM documents")).all()
    for source_id, text in rows:
        masked, hits = privacy.mask(text or "")
        if hits:
            conn.execute(
                sa.text("UPDATE documents SET text = :t WHERE source_id = :s"),
                {"t": masked, "s": source_id},
            )


def downgrade() -> None:
    # замасковане не відновити, і повертати його нема навіщо
    pass
