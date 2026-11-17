"""one switch for every automation, and a link from a vendor line to its purchase order

Revision ID: f9b1d3e5a7c9
Revises: e8a0c2d4f6b8
Create Date: 2026-11-09

Safe to run twice: the table and column are only added when missing.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'f9b1d3e5a7c9'
down_revision: Union[str, Sequence[str], None] = 'e8a0c2d4f6b8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    have = sa.inspect(op.get_bind())
    if not have.has_table('automation_modes'):
        op.create_table(
            'automation_modes',
            sa.Column('key', sa.String(length=80), primary_key=True),
            sa.Column('mode', sa.String(length=8), nullable=False),
            sa.Column('changed_by', sa.String(length=256), nullable=True),
            sa.Column('changed_at', sa.DateTime(timezone=True), nullable=True),
        )
    if 'purchase_order_id' not in {c['name'] for c in have.get_columns('vendor_jobs')}:
        op.add_column('vendor_jobs', sa.Column('purchase_order_id', sa.Integer(), nullable=True))
        op.create_index('ix_vendor_jobs_purchase_order_id', 'vendor_jobs', ['purchase_order_id'])


def downgrade() -> None:
    op.drop_index('ix_vendor_jobs_purchase_order_id', table_name='vendor_jobs')
    op.drop_column('vendor_jobs', 'purchase_order_id')
    op.drop_table('automation_modes')
