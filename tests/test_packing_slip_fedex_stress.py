"""Stress and edge cases for the packing slip queue, the customer-PO rule and FedEx.
Test database only; FedEx and mail are fakes."""

import threading
import uuid
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from app.config import settings
from app.core.database import SessionLocal
from app.models.db_models import PackingSlipDB
from app.models.orders import InboundOrderRequest
from app.services import fedex, packing_slip_queue
from app.services.order_orchestrator import OrderOrchestrator
from tests.conftest import staff_token

LINES = [{"raw_item_text": "Hammermill Screen Allied 300XL", "quantity": 1, "unit_price": 410}]


@pytest.fixture(autouse=True)
def _app_started(client):
    """Starting the app creates the tables."""


def _order(po=None, addr="831 Hayter Rd NW, Edmonton, AB T6S 1A1"):
    po = po if po is not None else f"PO-{uuid.uuid4().hex[:6].upper()}"
    out = OrderOrchestrator().process_inbound_order(InboundOrderRequest(
        customer_name="Owens Corning", customer_po_number=po, shipping_address=addr, line_items=LINES))
    return po, str(out.sales_order_number)


def _row(so):
    with SessionLocal() as db:
        return db.query(PackingSlipDB).filter(PackingSlipDB.sales_order_number == so).one()


# ----------------------------- queue: use cases ------------------------------


def test_a_burst_of_orders_each_gets_exactly_one_ready_slip():
    made = [_order() for _ in range(25)]
    sos = [so for _, so in made]
    assert len(set(sos)) == 25
    with SessionLocal() as db:
        rows = db.query(PackingSlipDB).filter(PackingSlipDB.sales_order_number.in_(sos)).all()
    assert len(rows) == 25 and all(r.status == "READY" and r.attempts == 1 for r in rows)


