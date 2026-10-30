"""monday event inbox and order/board link

Revision ID: 7c3e9a1f4b20
Revises: d106ed8066e3
Create Date: 2026-10-27 09:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '7c3e9a1f4b20'
down_revision: Union[str, Sequence[str], None] = 'd106ed8066e3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'monday_events',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('dedup_key', sa.String(length=128), nullable=False),
        sa.Column('event_type', sa.String(length=64), nullable=True),
        sa.Column('board_id', sa.String(length=32), nullable=True),
        sa.Column('item_id', sa.String(length=32), nullable=True),
        sa.Column('column_id', sa.String(length=64), nullable=True),
        sa.Column('user_id', sa.String(length=32), nullable=True),
        sa.Column('payload', sa.JSON(), nullable=False),
        sa.Column('status', sa.String(length=16), nullable=False),
        sa.Column('action', sa.String(length=32), nullable=True),
        sa.Column('attempts', sa.Integer(), nullable=False),
        sa.Column('last_error', sa.Text(), nullable=True),
        sa.Column('result', sa.JSON(), nullable=True),
        sa.Column('received_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('next_attempt_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('processed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('correlation_id', sa.String(length=64), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('monday_events', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_monday_events_dedup_key'), ['dedup_key'], unique=True)
        batch_op.create_index(batch_op.f('ix_monday_events_board_id'), ['board_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_monday_events_item_id'), ['item_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_monday_events_status'), ['status'], unique=False)
        batch_op.create_index(batch_op.f('ix_monday_events_next_attempt_at'), ['next_attempt_at'], unique=False)

    with op.batch_alter_table('orders', schema=None) as batch_op:
        batch_op.add_column(sa.Column('monday_board_id', sa.String(length=32), nullable=True))
        batch_op.add_column(sa.Column('monday_item_id', sa.String(length=32), nullable=True))
        batch_op.add_column(sa.Column('sage_exported_at', sa.DateTime(timezone=True), nullable=True))
        batch_op.create_index(batch_op.f('ix_orders_monday_item_id'), ['monday_item_id'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('orders', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_orders_monday_item_id'))
        batch_op.drop_column('sage_exported_at')
        batch_op.drop_column('monday_item_id')
        batch_op.drop_column('monday_board_id')

    with op.batch_alter_table('monday_events', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_monday_events_next_attempt_at'))
        batch_op.drop_index(batch_op.f('ix_monday_events_status'))
        batch_op.drop_index(batch_op.f('ix_monday_events_item_id'))
        batch_op.drop_index(batch_op.f('ix_monday_events_board_id'))
        batch_op.drop_index(batch_op.f('ix_monday_events_dedup_key'))
    op.drop_table('monday_events')
