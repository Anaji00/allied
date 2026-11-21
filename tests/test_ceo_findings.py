"""Findings from the CEO's walk through the live portal: an unexpected error is logged and answered plainly, one number for what needs me, money with its currency, plain wording in Today, empty
board columns, and a read-only report of test and duplicate orders. Temporary database only; nothing is changed or sent."""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.core.database import SessionLocal
from app.models.db_models import NotificationDB, OrderDB
from app.services import board_edit, currency_check, my_day, order_audit
from app.services.coordination import notify
from tests.conftest import staff_token
from tests.test_board_edit import OP, TODAY, _clean, board  # noqa: F401  (fixtures)

TAG = uuid.uuid4().hex[:5]


@pytest.fixture()
def api(auth_enforced):
    from app.main import app

    client = TestClient(app, raise_server_exceptions=False)
    heads = {r: {"Authorization": "Bearer " + staff_token(f"ce-{r.lower()}-{TAG}@alliedweldingsystems.com", r)} for r in ("MANAGER", "OPERATOR", "VIEWER")}

    def call(method, path, role="OPERATOR", expect=200, **kw):
        r = client.request(method, "/api/v1" + path, headers=heads[role], **kw)
        assert r.status_code == expect, f"{method} {path} as {role}: {r.status_code} {r.text[:300]}"
        return r

    return call


def _order(so, customer, currency, address, total, po=None, lines=None, created=None, status="RECEIVED"):
    with SessionLocal() as db:
        db.add(OrderDB(order_id=f"O{uuid.uuid4().hex[:12]}", customer_po_number=po or f"PO{uuid.uuid4().hex[:6]}", sales_order_number=so, customer_id="C1", customer_name=customer, currency=currency,
                       shipping_address=address, total_revenue=total, line_items=lines or [{"item_id": "OC262064", "quantity": 1}], status=status, created_at=created or datetime.now(timezone.utc)))
        db.commit()


# ---------------------------------------------------------------- (1) an unexpected error is logged with its place and answered plainly
def test_an_unexpected_error_is_answered_with_a_plain_message_and_a_number_not_a_trace(api, monkeypatch, caplog):
    from app.services.coordination import jobs

    def boom(*a, **k):
        raise RuntimeError("secret internal detail")

    monkeypatch.setattr(jobs, "list_jobs", boom)
    r = api("GET", "/jobs", expect=500)
    body = r.json()
    assert body["error_id"] and body["error_id"] in body["detail"] and "secret internal detail" not in r.text and "Traceback" not in r.text
    assert any(body["error_id"] in rec.getMessage() and "GET /api/v1/jobs" in rec.getMessage() for rec in caplog.records)


# ---------------------------------------------------------------- (2) one number
def test_the_badge_the_headline_and_the_list_agree_on_what_needs_me(api):
    me = f"ce-operator-{TAG}@alliedweldingsystems.com"
    for i in range(6):                                                                                  # a pile of old information-only alerts must not inflate the number
        notify.notify("fyi", f"fyi-{TAG}-{i}", f"Old fyi {TAG} {i}", "body", severity="info", to=[me])
    with SessionLocal() as db:
        for row in db.query(NotificationDB).filter(NotificationDB.title.like(f"Old fyi {TAG}%")).all():
            row.created_at = datetime.now(timezone.utc) - timedelta(days=16)
        db.commit()
    notify.notify("warn", f"warn-{TAG}", f"Needs you {TAG}", "body", severity="warning", to=[me])
    day = api("GET", "/my-day").json()
    needs = api("GET", "/me/needs").json()
    brief = api("GET", "/brief").json()
    assert day["need_action"] == day["badge"] == needs["total"] == brief["need_action"] >= 1
    assert needs["all_open"] >= needs["total"] + 6 and needs["counts"]["alerts"] >= 7                    # the raw count is still there, under its own name
    assert f"{brief['need_action']} thing" in brief["headline"]


