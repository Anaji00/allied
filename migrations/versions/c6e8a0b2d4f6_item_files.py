"""files attached to items and comments

Revision ID: c6e8a0b2d4f6
Revises: b5d7f9a1c3e2
Create Date: 2026-11-08

Safe to run twice: the table is only created when it is missing.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'c6e8a0b2d4f6'
down_revision: Union[str, Sequence[str], None] = 'b5d7f9a1c3e2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    if sa.inspect(op.get_bind()).has_table('item_files'):
        return
    op.create_table(
        'item_files',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('item_type', sa.String(length=24), nullable=False),
        sa.Column('item_ref', sa.String(length=64), nullable=False),
        sa.Column('update_id', sa.Integer(), nullable=True),
        sa.Column('filename', sa.String(length=255), nullable=False),
        sa.Column('stored_path', sa.String(length=1024), nullable=False),
        sa.Column('size', sa.Integer(), nullable=False),
        sa.Column('sha256', sa.String(length=64), nullable=False),
        sa.Column('content_type', sa.String(length=120), nullable=True),
        sa.Column('uploaded_by', sa.String(length=256), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index('ix_item_files_item', 'item_files', ['item_type', 'item_ref'])


def downgrade() -> None:
    op.drop_table('item_files')
