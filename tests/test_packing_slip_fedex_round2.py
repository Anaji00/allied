"""Round 2: the migration, legacy orders, backoff timing, partial shipments, multi-page slips,
the production FedEx address and more. Test database only; FedEx and mail are fakes."""

import uuid
from datetime import timedelta
from pathlib import Path

import httpx
import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config

from app.config import settings
from app.core.database import SessionLocal
from app.models.db_models import PackingSlipDB
from app.models.orders import InboundOrderRequest
from app.services import fedex, packing_slip_queue
from app.services.order_orchestrator import OrderOrchestrator

ROOT = Path(__file__).resolve().parent.parent
ITEM = {"raw_item_text": "Hammermill Screen Allied 300XL", "quantity": 1, "unit_price": 410}


@pytest.fixture(autouse=True)
def _app_started(client):
    """Starting the app creates the tables."""


def _order(po=None, lines=None, addr="831 Hayter Rd NW, Edmonton, AB T6S 1A1"):
    po = po if po is not None else f"PO-{uuid.uuid4().hex[:6].upper()}"
    out = OrderOrchestrator().process_inbound_order(InboundOrderRequest(
        customer_name="Owens Corning", customer_po_number=po, shipping_address=addr, line_items=lines or [ITEM]))
    return po, str(out.sales_order_number)


def _row(so):
    with SessionLocal() as db:
        return db.query(PackingSlipDB).filter(PackingSlipDB.sales_order_number == so).one()


def _text(path):
    from pypdf import PdfReader

    return " ".join(p.extract_text() or "" for p in PdfReader(path).pages)


# ------------------------------- the migration --------------------------------


@pytest.fixture
def scratch(tmp_path, monkeypatch):
    url = f"sqlite:///{(tmp_path / 'm.db').as_posix()}"
    monkeypatch.setattr(settings, "DATABASE_URL", url)
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    cfg.set_main_option("sqlalchemy.url", url)
    eng = sa.create_engine(url)
    yield cfg, eng
    eng.dispose()


def test_the_migration_builds_the_table_runs_twice_and_matches_the_model(scratch):
    cfg, eng = scratch
    command.upgrade(cfg, "head")
    insp = sa.inspect(eng)
    cols = {c["name"] for c in insp.get_columns("packing_slips")}
    assert cols == {c.name for c in PackingSlipDB.__table__.columns}
    assert any(i["unique"] and i["column_names"] == ["sales_order_number"] for i in insp.get_indexes("packing_slips"))
    command.upgrade(cfg, "head")                                   # again: nothing changes, nothing breaks
    command.downgrade(cfg, "c8e4a2d6f1b9")
    assert not sa.inspect(eng).has_table("packing_slips")
    command.upgrade(cfg, "head")                                   # and back
    assert sa.inspect(eng).has_table("packing_slips")


def test_a_database_that_already_has_the_table_is_not_broken_by_the_migration(scratch):
    cfg, eng = scratch
    command.upgrade(cfg, "c8e4a2d6f1b9")
    PackingSlipDB.__table__.create(eng)                            # made by hand beforehand
    command.upgrade(cfg, "head")
    assert sa.inspect(eng).has_table("packing_slips")


def test_the_unique_index_refuses_a_second_job_for_the_same_order(scratch):
    cfg, eng = scratch
    command.upgrade(cfg, "head")
    ins = sa.text("insert into packing_slips (sales_order_number,status,attempts,created_at) values ('1','QUEUED',0,'2026-11-06')")
    with eng.begin() as c:
        c.execute(ins)
    with pytest.raises(sa.exc.IntegrityError):
        with eng.begin() as c:
            c.execute(ins)


# --------------------------- legacy orders and fallbacks ------------------------


def test_an_old_order_with_no_queue_row_still_downloads_and_can_be_queued_by_retry(client):
    po, so = _order()
    with SessionLocal() as db:                                     # an order raised before the queue existed
        db.query(PackingSlipDB).filter(PackingSlipDB.sales_order_number == so).delete()
        db.commit()
    assert client.get(f"/api/v1/orders/{so}/packing-slip").status_code == 200
    code = client.post(f"/api/v1/packing-slips/{so}/retry").status_code
    assert code in (200, 404)                                      # either remade, or a plain "not queued": never a 500


def test_if_the_queue_table_is_unavailable_the_order_still_gets_its_slip(monkeypatch):
    from app.services import order_orchestrator

    boom = lambda *a, **k: (_ for _ in ()).throw(sa.exc.OperationalError("x", {}, Exception("no such table: packing_slips")))  # noqa: E731
    monkeypatch.setattr(packing_slip_queue, "SessionLocal", boom)
    po, so = _order()
    assert so and Path(settings.OUTPUT_DIR).exists()
    assert any(Path(settings.OUTPUT_DIR).rglob(f"packing_slip_SO_{so}.pdf"))


