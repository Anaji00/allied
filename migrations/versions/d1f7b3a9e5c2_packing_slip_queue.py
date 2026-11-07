"""packing slip queue

Revision ID: d1f7b3a9e5c2
Revises: c8e4a2d6f1b9
Create Date: 2026-11-06

One row per sales order's packing slip: queued when the order is created, made (and retried
with a wait between tries if it fails), and marked printed by a person. The order itself is
never held up by it. Safe to run twice: the table is only created when it is missing.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'd1f7b3a9e5c2'
down_revision: Union[str, Sequence[str], None] = 'c8e4a2d6f1b9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    if sa.inspect(op.get_bind()).has_table('packing_slips'):
        return
    op.create_table(
        'packing_slips',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('sales_order_number', sa.String(length=64), nullable=False),
        sa.Column('customer_po_number', sa.String(length=64), nullable=True),
        sa.Column('status', sa.String(length=16), nullable=False),
        sa.Column('attempts', sa.Integer(), nullable=False),
        sa.Column('last_error', sa.Text(), nullable=True),
        sa.Column('path', sa.String(length=512), nullable=True),
        sa.Column('payload', sa.JSON(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('next_try_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('ready_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('printed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('printed_by', sa.String(length=256), nullable=True),
    )
    op.create_index('ix_packing_slips_sales_order_number', 'packing_slips', ['sales_order_number'], unique=True)
    op.create_index('ix_packing_slips_status', 'packing_slips', ['status'])


def downgrade() -> None:
    op.drop_table('packing_slips')
