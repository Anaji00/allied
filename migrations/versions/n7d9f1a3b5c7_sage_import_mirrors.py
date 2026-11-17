"""read-only mirrors of Sage 50 export files: invoices, payments, open orders, vendor bills, and the list of files brought in

Revision ID: n7d9f1a3b5c7
Revises: m6c8e0f2a4b6
Create Date: 2026-11-17

Safe to run twice: each table is only created when missing. Only new tables; nothing existing is changed or removed.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'n7d9f1a3b5c7'
down_revision: Union[str, Sequence[str], None] = 'm6c8e0f2a4b6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_NOW = sa.DateTime(timezone=True)


def _has(name: str) -> bool:
    return sa.inspect(op.get_bind()).has_table(name)


def upgrade() -> None:
    if not _has('sage_imports'):
        op.create_table(
            'sage_imports',
            sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column('kind', sa.String(length=24), nullable=False),
            sa.Column('file_name', sa.String(length=255), nullable=False, server_default=''),
            sa.Column('file_hash', sa.String(length=64), nullable=False),
            sa.Column('rows_total', sa.Integer(), nullable=False, server_default='0'),
            sa.Column('created', sa.Integer(), nullable=False, server_default='0'),
            sa.Column('updated', sa.Integer(), nullable=False, server_default='0'),
            sa.Column('unchanged', sa.Integer(), nullable=False, server_default='0'),
            sa.Column('problems', sa.Integer(), nullable=False, server_default='0'),
            sa.Column('imported_by', sa.String(length=256), nullable=True),
            sa.Column('imported_at', _NOW, nullable=True),
            sa.UniqueConstraint('kind', 'file_hash', name='ux_sage_imports_file'),
        )
    if not _has('sage_invoices'):
        op.create_table(
            'sage_invoices',
            sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column('invoice_number', sa.String(length=64), nullable=False, unique=True),
            sa.Column('customer_id', sa.String(length=64), nullable=True),
            sa.Column('customer_name', sa.String(length=256), nullable=True),
            sa.Column('invoice_date', sa.String(length=10), nullable=False),
            sa.Column('due_date', sa.String(length=10), nullable=True),
            sa.Column('customer_po', sa.String(length=128), nullable=True),
            sa.Column('sales_order', sa.String(length=64), nullable=True),
            sa.Column('total', sa.Numeric(14, 2), nullable=False),
            sa.Column('amount_due', sa.Numeric(14, 2), nullable=True),
            sa.Column('import_id', sa.Integer(), nullable=True),
            sa.Column('updated_at', _NOW, nullable=True),
        )
        op.create_index('ix_sage_invoices_customer_id', 'sage_invoices', ['customer_id'])
        op.create_index('ix_sage_invoices_customer_po', 'sage_invoices', ['customer_po'])
        op.create_index('ix_sage_invoices_sales_order', 'sage_invoices', ['sales_order'])
    if not _has('sage_payments'):
        op.create_table(
            'sage_payments',
            sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column('pay_key', sa.String(length=64), nullable=False, unique=True),
            sa.Column('receipt_number', sa.String(length=64), nullable=True),
            sa.Column('payment_date', sa.String(length=10), nullable=False),
            sa.Column('customer_id', sa.String(length=64), nullable=True),
            sa.Column('invoice_number', sa.String(length=64), nullable=True),
            sa.Column('amount', sa.Numeric(14, 2), nullable=False),
            sa.Column('import_id', sa.Integer(), nullable=True),
            sa.Column('updated_at', _NOW, nullable=True),
        )
        op.create_index('ix_sage_payments_invoice_number', 'sage_payments', ['invoice_number'])
    if not _has('sage_open_orders'):
        op.create_table(
            'sage_open_orders',
            sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column('kind', sa.String(length=2), nullable=False),
            sa.Column('number', sa.String(length=64), nullable=False),
            sa.Column('party_id', sa.String(length=64), nullable=True),
            sa.Column('party_name', sa.String(length=256), nullable=True),
            sa.Column('order_date', sa.String(length=10), nullable=False),
            sa.Column('customer_po', sa.String(length=128), nullable=True),
            sa.Column('amount', sa.Numeric(14, 2), nullable=True),
            sa.Column('open_amount', sa.Numeric(14, 2), nullable=True),
            sa.Column('import_id', sa.Integer(), nullable=True),
            sa.Column('updated_at', _NOW, nullable=True),
            sa.UniqueConstraint('kind', 'number', name='ux_sage_open_orders'),
        )
        op.create_index('ix_sage_open_orders_customer_po', 'sage_open_orders', ['customer_po'])
    if not _has('sage_bills'):
        op.create_table(
            'sage_bills',
            sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column('bill_key', sa.String(length=160), nullable=False, unique=True),
            sa.Column('bill_number', sa.String(length=64), nullable=False),
            sa.Column('vendor_id', sa.String(length=64), nullable=True),
            sa.Column('vendor_name', sa.String(length=256), nullable=True),
            sa.Column('bill_date', sa.String(length=10), nullable=False),
            sa.Column('due_date', sa.String(length=10), nullable=True),
            sa.Column('amount', sa.Numeric(14, 2), nullable=False),
            sa.Column('amount_due', sa.Numeric(14, 2), nullable=True),
            sa.Column('po_number', sa.String(length=64), nullable=True),
            sa.Column('import_id', sa.Integer(), nullable=True),
            sa.Column('updated_at', _NOW, nullable=True),
        )
        op.create_index('ix_sage_bills_bill_number', 'sage_bills', ['bill_number'])


def downgrade() -> None:
    for table in ('sage_bills', 'sage_open_orders', 'sage_payments', 'sage_invoices', 'sage_imports'):
        if _has(table):
            op.drop_table(table)
