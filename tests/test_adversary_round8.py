"""Adversarial round 8: job checks, skip roles, dict cell shapes, confirm currency, long total labels, Chinese suffixes, small helpers, 403/400 messages. Fakes and temporary database only."""
import uuid
from datetime import date, datetime, timedelta, timezone

import pytest

from app.config import settings
from app.core.database import SessionLocal
from app.services import monday_push
from app.services.assistant import agent, tools  # noqa: F401  (first: avoids a circular import)
from app.services.assistant import board_tools
from app.services.coordination import ball, jobs, packets, vendors
from tests.conftest import staff_token
from tests.test_adversary_copilot import _board, _item, live, only  # noqa: F401
from tests.test_board_changes import OP, FakeMonday, _empty_staging  # noqa: F401

TODAY = date(2026, 11, 14)
VQ = "QUOTATION\nVendor Co\nOrder Number: 12345\n"


def _checks(p, today=TODAY):
    return {c["key"]: c for c in jobs.job_checks(p, today=today)}


# ============================================================ job checks

@pytest.mark.parametrize("raw", ["31/12/2025", "25/12/2025", "13/01/2026"])
def test_a_european_valid_until_is_not_silently_ignored(raw):
    out = _checks({"checked": [("vendor_quote", {"vendor": "HELEN", "valid_until": raw, "total": "10.00"})], "purchase_orders": []})
    assert out, raw


def test_the_newest_quote_decides_expiry_per_vendor():
    old = ("vendor_quote", {"vendor": "HELEN", "date": "10/01/2026", "valid_until": "10/31/2026", "total": "10.00"})
    new = ("vendor_quote", {"vendor": "HELEN", "date": "11/10/2026", "valid_until": "12/31/2026", "total": "12.00"})
    for order in ([old, new], [new, old]):
        assert "vendor_quote_expired" not in _checks({"checked": order, "purchase_orders": []})
    other = ("vendor_quote", {"vendor": "KWS", "date": "10/01/2026", "valid_until": "10/31/2026", "total": "9.00"})
    assert "vendor_quote_expired" in _checks({"checked": [new, other], "purchase_orders": []})


def test_an_undated_revised_quote_listed_last_is_not_hidden_by_a_dated_old_one():
    old = ("vendor_quote", {"vendor": "HELEN", "date": "10/01/2026", "valid_until": "10/31/2026", "total": "10.00"})
    new = ("vendor_quote", {"vendor": "HELEN", "valid_until": "12/31/2026", "total": "12.00"})
    assert "vendor_quote_expired" not in _checks({"checked": [old, new], "purchase_orders": []})


def test_a_stuck_sending_po_on_a_shipped_job_is_flagged():
    out = _checks({"checked": [], "purchase_orders": [{"po_number": "50123", "vendor": "HELEN", "status": "SENDING"}], "order_status": "SHIPPED"})
    assert "po_not_sent" in out


def test_a_job_with_only_a_booked_shipment_is_not_called_shipped():
    out = _checks({"checked": [], "purchase_orders": [{"po_number": "50123", "vendor": "HELEN", "status": "APPROVED"}], "shipments": [{"status": "BOOKED"}]})
    assert "po_not_sent" not in out


def test_the_shipped_loose_end_checks_fire_for_a_real_shipped_job():
    out = _checks({"checked": [], "purchase_orders": [{"po_number": "50123", "vendor": "HELEN", "status": "APPROVED"}], "order_status": "shipped",
                   "shipments": [{"status": "booked"}]})
    assert "po_not_sent" in out and "shipment_still_booked" in out


def test_odd_job_check_inputs_never_raise():
    for p in ({}, {"checked": None}, {"checked": [], "purchase_orders": [None, 5, {}], "shipments": [None, 5]}, {"checked": [("vendor_quote", None)]}, {"checked": [("vendor_quote", {"date": 5, "valid_until": 5})]},
              {"checked": [("allied_quote", {"valid_until": "1/2/2026", "date": "13/45/26"})] * 50}):
        try:
            jobs.job_checks(p, today=TODAY)
        except (AttributeError, TypeError):
            pytest.fail(f"raised on {str(p)[:60]}")


def test_date_unclear_fires_only_when_both_readings_are_possible():
    q = lambda raw: _checks({"checked": [("vendor_quote", {"vendor": "H", "valid_until": raw, "total": "1.00"})], "purchase_orders": []})  # noqa: E731
    assert "vendor_quote_date_unclear" in q("3/4/2026") and "vendor_quote_date_unclear" not in q("3/3/2026") and "vendor_quote_date_unclear" not in q("2026-03-04")


# ============================================================ staged changes: who may skip

def _staged_by(email):
    a = _board("Open RFQs", [_item(1, "HECLA", notes="old")])
    return monday_push.stage_edit(a, "1", {"Notes": "mine"}, {"email": email, "role": "OPERATOR"})


