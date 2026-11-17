"""Excel / CSV / PDF downloads and live exchange rates. Test data only; the FX service is a fake."""

import csv
import io
import uuid

import httpx
import pytest
from openpyxl import load_workbook

from app.config import settings
from app.services import exports, fx
from app.services.coordination import boards
from tests.conftest import staff_token

EVIL = '=HYPERLINK("http://evil","x")'


@pytest.fixture(autouse=True)
def _app_started(client):
    """Starting the app creates the tables."""


def _rows(data):
    return list(load_workbook(io.BytesIO(data)).active.iter_rows(values_only=True))


def _make_order(po=None):
    from app.models.orders import InboundOrderRequest
    from app.services.order_orchestrator import OrderOrchestrator

    po = po or f"PO-{uuid.uuid4().hex[:6].upper()}"
    OrderOrchestrator().process_inbound_order(InboundOrderRequest(
        customer_name="Owens Corning", customer_po_number=po, shipping_address="831 Hayter Rd NW, Edmonton, AB T6S 1A1",
        line_items=[{"raw_item_text": "Hammermill Screen Allied 300XL", "quantity": 1, "unit_price": 410}]))
    return po


def test_every_list_downloads_as_excel_csv_and_pdf(client):
    _make_order()
    for name in exports.DATASETS:
        for fmt, magic in (("xlsx", b"PK"), ("csv", b"\xef\xbb\xbf"), ("pdf", b"%PDF")):
            r = client.get(f"/api/v1/exports/{name}", params={"format": fmt})
            assert r.status_code == 200 and r.content.startswith(magic), (name, fmt)
            assert f"allied_{name}_" in r.headers["content-disposition"] and r.headers["content-disposition"].endswith(f'.{fmt}"')
    assert {x["id"] for x in client.get("/api/v1/exports").json()["lists"]} == set(exports.DATASETS)


def test_the_excel_file_has_a_header_the_data_and_the_customer_po(client):
    po = _make_order()
    rows = _rows(client.get("/api/v1/exports/orders").content)
    assert rows[0][0] == "Customer PO" and any(r[0] == po for r in rows[1:])


def test_formula_text_in_a_cell_is_never_left_to_run(client):
    _make_order(EVIL)
    rows = _rows(client.get("/api/v1/exports/orders").content)
    cell = next(r[0] for r in rows[1:] if "evil" in str(r[0]))
    assert cell.startswith("'=") and not cell.startswith("=")
    text = client.get("/api/v1/exports/orders", params={"format": "csv"}).content.decode("utf-8-sig")
    assert "'=HYPERLINK" in text


def test_csv_opens_in_excel_with_accents_and_commas_intact():
    data = exports.to_csv([("a", "Name"), ("b", "Note")], [{"a": "Müller, Jörg", "b": 'say "hi"'}]).decode("utf-8-sig")
    assert list(csv.reader(io.StringIO(data))) == [["Name", "Note"], ["Müller, Jörg", 'say "hi"']]


def test_margins_and_costs_are_for_managers_only(auth_enforced, client):
    def tok(role):
        return {"Authorization": f"Bearer {staff_token(f'{role.lower()}{uuid.uuid4().hex[:4]}@alliedweldingsystems.com', role)}"}

    assert client.get("/api/v1/exports/orders").status_code in (401, 403)
    assert client.get("/api/v1/exports/orders", headers=tok("VIEWER")).status_code == 403
    op = _rows(client.get("/api/v1/exports/orders", headers=tok("OPERATOR")).content)[0]
    mg = _rows(client.get("/api/v1/exports/orders", headers=tok("MANAGER")).content)[0]
    assert "Margin %" not in op and "Cost" not in op and "Margin %" in mg and "Cost" in mg
    assert client.get("/api/v1/exports", headers=tok("VIEWER")).status_code == 200


def test_bad_names_and_formats_are_plain_errors(client):
    assert client.get("/api/v1/exports/nope").status_code == 404
    r = client.get("/api/v1/exports/orders", params={"format": "docx"})
    assert r.status_code == 400 and "Choose a format" in r.json()["detail"]
    assert client.get("/api/v1/exports/boards/does-not-exist").status_code == 404


