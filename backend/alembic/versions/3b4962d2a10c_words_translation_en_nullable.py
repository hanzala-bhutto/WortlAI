"""words translation_en nullable

Revision ID: 3b4962d2a10c
Revises: 15e0b1d86ddd
Create Date: 2026-08-04 19:19:25.833118
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "3b4962d2a10c"
down_revision: str | None = "15e0b1d86ddd"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # The Goethe Wortlisten (#13) are German-only, so a Goethe row has no English
    # translation. Loosen the NOT NULL that the initial words table imposed. Glossary
    # rows are unaffected - they still supply translation_en.
    with op.batch_alter_table("words", schema=None) as batch_op:
        batch_op.alter_column("translation_en", existing_type=sa.Text(), nullable=True)


def downgrade() -> None:
    # Reverting to NOT NULL requires every row to carry a translation; any Goethe rows
    # written while nullable would violate it, so a real downgrade must backfill first.
    with op.batch_alter_table("words", schema=None) as batch_op:
        batch_op.alter_column("translation_en", existing_type=sa.Text(), nullable=False)
