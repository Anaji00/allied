"""an alert can be snoozed and handed to one person; a PO records which revision the vendor acknowledged

Revision ID: m6c8e0f2a4b6
Revises: l5b7d9e1f3a5
Create Date: 2026-11-16

Safe to run twice: each column is only added when missing. Three new empty columns; nothing existing is changed.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'm6c8e0f2a4b6'
down_revision: Union[str, Sequence[str], None] = 'l5b7d9e1f3a5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _cols(table: str) -> set:
    return {c['name'] for c in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    have = _cols('notifications')
    if 'snoozed_until' not in have:
        op.add_column('notifications', sa.Column('snoozed_until', sa.DateTime(timezone=True), nullable=True))
    if 'assignee' not in have:
        op.add_column('notifications', sa.Column('assignee', sa.String(length=256), nullable=True))
    if 'revision_marks' not in _cols('purchase_orders'):
        op.add_column('purchase_orders', sa.Column('revision_marks', sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column('purchase_orders', 'revision_marks')
    op.drop_column('notifications', 'assignee')
    op.drop_column('notifications', 'snoozed_until')
