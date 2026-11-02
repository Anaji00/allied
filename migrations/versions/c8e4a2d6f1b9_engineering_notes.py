"""engineering notes

Revision ID: c8e4a2d6f1b9
Revises: b6d2f8a4c1e7
Create Date: 2026-11-01

A note from management to the engineers on one engineering job (a monday item): the
English text, optional Mandarin and Vietnamese translations, who it went to, whether it
was urgent (sent as a text too), who has read it, and an optional voice recording with
what it says. Safe to run twice: the table is
only created when it is missing.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'c8e4a2d6f1b9'
down_revision: Union[str, Sequence[str], None] = 'b6d2f8a4c1e7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    if sa.inspect(op.get_bind()).has_table('engineering_notes'):
        return
    op.create_table(
        'engineering_notes',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('board_id', sa.String(length=32), nullable=True),
        sa.Column('item_id', sa.String(length=32), nullable=False),
        sa.Column('item_name', sa.String(length=512), nullable=True),
        sa.Column('author_email', sa.String(length=256), nullable=False),
        sa.Column('text', sa.Text(), nullable=False),
        sa.Column('translations', sa.JSON(), nullable=False),
        sa.Column('recipients', sa.JSON(), nullable=False),
        sa.Column('urgent', sa.Boolean(), nullable=False),
        sa.Column('read_by', sa.JSON(), nullable=False),
        sa.Column('audio', sa.JSON(), nullable=True),
        sa.Column('transcript', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index('ix_engineering_notes_item_id', 'engineering_notes', ['item_id'])
    op.create_index('ix_engineering_notes_board_id', 'engineering_notes', ['board_id'])


def downgrade() -> None:
    op.drop_table('engineering_notes')
