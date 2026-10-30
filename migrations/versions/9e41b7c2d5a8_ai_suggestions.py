"""ai suggestions log

Revision ID: 9e41b7c2d5a8
Revises: 7c3e9a1f4b20
Create Date: 2026-10-28 09:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '9e41b7c2d5a8'
down_revision: Union[str, Sequence[str], None] = '7c3e9a1f4b20'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'ai_suggestions',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('feature', sa.String(length=32), nullable=False),
        sa.Column('status', sa.String(length=16), nullable=False),
        sa.Column('title', sa.String(length=256), nullable=False),
        sa.Column('reference', sa.String(length=128), nullable=True),
        sa.Column('provider', sa.String(length=16), nullable=False),
        sa.Column('model', sa.String(length=64), nullable=True),
        sa.Column('confidence', sa.Numeric(precision=9, scale=4), nullable=True),
        sa.Column('needs_review', sa.Boolean(), nullable=False),
        sa.Column('input', sa.JSON(), nullable=True),
        sa.Column('output', sa.JSON(), nullable=True),
        sa.Column('duration_ms', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('created_by', sa.String(length=256), nullable=True),
        sa.Column('reviewed_by', sa.String(length=256), nullable=True),
        sa.Column('reviewed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('review_note', sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('ai_suggestions', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_ai_suggestions_feature'), ['feature'], unique=False)
        batch_op.create_index(batch_op.f('ix_ai_suggestions_status'), ['status'], unique=False)
        batch_op.create_index(batch_op.f('ix_ai_suggestions_reference'), ['reference'], unique=False)
        batch_op.create_index(batch_op.f('ix_ai_suggestions_created_at'), ['created_at'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('ai_suggestions', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_ai_suggestions_created_at'))
        batch_op.drop_index(batch_op.f('ix_ai_suggestions_reference'))
        batch_op.drop_index(batch_op.f('ix_ai_suggestions_status'))
        batch_op.drop_index(batch_op.f('ix_ai_suggestions_feature'))
    op.drop_table('ai_suggestions')
