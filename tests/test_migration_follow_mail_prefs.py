"""The combined new-tables migration, rehearsed on a scratch database: upgrade, a second run, downgrade and upgrade again. Nothing real is touched."""
import sqlite3
import tempfile
from pathlib import Path

from alembic import command
from alembic.config import Config

ROOT = Path(__file__).resolve().parent.parent
NEW = ("item_follows", "item_seen", "mail_ai_settings", "mail_ai_use", "notification_prefs")


def _cfg(url):
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.attributes["configure_logger"] = False
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    cfg.set_main_option("sqlalchemy.url", url)
    return cfg


def _tables(path):
    with sqlite3.connect(path) as c:
        return {r[0] for r in c.execute("select name from sqlite_master where type='table'")}


def test_the_migration_adds_only_new_tables_is_safe_twice_and_reverses(monkeypatch):
    from app.config import settings

    from alembic.script import ScriptDirectory

    heads = ScriptDirectory.from_config(_cfg("sqlite://")).get_heads()
    assert heads == ["t3d4f6b8c1e4"] and "p9f1b3d5e7a9" in {r.revision for r in ScriptDirectory.from_config(_cfg("sqlite://")).walk_revisions()}      # one head
    path = Path(tempfile.mkdtemp()) / "scratch.db"
    url = f"sqlite:///{path}"
    monkeypatch.setattr(settings, "DATABASE_URL", url)                 # the migration environment reads the database from settings
    cfg = _cfg(url)
    command.upgrade(cfg, "o8e0a2c4d6f8")
    before = _tables(path)
    assert not set(NEW) & before
    with sqlite3.connect(path) as c:
        cols_before = {t: [r[1] for r in c.execute(f"pragma table_info({t})")] for t in before if not t.startswith("alembic")}
    command.upgrade(cfg, "p9f1b3d5e7a9")
    after = _tables(path)
    assert set(NEW) <= after and before - {"alembic_version"} <= after
    with sqlite3.connect(path) as c:
        assert all(cols_before[t] == [r[1] for r in c.execute(f"pragma table_info({t})")] for t in cols_before)      # no existing table changed
    command.upgrade(cfg, "p9f1b3d5e7a9")                                         # a second run does nothing
    command.downgrade(cfg, "o8e0a2c4d6f8")
    assert not set(NEW) & _tables(path)
    command.upgrade(cfg, "p9f1b3d5e7a9")
    assert set(NEW) <= _tables(path)


LEADS = ("lead_accounts", "lead_contacts", "leads", "lead_outreach", "lead_do_not_contact", "lead_settings", "customer_aliases", "mail_templates")


def test_the_lead_migration_adds_only_new_tables_is_safe_twice_and_reverses(monkeypatch):
    from app.config import settings

    path = Path(tempfile.mkdtemp()) / "scratch2.db"
    url = f"sqlite:///{path}"
    monkeypatch.setattr(settings, "DATABASE_URL", url)
    cfg = _cfg(url)
    command.upgrade(cfg, "p9f1b3d5e7a9")
    before = _tables(path)
    assert not set(LEADS) & before
    with sqlite3.connect(path) as c:
        cols_before = {t: [r[1] for r in c.execute(f"pragma table_info({t})")] for t in before if not t.startswith("alembic")}
    command.upgrade(cfg, "q0a2c4e6f8b0")
    command.upgrade(cfg, "q0a2c4e6f8b0")                                          # a second run does nothing
    after = _tables(path)
    assert set(LEADS) <= after
    with sqlite3.connect(path) as c:
        assert all(cols_before[t] == [r[1] for r in c.execute(f"pragma table_info({t})")] for t in cols_before)
    command.downgrade(cfg, "p9f1b3d5e7a9")
    assert not set(LEADS) & _tables(path) and set(NEW) <= _tables(path)
    command.upgrade(cfg, "q0a2c4e6f8b0")
    assert set(LEADS) <= _tables(path)


ORGANIZE = ("customer_families", "alias_targets", "record_tags", "vendor_contacts", "record_owners", "view_pins", "refresh_state")


def test_the_organize_migration_adds_new_tables_and_one_empty_nullable_column_is_safe_twice_and_reverses(monkeypatch):
    from app.config import settings

    path = Path(tempfile.mkdtemp()) / "scratch3.db"
    url = f"sqlite:///{path}"
    monkeypatch.setattr(settings, "DATABASE_URL", url)
    cfg = _cfg(url)
    command.upgrade(cfg, "q0a2c4e6f8b0")
    with sqlite3.connect(path) as c:
        c.execute("insert into rfqs (number, customer, subject, items, complexity, status, history, received_at, ack_due_at, due_at) values ('RFQ-OLD', 'Old Co', 's', '[]', 'STANDARD', 'NEW', '[]', '2026-01-01', '2026-01-02', '2026-01-03')")
        before_tables = _tables(path)
        before_rfq = c.execute("select number, customer, subject, status from rfqs").fetchall()
        other_cols = {t: [r[1] for r in c.execute(f"pragma table_info({t})")] for t in before_tables if not t.startswith("alembic") and t != "rfqs"}
    assert not set(ORGANIZE) & before_tables
    command.upgrade(cfg, "r1b2d4f6a8c1")
    command.upgrade(cfg, "r1b2d4f6a8c1")                                                    # a second run does nothing
    with sqlite3.connect(path) as c:
        assert set(ORGANIZE) <= _tables(path)
        assert c.execute("select number, customer, subject, status from rfqs").fetchall() == before_rfq             # no existing row changed
        assert c.execute("select quote_currency from rfqs").fetchall() == [(None,)]                                 # the new column starts empty: nothing guessed
        assert all(other_cols[t] == [r[1] for r in c.execute(f"pragma table_info({t})")] for t in other_cols)     # no other existing table changed
        assert all(c.execute(f"select count(*) from {t}").fetchone()[0] == 0 for t in ORGANIZE)
    command.downgrade(cfg, "q0a2c4e6f8b0")
    with sqlite3.connect(path) as c:
        assert not set(ORGANIZE) & _tables(path) and "quote_currency" not in [r[1] for r in c.execute("pragma table_info(rfqs)")]
        assert c.execute("select number from rfqs").fetchall() == [("RFQ-OLD",)]
    command.upgrade(cfg, "r1b2d4f6a8c1")
    assert set(ORGANIZE) <= _tables(path)


