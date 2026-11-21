"""a job can be linked to one item on a monday board: two nullable columns on jobs and one unique index

Revision ID: o8e0a2c4d6f8
Revises: n7d9f1a3b5c7
Create Date: 2026-11-17

Safe to run twice: each column and the index are only added when missing. Only adds two empty (null) columns and an index; no existing row is changed, and nothing is filled in from
a guess: a job is linked only when a person links it (or it is made from, or pushed to, a monday item).
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'o8e0a2c4d6f8'
down_revision: Union[str, Sequence[str], None] = 'n7d9f1a3b5c7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_INDEX = 'ux_jobs_monday_item'


def _columns() -> set:
    return {c['name'] for c in sa.inspect(op.get_bind()).get_columns('jobs')}


def _indexes() -> set:
    return {i['name'] for i in sa.inspect(op.get_bind()).get_indexes('jobs')}


def upgrade() -> None:
    have = _columns()
    if 'monday_board_id' not in have:
        op.add_column('jobs', sa.Column('monday_board_id', sa.String(length=32), nullable=True))
    if 'monday_item_id' not in have:
        op.add_column('jobs', sa.Column('monday_item_id', sa.String(length=32), nullable=True))
    if _INDEX not in _indexes():
        # one monday item belongs to at most one job; any number of jobs may have no link (null values never clash)
        op.create_index(_INDEX, 'jobs', ['monday_board_id', 'monday_item_id'], unique=True)


def downgrade() -> None:
    if _INDEX in _indexes():
        op.drop_index(_INDEX, table_name='jobs')
    have = _columns()
    with op.batch_alter_table('jobs') as batch:
        for name in ('monday_item_id', 'monday_board_id'):
            if name in have:
                batch.drop_column(name)
