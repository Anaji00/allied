"""c3d9e5a1f7b2 repairs databases stamped at a9c4e2f7b1d8 before rfqs.monday_item_id was added to it."""

from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config

from app.config import settings

ROOT = Path(__file__).resolve().parent.parent


def _head(cfg):
    """The one head every database ends on (later migrations build on c3d9e5a1f7b2)."""
    from alembic.script import ScriptDirectory

    return ScriptDirectory.from_config(cfg).get_current_head()


@pytest.fixture
def scratch_db(tmp_path, monkeypatch):
    """A throwaway SQLite file; migrations/env.py reads the URL from settings."""
    url = f"sqlite:///{(tmp_path / 'migrate.db').as_posix()}"
    monkeypatch.setattr(settings, "DATABASE_URL", url)
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    cfg.set_main_option("sqlalchemy.url", url)
    engine = sa.create_engine(url)
    yield cfg, engine
    engine.dispose()


def _rfq_schema(engine):
    inspector = sa.inspect(engine)
    columns = {c["name"] for c in inspector.get_columns("rfqs")}
    indexes = {i["name"]: i for i in inspector.get_indexes("rfqs")}
    with engine.connect() as conn:
        version = conn.execute(sa.text("select version_num from alembic_version")).scalar()
    return columns, indexes, version


def test_fresh_database_upgrades_cleanly(scratch_db):
    cfg, engine = scratch_db
    command.upgrade(cfg, "head")
    columns, indexes, version = _rfq_schema(engine)
    assert "monday_item_id" in columns
    assert indexes["ix_rfqs_monday_item_id"]["unique"]
    assert version == _head(cfg)


def test_database_stamped_by_the_first_version_gets_the_column(scratch_db):
    cfg, engine = scratch_db
    command.upgrade(cfg, "f5b1c3d7e2a4")
    # What the first version of a9c4e2f7b1d8 did: only the vendor job column.
    with engine.begin() as conn:
        conn.execute(sa.text("alter table vendor_jobs add column customer_due varchar(10)"))
        conn.execute(
            sa.text(
                "insert into rfqs (number, customer, subject, items, status, received_at, ack_due_at, due_at, history, source) "
                "values ('RFQ-KEEP-1', 'Acme', 'Liners', '[]', 'NEW', '2026-10-30', '2026-10-31', '2026-11-06', '[]', 'manual')"
            )
        )
    command.stamp(cfg, "a9c4e2f7b1d8")
    assert "monday_item_id" not in _rfq_schema(engine)[0]

    command.upgrade(cfg, "head")
    columns, indexes, version = _rfq_schema(engine)
    assert "monday_item_id" in columns
    assert indexes["ix_rfqs_monday_item_id"]["unique"]
    assert version == _head(cfg)
    with engine.connect() as conn:
        assert conn.execute(sa.text("select number, monday_item_id from rfqs")).all() == [("RFQ-KEEP-1", None)]
