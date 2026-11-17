"""a purchase order can be cancelled by a manager, with a reason and a date

Revision ID: k4a6c8d0e2f4
Revises: j3f5b7c9d1e3
Create Date: 2026-11-15

Safe to run twice: each column is only added when missing. Two new empty columns; nothing existing is changed.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'k4a6c8d0e2f4'
down_revision: Union[str, Sequence[str], None] = 'j3f5b7c9d1e3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _cols(table: str) -> set:
    return {c['name'] for c in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    have = _cols('purchase_orders')
    if 'cancelled_at' not in have:
        op.add_column('purchase_orders', sa.Column('cancelled_at', sa.DateTime(timezone=True), nullable=True))
    if 'cancel_reason' not in have:
        op.add_column('purchase_orders', sa.Column('cancel_reason', sa.String(length=256), nullable=True))


def downgrade() -> None:
    op.drop_column('purchase_orders', 'cancel_reason')
    op.drop_column('purchase_orders', 'cancelled_at')
