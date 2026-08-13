"""word_states, review_events

Revision ID: a1c7f2e94b30
Revises: 3b4962d2a10c
Create Date: 2026-08-08 14:30:00.000000
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "a1c7f2e94b30"
down_revision: str | None = "3b4962d2a10c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "word_states",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("word_id", sa.Integer(), nullable=False),
        sa.Column("due", sa.DateTime(), nullable=False),
        sa.Column("state", sa.Integer(), nullable=False),
        sa.Column("last_reviewed_at", sa.DateTime(), nullable=True),
        sa.Column("card_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["word_id"],
            ["words.id"],
            name=op.f("fk_word_states_word_id_words"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_word_states")),
    )
    with op.batch_alter_table("word_states", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_word_states_due"), ["due"], unique=False)
        batch_op.create_index(
            batch_op.f("ix_word_states_word_id"), ["word_id"], unique=True
        )

    op.create_table(
        "review_events",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("word_id", sa.Integer(), nullable=False),
        sa.Column("session_id", sa.Integer(), nullable=True),
        sa.Column("rating", sa.Integer(), nullable=False),
        sa.Column("signal", sa.String(length=16), nullable=False),
        sa.Column("reviewed_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["sessions.id"],
            name=op.f("fk_review_events_session_id_sessions"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["word_id"],
            ["words.id"],
            name=op.f("fk_review_events_word_id_words"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_review_events")),
    )
    with op.batch_alter_table("review_events", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_review_events_reviewed_at"), ["reviewed_at"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_review_events_session_id"), ["session_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_review_events_word_id"), ["word_id"], unique=False
        )


def downgrade() -> None:
    with op.batch_alter_table("review_events", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_review_events_word_id"))
        batch_op.drop_index(batch_op.f("ix_review_events_session_id"))
        batch_op.drop_index(batch_op.f("ix_review_events_reviewed_at"))

    op.drop_table("review_events")
    with op.batch_alter_table("word_states", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_word_states_word_id"))
        batch_op.drop_index(batch_op.f("ix_word_states_due"))

    op.drop_table("word_states")