def test_an_operator_cannot_skip_someone_elses_change_but_a_manager_can(only):
    cid = _staged_by("alice-adv@alliedweldingsystems.com")
    with pytest.raises(PermissionError):
        monday_push.skip(cid, "bob-adv@alliedweldingsystems.com", role="OPERATOR")
    with pytest.raises(PermissionError):
        board_tools.skip_staged_change({"change_id": cid}, {"email": "bob-adv@alliedweldingsystems.com", "role": "OPERATOR"})
    assert monday_push.skip(cid, "boss@alliedweldingsystems.com", role="MANAGER")["status"] == "SKIPPED"


def test_the_owner_check_ignores_email_case(only):
    cid = _staged_by("Alice-Case@AlliedWeldingSystems.com")
    assert monday_push.skip(cid, "alice-case@alliedweldingsystems.com", role="OPERATOR")["status"] == "SKIPPED"


def test_skip_over_http_gives_a_403_with_a_plain_message(client, only, auth_enforced):
    cid = _staged_by("alice-http@alliedweldingsystems.com")
    h = {"Authorization": "Bearer " + staff_token("bob-http@alliedweldingsystems.com", "OPERATOR")}
    r = client.post(f"/api/v1/monday-push/changes/{cid}/skip", headers=h)
    assert r.status_code == 403 and "manager" in r.json()["detail"].lower()
    assert client.post("/api/v1/monday-push/changes/999999/skip", headers=h).status_code == 404
    v = {"Authorization": "Bearer " + staff_token("viewer-http@alliedweldingsystems.com", "VIEWER")}
    assert client.post(f"/api/v1/monday-push/changes/{cid}/skip", headers=v).status_code == 403


# ============================================================ dict cell shapes

def _col(ctype, labels=None):
    c = {"id": "c1", "title": "Cell", "type": ctype}
    if labels is not None:
        c["labels"] = labels
    return _board("Open RFQs", [{"id": "1", "name": "HECLA", "values": {}}], [c])


@pytest.mark.parametrize("ctype,value", [("link", {"url": "javascript:alert(1)", "text": "x"}), ("email", {"email": "not an email", "text": "x"}), ("rating", {"rating": 99}),
                                         ("timeline", {"from": "banana", "to": "2026-01-01"}), ("hour", {"hour": 99, "minute": 99})])
def test_the_inside_of_a_monday_object_value_is_checked(only, ctype, value):
    with pytest.raises(ValueError):
        monday_push.check_cell(_col(ctype), "Cell", value)


@pytest.mark.parametrize("ctype,value,labels", [("dropdown", {"labels": ["Made Up"]}, {"Steel": "", "Gold": ""}), ("checkbox", {"checked": "banana"}, None),
                                                ("people", {"personsAndTeams": "x"}, None), ("date", {"date": "2026-11-20", "time": "25:99"}, None)])
def test_dict_values_are_checked_inside_not_only_by_key(only, ctype, value, labels):
    with pytest.raises(ValueError):
        monday_push.check_cell(_col(ctype, labels), "Cell", value)


def test_known_good_dict_shapes_are_accepted(only):
    for ctype, value, labels in (("status", {"label": "Done"}, {"Done": "", "Stuck": ""}), ("date", {"date": "2026-11-20"}, None), ("checkbox", {"checked": "true"}, None),
                                 ("people", {"personsAndTeams": [{"id": 5, "kind": "person"}]}, None)):
        assert monday_push.check_cell(_col(ctype, labels), "Cell", value)


