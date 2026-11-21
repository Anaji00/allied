"""new tables for organizing data beside the records (families and spellings, tags, vendor contacts, owners, pinned views) and one nullable currency column on RFQs

Revision ID: r1b2d4f6a8c1
Revises: q0a2c4e6f8b0
Create Date: 2026-11-21

Safe to run twice: each table, index and column is only created when missing. Nothing existing is changed, filled in or deleted: the six tables are new and empty, and the one column added to
`rfqs` (`quote_currency`) is nullable and starts empty. Downgrade drops only what this migration added.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'r1b2d4f6a8c1'
down_revision: Union[str, Sequence[str], None] = 'q0a2c4e6f8b0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLES = ('customer_families', 'alias_targets', 'record_tags', 'vendor_contacts', 'record_owners', 'view_pins', 'refresh_state')


def _have() -> set:
    return set(sa.inspect(op.get_bind()).get_table_names())


def upgrade() -> None:
    have = _have()
    if 'customer_families' not in have:
        op.create_table('customer_families',
                        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
                        sa.Column('company', sa.String(length=256), nullable=False, index=True),
                        sa.Column('plant', sa.String(length=256), nullable=True),
                        sa.Column('family', sa.String(length=256), nullable=False, index=True),
                        sa.Column('created_by', sa.String(length=320), nullable=True),
                        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False))
    if 'alias_targets' not in have:
        op.create_table('alias_targets',
                        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
                        sa.Column('alias_norm', sa.String(length=256), nullable=False),
                        sa.Column('alias_text', sa.String(length=256), nullable=False),
                        sa.Column('family_id', sa.Integer(), nullable=False, index=True),
                        sa.Column('created_by', sa.String(length=320), nullable=True),
                        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False))
        op.create_index('ux_alias_targets_norm', 'alias_targets', ['alias_norm'], unique=True)
    if 'record_tags' not in have:
        op.create_table('record_tags',
                        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
                        sa.Column('item_kind', sa.String(length=24), nullable=False),
                        sa.Column('item_ref', sa.String(length=120), nullable=False),
                        sa.Column('tag', sa.String(length=40), nullable=False, index=True),
                        sa.Column('added_by', sa.String(length=320), nullable=True),
                        sa.Column('added_at', sa.DateTime(timezone=True), nullable=False))
        op.create_index('ux_record_tags', 'record_tags', ['item_kind', 'item_ref', 'tag'], unique=True)
    if 'vendor_contacts' not in have:
        op.create_table('vendor_contacts',
                        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
                        sa.Column('name', sa.String(length=256), nullable=False),
                        sa.Column('company', sa.String(length=256), nullable=True, index=True),
                        sa.Column('country', sa.String(length=80), nullable=True),
                        sa.Column('email', sa.String(length=320), nullable=True),
                        sa.Column('phone', sa.String(length=60), nullable=True),
                        sa.Column('notes', sa.String(length=500), nullable=True),
                        sa.Column('created_by', sa.String(length=320), nullable=True),
                        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False))
    if 'record_owners' not in have:
        op.create_table('record_owners',
                        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
                        sa.Column('item_kind', sa.String(length=24), nullable=False),
                        sa.Column('item_ref', sa.String(length=120), nullable=False),
                        sa.Column('owner', sa.String(length=320), nullable=True),
                        sa.Column('ball_with', sa.String(length=80), nullable=True),
                        sa.Column('note', sa.String(length=300), nullable=True),
                        sa.Column('updated_by', sa.String(length=320), nullable=True),
                        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False))
        op.create_index('ux_record_owners', 'record_owners', ['item_kind', 'item_ref'], unique=True)
    if 'view_pins' not in have:
        op.create_table('view_pins',
                        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
                        sa.Column('view_id', sa.String(length=40), nullable=False),
                        sa.Column('user_email', sa.String(length=320), nullable=False),
                        sa.Column('pinned_at', sa.DateTime(timezone=True), nullable=False))
        op.create_index('ux_view_pins', 'view_pins', ['view_id', 'user_email'], unique=True)
    if 'refresh_state' not in have:
        op.create_table('refresh_state',
                        sa.Column('user_email', sa.String(length=320), primary_key=True),
                        sa.Column('latest_jti', sa.String(length=40), nullable=True),
                        sa.Column('latest_exp', sa.Float(), nullable=True),
                        sa.Column('prev_jti', sa.String(length=40), nullable=True),
                        sa.Column('prev_superseded_at', sa.Float(), nullable=True),
                        sa.Column('revoked_before', sa.Float(), nullable=False, server_default='0'),
                        sa.Column('updated_at', sa.Float(), nullable=True))
    cols = {c['name'] for c in sa.inspect(op.get_bind()).get_columns('rfqs')}
    if 'quote_currency' not in cols:
        op.add_column('rfqs', sa.Column('quote_currency', sa.String(length=3), nullable=True))      # empty for every existing RFQ; nothing is guessed or filled in


def downgrade() -> None:
    cols = {c['name'] for c in sa.inspect(op.get_bind()).get_columns('rfqs')}
    if 'quote_currency' in cols:
        with op.batch_alter_table('rfqs') as batch:
            batch.drop_column('quote_currency')
    have = _have()
    for name in reversed(_TABLES):
        if name in have:
            op.drop_table(name)
