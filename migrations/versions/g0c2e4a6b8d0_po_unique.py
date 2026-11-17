"""one purchase order per vendor per job, and PO numbers that cannot repeat

Revision ID: g0c2e4a6b8d0
Revises: f9b1d3e5a7c9
Create Date: 2026-11-14

Safe to run twice (IF NOT EXISTS). Fails loudly if two POs already share a job and vendor, so nothing is hidden.
"""
from typing import Sequence, Union

from alembic import op

revision: str = 'g0c2e4a6b8d0'
down_revision: Union[str, Sequence[str], None] = 'f9b1d3e5a7c9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("CREATE UNIQUE INDEX IF NOT EXISTS ux_po_job_vendor ON purchase_orders (job_id, lower(vendor))")
    op.execute("CREATE UNIQUE INDEX IF NOT EXISTS ux_po_number ON purchase_orders (po_number) WHERE po_number IS NOT NULL")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ux_po_number")
    op.execute("DROP INDEX IF EXISTS ux_po_job_vendor")
