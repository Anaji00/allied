"""new tables only: follows and last-seen, a person's mail-AI settings and AI-use log, and notification preferences

Revision ID: p9f1b3d5e7a9
Revises: o8e0a2c4d6f8
Create Date: 2026-11-18

Safe to run twice: each table and index is only created when missing. Only NEW tables are added: no existing table or column is changed and nothing is backfilled. Downgrade drops only these tables.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'p9f1b3d5e7a9'
down_revision: Union[str, Sequence[str], None] = 'o8e0a2c4d6f8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLES = ('item_follows', 'item_seen', 'mail_ai_settings', 'mail_ai_use', 'notification_prefs')


def _have() -> set:
    return set(sa.inspect(op.get_bind()).get_table_names())


def upgrade() -> None:
    have = _have()
    if 'item_follows' not in have:
        op.create_table('item_follows',
                        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
                        sa.Column('user_email', sa.String(length=320), nullable=False),
                        sa.Column('item_kind', sa.String(length=24), nullable=False),
                        sa.Column('item_ref', sa.String(length=120), nullable=False),
                        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False))
        op.create_index('ux_item_follows', 'item_follows', ['user_email', 'item_kind', 'item_ref'], unique=True)
    if 'item_seen' not in have:
        op.create_table('item_seen',
                        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
                        sa.Column('user_email', sa.String(length=320), nullable=False),
                        sa.Column('item_kind', sa.String(length=24), nullable=False),
                        sa.Column('item_ref', sa.String(length=120), nullable=False),
                        sa.Column('seen_at', sa.DateTime(timezone=True), nullable=False))
        op.create_index('ux_item_seen', 'item_seen', ['user_email', 'item_kind', 'item_ref'], unique=True)
    if 'mail_ai_settings' not in have:
        op.create_table('mail_ai_settings',
                        sa.Column('user_email', sa.String(length=320), primary_key=True),
                        sa.Column('ai_opt_in', sa.Boolean(), nullable=False, server_default=sa.false()),
                        sa.Column('auto_draft', sa.Boolean(), nullable=False, server_default=sa.false()),
                        sa.Column('style_note', sa.String(length=500), nullable=False, server_default=''),
                        sa.Column('exclude_senders', sa.JSON(), nullable=False),
                        sa.Column('exclude_words', sa.JSON(), nullable=False),
                        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True))
    if 'mail_ai_use' not in have:
        op.create_table('mail_ai_use',
                        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
                        sa.Column('user_email', sa.String(length=320), nullable=False, index=True),
                        sa.Column('at', sa.DateTime(timezone=True), nullable=False),
                        sa.Column('mail_id', sa.Integer(), nullable=True),
                        sa.Column('model', sa.String(length=80), nullable=True),
                        sa.Column('outcome', sa.String(length=40), nullable=False))      # no message text and no prompt are ever stored
    if 'notification_prefs' not in have:
        op.create_table('notification_prefs',
                        sa.Column('user_email', sa.String(length=320), primary_key=True),
                        sa.Column('prefs', sa.JSON(), nullable=False),
                        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    have = _have()
    for name in reversed(_TABLES):
        if name in have:
            op.drop_table(name)
