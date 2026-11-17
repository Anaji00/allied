"""item balls (whose turn it is)

Revision ID: f3b9d5e7a2c4
Revises: e2a8c4d6f1b3
Create Date: 2026-11-06

One row per item saying who owns it, who has the ball and since when, with the chasing level and history.
Safe to run twice: the table is only created when missing.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'f3b9d5e7a2c4'
down_revision: Union[str, Sequence[str], None] = 'e2a8c4d6f1b3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    if sa.inspect(op.get_bind()).has_table('item_balls'):
        return
    op.create_table(
        'item_balls',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('item_type', sa.String(length=24), nullable=False),
        sa.Column('item_ref', sa.String(length=64), nullable=False),
        sa.Column('owner_email', sa.String(length=256), nullable=True),
        sa.Column('ball_with', sa.String(length=16), nullable=False),
        sa.Column('ball_person', sa.String(length=256), nullable=True),
        sa.Column('note', sa.String(length=300), nullable=True),
        sa.Column('due_at', sa.String(length=10), nullable=True),
        sa.Column('ball_since', sa.DateTime(timezone=True), nullable=False),
        sa.Column('escalation_level', sa.Integer(), nullable=False),
        sa.Column('last_escalated_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('resolved_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('history', sa.JSON(), nullable=False),
        sa.Column('set_by', sa.String(length=256), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index('ix_item_balls_item', 'item_balls', ['item_type', 'item_ref'], unique=True)
    op.create_index('ix_item_balls_ball_person', 'item_balls', ['ball_person'])


def downgrade() -> None:
    op.drop_table('item_balls')
