"""item updates (comments with @mentions)

Revision ID: e2a8c4d6f1b3
Revises: d1f7b3a9e5c2
Create Date: 2026-11-06

One thread of comments per order, RFQ, vendor job, shipment, milestone or board item, with one level of
replies and the people each comment names. Safe to run twice: the table is only created when missing.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'e2a8c4d6f1b3'
down_revision: Union[str, Sequence[str], None] = 'd1f7b3a9e5c2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    if sa.inspect(op.get_bind()).has_table('item_updates'):
        return
    op.create_table(
        'item_updates',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('item_type', sa.String(length=24), nullable=False),
        sa.Column('item_ref', sa.String(length=64), nullable=False),
        sa.Column('parent_id', sa.Integer(), nullable=True),
        sa.Column('author_email', sa.String(length=256), nullable=False),
        sa.Column('author_name', sa.String(length=256), nullable=False),
        sa.Column('body', sa.Text(), nullable=False),
        sa.Column('mentions', sa.JSON(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('edited_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index('ix_item_updates_item', 'item_updates', ['item_type', 'item_ref'])
    op.create_index('ix_item_updates_author_email', 'item_updates', ['author_email'])


def downgrade() -> None:
    op.drop_table('item_updates')
