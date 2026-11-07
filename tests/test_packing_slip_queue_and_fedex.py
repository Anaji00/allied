"""Packing slips queued on every new sales order, the customer's PO (not our SO) on what customers see,
and FedEx: config, connection check and live parcel prices. FedEx is a fake; test database only."""

import uuid

import httpx
import pytest

from app.config import settings
from app.core.database import SessionLocal
from app.models.db_models import PackingSlipDB
from app.models.orders import InboundOrderRequest
from app.services import fedex, packing_slip_queue
from app.services.order_orchestrator import OrderOrchestrator

LINES = [{"raw_item_text": "Hammermill Screen Allied 300XL", "quantity": 2, "unit_price": 410}]


@pytest.fixture(autouse=True)
def _app_started(client):
    """Starting the app creates the tables and loads the catalogs."""


def _po():
    return f"PO-{uuid.uuid4().hex[:6].upper()}"


def _raise_order(po=None):
    po = po or _po()
    out = OrderOrchestrator().process_inbound_order(InboundOrderRequest(
        customer_name="Owens Corning", customer_po_number=po, shipping_address="831 Hayter Rd NW, Edmonton, AB T6S 1A1", line_items=LINES))
    return po, str(out.sales_order_number)


def _job(so):
    with SessionLocal() as db:
        return db.query(PackingSlipDB).filter(PackingSlipDB.sales_order_number == so).one()


# ---------------------------------------------------------------------------
# The queue
# ---------------------------------------------------------------------------


def test_every_new_sales_order_has_its_packing_slip_queued_and_made(client):
    po, so = _raise_order()
    job = _job(so)
    assert job.status == "READY" and job.customer_po_number == po and job.attempts == 1 and job.payload
    assert client.get(f"/api/v1/orders/{so}/packing-slip").status_code == 200
    got = client.get("/api/v1/packing-slips/queue").json()
    assert got["counts"].get("READY", 0) >= 1 and any(j["sales_order_number"] == so and j["ready"] for j in got["jobs"])
    again = _raise_order(po)                                    # the same PO again: still one order, one slip
    assert again[1] == so
    with SessionLocal() as db:
        assert db.query(PackingSlipDB).filter(PackingSlipDB.sales_order_number == so).count() == 1


def test_a_slip_that_cannot_be_made_never_stops_the_order_and_is_retried_then_flagged(monkeypatch):
    from app.services.packing_slips import PackingSlipGenerator

    real = PackingSlipGenerator.generate_pdf
    state = {"fail": True}

    def flaky(self, *a, **k):
        if state["fail"]:
            raise OSError("disk is full")
        return real(self, *a, **k)

    monkeypatch.setattr(PackingSlipGenerator, "generate_pdf", flaky)
    po, so = _raise_order()                                      # the order is still raised
    job = _job(so)
    assert job.status == "QUEUED" and job.attempts == 1 and "disk is full" in job.last_error and job.next_try_at is not None
    assert packing_slip_queue.retry_due()["due"] == 0            # not yet: it waits a minute
    with SessionLocal() as db:                                    # time passes
        db.query(PackingSlipDB).filter(PackingSlipDB.sales_order_number == so).update({"next_try_at": packing_slip_queue._now()})
        db.commit()
    state["fail"] = False                                         # the disk is fixed
    assert packing_slip_queue.retry_due()["made"] == 1 and _job(so).status == "READY"


def test_after_the_last_try_the_job_is_failed_and_a_person_is_told_once_then_retry_fixes_it(monkeypatch, client):
    from app.models.db_models import NotificationDB
    from app.services.packing_slips import PackingSlipGenerator

    real = PackingSlipGenerator.generate_pdf
    state = {"fail": True}
    monkeypatch.setattr(PackingSlipGenerator, "generate_pdf",
                        lambda self, *a, **k: (_ for _ in ()).throw(OSError("printer driver")) if state["fail"] else real(self, *a, **k))
    po, so = _raise_order()
    for _ in range(packing_slip_queue.MAX_ATTEMPTS - 1):
        with SessionLocal() as db:
            db.query(PackingSlipDB).filter(PackingSlipDB.sales_order_number == so).update({"next_try_at": packing_slip_queue._now()})
            db.commit()
        packing_slip_queue.retry_due()
    job = _job(so)
    assert job.status == "FAILED" and job.attempts == packing_slip_queue.MAX_ATTEMPTS and job.next_try_at is None
    with SessionLocal() as db:
        assert db.query(NotificationDB).filter(NotificationDB.dedup_key == f"slip-failed:{so}:").count() == 1
    state["fail"] = False
    assert client.post(f"/api/v1/packing-slips/SO-{so}/retry").json()["status"] == "READY"
    assert client.get("/api/v1/packing-slips/queue", params={"state": "FAILED"}).json()["jobs"] == [] or all(
        j["sales_order_number"] != so for j in client.get("/api/v1/packing-slips/queue", params={"state": "FAILED"}).json()["jobs"])