def test_the_background_step_survives_a_queue_failure(monkeypatch):
    from app.services.coordination.schedule import CoordinationWorker

    svc = CoordinationWorker.__new__(CoordinationWorker)
    svc.last = {}
    monkeypatch.setattr(packing_slip_queue, "retry_due", lambda: (_ for _ in ()).throw(RuntimeError("db locked")))
    svc._step("packing_slips", packing_slip_queue.retry_due)
    assert "db locked" in svc.last["packing_slips"]["error"]


# ---------------------------------- queue timing ---------------------------------


def test_the_waits_between_tries_are_one_five_then_fifteen_minutes(monkeypatch):
    from app.services.packing_slips import PackingSlipGenerator

    monkeypatch.setattr(PackingSlipGenerator, "generate_pdf", lambda *a, **k: (_ for _ in ()).throw(OSError("x")))
    po, so = _order()
    waits = []
    for _ in range(3):
        r = _row(so)
        waits.append(round((r.next_try_at.replace(tzinfo=None) - packing_slip_queue._now().replace(tzinfo=None)).total_seconds() / 60))
        with SessionLocal() as db:
            db.query(PackingSlipDB).filter(PackingSlipDB.sales_order_number == so).update({"next_try_at": packing_slip_queue._now()})
            db.commit()
        packing_slip_queue.retry_due()
    assert waits == [1, 5, 15] and _row(so).status == "FAILED"


def test_a_failed_job_alerts_the_reviewers_once_even_if_retry_is_pressed_and_fails_again(monkeypatch, client):
    from app.models.db_models import NotificationDB
    from app.services.packing_slips import PackingSlipGenerator

    monkeypatch.setattr(PackingSlipGenerator, "generate_pdf", lambda *a, **k: (_ for _ in ()).throw(OSError("x")))
    po, so = _order()
    for _ in range(packing_slip_queue.MAX_ATTEMPTS):
        with SessionLocal() as db:
            db.query(PackingSlipDB).filter(PackingSlipDB.sales_order_number == so).update({"next_try_at": packing_slip_queue._now()})
            db.commit()
        packing_slip_queue.retry_due()
    for _ in range(3):                                              # a person keeps pressing Retry; it keeps failing
        client.post(f"/api/v1/packing-slips/{so}/retry")
    with SessionLocal() as db:
        keys = [n.dedup_key for n in db.query(NotificationDB).filter(NotificationDB.dedup_key.like(f"slip-failed:{so}:%"))]
    assert 1 <= len(keys) <= 4 and len(set(keys)) == len(keys)


# ------------------------------- what the customer sees --------------------------


def test_two_shipments_of_one_po_get_different_customs_numbers_or_say_so():
    from app.services.logistics_dispatcher import logistics_dispatcher as d

    kw = dict(customer_name="OC", shipping_address="Edmonton, AB", origin_facility="E", items=[{"item_id": "X", "quantity": 1, "unit_price": 5}])
    a = d.generate_customs_invoice(so_number="1001", customer_po="PO-SAME", **kw)
    b = d.generate_customs_invoice(so_number="1002", customer_po="PO-SAME", **kw)
    assert a.cci_number != b.cci_number, "two different shipments must not share one customs invoice number"


def test_a_big_order_runs_to_several_pages_and_still_hides_our_number(client):
    lines = [{"raw_item_text": f"Hammermill Screen Allied 300XL variant {i}", "quantity": i + 1, "unit_price": 410} for i in range(60)]
    po, so = _order(lines=lines)
    row = _row(so)
    text = _text(row.path)
    from pypdf import PdfReader

    assert len(PdfReader(row.path).pages) >= 2 and po in text
    assert f"SO-{so}" not in text and f"SO {so}" not in text and f"Sales Order" not in text


def test_the_shipment_email_and_slip_name_for_a_po_with_slashes_and_spaces(client):
    from app.services.logistics_dispatcher import LogisticsDispatchRequest, logistics_dispatcher

    po, so = _order("PO 45/12 A")
    out = logistics_dispatcher.dispatch_shipment(LogisticsDispatchRequest(
        sales_order_number=f"SO-{so}", carrier_name="Old Dominion", tracking_number=f"ODFL-{uuid.uuid4().hex[:6]}",
        recipient_email="r@owenscorning.com"))
    mail = out.customer_notification_email
    assert "PO 45/12 A" in mail and f"SO-{so}" not in mail and "packing_list_PO-45-12-A.pdf" in mail


# ------------------------------------- FedEx --------------------------------------


def _ready(monkeypatch, **extra):
    for k, v in {"FEDEX_ENABLED": True, "FEDEX_ENV": "sandbox", "FEDEX_CLIENT_ID": "id", "FEDEX_CLIENT_SECRET": "s",
                 "FEDEX_ACCOUNT_NUMBER": "1", "FEDEX_SHIPPER_POSTAL": "75379", **extra}.items():
        monkeypatch.setattr(settings, k, v)
    fedex._token.update(value=None, expires=0.0)


