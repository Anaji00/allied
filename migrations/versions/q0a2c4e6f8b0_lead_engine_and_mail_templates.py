"""new tables only: the lead engine (accounts, contacts, leads, outreach log, do-not-contact, settings, customer aliases) and the reply template store

Revision ID: q0a2c4e6f8b0
Revises: p9f1b3d5e7a9
Create Date: 2026-11-18

Safe to run twice: each table and index is only created when missing. Only NEW tables are added: no existing table or column is changed and nothing is backfilled or seeded here (the five
example target accounts are added by the app, once, on first use). Downgrade drops only these tables.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'q0a2c4e6f8b0'
down_revision: Union[str, Sequence[str], None] = 'p9f1b3d5e7a9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLES = ('lead_accounts', 'lead_contacts', 'leads', 'lead_outreach', 'lead_do_not_contact', 'lead_settings', 'customer_aliases', 'mail_templates')


def _have() -> set:
    return set(sa.inspect(op.get_bind()).get_table_names())


def upgrade() -> None:
    have = _have()
    if 'lead_accounts' not in have:
        op.create_table('lead_accounts',
                        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
                        sa.Column('company', sa.String(length=256), nullable=False, index=True),
                        sa.Column('site', sa.String(length=256), nullable=True),
                        sa.Column('industry', sa.String(length=120), nullable=True),
                        sa.Column('location', sa.String(length=200), nullable=True),
                        sa.Column('domain', sa.String(length=200), nullable=True, index=True),
                        sa.Column('bottlenecks', sa.JSON(), nullable=False),
                        sa.Column('estimated_annual_spend', sa.Float(), nullable=True),
                        sa.Column('source', sa.String(length=24), nullable=False, server_default='manual'),
                        sa.Column('archived_at', sa.DateTime(timezone=True), nullable=True),
                        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False))
    if 'lead_contacts' not in have:
        op.create_table('lead_contacts',
                        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
                        sa.Column('account_id', sa.Integer(), nullable=True, index=True),
                        sa.Column('name', sa.String(length=256), nullable=False),
                        sa.Column('title', sa.String(length=200), nullable=True),
                        sa.Column('email', sa.String(length=320), nullable=True, index=True),
                        sa.Column('phone', sa.String(length=60), nullable=True),
                        sa.Column('source', sa.String(length=24), nullable=False, server_default='manual'),
                        sa.Column('consent_note', sa.String(length=300), nullable=True),
                        sa.Column('bad_address', sa.Boolean(), nullable=False, server_default=sa.false()),
                        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False))
    if 'leads' not in have:
        op.create_table('leads',
                        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
                        sa.Column('account_id', sa.Integer(), nullable=True, index=True),
                        sa.Column('contact_id', sa.Integer(), nullable=True, index=True),
                        sa.Column('status', sa.String(length=24), nullable=False, server_default='Found', index=True),
                        sa.Column('source', sa.String(length=24), nullable=False, server_default='manual'),
                        sa.Column('source_detail', sa.String(length=300), nullable=True),
                        sa.Column('reason', sa.Text(), nullable=True),
                        sa.Column('score', sa.Integer(), nullable=True),
                        sa.Column('score_parts', sa.JSON(), nullable=True),
                        sa.Column('owner', sa.String(length=320), nullable=True),
                        sa.Column('notes', sa.Text(), nullable=True),
                        sa.Column('next_touch_at', sa.DateTime(timezone=True), nullable=True),
                        sa.Column('last_touch_at', sa.DateTime(timezone=True), nullable=True),
                        sa.Column('sequence_name', sa.String(length=60), nullable=True),
                        sa.Column('sequence_step', sa.Integer(), nullable=False, server_default='0'),
                        sa.Column('sequence_paused', sa.Boolean(), nullable=False, server_default=sa.false()),
                        sa.Column('monday_board_id', sa.String(length=32), nullable=True),
                        sa.Column('monday_item_id', sa.String(length=32), nullable=True),
                        sa.Column('archived_at', sa.DateTime(timezone=True), nullable=True),
                        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
                        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True))
    if 'lead_outreach' not in have:
        op.create_table('lead_outreach',
                        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
                        sa.Column('lead_id', sa.Integer(), nullable=False, index=True),
                        sa.Column('kind', sa.String(length=24), nullable=False),         # draft, reply, bounce, out_of_office, unsubscribe
                        sa.Column('template', sa.String(length=40), nullable=True),
                        sa.Column('step', sa.Integer(), nullable=True),
                        sa.Column('action_id', sa.String(length=64), nullable=True),
                        sa.Column('mail_id', sa.Integer(), nullable=True),
                        sa.Column('note', sa.String(length=500), nullable=True),
                        sa.Column('by', sa.String(length=320), nullable=True),
                        sa.Column('at', sa.DateTime(timezone=True), nullable=False))
    if 'lead_do_not_contact' not in have:
        op.create_table('lead_do_not_contact',
                        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
                        sa.Column('kind', sa.String(length=8), nullable=False),          # email or domain
                        sa.Column('value', sa.String(length=320), nullable=False),
                        sa.Column('reason', sa.String(length=300), nullable=True),
                        sa.Column('source', sa.String(length=24), nullable=False, server_default='manual'),
                        sa.Column('added_by', sa.String(length=320), nullable=True),
                        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False))
        op.create_index('ux_lead_dnc', 'lead_do_not_contact', ['kind', 'value'], unique=True)
    if 'lead_settings' not in have:
        op.create_table('lead_settings',
                        sa.Column('id', sa.Integer(), primary_key=True),
                        sa.Column('settings', sa.JSON(), nullable=False),
                        sa.Column('updated_by', sa.String(length=320), nullable=True),
                        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True))
    if 'customer_aliases' not in have:
        op.create_table('customer_aliases',
                        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
                        sa.Column('alias', sa.String(length=256), nullable=False),
                        sa.Column('canonical', sa.String(length=256), nullable=False),
                        sa.Column('created_by', sa.String(length=320), nullable=True),
                        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False))
        op.create_index('ux_customer_alias', 'customer_aliases', ['alias'], unique=True)
    if 'mail_templates' not in have:
        op.create_table('mail_templates',
                        sa.Column('key', sa.String(length=60), primary_key=True),
                        sa.Column('title', sa.String(length=200), nullable=False),
                        sa.Column('subject', sa.String(length=300), nullable=False),
                        sa.Column('body', sa.Text(), nullable=False),
                        sa.Column('situation', sa.String(length=40), nullable=True),
                        sa.Column('updated_by', sa.String(length=320), nullable=True),
                        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    have = _have()
    for name in reversed(_TABLES):
        if name in have:
            op.drop_table(name)
