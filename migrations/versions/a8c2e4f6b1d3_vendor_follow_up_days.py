"""vendor follow-up days

Revision ID: a8c2e4f6b1d3
Revises: d4e8a1b6c2f9
Create Date: 2026-10-31

How many days after its PO a vendor order may go unshipped before the vendor is
followed up, set by hand on one order. Empty means the order's quoted lead time,
or VENDOR_FOLLOWUP_DAYS when it has none. Safe to run twice: the column is only
added when it is missing.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'a8c2e4f6b1d3'
down_revision: Union[str, Sequence[str], None] = 'd4e8a1b6c2f9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_column() -> bool:
    return any(c['name'] == 'follow_up_days' for c in sa.inspect(op.get_bind()).get_columns('vendor_jobs'))


def upgrade() -> None:
    if not _has_column():
        with op.batch_alter_table('vendor_jobs') as batch:
            batch.add_column(sa.Column('follow_up_days', sa.Integer(), nullable=True))


def downgrade() -> None:
    if _has_column():
        with op.batch_alter_table('vendor_jobs') as batch:
            batch.drop_column('follow_up_days')
