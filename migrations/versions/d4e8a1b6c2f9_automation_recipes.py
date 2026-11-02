"""automation recipes

Revision ID: d4e8a1b6c2f9
Revises: c3d9e5a1f7b2
Create Date: 2026-10-31

People's own "when this happens, then do that" rules, per board: when an item's
status becomes a label, or a date column is a set number of days away, tell
someone or draft an email for a person to send. Nothing is ever written to monday.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'd4e8a1b6c2f9'
down_revision: Union[str, Sequence[str], None] = 'c3d9e5a1f7b2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'automation_recipes',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('name', sa.String(200), nullable=False),
        sa.Column('board_role', sa.String(16), nullable=False),
        sa.Column('trigger', sa.String(16), nullable=False),
        sa.Column('column', sa.String(128), nullable=True),
        sa.Column('value', sa.String(128), nullable=True),
        sa.Column('action', sa.String(16), nullable=False),
        sa.Column('recipients', sa.String(512), nullable=True),
        sa.Column('email_to', sa.String(256), nullable=True),
        sa.Column('subject', sa.String(256), nullable=True),
        sa.Column('body', sa.Text(), nullable=True),
        sa.Column('mode', sa.String(8), nullable=False, server_default='suggest'),
        sa.Column('enabled', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('created_by', sa.String(256), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table('automation_recipes')