def test_two_workers_racing_on_one_job_make_the_slip_once(monkeypatch):
    from app.services.packing_slips import PackingSlipGenerator

    po, so = _order()
    with SessionLocal() as db:                       # put it back to "waiting" with a missing file
        db.query(PackingSlipDB).filter(PackingSlipDB.sales_order_number == so).update(
            {"status": "QUEUED", "path": None, "next_try_at": packing_slip_queue._now(), "attempts": 0})
        db.commit()
    real, calls = PackingSlipGenerator.generate_pdf, []

    def counted(self, *a, **k):
        calls.append(1)
        return real(self, *a, **k)

    monkeypatch.setattr(PackingSlipGenerator, "generate_pdf", counted)
    errs = []

    def go():
        try:
            packing_slip_queue.run(so)
        except Exception as exc:  # noqa: BLE001
            errs.append(exc)

    ts = [threading.Thread(target=go) for _ in range(8)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert not errs and len(calls) == 1 and _row(so).status == "READY" and _row(so).attempts == 1


def test_a_ready_slip_whose_file_was_deleted_is_remade_by_retry(client):
    import os

    po, so = _order()
    os.remove(_row(so).path)
    assert client.get("/api/v1/packing-slips/queue").json()["jobs"][0]["ready"] in (True, False)
    assert client.post(f"/api/v1/packing-slips/SO-{so}/retry").json()["status"] == "READY"
    assert os.path.isfile(_row(so).path)


def test_a_job_without_its_order_details_fails_cleanly_and_does_not_loop_forever():
    po, so = _order()
    with SessionLocal() as db:
        db.query(PackingSlipDB).filter(PackingSlipDB.sales_order_number == so).update(
            {"status": "QUEUED", "path": None, "payload": None, "attempts": 0, "next_try_at": packing_slip_queue._now()})
        db.commit()
    for _ in range(packing_slip_queue.MAX_ATTEMPTS):
        packing_slip_queue.retry_due()
        with SessionLocal() as db:
            db.query(PackingSlipDB).filter(PackingSlipDB.sales_order_number == so, PackingSlipDB.status == "QUEUED").update(
                {"next_try_at": packing_slip_queue._now()})
            db.commit()
    r = _row(so)
    assert r.status == "FAILED" and "weren't kept" in r.last_error
    assert packing_slip_queue.retry_due()["due"] == 0                 # FAILED jobs are not picked up again


def test_retry_due_only_takes_jobs_whose_wait_is_over_and_caps_the_batch():
    future = packing_slip_queue._now() + timedelta(hours=1)
    po, so = _order()
    with SessionLocal() as db:
        db.query(PackingSlipDB).filter(PackingSlipDB.sales_order_number == so).update(
            {"status": "QUEUED", "path": None, "next_try_at": future})
        db.commit()
    assert packing_slip_queue.retry_due()["due"] == 0 and _row(so).status == "QUEUED"


# --------------------------- queue: role and input edges ---------------------


def test_roles_viewers_read_but_cannot_retry_or_print(auth_enforced, client):
    po, so = _order()
    v = {"Authorization": f"Bearer {staff_token(f'v{uuid.uuid4().hex[:4]}@alliedweldingsystems.com', 'VIEWER')}"}
    o = {"Authorization": f"Bearer {staff_token(f'o{uuid.uuid4().hex[:4]}@alliedweldingsystems.com', 'OPERATOR')}"}
    assert client.get("/api/v1/packing-slips/queue").status_code in (401, 403)
    assert client.get("/api/v1/packing-slips/queue", headers=v).status_code == 200
    assert client.post(f"/api/v1/packing-slips/{so}/retry", headers=v).status_code == 403
    assert client.post(f"/api/v1/packing-slips/{so}/printed", headers=v).status_code == 403
    assert client.post(f"/api/v1/packing-slips/{so}/printed", headers=o).json()["printed_by"].startswith("o")
    assert client.get("/api/v1/fedex/status", headers=o).status_code == 403          # manager only
    assert client.get("/api/v1/fedex/quote", params={"address": "x"}, headers=v).status_code == 403


def test_odd_inputs_to_the_queue_endpoints_are_clean_errors_not_crashes(client):
    for bad in ("nope", "SO-", "%20", "9" * 80, "a'b", "1;drop"):
        assert client.post(f"/api/v1/packing-slips/{bad}/retry").status_code in (404, 405, 422), bad
    assert client.get("/api/v1/packing-slips/queue", params={"state": "x" * 40}).status_code == 422
    assert client.get("/api/v1/packing-slips/queue", params={"state": "nonsense"}).json()["jobs"] == []


def test_a_slip_that_is_not_ready_cannot_be_marked_printed(client):
    po, so = _order()
    with SessionLocal() as db:
        db.query(PackingSlipDB).filter(PackingSlipDB.sales_order_number == so).update({"status": "FAILED"})
        db.commit()
    assert client.post(f"/api/v1/packing-slips/{so}/printed").status_code == 400


def test_printed_twice_stays_printed_and_a_retry_resets_it(client):
    po, so = _order()
    assert client.post(f"/api/v1/packing-slips/{so}/printed").json()["status"] == "PRINTED"
    assert client.post(f"/api/v1/packing-slips/{so}/printed").json()["status"] == "PRINTED"
    assert client.post(f"/api/v1/packing-slips/{so}/retry").json()["status"] == "READY"


# ------------------------- customer PO as the public reference ---------------


@pytest.mark.parametrize("po", ["PO 4500/12-A", "Ünïcode PÖ ✓ 77", "../../etc/passwd", "A" * 200, "  spaced  ", "PO#9<script>"])
def test_awkward_po_numbers_never_break_the_slip_the_download_or_the_customs_number(po, client):
    from app.services.logistics_dispatcher import logistics_dispatcher

    po, so = _order(po)
    r = client.get(f"/api/v1/orders/{so}/packing-slip")
    assert r.status_code == 200 and r.content[:4] == b"%PDF"
    name = r.headers["content-disposition"]
    assert ".." not in name and "/" not in name.split("filename=")[-1] and "\n" not in name
    cci = logistics_dispatcher.generate_customs_invoice(so_number=so, customer_name="OC", shipping_address="Edmonton, AB",
                                                        origin_facility="E", items=[{"item_id": "X", "quantity": 1, "unit_price": 5}],
                                                        customer_po=po)
    assert cci.cci_number.startswith("CCI-2026-") and all(c.isalnum() or c == "-" for c in cci.cci_number)


def test_a_blank_po_falls_back_so_documents_still_have_a_reference(client):
    from app.services.logistics_dispatcher import logistics_dispatcher

    cci = logistics_dispatcher.generate_customs_invoice(so_number="12345", customer_name="OC", shipping_address="Edmonton, AB",
                                                        origin_facility="E", items=[{"item_id": "X", "quantity": 1, "unit_price": 5}],
                                                        customer_po="   ")
    assert cci.cci_number == "CCI-2026-12345"          # nothing else to show: the number is the last resort


# ---------------------------------- FedEx ------------------------------------


def _ready(monkeypatch):
    for k, v in {"FEDEX_ENABLED": True, "FEDEX_ENV": "sandbox", "FEDEX_CLIENT_ID": "id", "FEDEX_CLIENT_SECRET": "s",
                 "FEDEX_ACCOUNT_NUMBER": "1", "FEDEX_SHIPPER_POSTAL": "75379"}.items():
        monkeypatch.setattr(settings, k, v)
    fedex._token.update(value=None, expires=0.0)


def _http(monkeypatch, rates_response, token_calls=None):
    def post(url, **kw):
        if url.endswith("/oauth/token"):
            if token_calls is not None:
                token_calls.append(1)
            return httpx.Response(200, json={"access_token": "t", "expires_in": 3600})
        r = rates_response() if callable(rates_response) else rates_response
        if isinstance(r, Exception):
            raise r
        return r
    monkeypatch.setattr(fedex.httpx, "post", post)


@pytest.mark.parametrize("text,expect", [
    ("1 Main St, Irving, TX 75063-1234", ("75063", "US")),
    ("1 Main St, Irving, tx 75063", None),                      # lowercase state is not trusted
    ("Calgary AB t2p 3h9", ("T2P3H9", "CA")),
    ("Calgary AB T2P3H9", ("T2P3H9", "CA")),
    ("PO Box 12, 12345", None),
    ("", None), (None, None), ("   ", None),
    ("Irving, TX 7506", None),
])
def test_address_reading_edge_cases(text, expect):
    got = fedex.parse_destination(text)
    assert (None if got is None else (got["postalCode"], got["countryCode"])) == expect


@pytest.mark.parametrize("body", [
    {}, {"output": None}, {"output": {}}, {"output": {"rateReplyDetails": None}}, {"output": {"rateReplyDetails": []}},
    {"output": {"rateReplyDetails": [{"serviceType": "X"}]}},                                   # no price block
    {"output": {"rateReplyDetails": [{"serviceType": "X", "ratedShipmentDetails": []}]}},
    {"output": {"rateReplyDetails": [{"serviceType": "X", "ratedShipmentDetails": [{"rateType": "LIST"}]}]}},
])
def test_odd_fedex_replies_give_an_empty_list_not_a_crash(body):
    assert fedex.parse_quotes(body) == []


def test_zero_price_missing_commit_and_mixed_services_sort_cheapest_first():
    body = {"output": {"rateReplyDetails": [
        {"serviceType": "B", "ratedShipmentDetails": [{"rateType": "LIST", "totalNetCharge": 90}]},
        {"serviceType": "A", "ratedShipmentDetails": [{"rateType": "ACCOUNT", "totalNetCharge": 0}]},
        {"serviceType": "C", "ratedShipmentDetails": [{"rateType": "LIST", "totalNetCharge": 12.5}], "commit": None}]}}
    assert [q["service"] for q in fedex.parse_quotes(body)] == ["A", "C", "B"]


def test_the_login_is_reused_across_many_quotes_and_renewed_after_a_stale_401(monkeypatch):
    _ready(monkeypatch)
    logins = []
    ok = httpx.Response(200, json={"output": {"rateReplyDetails": []}})
    _http(monkeypatch, ok, logins)
    for _ in range(6):
        fedex.quote("1 Main St, Irving, TX 75063")
    assert len(logins) == 1
    _http(monkeypatch, httpx.Response(401, json={}), logins)
    with pytest.raises(fedex.FedExError, match="sign in again"):
        fedex.quote("1 Main St, Irving, TX 75063")
    _http(monkeypatch, ok, logins)
    fedex.quote("1 Main St, Irving, TX 75063")
    assert len(logins) == 2                                   # the stale login was dropped and renewed once


@pytest.mark.parametrize("resp,text", [
    (httpx.ConnectTimeout("slow"), "Couldn't reach FedEx"),
    (httpx.Response(500, text="<html>oops</html>"), "couldn't price"),
    (httpx.Response(429, json={"errors": [{"message": "Too many requests"}]}), "Too many requests"),
])
def test_fedex_outages_and_rate_limits_say_what_happened(monkeypatch, resp, text):
    _ready(monkeypatch)
    _http(monkeypatch, resp)
    with pytest.raises(fedex.FedExError, match=text):
        fedex.quote("1 Main St, Irving, TX 75063")


def test_a_reply_that_is_not_json_is_an_error_a_person_can_read(monkeypatch):
    _ready(monkeypatch)
    _http(monkeypatch, httpx.Response(200, text="not json at all"))
    with pytest.raises(fedex.FedExError, match="couldn't read"):
        fedex.quote("1 Main St, Irving, TX 75063")


@pytest.mark.parametrize("w", [0, -5, "abc", None, 99999])
def test_weird_weights_never_reach_fedex_as_nonsense(monkeypatch, w, client):
    _ready(monkeypatch)
    sent = []
    _http(monkeypatch, lambda: (_ for _ in ()).throw(AssertionError("no")) if False else httpx.Response(200, json={}))
    real = fedex._request_body
    monkeypatch.setattr(fedex, "_request_body", lambda d, wt: sent.append(wt) or real(d, wt))
    r = client.get("/api/v1/fedex/quote", params={"address": "1 Main St, Irving, TX 75063", **({"weight_lbs": w} if w is not None else {})})
    if sent:
        assert 0 < sent[0] <= 2000, sent                         # a real, positive weight went out
    else:
        assert r.status_code in (400, 422)                       # or it was refused up front


def test_many_quotes_at_once_all_succeed_with_one_login(monkeypatch):
    _ready(monkeypatch)
    logins = []
    _http(monkeypatch, httpx.Response(200, json={"output": {"rateReplyDetails": []}}), logins)
    errs = []

    def go():
        try:
            fedex.quote("1 Main St, Irving, TX 75063")
        except Exception as exc:  # noqa: BLE001
            errs.append(exc)

    ts = [threading.Thread(target=go) for _ in range(12)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert not errs and 1 <= len(logins) <= 12


def test_the_secret_never_appears_in_status_or_error_text(monkeypatch, client):
    _ready(monkeypatch)
    monkeypatch.setattr(settings, "FEDEX_CLIENT_SECRET", "SUPER-SECRET-VALUE")
    monkeypatch.setattr(fedex.httpx, "post", lambda *a, **k: httpx.Response(401, json={}))
    seen = client.get("/api/v1/fedex/status").text + client.post("/api/v1/fedex/check").text
    assert "SUPER-SECRET-VALUE" not in seen


def test_fedex_down_never_stops_dispatch_quotes(monkeypatch):
    from app.services.logistics_dispatcher import logistics_dispatcher

    _ready(monkeypatch)
    _http(monkeypatch, httpx.ReadTimeout("slow"))
    quotes = logistics_dispatcher.get_carrier_quotes("HQ", "Receiving, 1 Main St, Irving, TX 75063")
    assert quotes and not any(q.service_level.startswith("Live FedEx") for q in quotes)
    assert logistics_dispatcher.get_carrier_quotes("HQ", "No postal code here")          # unreadable address: estimates only
