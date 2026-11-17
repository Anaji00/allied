"""the send ledger: every mail the app sent or only drafted, and what happened

Revision ID: j3f5b7c9d1e3
Revises: i2e4a6b8d0f2
Create Date: 2026-11-15

Safe to run twice: the table and its index are only created when missing. New table only; nothing existing is changed.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'j3f5b7c9d1e3'
down_revision: Union[str, Sequence[str], None] = 'i2e4a6b8d0f2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    insp = sa.inspect(op.get_bind())
    if 'outbound_mail_log' not in insp.get_table_names():
        op.create_table(
            'outbound_mail_log',
            sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column('message_id', sa.String(length=32), nullable=False, unique=True),
            sa.Column('category', sa.String(length=32), nullable=False),
            sa.Column('reference', sa.String(length=128), nullable=True),
            sa.Column('to_addr', sa.String(length=512), nullable=True),
            sa.Column('intended_to', sa.String(length=512), nullable=True),
            sa.Column('status', sa.String(length=16), nullable=False),
            sa.Column('redirected', sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column('subject', sa.String(length=256), nullable=True),
            sa.Column('detail', sa.String(length=400), nullable=True),
            sa.Column('created_at', sa.DateTime(timezone=True), nullable=True),
        )
    if 'ix_mail_log_ref' not in {i['name'] for i in sa.inspect(op.get_bind()).get_indexes('outbound_mail_log')}:
        op.create_index('ix_mail_log_ref', 'outbound_mail_log', ['category', 'reference'])


def downgrade() -> None:
    op.drop_index('ix_mail_log_ref', table_name='outbound_mail_log')
    op.drop_table('outbound_mail_log')
