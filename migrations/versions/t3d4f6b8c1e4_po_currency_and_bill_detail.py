"""a nullable currency on purchase orders and the bill's own lines on reconciliations

Revision ID: t3d4f6b8c1e4
Revises: s2c3e5a7b9d2
Create Date: 2026-11-22

Two new nullable columns, each added only when missing (safe to run twice): purchase_orders.currency (empty for every existing PO, nothing is guessed or filled in) and
reconciliations.bill_detail (empty for every existing bill; only bills read in from now on carry their lines). No data is rewritten. Downgrade drops only these two columns.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 't3d4f6b8c1e4'
down_revision: Union[str, Sequence[str], None] = 's2c3e5a7b9d2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _cols(table: str) -> set:
    insp = sa.inspect(op.get_bind())
    return {c['name'] for c in insp.get_columns(table)} if table in insp.get_table_names() else set()


def upgrade() -> None:
    if 'purchase_orders' in sa.inspect(op.get_bind()).get_table_names() and 'currency' not in _cols('purchase_orders'):
        op.add_column('purchase_orders', sa.Column('currency', sa.String(length=3), nullable=True))
    if 'reconciliations' in sa.inspect(op.get_bind()).get_table_names() and 'bill_detail' not in _cols('reconciliations'):
        op.add_column('reconciliations', sa.Column('bill_detail', sa.JSON(), nullable=True))


def downgrade() -> None:
    if 'currency' in _cols('purchase_orders'):
        op.drop_column('purchase_orders', 'currency')                 # a plain DROP COLUMN: a table rebuild would lose the table's partial unique indexes
    if 'bill_detail' in _cols('reconciliations'):
        op.drop_column('reconciliations', 'bill_detail')
