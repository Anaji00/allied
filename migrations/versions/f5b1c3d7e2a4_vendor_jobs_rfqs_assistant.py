"""vendor jobs, rfqs, assistant actions

Revision ID: f5b1c3d7e2a4
Revises: e3f8a2b6c9d1
Create Date: 2026-10-29

Vendor POs with requested, promised and shipped dates (so lateness against a
project can be seen before it happens), customer RFQs with their quote
deadlines, and the changes the assistant proposes for a person to confirm.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'f5b1c3d7e2a4'
down_revision: Union[str, Sequence[str], None] = 'e3f8a2b6c9d1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'vendor_jobs',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('monday_item_id', sa.String(32), nullable=True, unique=True),
        sa.Column('board_id', sa.String(32), nullable=True),
        sa.Column('vendor', sa.String(256), nullable=False),
        sa.Column('description', sa.String(512), nullable=False, server_default=''),
        sa.Column('project_item_id', sa.String(32), nullable=True),
        sa.Column('po_number', sa.String(64), nullable=True),
        sa.Column('owner_email', sa.String(256), nullable=True),
        sa.Column('status', sa.String(128), nullable=True),
        sa.Column('mode', sa.String(8), nullable=False, server_default='SEA'),
        sa.Column('ordered_on', sa.String(10), nullable=True),
        sa.Column('requested_on', sa.String(10), nullable=True),
        sa.Column('acknowledged_on', sa.String(10), nullable=True),
        sa.Column('quoted_lead_days', sa.Integer(), nullable=True),
        sa.Column('promised_original', sa.String(10), nullable=True),
        sa.Column('promised_on', sa.String(10), nullable=True),
        sa.Column('shipped_on', sa.String(10), nullable=True),
        sa.Column('need_by', sa.String(10), nullable=True),
        sa.Column('slips', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('history', sa.JSON(), nullable=False),
        sa.Column('source', sa.String(16), nullable=False, server_default='manual'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index('ix_vendor_jobs_vendor', 'vendor_jobs', ['vendor'])
    op.create_index('ix_vendor_jobs_project_item_id', 'vendor_jobs', ['project_item_id'])

    op.create_table(
        'rfqs',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('number', sa.String(24), nullable=False, unique=True),
        sa.Column('email_id', sa.String(256), nullable=True, unique=True),
        sa.Column('customer', sa.String(256), nullable=False, server_default=''),
        sa.Column('contact_email', sa.String(256), nullable=True),
        sa.Column('subject', sa.String(512), nullable=False, server_default=''),
        sa.Column('summary', sa.Text(), nullable=True),
        sa.Column('items', sa.JSON(), nullable=False),
        sa.Column('complexity', sa.String(16), nullable=False, server_default='STANDARD'),
        sa.Column('status', sa.String(20), nullable=False, server_default='NEW'),
        sa.Column('owner_email', sa.String(256), nullable=True),
        sa.Column('vendor', sa.String(256), nullable=True),
        sa.Column('received_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('ack_due_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('due_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('acknowledged_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('waiting_since', sa.DateTime(timezone=True), nullable=True),
        sa.Column('paused_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('paused_hours', sa.Numeric(9, 2), nullable=False, server_default='0'),
        sa.Column('quoted_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('closed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('quote_number', sa.String(64), nullable=True),
        sa.Column('quote_amount', sa.Numeric(14, 2), nullable=True),
        sa.Column('close_reason', sa.String(256), nullable=True),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('history', sa.JSON(), nullable=False),
        sa.Column('source', sa.String(16), nullable=False, server_default='manual'),
        sa.Column('created_by', sa.String(256), nullable=True),
    )
    op.create_index('ix_rfqs_status', 'rfqs', ['status'])
    op.create_index('ix_rfqs_owner_email', 'rfqs', ['owner_email'])

    op.create_table(
        'assistant_actions',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('user_email', sa.String(256), nullable=False),
        sa.Column('user_role', sa.String(32), nullable=False),
        sa.Column('tool', sa.String(128), nullable=False),
        sa.Column('args', sa.JSON(), nullable=False),
        sa.Column('summary', sa.Text(), nullable=False),
        sa.Column('status', sa.String(16), nullable=False, server_default='PENDING'),
        sa.Column('result', sa.JSON(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('decided_at', sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index('ix_assistant_actions_user_email', 'assistant_actions', ['user_email'])


def downgrade() -> None:
    op.drop_index('ix_assistant_actions_user_email', table_name='assistant_actions')
    op.drop_table('assistant_actions')
    op.drop_index('ix_rfqs_owner_email', table_name='rfqs')
    op.drop_index('ix_rfqs_status', table_name='rfqs')
    op.drop_table('rfqs')
    op.drop_index('ix_vendor_jobs_project_item_id', table_name='vendor_jobs')
    op.drop_index('ix_vendor_jobs_vendor', table_name='vendor_jobs')
    op.drop_table('vendor_jobs')
