"""Round 19b (adversary): re-attack of the Sage importer fixes. Temporary database only. xfail(strict=False, reason="ADV19b-Tn ...") marks open holes."""
import threading
import uuid

import pytest

from app.core.database import SessionLocal
from app.models.db_models import SageOpenOrderDB
from app.services import sage_import as si

TAG = uuid.uuid4().hex[:6].upper()
MGR = {"email": "mgr-a19b@alliedweldingsystems.com", "role": "MANAGER"}
HEAD = "SO/Proposal Number,Date,Customer ID,Customer PO,Amount"


def _csv(*lines):
    return ("\r\n".join(lines) + "\r\n").encode()


def _u(p="X"):
    return f"{p}{TAG}{uuid.uuid4().hex[:5].upper()}"


def _commit(data, kind="auto", name="f.csv"):
    return si.commit(si.preview(data, name, kind, MGR)["token"], MGR)


def _open(number):
    with SessionLocal() as db:
        x = db.query(SageOpenOrderDB).filter(SageOpenOrderDB.kind == "SO", SageOpenOrderDB.number == number).first()
        return x is not None and (x.open_amount is None or x.open_amount > 0)


def test_t1_a_short_export_cannot_close_most_of_the_open_orders():
    sos = [_u("S") for _ in range(6)]
    _commit(_csv(HEAD, *[f"{s},2026-11-01,C1,{_u('CP')},10.00" for s in sos]), "open_sales_orders")
    _commit(_csv(HEAD, f"{sos[0]},2026-11-01,C1,{_u('CP')},10.00"), "open_sales_orders", "short.csv")
    assert sum(_open(s) for s in sos) >= 5


@pytest.mark.parametrize("cell", ["C$1,000.00", "CA$500", "HK$100", "MX$100", "S$5", "A$9", "CAD$5"])
def test_t2_a_prefixed_dollar_sign_is_not_us_dollars(cell):
    row = si.parse(_csv("Vendor Invoice #,Vendor ID,Date,Invoice Amount", f"{_u('B')},V1,2026-11-20,\"{cell}\""), "b.csv")["rows"][0]
    assert row["problems"] or "amount" not in row["values"]


@pytest.mark.parametrize("cell", ["US$5.00", "$5.00", "USD 5.00", "5.00", "$ 5.00", "usd 5"])
def test_dollars_still_read(cell):
    row = si.parse(_csv("Vendor Invoice #,Vendor ID,Date,Invoice Amount", f"{_u('B')},V1,2026-11-20,\"{cell}\""), "b.csv")["rows"][0]
    assert not row["problems"] and row["values"]["amount"] == "5.00"


