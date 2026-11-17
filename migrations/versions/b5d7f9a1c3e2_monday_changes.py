"""staged monday changes (write now, push later)

Revision ID: b5d7f9a1c3e2
Revises: a4c6e8b1d3f5
Create Date: 2026-11-07

Changes the app wants to make on monday are saved here (staged) until a person pushes them. Safe to run twice:
the table is only created when it is missing.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'b5d7f9a1c3e2'
down_revision: Union[str, Sequence[str], None] = 'a4c6e8b1d3f5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    if sa.inspect(op.get_bind()).has_table('monday_changes'):
        return
    op.create_table(
        'monday_changes',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('kind', sa.String(length=16), nullable=False),
        sa.Column('board_id', sa.String(length=32), nullable=False),
        sa.Column('item_id', sa.String(length=32), nullable=False),
        sa.Column('payload', sa.JSON(), nullable=False),
        sa.Column('summary', sa.String(length=500), nullable=False),
        sa.Column('source', sa.String(length=40), nullable=False),
        sa.Column('status', sa.String(length=12), nullable=False),
        sa.Column('dedupe_key', sa.String(length=120), nullable=False),
        sa.Column('staged_by', sa.String(length=256), nullable=True),
        sa.Column('attempts', sa.Integer(), nullable=False),
        sa.Column('error', sa.Text(), nullable=True),
        sa.Column('result', sa.JSON(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('pushed_at', sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index('ix_monday_changes_status', 'monday_changes', ['status'])
    op.create_index('ix_monday_changes_dedupe_key', 'monday_changes', ['dedupe_key'])


def downgrade() -> None:
    op.drop_table('monday_changes')
