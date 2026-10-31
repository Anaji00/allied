"""rfq board link, for databases that missed it

Revision ID: c3d9e5a1f7b2
Revises: a9c4e2f7b1d8
Create Date: 2026-10-30

a9c4e2f7b1d8 first shipped adding only vendor_jobs.customer_due; the
rfqs.monday_item_id column was added to it afterwards. A database that had
already run the first version is stamped at a9c4e2f7b1d8 without the column,
and Alembic will never run that revision again, so every RFQ query fails
with "no such column". This adds the column and its index only where they
are missing, so it is a no-op on a database built from scratch.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'c3d9e5a1f7b2'
down_revision: Union[str, Sequence[str], None] = 'a9c4e2f7b1d8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_INDEX = 'ix_rfqs_monday_item_id'


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    columns = {c['name'] for c in inspector.get_columns('rfqs')}
    indexes = {i['name'] for i in inspector.get_indexes('rfqs')}
    if 'monday_item_id' in columns and _INDEX in indexes:
        return
    with op.batch_alter_table('rfqs') as batch:
        if 'monday_item_id' not in columns:
            batch.add_column(sa.Column('monday_item_id', sa.String(32), nullable=True))
        if _INDEX not in indexes:
            batch.create_index(_INDEX, ['monday_item_id'], unique=True)


def downgrade() -> None:
    # Nothing to undo here: on a fresh database the column belongs to
    # a9c4e2f7b1d8, whose downgrade drops it.
    pass