WORKFLOW = ("payment_schedules", "payment_tranches", "vendor_payments", "engineer_gates", "redacted_artifacts", "landed_cost_sheets", "tariff_rates", "shipment_pipeline", "sage_import_rows")


def test_the_verified_workflow_migration_adds_only_empty_new_tables_is_safe_twice_and_reverses(monkeypatch):
    from app.config import settings

    path = Path(tempfile.mkdtemp()) / "scratch4.db"
    url = f"sqlite:///{path}"
    monkeypatch.setattr(settings, "DATABASE_URL", url)
    cfg = _cfg(url)
    command.upgrade(cfg, "r1b2d4f6a8c1")
    before = _tables(path)
    assert not set(WORKFLOW) & before
    with sqlite3.connect(path) as c:
        cols = {t: [r[1] for r in c.execute(f"pragma table_info({t})")] for t in before if not t.startswith("alembic")}
    command.upgrade(cfg, "s2c3e5a7b9d2")
    command.upgrade(cfg, "s2c3e5a7b9d2")                                                    # a second run does nothing
    with sqlite3.connect(path) as c:
        assert set(WORKFLOW) <= _tables(path)
        assert all(cols[t] == [r[1] for r in c.execute(f"pragma table_info({t})")] for t in cols)      # no existing table changed
        assert all(c.execute(f"select count(*) from {t}").fetchone()[0] == 0 for t in WORKFLOW)
    command.downgrade(cfg, "r1b2d4f6a8c1")
    assert not set(WORKFLOW) & _tables(path) and set(ORGANIZE) <= _tables(path)
    command.upgrade(cfg, "s2c3e5a7b9d2")
    assert set(WORKFLOW) <= _tables(path)


def test_the_models_and_the_migration_agree_on_every_new_table_and_column(monkeypatch):
    from sqlalchemy import create_engine, inspect

    from app.config import settings
    from app.core.database import Base
    import app.models.db_models  # noqa: F401

    path = Path(tempfile.mkdtemp()) / "scratch5.db"
    url = f"sqlite:///{path}"
    monkeypatch.setattr(settings, "DATABASE_URL", url)
    command.upgrade(_cfg(url), "head")
    insp = inspect(create_engine(url))
    for t in WORKFLOW:
        assert {c["name"] for c in insp.get_columns(t)} == set(Base.metadata.tables[t].columns.keys()), t


def test_the_currency_and_bill_detail_migration_adds_two_empty_nullable_columns_keeps_every_row_and_reverses(monkeypatch):
    from app.config import settings

    path = Path(tempfile.mkdtemp()) / "scratch6.db"
    url = f"sqlite:///{path}"
    monkeypatch.setattr(settings, "DATABASE_URL", url)
    cfg = _cfg(url)
    command.upgrade(cfg, "s2c3e5a7b9d2")
    with sqlite3.connect(path) as c:
        c.execute("insert into purchase_orders (job_id, po_number, vendor, lines, status, created_at) values (1, 'PO-OLD', 'Old Vendor', '[]', 'SENT', '2026-01-01')")
        c.execute("insert into reconciliations (reconciliation_id, invoice_number, vendor_name, po_number, reconciliation_status, discrepancies) values ('r1', 'I1', 'Old Vendor', 'PO-OLD', 'APPROVED', '[]')")
        before = {t: c.execute(f"select * from {t}").fetchall() for t in ("purchase_orders", "reconciliations")}
        cols = {t: [r[1] for r in c.execute(f"pragma table_info({t})")] for t in _tables(path) if not t.startswith("alembic") and t not in before}
        idx = c.execute("select name from sqlite_master where type='index' and tbl_name='purchase_orders'").fetchall()
    command.upgrade(cfg, "head")
    command.upgrade(cfg, "head")                                                    # a second run does nothing
    with sqlite3.connect(path) as c:
        assert c.execute("select po_number, vendor, status, currency from purchase_orders").fetchall() == [("PO-OLD", "Old Vendor", "SENT", None)]          # an old PO keeps showing as before: no currency
        assert c.execute("select invoice_number, bill_detail from reconciliations").fetchall() == [("I1", None)]
        assert all(cols[t] == [r[1] for r in c.execute(f"pragma table_info({t})")] for t in cols)                 # no other table changed
        assert c.execute("select name from sqlite_master where type='index' and tbl_name='purchase_orders'").fetchall() == idx
        assert c.execute("pragma integrity_check").fetchone()[0] == "ok"
    command.downgrade(cfg, "s2c3e5a7b9d2")
    with sqlite3.connect(path) as c:
        assert "currency" not in [r[1] for r in c.execute("pragma table_info(purchase_orders)")] and "bill_detail" not in [r[1] for r in c.execute("pragma table_info(reconciliations)")]
        assert c.execute("select po_number from purchase_orders").fetchall() == [("PO-OLD",)]
        assert c.execute("select name from sqlite_master where type='index' and tbl_name='purchase_orders'").fetchall() == idx
    command.upgrade(cfg, "head")
