"""mail log

Revision ID: b6d2f8a4c1e7
Revises: a8c2e4f6b1d3
Create Date: 2026-11-01

Every email the mailbox reader sees, once: who sent it, what it was (an order, a
request for quote, a vendor reply, a delivery question, internal mail, ...) and what
the system did with it. It is how the reader knows a message was already handled
without marking it read in Gmail. Each carries rule-based tags (rfq, invoice, urgent,
newsletter, ...) and a thread key, so one conversation raises one alert. Safe to run twice: the table is only created when
it is missing.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'b6d2f8a4c1e7'
down_revision: Union[str, Sequence[str], None] = 'a8c2e4f6b1d3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    if sa.inspect(op.get_bind()).has_table('mail_log'):
        return
    op.create_table(
        'mail_log',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('message_id', sa.String(length=512), nullable=False),
        sa.Column('folder', sa.String(length=128), nullable=False),
        sa.Column('sender', sa.String(length=320), nullable=False),
        sa.Column('sender_name', sa.String(length=320), nullable=True),
        sa.Column('recipients', sa.String(length=2000), nullable=True),
        sa.Column('subject', sa.String(length=500), nullable=False),
        sa.Column('received_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('direction', sa.String(length=16), nullable=False),
        sa.Column('category', sa.String(length=32), nullable=False),
        sa.Column('tags', sa.String(length=300), nullable=True),
        sa.Column('thread_key', sa.String(length=200), nullable=True),
        sa.Column('summary', sa.Text(), nullable=True),
        sa.Column('outcome', sa.String(length=64), nullable=False),
        sa.Column('needs_action', sa.Boolean(), nullable=False),
        sa.Column('handled_by', sa.String(length=256), nullable=True),
        sa.Column('handled_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index('ix_mail_log_message_id', 'mail_log', ['message_id'], unique=True)
    op.create_index('ix_mail_log_received_at', 'mail_log', ['received_at'])
    op.create_index('ix_mail_log_category', 'mail_log', ['category'])
    op.create_index('ix_mail_log_needs_action', 'mail_log', ['needs_action'])
    op.create_index('ix_mail_log_thread_key', 'mail_log', ['thread_key'])


def downgrade() -> None:
    op.drop_table('mail_log')
