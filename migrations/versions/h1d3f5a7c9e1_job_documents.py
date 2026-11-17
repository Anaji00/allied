"""documents found inside scanned job packets

Revision ID: h1d3f5a7c9e1
Revises: g0c2e4a6b8d0
Create Date: 2026-11-14

Safe to run twice: the table is only created when it is missing.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'h1d3f5a7c9e1'
down_revision: Union[str, Sequence[str], None] = 'g0c2e4a6b8d0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    if sa.inspect(op.get_bind()).has_table('job_documents'):
        return
    op.create_table(
        'job_documents',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('job_id', sa.Integer(), nullable=True),
        sa.Column('kind', sa.String(length=24), nullable=False),
        sa.Column('label', sa.String(length=80), nullable=False, server_default=''),
        sa.Column('page_from', sa.Integer(), nullable=False),
        sa.Column('page_to', sa.Integer(), nullable=False),
        sa.Column('fields', sa.JSON(), nullable=False),
        sa.Column('text', sa.Text(), nullable=True),
        sa.Column('source_name', sa.String(length=255), nullable=False, server_default=''),
        sa.Column('source_sha', sa.String(length=64), nullable=False),
        sa.Column('created_by', sa.String(length=256), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint('source_sha', 'page_from', name='ux_job_documents_page'),
    )
    op.create_index('ix_job_documents_job_id', 'job_documents', ['job_id'])
    op.create_index('ix_job_documents_source_sha', 'job_documents', ['source_sha'])


def downgrade() -> None:
    op.drop_table('job_documents')
