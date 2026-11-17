"""jobs: one record linking an RFQ to its design approval, POs, sales order and shipments

Revision ID: d7f9b1c3e5a7
Revises: c6e8a0b2d4f6
Create Date: 2026-11-08

Safe to run twice: the table is only created when it is missing.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'd7f9b1c3e5a7'
down_revision: Union[str, Sequence[str], None] = 'c6e8a0b2d4f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    if sa.inspect(op.get_bind()).has_table('jobs'):
        return
    op.create_table(
        'jobs',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('ref', sa.String(length=24), nullable=False, unique=True),
        sa.Column('title', sa.String(length=256), nullable=False, server_default=''),
        sa.Column('customer', sa.String(length=256), nullable=True),
        sa.Column('rfq_id', sa.Integer(), nullable=True, unique=True),
        sa.Column('sales_order_number', sa.String(length=64), nullable=True),
        sa.Column('design_approved_by', sa.String(length=256), nullable=True),
        sa.Column('design_approved_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('closed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_by', sa.String(length=256), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index('ix_jobs_sales_order_number', 'jobs', ['sales_order_number'])


def downgrade() -> None:
    op.drop_table('jobs')
