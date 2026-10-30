"""monday projects and milestones mirror, design-doc submissions, notifications

Revision ID: c7a1d4e9b2f3
Revises: b5d2e8f1a3c6
Create Date: 2026-10-28 14:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c7a1d4e9b2f3'
down_revision: Union[str, Sequence[str], None] = 'b5d2e8f1a3c6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'staff_contacts',
        sa.Column('email', sa.String(length=256), nullable=False),
        sa.Column('name', sa.String(length=256), nullable=False),
        sa.Column('role_tag', sa.String(length=32), nullable=False),
        sa.Column('phone', sa.String(length=32), nullable=True),
        sa.Column('channels', sa.JSON(), nullable=False),
        sa.Column('daily_summary', sa.Boolean(), nullable=False),
        sa.Column('active', sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint('email'),
    )
    op.create_table(
        'notifications',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('dedup_key', sa.String(length=200), nullable=False),
        sa.Column('kind', sa.String(length=48), nullable=False),
        sa.Column('severity', sa.String(length=16), nullable=False),
        sa.Column('title', sa.String(length=300), nullable=False),
        sa.Column('body', sa.Text(), nullable=False),
        sa.Column('link', sa.String(length=300), nullable=True),
        sa.Column('project_item_id', sa.String(length=32), nullable=True),
        sa.Column('recipients', sa.JSON(), nullable=False),
        sa.Column('deliveries', sa.JSON(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('acknowledged_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('acknowledged_by', sa.String(length=256), nullable=True),
        sa.Column('escalated_at', sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('notifications', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_notifications_dedup_key'), ['dedup_key'], unique=False)
        batch_op.create_index(batch_op.f('ix_notifications_kind'), ['kind'], unique=False)
        batch_op.create_index(batch_op.f('ix_notifications_project_item_id'), ['project_item_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_notifications_created_at'), ['created_at'], unique=False)
    op.create_table(
        'monday_projects',
        sa.Column('item_id', sa.String(length=32), nullable=False),
        sa.Column('board_id', sa.String(length=32), nullable=False),
        sa.Column('name', sa.String(length=512), nullable=False),
        sa.Column('group_title', sa.String(length=256), nullable=True),
        sa.Column('owner_emails', sa.JSON(), nullable=False),
        sa.Column('status', sa.String(length=128), nullable=True),
        sa.Column('due_date', sa.String(length=10), nullable=True),
        sa.Column('done', sa.Boolean(), nullable=False),
        sa.Column('synced_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('item_id'),
    )
    with op.batch_alter_table('monday_projects', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_monday_projects_board_id'), ['board_id'], unique=False)
    op.create_table(
        'monday_milestones',
        sa.Column('item_id', sa.String(length=32), nullable=False),
        sa.Column('project_item_id', sa.String(length=32), nullable=False),
        sa.Column('board_id', sa.String(length=32), nullable=True),
        sa.Column('name', sa.String(length=512), nullable=False),
        sa.Column('owner_emails', sa.JSON(), nullable=False),
        sa.Column('reviewer_emails', sa.JSON(), nullable=False),
        sa.Column('status', sa.String(length=128), nullable=True),
        sa.Column('due_date', sa.String(length=10), nullable=True),
        sa.Column('done', sa.Boolean(), nullable=False),
        sa.Column('needs_submission', sa.Boolean(), nullable=False),
        sa.Column('synced_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('item_id'),
    )
    with op.batch_alter_table('monday_milestones', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_monday_milestones_project_item_id'), ['project_item_id'], unique=False)
    op.create_table(
        'shipments',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('project_item_id', sa.String(length=32), nullable=True),
        sa.Column('forwarder', sa.String(length=128), nullable=True),
        sa.Column('mode', sa.String(length=8), nullable=False),
        sa.Column('reference', sa.String(length=64), nullable=True),
        sa.Column('container_no', sa.String(length=16), nullable=True),
        sa.Column('awb', sa.String(length=16), nullable=True),
        sa.Column('carrier', sa.String(length=64), nullable=True),
        sa.Column('origin', sa.String(length=128), nullable=True),
        sa.Column('destination', sa.String(length=128), nullable=True),
        sa.Column('etd', sa.String(length=10), nullable=True),
        sa.Column('eta_original', sa.String(length=10), nullable=True),
        sa.Column('eta_current', sa.String(length=10), nullable=True),
        sa.Column('free_time_ends', sa.String(length=10), nullable=True),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('owner_email', sa.String(length=256), nullable=True),
        sa.Column('last_update_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('shipments', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_shipments_project_item_id'), ['project_item_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_shipments_reference'), ['reference'], unique=False)
        batch_op.create_index(batch_op.f('ix_shipments_container_no'), ['container_no'], unique=False)
        batch_op.create_index(batch_op.f('ix_shipments_awb'), ['awb'], unique=False)
    op.create_table(
        'shipment_events',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('shipment_id', sa.Integer(), nullable=False),
        sa.Column('at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=True),
        sa.Column('eta', sa.String(length=10), nullable=True),
        sa.Column('description', sa.Text(), nullable=False),
        sa.Column('source', sa.String(length=16), nullable=False),
        sa.Column('raw', sa.JSON(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('shipment_events', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_shipment_events_shipment_id'), ['shipment_id'], unique=False)
    op.create_table(
        'submissions',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('milestone_item_id', sa.String(length=32), nullable=False),
        sa.Column('project_item_id', sa.String(length=32), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.Column('files', sa.JSON(), nullable=False),
        sa.Column('note', sa.Text(), nullable=True),
        sa.Column('submitted_by', sa.String(length=256), nullable=False),
        sa.Column('submitted_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('reviewed_by', sa.String(length=256), nullable=True),
        sa.Column('reviewed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('review_note', sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('submissions', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_submissions_milestone_item_id'), ['milestone_item_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_submissions_project_item_id'), ['project_item_id'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('submissions')
    op.drop_table('shipment_events')
    op.drop_table('shipments')
    op.drop_table('monday_milestones')
    op.drop_table('monday_projects')
    op.drop_table('notifications')
    op.drop_table('staff_contacts')