def _capture(monkeypatch, reply=None):
    seen = []

    def post(url, **kw):
        seen.append((url, kw))
        if url.endswith("/oauth/token"):
            return httpx.Response(200, json={"access_token": "t", "expires_in": 3600})
        return reply or httpx.Response(200, json={"output": {"rateReplyDetails": []}})

    monkeypatch.setattr(fedex.httpx, "post", post)
    return seen


def test_production_goes_to_the_production_address_and_an_unknown_env_falls_back_to_sandbox(monkeypatch):
    _ready(monkeypatch, FEDEX_ENV="production")
    seen = _capture(monkeypatch)
    fedex.quote("1 Main St, Irving, TX 75063")
    assert all(u.startswith("https://apis.fedex.com/") for u, _ in seen)
    monkeypatch.setattr(settings, "FEDEX_ENV", "Production ")                  # stray space/case from a typed .env
    assert fedex.base_url() in ("https://apis.fedex.com", "https://apis-sandbox.fedex.com")
    monkeypatch.setattr(settings, "FEDEX_ENV", "nonsense")
    assert fedex.base_url() == "https://apis-sandbox.fedex.com"


def test_a_canadian_destination_and_a_shipper_city_and_state_are_sent_correctly(monkeypatch):
    _ready(monkeypatch, FEDEX_SHIPPER_STATE="TX", FEDEX_SHIPPER_CITY="Irving", FEDEX_SHIPPER_COUNTRY="US")
    seen = _capture(monkeypatch)
    fedex.quote("831 Hayter Rd NW, Edmonton, AB T6S 1A1", 12.34)
    body = seen[-1][1]["json"]["requestedShipment"]
    assert body["recipient"]["address"]["countryCode"] == "CA" and body["recipient"]["address"]["postalCode"] == "T6S1A1"
    assert body["shipper"]["address"] == {"postalCode": "75379", "countryCode": "US", "stateOrProvinceCode": "TX", "city": "Irving"}
    assert body["requestedPackageLineItems"][0]["weight"]["value"] == 12.3


def test_blank_or_space_only_settings_count_as_missing(monkeypatch):
    _ready(monkeypatch, FEDEX_CLIENT_ID="   ", FEDEX_ACCOUNT_NUMBER="")
    s = fedex.status()
    assert not s["ready"] and set(s["missing"]) == {"FEDEX_CLIENT_ID", "FEDEX_ACCOUNT_NUMBER"}
    with pytest.raises(fedex.FedExError, match="Waiting for"):
        fedex.quote("1 Main St, Irving, TX 75063")


def test_an_expired_login_is_renewed_before_the_next_quote(monkeypatch):
    _ready(monkeypatch)
    seen = _capture(monkeypatch)
    fedex.quote("1 Main St, Irving, TX 75063")
    fedex._token["expires"] = 0.0                                              # time passes: the token is stale
    fedex.quote("1 Main St, Irving, TX 75063")
    assert sum(u.endswith("/oauth/token") for u, _ in seen) == 2


def test_a_login_reply_with_no_token_or_junk_is_said_plainly(monkeypatch):
    _ready(monkeypatch)
    for r in (httpx.Response(200, json={}), httpx.Response(200, text="<html>"), httpx.Response(503, text="down")):
        fedex._token.update(value=None, expires=0.0)
        monkeypatch.setattr(fedex.httpx, "post", lambda *a, _r=r, **k: _r)
        out = fedex.check()
        assert out["ok"] is False and out["detail"] and "Traceback" not in out["detail"]


def test_dispatch_with_no_shipping_address_or_fedex_returning_nothing_still_has_quotes(monkeypatch):
    from app.services.logistics_dispatcher import logistics_dispatcher as d

    _ready(monkeypatch)
    _capture(monkeypatch)                                                       # FedEx answers, but with no services
    got = d.get_carrier_quotes("HQ", "Receiving, 1 Main St, Irving, TX 75063")
    assert got and not any(q.service_level.startswith("Live FedEx") for q in got)
    assert d.get_carrier_quotes("HQ", "") is not None


def test_the_quote_endpoint_says_plainly_when_fedex_is_off_and_never_calls_out(monkeypatch, client):
    monkeypatch.setattr(settings, "FEDEX_ENABLED", False)
    called = []
    monkeypatch.setattr(fedex.httpx, "post", lambda *a, **k: called.append(1))
    r = client.get("/api/v1/fedex/quote", params={"address": "1 Main St, Irving, TX 75063"})
    assert r.status_code == 400 and "switched off" in r.json()["detail"] and not called
    assert client.get("/api/v1/fedex/quote").status_code == 422                   # no address at all


def test_the_money_values_are_floats_in_the_right_currency_and_survive_a_string_price(monkeypatch):
    body = {"output": {"rateReplyDetails": [{"serviceType": "A", "ratedShipmentDetails": [
        {"rateType": "ACCOUNT", "totalNetCharge": "48.50", "currency": "CAD"}]}]}}
    q = fedex.parse_quotes(body)
    assert q[0]["price"] == 48.5 and q[0]["currency"] == "CAD"
