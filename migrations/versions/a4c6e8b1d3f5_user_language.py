"""each person's language (English, Mandarin or Vietnamese)

Revision ID: a4c6e8b1d3f5
Revises: f3b9d5e7a2c4
Create Date: 2026-11-06

Adds users.language so comments, forms and notes can be shown to each person in their own language.
Safe to run twice: the column is only added when it is missing.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'a4c6e8b1d3f5'
down_revision: Union[str, Sequence[str], None] = 'f3b9d5e7a2c4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    cols = {c['name'] for c in sa.inspect(op.get_bind()).get_columns('users')}
    if 'language' not in cols:
        with op.batch_alter_table('users') as batch:
            batch.add_column(sa.Column('language', sa.String(length=8), nullable=False, server_default='en'))


def downgrade() -> None:
    with op.batch_alter_table('users') as batch:
        batch.drop_column('language')
