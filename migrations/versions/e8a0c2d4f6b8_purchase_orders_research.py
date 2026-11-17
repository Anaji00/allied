"""purchase orders and research notes on a job

Revision ID: e8a0c2d4f6b8
Revises: d7f9b1c3e5a7
Create Date: 2026-11-08

Safe to run twice: each table is only created when it is missing.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'e8a0c2d4f6b8'
down_revision: Union[str, Sequence[str], None] = 'd7f9b1c3e5a7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    have = sa.inspect(op.get_bind())
    if not have.has_table('purchase_orders'):
        op.create_table(
            'purchase_orders',
            sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column('job_id', sa.Integer(), nullable=False),
            sa.Column('po_number', sa.String(length=64), nullable=True),
            sa.Column('vendor', sa.String(length=256), nullable=False),
            sa.Column('vendor_email', sa.String(length=256), nullable=True),
            sa.Column('lines', sa.JSON(), nullable=False),
            sa.Column('terms', sa.String(length=64), nullable=True),
            sa.Column('ship_via', sa.String(length=64), nullable=True),
            sa.Column('good_thru', sa.String(length=16), nullable=True),
            sa.Column('status', sa.String(length=16), nullable=False, server_default='DRAFT'),
            sa.Column('approved_by', sa.String(length=256), nullable=True),
            sa.Column('approved_at', sa.DateTime(timezone=True), nullable=True),
            sa.Column('sent_at', sa.DateTime(timezone=True), nullable=True),
            sa.Column('action_id', sa.String(length=36), nullable=True),
            sa.Column('created_by', sa.String(length=256), nullable=True),
            sa.Column('created_at', sa.DateTime(timezone=True), nullable=True),
        )
        op.create_index('ix_purchase_orders_job_id', 'purchase_orders', ['job_id'])
        op.create_index('ix_purchase_orders_po_number', 'purchase_orders', ['po_number'])
    if not have.has_table('job_research'):
        op.create_table(
            'job_research',
            sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column('job_id', sa.Integer(), nullable=False),
            sa.Column('title', sa.String(length=512), nullable=False, server_default=''),
            sa.Column('url', sa.String(length=1024), nullable=True),
            sa.Column('note', sa.Text(), nullable=True),
            sa.Column('source', sa.String(length=16), nullable=False, server_default='manual'),
            sa.Column('created_by', sa.String(length=256), nullable=True),
            sa.Column('created_at', sa.DateTime(timezone=True), nullable=True),
        )
        op.create_index('ix_job_research_job_id', 'job_research', ['job_id'])


def downgrade() -> None:
    op.drop_table('job_research')
    op.drop_table('purchase_orders')
