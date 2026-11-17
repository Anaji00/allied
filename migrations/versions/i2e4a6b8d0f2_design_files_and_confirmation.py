"""a file can be marked as a design file (only those go to vendors); a document's facts can be confirmed by a person

Revision ID: i2e4a6b8d0f2
Revises: h1d3f5a7c9e1
Create Date: 2026-11-14

Safe to run twice: each column is only added when missing.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'i2e4a6b8d0f2'
down_revision: Union[str, Sequence[str], None] = 'h1d3f5a7c9e1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _cols(table: str) -> set:
    return {c['name'] for c in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    if 'design' not in _cols('item_files'):
        op.add_column('item_files', sa.Column('design', sa.Boolean(), nullable=False, server_default=sa.false()))
    have = _cols('job_documents')
    if 'confirmed_by' not in have:
        op.add_column('job_documents', sa.Column('confirmed_by', sa.String(length=256), nullable=True))
    if 'confirmed_at' not in have:
        op.add_column('job_documents', sa.Column('confirmed_at', sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column('job_documents', 'confirmed_at')
    op.drop_column('job_documents', 'confirmed_by')
    op.drop_column('item_files', 'design')