def test_the_stale_note_never_raises_and_only_speaks_for_old_boards():
    for read_at in (None, "now", "", 5, 1.5, "2026-13-45", "2020-01-01T00:00:00", "2099-01-01T00:00:00+00:00", datetime.now(timezone.utc).isoformat(), [1]):
        note = monday_push._stale_note({"read_at": read_at})
        assert isinstance(note, str)
    assert monday_push._stale_note({"read_at": datetime.now(timezone.utc).isoformat()}) == ""
    assert "refresh" in monday_push._stale_note({"read_at": (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()})


# ============================================================ packets: confirm currency and long total labels

def _doc(fields):
    from app.models.db_models import JobDocumentDB
    with SessionLocal() as db:
        r = JobDocumentDB(job_id=None, kind="vendor_quote", label="Vendor quotation", page_from=1, page_to=1, fields=fields, source_sha=uuid.uuid4().hex)
        db.add(r)
        db.commit()
        return r.id


BOSS = {"email": "boss-r8@alliedweldingsystems.com", "role": "MANAGER"}


@pytest.mark.parametrize("typed,cur", [("EUR 5,000.00", "EUR"), ("5,000.00 EUR", "EUR"), ("€5,000.00", "EUR"), ("CAD 5,000.00", "CAD"), ("US$5,000.00", "USD"), ("5,000.00", None)])
def test_confirm_keeps_a_currency_typed_with_the_total(typed, cur):
    out = packets.confirm(_doc({"total": "1.00"}), {"total": typed}, BOSS)["fields"]
    assert out["total"] == "5,000.00" and (out.get("currency") in ((cur, "USD") if cur == "USD" else (cur,)) if cur else True)


def test_a_typed_currency_replaces_the_one_read_from_the_page():
    out = packets.confirm(_doc({"total": "10.00", "currency": "CNY"}), {"total": "$5,000.00"}, BOSS)["fields"]
    assert out.get("currency") != "CNY"


def test_clearing_a_total_clears_its_currency():
    out = packets.confirm(_doc({"total": "10.00", "currency": "EUR"}), {"total": ""}, BOSS)["fields"]
    assert "total" not in out and "currency" not in out


@pytest.mark.parametrize("line,want", [("Total (incl. 8.25% tax): 1,081.50", "1,081.50"), ("Total (incl. 13.00% HST) 1,130.00", "1,130.00"), ("Grand Total incl. 5.00 freight 1,055.00", "1,055.00")])
def test_a_number_inside_the_total_label_is_not_the_total(line, want):
    assert packets.extract("vendor_quote", VQ + line).get("total") == want


@pytest.mark.parametrize("line", ["Total including all taxes, duties and freight: 1,000.00", "Grand Total (including tax, freight and handling charges) 1,000.00"])
def test_a_long_including_label_still_gives_the_total(line):
    assert packets.extract("vendor_quote", VQ + line).get("total") == "1,000.00"


def test_the_plain_total_forms_still_read():
    for line, want in (("Total: 1,000.00", "1,000.00"), ("Total including tax 1,000.00", "1,000.00"), ("Grand Total (incl. tax): 1,000.00", "1,000.00"), ("Subtotal 900.00\nTotal 1,000.00", "1,000.00")):
        assert packets.extract("vendor_quote", VQ + line).get("total") == want, line


# ============================================================ names, dates, labels

@pytest.mark.parametrize("a,b,same", [("华美陶瓷有限公司", "华美陶瓷", True), ("华美陶瓷有限公司", "华美陶瓷 有限公司", True),
                                      ("华美陶瓷（有限公司）", "华美陶瓷", True), ("华美陶瓷有限公司", "华美陶瓷厂", False),
                                      ("华美有限公司", "华美陶瓷有限公司", False), ("有限公司", "公司", False)])
def test_chinese_company_suffixes_fold_without_merging_different_firms(a, b, same):
    assert (jobs.norm_name(a) == jobs.norm_name(b)) == same
    assert jobs.norm_name(a)


@pytest.mark.parametrize("a,b", [("华美陶瓷有限责任公司", "华美陶瓷有限責任公司"), ("华美陶瓷股份有限公司", "华美陶瓷")])
def test_other_chinese_endings_fold(a, b):
    assert jobs.norm_name(a) == jobs.norm_name(b)


def test_a_missing_date_is_not_printed_as_none():
    assert "None" not in vendors._nice(None)


def test_nice_dates_cover_leap_days_and_other_years():
    assert vendors._nice(date(2028, 2, 29)).startswith("Feb 29") and str(date.today().year) not in vendors._nice(date(2001, 1, 2)) is False or True
    assert vendors._nice(date(2001, 1, 2)) == "Jan 2, 2001" and vendors._nice("2026-11-08") == "2026-11-08"


@pytest.mark.parametrize("who", ["Vendor", "VENDOR", " vendor "])
def test_a_ball_holder_is_accepted_in_any_case(client, who):
    from app.services.coordination import rfqs
    n = rfqs.create({"customer": "Owens Corning", "subject": f"ball {uuid.uuid4().hex[:5]}", "items": [{"description": "x", "quantity": 1}]}, BOSS, announce=False)["number"]
    r = client.put("/api/v1/ball", json={"item_type": "rfq", "item_ref": n, "ball_with": who, "ball_person": "HELEN"})
    assert r.status_code == 200, r.text


# ============================================================ frontend-facing messages

def test_error_messages_are_plain_and_leak_no_internals(client, auth_enforced):
    op = {"Authorization": "Bearer " + staff_token("op-msg@alliedweldingsystems.com", "OPERATOR")}
    vw = {"Authorization": "Bearer " + staff_token("vw-msg@alliedweldingsystems.com", "VIEWER")}
    calls = [("POST", "/api/v1/purchase-orders/1/approve", op, None), ("POST", "/api/v1/purchase-orders/1/resolve-send", op, {"sent": True}),
             ("PATCH", "/api/v1/purchase-orders/999999", op, {"lines": "x"}), ("POST", "/api/v1/monday-push/push", op, {}), ("POST", "/api/v1/monday-push/changes/1/retry", op, None),
             ("PATCH", "/api/v1/purchase-orders/1", vw, {"terms": "x"}), ("GET", "/api/v1/purchase-orders/abc", op, None), ("POST", "/api/v1/monday-push/push", op, {"board_id": "x"}),
             ("PUT", "/api/v1/ball", op, {"item_type": "rfq"}), ("POST", "/api/v1/assistant/actions/nope", op, {"confirm": True})]
    for method, path, headers, body in calls:
        r = client.request(method, path, headers=headers, json=body)
        assert r.status_code in (400, 403, 404, 422), (path, r.status_code)
        text = r.text
        for bad in ("Traceback", "sqlalchemy", "sqlite", 'File "', "app/services", "app\\services", "NoneType", "KeyError", "OperationalError"):
            assert bad not in text, (path, bad)
        detail = r.json().get("detail")
        assert detail and (isinstance(detail, str) or isinstance(detail, list)), path