def test_a_person_marks_a_ready_slip_printed(client):
    po, so = _raise_order()
    done = client.post(f"/api/v1/packing-slips/{so}/printed").json()
    assert done["status"] == "PRINTED" and done["printed_by"]
    assert client.post("/api/v1/packing-slips/NOPE/printed").status_code == 404


# ---------------------------------------------------------------------------
# Our sales order number is internal
# ---------------------------------------------------------------------------


def _pdf_text(path):
    from pypdf import PdfReader

    return " ".join(pg.extract_text() or "" for pg in PdfReader(path).pages)


def test_the_slip_shows_the_customers_po_and_not_our_sales_order_number(client):
    po, so = _raise_order()
    from pathlib import Path

    text = _pdf_text(_job(so).path)
    assert po in text and "Your PO Number" in text
    assert f"Sales Order Number" not in text and f"SO {so}" not in text
    r = client.get(f"/api/v1/orders/{so}/packing-slip")
    assert f"packing_list_{po}.pdf" in r.headers["content-disposition"] and f"SO_{so}" not in r.headers["content-disposition"]


def test_the_shipment_email_and_customs_number_use_the_customers_po(monkeypatch):
    from app.services.logistics_dispatcher import LogisticsDispatchRequest, logistics_dispatcher

    po, so = _raise_order()
    out = logistics_dispatcher.dispatch_shipment(LogisticsDispatchRequest(
        sales_order_number=f"SO-{so}", carrier_name="Old Dominion", tracking_number=f"ODFL-{uuid.uuid4().hex[:6]}",
        recipient_email="receiving@owenscorning.com"))
    mail = out.customer_notification_email
    assert po in mail and f"SO-{so}" not in mail and f"#{so}" not in mail and f"packing_list_{po}.pdf" in mail
    cci = logistics_dispatcher.generate_customs_invoice(so_number=so, customer_name="OC", shipping_address="Edmonton, AB",
                                                        origin_facility="EDMONTON", items=[{"item_id": "X", "quantity": 1, "unit_price": 5}],
                                                        customer_po=po)
    assert cci.cci_number.startswith(f"CCI-2026-{po}-") and so not in cci.cci_number


# ---------------------------------------------------------------------------
# FedEx
# ---------------------------------------------------------------------------

SAMPLE = {"output": {"rateReplyDetails": [
    {"serviceType": "FEDEX_GROUND", "serviceName": "FedEx Ground", "commit": {"transitDays": {"description": "2 Business Days"},
                                                                         "dateDetail": {"dayCxsFormat": "2026-11-10T20:00:00"}},
     "ratedShipmentDetails": [{"rateType": "LIST", "totalNetCharge": 61.2, "currency": "USD"},
                              {"rateType": "ACCOUNT", "totalNetCharge": 48.5, "currency": "USD"}]},
    {"serviceType": "PRIORITY_OVERNIGHT", "serviceName": "FedEx Priority Overnight",
     "ratedShipmentDetails": [{"rateType": "LIST", "totalNetFedExCharge": 144.0, "currency": "USD"}]}]}}


class FakeFedEx:
    def __init__(self, bad_secret=False, status=200):
        self.calls, self.bad_secret, self.status = [], bad_secret, status

    def __call__(self, url, **kw):
        self.calls.append((url, kw))
        if url.endswith("/oauth/token"):
            if self.bad_secret:
                return httpx.Response(401, json={"errors": [{"code": "NOT.AUTHORIZED.ERROR"}]})
            return httpx.Response(200, json={"access_token": "tok-123", "expires_in": 3600})
        return httpx.Response(self.status, json=SAMPLE if self.status == 200 else {"errors": [{"message": "Invalid postal code"}]})


@pytest.fixture
def fed(monkeypatch):
    for k, v in {"FEDEX_ENABLED": True, "FEDEX_ENV": "sandbox", "FEDEX_CLIENT_ID": "id", "FEDEX_CLIENT_SECRET": "secret",
                 "FEDEX_ACCOUNT_NUMBER": "123456789", "FEDEX_SHIPPER_POSTAL": "75379", "FEDEX_SHIPPER_STATE": "TX"}.items():
        monkeypatch.setattr(settings, k, v)
    fedex._token.update(value=None, expires=0.0)
    fake = FakeFedEx()
    monkeypatch.setattr(fedex.httpx, "post", fake)
    return fake