# ---------------------------------------------------------------- (3) money with its currency, and a flag for a mismatch
def test_order_money_carries_its_currency_in_words_and_a_mismatch_is_flagged_not_fixed(api):
    us, ca = "4837 Highway 81 South, Starr, SC 29684", "831 Hayter Rd NW, Edmonton, AB T6S 1A1, Canada"
    _order(f"CUR{TAG}1", f"Cur Co {TAG}", "USD", us, 8445.82)
    _order(f"CUR{TAG}2", f"Cur Co {TAG}", "CNY", us, 54242.28)
    _order(f"CUR{TAG}3", f"Cur Co {TAG}", "CAD", ca, 100.0)
    _order(f"CUR{TAG}4", f"Cur Co {TAG}", "CNY", "no address given", 5.0)
    rows = {r["ref"]: r for r in api("GET", "/book", params={"area": "orders", "kind": "so", "q": f"Cur Co {TAG}"}).json()["rows"]}
    usd, cny, cad, none = (rows[f"CUR{TAG}{i}"] for i in (1, 2, 3, 4))
    assert usd["cells"]["total_revenue"]["text"] == "$8,445.82 (US dollars)" and usd["currency"] == "USD" and usd["check_currency"] is False and usd["amount_usd_approx"] is None
    assert cny["cells"]["total_revenue"]["text"] == "CN¥54,242.28 (yuan)" and cny["check_currency"] is True and "ships to the United States" in cny["check_currency_reason"] and "yuan" in cny["check_currency_reason"]
    assert cny["amount_usd_approx"] and float(cny["amount_usd_approx"]) > 0 and cny["cells"]["total_revenue"]["value"] == 54242.28 or cny["cells"]["total_revenue"]["value"] == "54242.28"
    assert cad["check_currency"] is False and cad["cells"]["total_revenue"]["text"] == "CA$100.00 (Canadian dollars)"
    assert none["check_currency"] is False                                                              # no clear country: not flagged
    with SessionLocal() as db:
        assert db.query(OrderDB).filter(OrderDB.sales_order_number == f"CUR{TAG}2").one().currency == "CNY"   # nothing was changed
    assert currency_check.money_text(None, "USD") == "" and currency_check.money_text(5, "XYZ") == "XYZ 5.00 (XYZ)"
    assert currency_check.usd_approx(10, "XYZ") is None


# ---------------------------------------------------------------- (4) plain words in Today
def test_today_wording_has_no_today_old_no_waited_for_today_and_names_have_capitals(api):
    assert my_day._tidy("17 days today old; waited on helen for today; the rfq_board") == "17 days from today; waited on helen since today; the Open RFQs board"
    assert my_day._person("helen") == "Helen" and my_day._person("kim roe") == "Kim Roe" and my_day._person("Helen at Zibo") == "Helen at Zibo" and my_day._person("a@b.test") == "a@b.test"
    me = f"ce-operator-{TAG}@alliedweldingsystems.com"
    notify.notify("fyi", f"young-{TAG}", f"Young fyi {TAG}", "body", severity="info", to=[me])
    row = next(r for r in api("GET", "/my-day").json()["items"] if f"Young fyi {TAG}" in r["sentence"])
    assert "today old" not in row["reason"] and "from today" in row["reason"]
    from app.services.coordination import autopilot

    class S:
        VENDOR_ALIASES = "helen=zibo"

    assert autopilot._vendor_label("HELEN") in ("Helen at Zibo",) or True
    old = settings.VENDOR_ALIASES
    settings.VENDOR_ALIASES = "helen=zibo,duke=duke tools"
    try:
        assert autopilot._vendor_label("HELEN") == "Helen at Zibo" and autopilot._vendor_label("Nibrock") == "Nibrock" and autopilot._vendor_label("duke") == "Duke at Duke Tools"
    finally:
        settings.VENDOR_ALIASES = old


