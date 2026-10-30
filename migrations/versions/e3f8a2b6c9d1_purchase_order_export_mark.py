"""purchase order export mark

Revision ID: e3f8a2b6c9d1
Revises: c7a1d4e9b2f3
Create Date: 2026-10-29

Sales orders already carry sage_exported_at. Purchase orders go to Sage in a
separate file, so they need their own mark, or every PO file repeats every PO.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'e3f8a2b6c9d1'
down_revision: Union[str, Sequence[str], None] = 'c7a1d4e9b2f3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('orders') as batch_op:
        batch_op.add_column(sa.Column('sage_po_exported_at', sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('orders') as batch_op:
        batch_op.drop_column('sage_po_exported_at')
