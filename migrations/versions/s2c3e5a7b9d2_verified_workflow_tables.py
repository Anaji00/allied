"""new tables for vendor payment schedules, engineer gates, redacted drawings, landed cost, tariffs and shipment pipelines

Revision ID: s2c3e5a7b9d2
Revises: r1b2d4f6a8c1
Create Date: 2026-11-22

New tables only, empty, each created only when missing (safe to run twice). Nothing existing is changed, filled in or deleted. Downgrade drops only these tables.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 's2c3e5a7b9d2'
down_revision: Union[str, Sequence[str], None] = 'r1b2d4f6a8c1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLES = ('payment_schedules', 'payment_tranches', 'vendor_payments', 'engineer_gates', 'redacted_artifacts', 'landed_cost_sheets', 'tariff_rates', 'shipment_pipeline', 'sage_import_rows')


def _pk():
    return sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True)


def _now(name='created_at'):
    return sa.Column(name, sa.DateTime(timezone=True), nullable=False)


def _dt(name):
    return sa.Column(name, sa.DateTime(timezone=True), nullable=True)


def upgrade() -> None:
    have = set(sa.inspect(op.get_bind()).get_table_names())
    if 'payment_schedules' not in have:
        op.create_table('payment_schedules', _pk(),
                        sa.Column('po_id', sa.Integer(), nullable=False, index=True), sa.Column('job_id', sa.Integer(), nullable=True, index=True),
                        sa.Column('vendor', sa.String(256)), sa.Column('currency', sa.String(3)), sa.Column('total', sa.Numeric(14, 2), nullable=False),
                        sa.Column('terms_code', sa.String(64)), sa.Column('status', sa.String(16), nullable=False), sa.Column('created_by', sa.String(320)), _now())
    if 'payment_tranches' not in have:
        op.create_table('payment_tranches', _pk(),
                        sa.Column('schedule_id', sa.Integer(), nullable=False, index=True), sa.Column('seq', sa.Integer(), nullable=False), sa.Column('label', sa.String(80)),
                        sa.Column('percent', sa.Numeric(6, 2), nullable=False), sa.Column('amount', sa.Numeric(14, 2), nullable=False), sa.Column('trigger', sa.String(24), nullable=False),
                        sa.Column('due_on', sa.String(10)), sa.Column('status', sa.String(12), nullable=False), _dt('triggered_at'), _dt('paid_at'))
    if 'vendor_payments' not in have:
        op.create_table('vendor_payments', _pk(),
                        sa.Column('tranche_id', sa.Integer(), nullable=False, index=True), sa.Column('idem_key', sa.String(80), nullable=False, unique=True),
                        sa.Column('paid_on', sa.String(10), nullable=False), sa.Column('amount', sa.Numeric(14, 2), nullable=False), sa.Column('currency', sa.String(3)),
                        sa.Column('method', sa.String(24)), sa.Column('reference', sa.String(120)), sa.Column('wire_file_id', sa.Integer()), sa.Column('proforma_file_id', sa.Integer()),
                        sa.Column('recorded_by', sa.String(320)), _now('recorded_at'), _dt('ledger_written_at'), _dt('monday_staged_at'),
                        sa.Column('remittance_action_id', sa.String(36)), _dt('voided_at'))
    if 'engineer_gates' not in have:
        op.create_table('engineer_gates', _pk(),
                        sa.Column('job_id', sa.Integer(), nullable=False, index=True), sa.Column('po_id', sa.Integer(), nullable=True, index=True), sa.Column('gate', sa.Integer(), nullable=False),
                        sa.Column('file_ids', sa.JSON(), nullable=False), sa.Column('status', sa.String(12), nullable=False), sa.Column('requested_by', sa.String(320)), _now('requested_at'),
                        sa.Column('decided_by', sa.String(320)), _dt('decided_at'), sa.Column('note', sa.String(500)),
                        sa.Column('override_by', sa.String(320)), sa.Column('override_reason', sa.String(500)), _dt('override_at'),
                        sa.Column('revoked_by', sa.String(320)), _dt('revoked_at'), sa.Column('revoke_reason', sa.String(500)))
    if 'redacted_artifacts' not in have:
        op.create_table('redacted_artifacts', _pk(),
                        sa.Column('job_id', sa.Integer(), nullable=False, index=True), sa.Column('source_file_id', sa.Integer(), nullable=False, index=True),
                        sa.Column('source_sha256', sa.String(64), nullable=False), sa.Column('redacted_file_id', sa.Integer()), sa.Column('redacted_sha256', sa.String(64)),
                        sa.Column('method', sa.String(12), nullable=False), sa.Column('findings', sa.JSON(), nullable=False), sa.Column('status', sa.String(12), nullable=False),
                        sa.Column('verified_by', sa.String(320)), _dt('verified_at'), sa.Column('attested_by', sa.String(320)), _dt('attested_at'), sa.Column('created_by', sa.String(320)), _now())
    if 'landed_cost_sheets' not in have:
        op.create_table('landed_cost_sheets', _pk(),
                        sa.Column('job_id', sa.Integer(), nullable=True, index=True), sa.Column('rfq_id', sa.Integer(), nullable=True, index=True),
                        sa.Column('vendor_quote_ref', sa.String(80)), sa.Column('currency', sa.String(3)), sa.Column('lines', sa.JSON(), nullable=False),
                        sa.Column('freight', sa.Numeric(14, 2)), sa.Column('tariff_total', sa.Numeric(14, 2)), sa.Column('tariff_unknown', sa.Boolean(), nullable=False),
                        sa.Column('landed_total', sa.Numeric(14, 2)), sa.Column('tiers', sa.JSON(), nullable=False), sa.Column('picked_tier', sa.String(40)),
                        sa.Column('created_by', sa.String(320)), _now())
    if 'tariff_rates' not in have:
        op.create_table('tariff_rates', _pk(),
                        sa.Column('category', sa.String(120), nullable=False, index=True), sa.Column('hs_code', sa.String(16)), sa.Column('country', sa.String(2)),
                        sa.Column('rate_percent', sa.Numeric(6, 2), nullable=False), sa.Column('effective_from', sa.String(10)), sa.Column('note', sa.String(300)),
                        sa.Column('updated_by', sa.String(320)), _now('updated_at'))
    if 'shipment_pipeline' not in have:
        op.create_table('shipment_pipeline',
                        sa.Column('shipment_id', sa.Integer(), primary_key=True), sa.Column('pipeline', sa.String(32), nullable=False), sa.Column('tracking_no', sa.String(64)),
                        sa.Column('carrier', sa.String(64)), sa.Column('bol_file_id', sa.Integer()), sa.Column('quote_options', sa.JSON(), nullable=False), _dt('forwarder_digest_at'),
                        sa.Column('freight_cost_actual', sa.Numeric(14, 2)), sa.Column('freight_cost_quoted', sa.Numeric(14, 2)), _dt('freight_populated_at'),
                        sa.Column('variance_flag', sa.Boolean(), nullable=False), _now('updated_at'))


    if 'sage_import_rows' not in have:
        op.create_table('sage_import_rows', _pk(),
                        sa.Column('import_id', sa.Integer(), nullable=False, index=True), sa.Column('kind', sa.String(24), nullable=False), sa.Column('row_key', sa.String(200), nullable=False),
                        sa.Column('action', sa.String(8), nullable=False), sa.Column('before', sa.JSON()), _now())


def downgrade() -> None:
    have = set(sa.inspect(op.get_bind()).get_table_names())
    for name in reversed(_TABLES):
        if name in have:
            op.drop_table(name)
