"""a new purchase order can follow a cancelled one; optional overseas terms on a purchase order

Revision ID: l5b7d9e1f3a5
Revises: k4a6c8d0e2f4
Create Date: 2026-11-15

The one-PO-per-vendor-per-job index now ignores CANCELLED rows (PO numbers stay unique across all rows). Two new empty optional columns hold the
Incoterm and its place. Safe to run twice. Nothing existing is changed or removed.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'l5b7d9e1f3a5'
down_revision: Union[str, Sequence[str], None] = 'k4a6c8d0e2f4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _cols(table: str) -> set:
    return {c['name'] for c in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    have = _cols('purchase_orders')
    if 'incoterm' not in have:
        op.add_column('purchase_orders', sa.Column('incoterm', sa.String(length=16), nullable=True))
    if 'incoterm_place' not in have:
        op.add_column('purchase_orders', sa.Column('incoterm_place', sa.String(length=64), nullable=True))
    op.execute("DROP INDEX IF EXISTS ux_po_job_vendor")
    op.execute("CREATE UNIQUE INDEX IF NOT EXISTS ux_po_job_vendor ON purchase_orders (job_id, lower(vendor)) WHERE status <> 'CANCELLED'")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ux_po_job_vendor")
    dupes = op.get_bind().execute(sa.text("SELECT 1 FROM purchase_orders GROUP BY job_id, lower(vendor) HAVING count(*) > 1 LIMIT 1")).fetchone()
    # A new PO after a cancel can leave two rows for one vendor and job. Then the old rule cannot come back as a UNIQUE index (nothing is deleted); a plain
    # index of the same name keeps lookups fast, and one live PO per vendor per job is then kept by the application.
    if dupes:
        import logging
        logging.getLogger("alembic.runtime.migration").warning(
            "Downgrade: some job and vendor pairs have more than one PO (a PO followed a cancelled one), so the unique "
            "index was restored as a plain index. One live PO per vendor per job is then kept by the application only.")
    op.execute(("CREATE INDEX IF NOT EXISTS ux_po_job_vendor ON purchase_orders (job_id, lower(vendor))" if dupes else
                "CREATE UNIQUE INDEX IF NOT EXISTS ux_po_job_vendor ON purchase_orders (job_id, lower(vendor))"))
    op.drop_column('purchase_orders', 'incoterm_place')
    op.drop_column('purchase_orders', 'incoterm')