def test_two_different_files_with_the_same_new_invoice_committed_at_once_never_crash():
    inv = _u("I")
    a = si.preview(_csv("Invoice/CM #,Customer ID,Date,Invoice Total", f"{inv},C1,2026-11-20,5.00"), "a.csv", "auto", MGR)
    b = si.preview(_csv("Invoice/CM #,Customer ID,Date,Invoice Total", f"{inv},C1,2026-11-20,5.00", f"{_u('I')},C1,2026-11-20,6.00"), "b.csv", "auto", MGR)
    errs = []

    def go(t):
        try:
            si.commit(t["token"], MGR)
        except si.ImportRefused:
            pass
        except Exception as e:  # noqa: BLE001
            errs.append(type(e).__name__)

    ts = [threading.Thread(target=go, args=(t,)) for t in (a, b, a, b)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert not errs, errs


def test_an_order_with_a_problem_row_is_not_closed_by_the_newer_export():
    s1, s2 = _u("S"), _u("S")
    _commit(_csv(HEAD, f"{s1},2026-11-01,C1,{_u('CP')},10.00", f"{s2},2026-11-01,C1,{_u('CP')},10.00"), "open_sales_orders")
    _commit(_csv(HEAD, f"{s1},not-a-date,C1,{_u('CP')},10.00", f"{s2},2026-11-01,C1,{_u('CP')},10.00"), "open_sales_orders", "p.csv")
    assert _open(s1)


def test_the_same_file_with_another_date_order_is_a_fresh_import_and_the_same_order_twice_is_not():
    data = _csv("Invoice/CM #,Customer ID,Date,Invoice Total", f"{_u('I')},C1,03/04/2026,5.00")
    assert _commit(data, "invoices")["created"] == 0 or True
    first = si.commit(si.preview(data, "d.csv", "invoices", MGR, "mdy")["token"], MGR)
    again = si.commit(si.preview(data, "d.csv", "invoices", MGR, "mdy")["token"], MGR)
    assert again["already_imported"] and not first["already_imported"]


def test_order_money_status_still_matches_the_same_customer_by_name_in_any_case_and_form():
    po, so = _u("PO"), _u("SO")
    _commit(_csv("Invoice/CM #,Customer ID,Customer Name,Date,Invoice Total,Customer PO", f"{_u('I')},OWEN1,Owens Corning Starr Composites,2026-11-20,100.00,{po}"), "invoices")
    for name in ("Owens Corning Starr Composites", "OWENS CORNING STARR COMPOSITES", " owens corning starr composites "):
        assert si.order_money_status([{"sales_order_number": so, "customer_po_number": po, "customer": name}]), name
    assert not si.order_money_status([{"sales_order_number": so, "customer_po_number": po, "customer": "Nibrock Inc"}])


def test_c1_a_file_held_back_from_closing_can_be_committed_again_with_confirm():
    sos = [_u("S") for _ in range(6)]
    _commit(_csv(HEAD, *[f"{s},2026-11-01,C1,{_u('CP')},10.00" for s in sos]), "open_sales_orders")
    short = _csv(HEAD, f"{sos[0]},2026-11-01,C1,{_u('CP')},10.00")
    first = si.commit(si.preview(short, "s.csv", "open_sales_orders", MGR)["token"], MGR)
    assert first.get("not_closed")
    again = si.commit(si.preview(short, "s.csv", "open_sales_orders", MGR)["token"], MGR, confirm_close=True)
    assert not again["already_imported"] and sum(_open(s) for s in sos) == 1


def test_c2_confirm_close_over_http_needs_a_real_true_and_a_manager(client, auth_enforced):
    from tests.conftest import staff_token
    H = lambda e, r: {"Authorization": f"Bearer {staff_token(e, r)}"}
    op, mg = H("o-a19c@alliedweldingsystems.com", "OPERATOR"), H("m-a19c@alliedweldingsystems.com", "MANAGER")
    sos = [_u("S") for _ in range(6)]
    _commit(_csv(HEAD, *[f"{s},2026-11-01,C1,{_u('CP')},10.00" for s in sos]), "open_sales_orders")
    for val in ("true", 1, "yes", [True]):
        t = client.post("/api/v1/sage-import/preview", files={"file": ("s.csv", _csv(HEAD, f"{sos[0]},2026-11-01,C1,{_u('CP')},10.00"))}, headers=op).json()["token"]
        assert client.post("/api/v1/sage-import/commit", json={"token": t, "confirm_close": val}, headers=op).status_code == 403
        r = client.post("/api/v1/sage-import/commit", json={"token": t, "confirm_close": val}, headers=mg)
        assert r.status_code == 200 and sum(_open(s) for s in sos) == 6, val


def _ids(n):
    return [_u("S") for _ in range(n)]


def _full(sos):
    return _csv(HEAD, *[f"{s},2026-11-01,C1,{_u('CP') if False else 'CPX'+s},10.00" for s in sos])


def test_d1_replaying_an_old_complete_file_with_confirm_does_not_close_newer_orders():
    a, extra = _ids(2), _ids(1)
    old = _full(a)
    _commit(old, "open_sales_orders", "old.csv")
    _commit(_full(a + extra), "open_sales_orders", "new.csv")
    assert _open(extra[0])
    si.commit(si.preview(old, "old.csv", "open_sales_orders", MGR)["token"], MGR, confirm_close=True)
    assert _open(extra[0])


def test_d2_a_held_back_file_confirmed_after_a_newer_export_does_not_close_what_the_newer_one_opened():
    base = _ids(6)
    _commit(_full(base), "open_sales_orders", "base.csv")
    short = _full(base[:1])
    held = si.commit(si.preview(short, "short.csv", "open_sales_orders", MGR)["token"], MGR)
    assert held.get("not_closed")
    newer = _ids(1)
    _commit(_full(base + newer), "open_sales_orders", "newer.csv")
    si.commit(si.preview(short, "short.csv", "open_sales_orders", MGR)["token"], MGR, confirm_close=True)
    assert _open(newer[0]) and sum(_open(s) for s in base) >= 5


def test_d3_confirming_twice_closes_once_and_the_second_does_nothing():
    base = _ids(6)
    _commit(_full(base), "open_sales_orders", "b.csv")
    short = _full(base[:1])
    si.commit(si.preview(short, "s.csv", "open_sales_orders", MGR)["token"], MGR)
    one = si.commit(si.preview(short, "s.csv", "open_sales_orders", MGR)["token"], MGR, confirm_close=True)
    two = si.commit(si.preview(short, "s.csv", "open_sales_orders", MGR)["token"], MGR, confirm_close=True)
    assert one["closed"] >= 5 and two["closed"] == 0
