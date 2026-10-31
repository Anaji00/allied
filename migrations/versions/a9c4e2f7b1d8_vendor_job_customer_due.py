"""vendor job customer due date, rfq board link

Revision ID: a9c4e2f7b1d8
Revises: f5b1c3d7e2a4
Create Date: 2026-10-30

The date the customer was promised, read from the vendor board (Allied's
2026 Orders board works it out as Vendor PO Issued + Cust Lead weeks). A
vendor job with no linked project is checked against it. RFQs read from
the monday Open RFQs board keep the item they came from.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'a9c4e2f7b1d8'
down_revision: Union[str, Sequence[str], None] = 'f5b1c3d7e2a4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('vendor_jobs') as batch:
        batch.add_column(sa.Column('customer_due', sa.String(10), nullable=True))
    with op.batch_alter_table('rfqs') as batch:
        batch.add_column(sa.Column('monday_item_id', sa.String(32), nullable=True))
        batch.create_index('ix_rfqs_monday_item_id', ['monday_item_id'], unique=True)


def downgrade() -> None:
    with op.batch_alter_table('rfqs') as batch:
        batch.drop_index('ix_rfqs_monday_item_id')
        batch.drop_column('monday_item_id')
    with op.batch_alter_table('vendor_jobs') as batch:
        batch.drop_column('customer_due')