def test_a_monday_board_copy_downloads_with_its_columns_group_and_app_view(client):
    bid = str(uuid.uuid4().int % 10**9)
    with boards._lock:
        boards._mirror[bid] = {"board_id": bid, "name": "Open RFQs / Test", "role": "other", "read_at": "now",
                               "groups": [{"id": "g1", "title": "This week"}],
                               "columns": [{"id": "status", "title": "Status", "type": "status"}, {"id": "t", "title": "Notes", "type": "text"}],
                               "items": [{"id": "1", "name": "ACME", "group": "g1", "group_title": "This week",
                                          "values": {"status": "Working", "t": "=cmd|' /C calc'!A0"}}]}
    try:
        r = client.get(f"/api/v1/exports/boards/{bid}")
        rows = _rows(r.content)
        assert rows[0][:4] == ("Item", "Group", "Status", "Notes") and rows[1][:3] == ("ACME", "This week", "Working")
        assert str(rows[1][3]).startswith("'=")
        assert "board-open-rfqs-test" in r.headers["content-disposition"]
        assert client.get(f"/api/v1/exports/boards/{bid}", params={"format": "pdf"}).content[:4] == b"%PDF"
    finally:
        with boards._lock:
            boards._mirror.pop(bid, None)


def test_a_big_wide_list_still_makes_all_three_files():
    cols = [(f"c{i}", f"Column {i}") for i in range(14)]
    rows = [{f"c{i}": ("x" * 200 if i == 3 else i * j) for i in range(14)} for j in range(300)]
    for fmt in ("xlsx", "csv", "pdf"):
        assert len(exports.render(fmt, "Big / list: [1]", cols, rows)) > 1000
    assert len(_rows(exports.to_xlsx("Big", cols, rows))) == 301


def test_empty_lists_and_odd_values_do_not_break_a_download():
    assert exports.render("xlsx", "Empty", [("a", "A")], [])[:2] == b"PK"
    odd = [{"a": None, "b": {"k": 1}, "c": [1, 2], "d": True, "e": "\x00bad"}]
    got = _rows(exports.to_xlsx("Odd", [(k, k) for k in "abcde"], odd))[1]
    assert got[1:] == ("k: 1", "1, 2", "Yes", "bad")


# ------------------------------------ exchange rates ------------------------------------


def _reply(status=200, **kw):
    return httpx.Response(status, request=httpx.Request("GET", "http://x"), **kw)


@pytest.fixture
def live(monkeypatch):
    monkeypatch.setattr(settings, "FX_LIVE_RATES", True)
    fx._cache.update(at=float("-inf"), cad=None, cny=None, date=None)
    calls = []

    def get(url, params=None, timeout=None):
        calls.append(params)
        return _reply(json={"date": "2026-11-06", "rates": {"CAD": 1.25, "CNY": 5.0}})

    monkeypatch.setattr(fx.httpx, "get", get)
    yield calls
    fx._cache.update(at=float("-inf"), cad=None, cny=None, date=None)


def test_live_rates_are_used_cached_and_sent_no_allied_data(live):
    assert fx.cad_to_usd() == 0.8 and fx.cny_to_usd() == 0.2
    fx.status()
    assert len(live) == 1 and live[0] == {"base": "USD", "symbols": "CAD,CNY"}
    s = fx.status()
    assert s["source"] == "live" and s["as_of"] == "2026-11-06"


def test_quotes_and_customs_use_the_live_rate(live):
    from app.services.logistics_dispatcher import logistics_dispatcher as d

    assert d.calculate_tri_currency_rates(100) == (125.0, 500.0)


@pytest.mark.parametrize("kind", ["down", "500", "text", "wild", "empty"])
def test_any_trouble_with_the_rate_service_falls_back_to_the_fixed_rates(live, monkeypatch, kind):
    def get(*a, **k):
        if kind == "down":
            raise httpx.ConnectError("down")
        return {"500": _reply(500), "text": _reply(text="nope"), "wild": _reply(json={"rates": {"CAD": 99, "CNY": 6.7}}),
                "empty": _reply(json={"rates": {}})}[kind]

    monkeypatch.setattr(fx.httpx, "get", get)
    assert fx.cad_to_usd() == settings.CAD_TO_USD_RATE and fx.cny_to_usd() == settings.CNY_TO_USD_RATE
    assert fx.status()["source"] == "settings"


def test_a_failed_lookup_is_not_retried_on_every_quote(live, monkeypatch):
    n = []
    monkeypatch.setattr(fx.httpx, "get", lambda *a, **k: n.append(1) or (_ for _ in ()).throw(httpx.ConnectError("down")))
    for _ in range(5):
        fx.cad_to_usd()
    assert len(n) == 1


def test_switched_off_means_no_call_at_all(monkeypatch):
    monkeypatch.setattr(settings, "FX_LIVE_RATES", False)
    monkeypatch.setattr(fx.httpx, "get", lambda *a, **k: (_ for _ in ()).throw(AssertionError("called out")))
    assert fx.cad_to_usd() == settings.CAD_TO_USD_RATE
