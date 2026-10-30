"""monday board reader state

Revision ID: b5d2e8f1a3c6
Revises: 9e41b7c2d5a8
Create Date: 2026-10-28 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b5d2e8f1a3c6'
down_revision: Union[str, Sequence[str], None] = '9e41b7c2d5a8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'monday_item_state',
        sa.Column('item_id', sa.String(length=32), nullable=False),
        sa.Column('board_id', sa.String(length=32), nullable=False),
        sa.Column('label', sa.String(length=128), nullable=True),
        sa.Column('item_name', sa.String(length=512), nullable=True),
        sa.Column('seen_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('changed_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('item_id'),
    )
    with op.batch_alter_table('monday_item_state', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_monday_item_state_board_id'), ['board_id'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('monday_item_state', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_monday_item_state_board_id'))
    op.drop_table('monday_item_state')