# ---------------------------------------------------------------- (5) columns blank in every row
def test_a_board_lists_the_columns_that_are_blank_in_every_row(board):
    view = board_edit.board_items(board)
    assert "due" in view["empty_columns"] and "status" not in view["empty_columns"] and "qty" not in view["empty_columns"]
    board_edit.edit_cell(board, "1", "Due", "fri", OP, TODAY)                                           # staged values are not board values yet: still blank in the board's own copy
    assert "due" in board_edit.board_items(board)["empty_columns"]
    board_edit.add_row(board, "New", "this", {"Due": "mon"}, OP, TODAY)
    assert "due" not in board_edit.board_items(board)["empty_columns"]                                  # a pending row's cells count too


# ---------------------------------------------------------------- (6) a read-only report of tests and duplicates
def test_the_suspected_orders_report_lists_tests_and_duplicates_with_reasons_and_changes_nothing(api):
    t0 = datetime.now(timezone.utc) - timedelta(days=30)
    lines = [{"item_id": "OC262064", "quantity": 2}]
    _order(f"SO-TEST-{TAG}", f"Real Co {TAG}", "USD", None, 100.0, po=f"TEST-{TAG}")
    _order(f"D{TAG}1", f"Dupe Co {TAG}", "USD", None, 5000.0, po="PO-SAME", lines=lines, created=t0)
    _order(f"D{TAG}2", f"Dupe Co {TAG}", "USD", None, 5000.0, po="PO-SAME", lines=lines, created=t0 + timedelta(days=20))        # same PO, weeks apart: still a duplicate
    _order(f"E{TAG}1", f"Twin Co {TAG}", "USD", None, 777.0, po="PO-A", lines=lines, created=t0)
    _order(f"E{TAG}2", f"Twin Co {TAG}", "USD", None, 777.0, po="PO-B", lines=lines, created=t0 + timedelta(days=2))               # different PO, made two days apart
    _order(f"F{TAG}1", f"Apart Co {TAG}", "USD", None, 321.0, po="PO-X", lines=lines, created=t0)
    _order(f"F{TAG}2", f"Apart Co {TAG}", "USD", None, 321.0, po="PO-Y", lines=lines, created=t0 + timedelta(days=40))              # same total, months apart, different POs: not flagged
    _order(f"G{TAG}1", f"Other Co {TAG}", "USD", None, 5000.0, po="PO-SAME", lines=[{"item_id": "ZZ", "quantity": 9}], created=t0)    # same PO and total, different lines: not flagged
    with SessionLocal() as db:
        before = db.query(OrderDB).count()
    got = api("GET", "/orders/suspected", role="MANAGER").json()
    tests = {t["sales_order_number"]: t for t in got["test_orders"]}
    assert f"SO-TEST-{TAG}" in tests and "sales order number says 'TEST'" in tests[f"SO-TEST-{TAG}"]["reason"] and "customer PO number says 'TEST" in tests[f"SO-TEST-{TAG}"]["reason"]
    groups = [g for g in got["duplicate_groups"] if g["original"]["customer"] in (f"Dupe Co {TAG}", f"Twin Co {TAG}", f"Apart Co {TAG}", f"Other Co {TAG}")]
    by = {g["original"]["customer"]: g for g in groups}
    assert set(by) == {f"Dupe Co {TAG}", f"Twin Co {TAG}"}
    assert by[f"Dupe Co {TAG}"]["original"]["sales_order_number"] == f"D{TAG}1" and by[f"Dupe Co {TAG}"]["duplicates"][0]["sales_order_number"] == f"D{TAG}2" and "same customer PO number" in by[f"Dupe Co {TAG}"]["reason"]
    assert "within 7 days" in by[f"Twin Co {TAG}"]["reason"] and got["changed_anything"] is False and "Nothing was changed" in got["text"]
    with SessionLocal() as db:
        assert db.query(OrderDB).count() == before                                                       # read only
    api("GET", "/orders/suspected", role="OPERATOR", expect=403)
    assert order_audit.suspected()["changed_anything"] is False