def test_fedex_says_what_is_missing_and_never_calls_out_until_set_up(monkeypatch, client):
    for k in ("FEDEX_ENABLED", "FEDEX_CLIENT_ID", "FEDEX_CLIENT_SECRET", "FEDEX_ACCOUNT_NUMBER", "FEDEX_SHIPPER_POSTAL"):
        monkeypatch.setattr(settings, k, False if k == "FEDEX_ENABLED" else "")
    called = []
    monkeypatch.setattr(fedex.httpx, "post", lambda *a, **k: called.append(a))
    s = client.get("/api/v1/fedex/status").json()
    assert not s["ready"] and "switched off" in s["detail"] and "FEDEX_CLIENT_ID" in s["missing"]
    assert client.post("/api/v1/fedex/check").json()["ok"] is False and called == []
    monkeypatch.setattr(settings, "FEDEX_ENABLED", True)
    assert "Waiting for FEDEX_CLIENT_ID" in client.get("/api/v1/fedex/status").json()["detail"]
    assert client.get("/api/v1/fedex/quote", params={"address": "1 Main St, Irving, TX 75063"}).status_code == 400


def test_the_connection_check_logs_in_to_the_sandbox_and_says_when_credentials_are_wrong(fed, client, monkeypatch):
    ok = client.post("/api/v1/fedex/check").json()
    assert ok["ok"] and "Connected to FedEx (sandbox)" in ok["detail"] and fed.calls[0][0] == "https://apis-sandbox.fedex.com/oauth/token"
    assert fed.calls[0][1]["data"]["grant_type"] == "client_credentials"
    fedex._token.update(value=None, expires=0.0)
    monkeypatch.setattr(fedex.httpx, "post", FakeFedEx(bad_secret=True))
    bad = client.post("/api/v1/fedex/check").json()
    assert not bad["ok"] and "refused the credentials" in bad["detail"]
    monkeypatch.setattr(settings, "FEDEX_ENV", "production")
    assert fedex.base_url() == "https://apis.fedex.com"


def test_live_parcel_prices_come_back_cheapest_first_with_the_account_rate(fed, client):
    quotes = client.get("/api/v1/fedex/quote", params={"address": "OC Irving, 1 Main St, Irving, TX 75063", "weight_lbs": 80}).json()
    assert [q["name"] for q in quotes] == ["FedEx Ground", "FedEx Priority Overnight"]
    assert quotes[0]["price"] == 48.5 and quotes[0]["rate_type"] == "ACCOUNT" and quotes[0]["transit"] == "2 Business Days"
    assert quotes[0]["arrives"] == "2026-11-10" and quotes[1]["price"] == 144.0
    url, kw = fed.calls[-1]
    body = kw["json"]
    assert url.endswith("/rate/v1/rates/quotes") and kw["headers"]["Authorization"] == "Bearer tok-123"
    assert body["accountNumber"] == {"value": "123456789"} and body["requestedShipment"]["recipient"]["address"]["postalCode"] == "75063"
    assert body["requestedShipment"]["requestedPackageLineItems"][0]["weight"] == {"units": "LB", "value": 80.0}
    assert "labelSpecification" not in body["requestedShipment"]                    # prices only: no shipment is created


def test_addresses_without_a_zip_and_fedex_errors_are_said_plainly(fed, client, monkeypatch):
    assert fedex.parse_destination("831 Hayter Rd NW, Edmonton, AB T6S 1A1") == {"postalCode": "T6S1A1", "countryCode": "CA"}
    assert client.get("/api/v1/fedex/quote", params={"address": "Somewhere near the plant"}).json()["detail"].startswith("I couldn't read a US ZIP")
    monkeypatch.setattr(fedex.httpx, "post", FakeFedEx(status=400))
    fedex._token.update(value="tok", expires=9e9)
    r = client.get("/api/v1/fedex/quote", params={"address": "1 Main St, Irving, TX 75063"})
    assert r.status_code == 400 and "Invalid postal code" in r.json()["detail"]


def test_dispatch_shows_live_fedex_prices_when_set_up_and_estimates_when_not_or_down(fed, monkeypatch):
    from app.services.logistics_dispatcher import logistics_dispatcher

    live = logistics_dispatcher.get_carrier_quotes("HQ", "Receiving, 1 Main St, Irving, TX 75063")
    assert [q.carrier_name for q in live if q.service_level.startswith("Live FedEx")] == ["FedEx Ground", "FedEx Priority Overnight"]
    assert any(q.carrier_name == "FedEx Freight Priority" and "estimate" in q.service_level for q in live)   # freight stays an estimate
    monkeypatch.setattr(fedex.httpx, "post", lambda *a, **k: (_ for _ in ()).throw(httpx.ConnectError("down")))
    fedex._token.update(value=None, expires=0.0)
    down = logistics_dispatcher.get_carrier_quotes("HQ", "Receiving, 1 Main St, Irving, TX 75063")
    assert not any(q.service_level.startswith("Live FedEx") for q in down) and any(q.carrier_name == "TForce Freight" for q in down)


def test_the_order_acknowledgment_email_never_shows_our_sales_order_number():
    from app.services.inbound_email_daemon import inbound_email_daemon as d
    from app.models.orders import OrderLineItem

    mail = d._generate_order_acknowledgment("Owens Corning", "PO-77", "SO-12345", [OrderLineItem(raw_item_text="Screen", quantity=2, unit_price=5)], "a@b.com")
    assert "PO-77" in mail and "12345" not in mail
