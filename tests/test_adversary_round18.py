"""Round 18 (adversary): import paperwork (commercial invoice / packing list) and the PO approval limit. Temporary database and fakes only.
Confirmed holes are marked xfail(strict=False, reason="ADV18-Tn ...")."""
import uuid

import pytest
from reportlab import rl_config

from app.config import settings
from app.core.database import SessionLocal
from app.models.db_models import PurchaseOrderDB
from app.services import import_paperwork as ip
from app.services.assistant import tools  # noqa: F401  (import order: tools before board_tools)
from app.services.coordination import jobs, purchase_orders, rfqs
from tests.conftest import staff_token

BOSS = {"email": "boss-a18@alliedweldingsystems.com", "role": "MANAGER"}
BOSS2 = {"email": "boss2-a18@alliedweldingsystems.com", "role": "MANAGER"}
OP = {"email": "op-a18@alliedweldingsystems.com", "role": "OPERATOR"}
ADMIN = {"email": "admin-a18@alliedweldingsystems.com", "role": "ADMIN"}

FULL = {"quantity": 4, "item": "LINER-1", "description": "Mill liner", "unit_cost": 25, "hts_code": "7326.90.8588", "origin": "PRC", "uom": "PC", "net_kg": 12.5}


def _job(customer="Owens Corning", user=BOSS):
    r = rfqs.create({"customer": customer, "subject": f"a18 {uuid.uuid4().hex[:6]}", "items": [{"description": "liner", "quantity": 2}]}, user, announce=False)
    j = jobs.from_rfq(r["id"], user)
    jobs.approve_design(j["id"], user)
    return r, j


def _po(lines, vendor=None, incoterm="FOB", customer="Owens Corning", user=BOSS):
    _r, j = _job(customer, user)
    po = purchase_orders.create(j["id"], vendor or f"HELEN-{uuid.uuid4().hex[:4]}", user)
    purchase_orders.update(po["id"], {"lines": lines, "incoterm": incoterm}, user)
    return po["id"]


def _set_lines(po_id, lines):
    """Lines stored as an RFQ-converted (non-strict) PO would hold them, straight into the temporary database."""
    with SessionLocal() as db:
        row = db.get(PurchaseOrderDB, po_id)
        row.lines = lines
        db.commit()


@pytest.fixture()
def plain(monkeypatch):
    monkeypatch.setattr(rl_config, "pageCompression", 0)


def _h(email, role):
    return {"Authorization": f"Bearer {staff_token(email, role)}"}


def _try_get(client, path, headers):
    try:
        return client.get(path, headers=headers).status_code
    except Exception:  # noqa: BLE001 - an unhandled crash is what we are looking for
        return 500


# =============================================================== import paperwork

@pytest.mark.parametrize("field,text", [("description", "Cast at Zibo Foundry, plant 2"), ("item", "ZIBO-FOUNDRY-77"), ("description", "for Owens Corning line 4"), ("item", "OWENS-CORNING-A")])
def test_t1_packing_list_names_neither_vendor_nor_customer_even_in_line_text(plain, field, text):
    po = _po([{**FULL, field: text}], vendor="ZIBO FOUNDRY")
    pdf = ip.packing_list_pdf(ip.build(po)).lower()
    assert b"zibo" not in pdf and b"owens" not in pdf


def test_the_packing_list_has_no_vendor_customer_sales_order_or_price_in_its_plain_fields(plain):
    po = _po([FULL], vendor="ZIBO FOUNDRY")
    pdf = ip.packing_list_pdf(ip.build(po, "CA", "CAD"))
    for bad in (b"ZIBO", b"Owens", b"25.00", b"100.00", b"SO-", b"Sales Order"):
        assert bad not in pdf


@pytest.mark.parametrize("lines", [[{"quantity": 1, "item": "X", "description": "no details"}], []])
def test_t2_an_incomplete_packing_list_says_so_on_its_face(plain, lines):
    info = ip.build(_po(lines))
    assert not info["complete"]
    assert b"INCOMPLETE" in ip.packing_list_pdf(info)


@pytest.mark.parametrize("qty,want", [("10 pcs", 50.0), ("ten", 50.0), (0, 0.0)])
def test_t3_total_net_weight_is_not_silently_wrong_for_odd_quantities(qty, want):
    po = _po([{"quantity": 1, "item": "X", "description": "d", "net_kg": 5}])
    _set_lines(po, [{"qty": qty, "quantity": qty, "item": "X", "description": "d", "net_kg": 5.0}])
    out = ip.build(po)
    assert out["total_net_kg"] == want or any(m["field"] == "quantity" for m in out["missing"])


@pytest.mark.parametrize("raw", ["CHN", "Shenzhen, China", "Product of China", "Made in P.R.C.", "ＣＨＩＮＡ", "中国制造", "China (Mainland)", "PEOPLE’S REPUBLIC OF CHINA", "Ontario, Canada"])
def test_t4_every_common_way_of_writing_china_or_canada_is_recognised(raw):
    want = "Canada" if "anada" in raw else "China"
    assert ip.country(raw) == want


def test_t5_the_invoice_does_not_print_prices_the_check_says_are_missing(plain):
    po = _po([{**FULL, "unit_cost": 25}])
    _set_lines(po, [{"qty": 4, "quantity": 4, "item": "A", "description": "d", "unit_cost": "nan", "amount": "６００００", "hts_code": "7326.90.8588", "origin": "China", "uom": "PC", "net_kg": 1.0}])
    info = ip.build(po)
    assert any(m["field"] == "amount" for m in info["missing"])
    pdf = ip.commercial_invoice_pdf(info)
    assert b"(nan)" not in pdf and b"(60,000.00)" not in pdf


_T8 = None          # fixed: ADV18-T8, a long item is clipped when drawn


@pytest.mark.parametrize("item", [pytest.param("X" * 6000, marks=[]), pytest.param("ÀÉÎ" * 800, marks=[]), pytest.param("中文" * 600, marks=[]),
                                  "lone " + chr(0xD800) + " surrogate", "a" + chr(0) + "b" + chr(12) + "c" + chr(27) + "[31m",
                                  "<b>bold</b>&amp;<para>", pytest.param("word " * 3000, marks=[])], ids=["x6000", "accents", "cjk", "surrogate", "control", "markup", "words"])
def test_t8_huge_or_weird_item_text_still_makes_every_document(plain, item):
    po = _po([FULL])
    _set_lines(po, [{"qty": 1, "quantity": 1, "item": item, "description": item[:500], "hts_code": "7326.90.8588", "origin": "China", "uom": "PC", "net_kg": 1.0, "unit_cost": 1, "amount": 1.0}])
    info = ip.build(po)
    assert purchase_orders.pdf(po).startswith(b"%PDF")
    assert ip.packing_list_pdf(info).startswith(b"%PDF") and ip.commercial_invoice_pdf(info).startswith(b"%PDF")


def test_t8_a_long_item_is_refused_or_clipped_when_the_draft_is_saved():
    po = _po([FULL])
    try:
        purchase_orders.update(po, {"lines": [{"quantity": 1, "item": "X" * 3000, "description": "d", "unit_cost": 1}]}, BOSS)
    except jobs.JobError:
        return
    assert len(purchase_orders.get(po)["lines"][0]["item"]) <= 500


def test_t6_paperwork_for_a_draft_po_is_marked_draft(plain):
    po = _po([FULL])
    assert purchase_orders.get(po)["status"] == "DRAFT"
    info = ip.build(po)
    assert b"DRAFT" in ip.commercial_invoice_pdf(info) and b"DRAFT" in ip.packing_list_pdf(info)


@pytest.mark.parametrize("cur", ["ZZZ", "XXX", "ABC"])
def test_t7_an_unknown_currency_code_is_refused(cur):
    with pytest.raises(jobs.JobError):
        ip.build(_po([FULL]), "US", cur)


def test_zero_line_po_still_builds_and_lists_it():
    out = ip.build(_po([]))
    assert out["lines"] == [] and not out["complete"] and any(m["field"] == "lines" for m in out["missing"])


def test_nan_and_inf_quantities_and_weights_do_not_crash_the_totals(plain):
    po = _po([FULL])
    _set_lines(po, [{"qty": float("nan"), "quantity": float("nan"), "item": "A", "description": "d", "net_kg": float("inf"), "unit_cost": float("inf"), "amount": float("nan")},
                    {"qty": float("inf"), "quantity": float("inf"), "item": "B", "description": "d", "net_kg": 2.0}])
    info = ip.build(po)
    assert info["total_net_kg"] == info["total_net_kg"] and abs(info["total_net_kg"]) != float("inf")
    assert ip.packing_list_pdf(info).startswith(b"%PDF") and ip.commercial_invoice_pdf(info).startswith(b"%PDF")


def test_a_cancelled_po_is_refused_on_every_route_and_a_missing_one_is_404(client, auth_enforced):
    op = _h("op-a18@alliedweldingsystems.com", "OPERATOR")
    po = _po([FULL])
    purchase_orders.approve(po, BOSS)
    purchase_orders.cancel(po, "customer cancelled", BOSS)
    for path in ("import-paperwork", "commercial-invoice.pdf", "packing-list.pdf"):
        assert client.get(f"/api/v1/purchase-orders/{po}/{path}", headers=op).status_code == 400
        assert client.get(f"/api/v1/purchase-orders/0/{path}", headers=op).status_code in (404, 422)
        assert client.get(f"/api/v1/purchase-orders/-5/{path}", headers=op).status_code in (404, 422)


def test_anonymous_and_viewer_get_nothing_and_odd_query_values_are_never_a_crash(client, auth_enforced):
    po = _po([FULL])
    op, viewer = _h("op-a18@alliedweldingsystems.com", "OPERATOR"), _h("viewer-a18@alliedweldingsystems.com", "VIEWER")
    for path in ("import-paperwork", "commercial-invoice.pdf", "packing-list.pdf"):
        base = f"/api/v1/purchase-orders/{po}/{path}"
        assert client.get(base).status_code in (401, 403)
        assert client.get(base, headers=viewer).status_code == 403
        for q in ("destination=us&currency=usd", "destination=CA&currency=cad", "destination=U", "destination=USA", "destination=%EF%BC%B5%EF%BC%B3", "destination=&currency=",
                  "currency=U%00D", "currency=%C3%9F%C3%9F%C3%9F", "destination=US&destination=CA", "destination=%00%00", "currency=%E2%82%AC"):
            assert _try_get(client, f"{base}?{q}", op) in (200, 400, 422), q


# =============================================================== approval limit

def _limit(monkeypatch, n=50000.0):
    monkeypatch.setattr(settings, "PO_APPROVAL_LIMIT", n)


@pytest.mark.parametrize("amount", ["$60,000", "1e5", "６００００", "60_000", "60,000 USD", "¥500,000"])
def test_l1_an_amount_the_pdf_prints_as_money_counts_toward_the_limit(monkeypatch, amount):
    _limit(monkeypatch)
    with pytest.raises(jobs.JobError):          # an amount that is not a readable number is refused when saved; a readable one is counted at approval
        po = _po([{"quantity": 1, "item": "X", "description": "big", "amount": amount}])
        purchase_orders.approve(po, BOSS)


def test_l2_text_quantity_with_a_big_unit_cost_cannot_slip_under_the_limit(monkeypatch):
    _limit(monkeypatch)
    po = _po([{"quantity": "10 pcs", "item": "X", "description": "big", "unit_cost": 10000}])
    assert purchase_orders.get(po)["total"] == 0.0
    with pytest.raises(jobs.JobError):
        purchase_orders.approve(po, BOSS)


def test_l3_amount_must_agree_with_quantity_times_unit_cost_or_the_larger_counts(monkeypatch):
    _limit(monkeypatch)
    po = _po([{"quantity": 100, "item": "X", "description": "big", "unit_cost": 10000, "amount": 1}])
    with pytest.raises(jobs.JobError):
        purchase_orders.approve(po, BOSS)


def test_l4_a_credit_line_does_not_hide_the_gross_value_of_the_po(monkeypatch):
    _limit(monkeypatch)
    po = _po([{"quantity": 1, "item": "A", "description": "big", "amount": 90000}, {"quantity": 1, "item": "B", "description": "credit", "amount": -60000}])
    with pytest.raises(jobs.JobError):
        purchase_orders.approve(po, BOSS)


@pytest.mark.parametrize("bad", [float("nan"), float("inf")])
def test_l5_a_non_finite_amount_is_refused_on_a_draft(bad):
    with pytest.raises(jobs.JobError):
        _po([{"quantity": 1, "item": "X", "description": "d", "amount": bad}])


def test_l6_the_limit_also_holds_for_a_po_sent_without_a_record(monkeypatch):
    from app.services.outbound_email_service import outbound_email_service

    sent = []
    monkeypatch.setattr(outbound_email_service, "send_purchase_order", lambda **kw: sent.append(kw) or {"status": "SENT"})
    _limit(monkeypatch)
    args = {"to": "helen@zbsupereal.com", "vendor_name": f"NOREC-{uuid.uuid4().hex[:4]}", "po_number": "98765001", "body": "see {po_number}",
            "lines": [{"quantity": 1, "description": "big", "unit_cost": 100000}]}
    with pytest.raises(Exception):
        tools.send_vendor_po(args, BOSS)
    assert not sent


def test_l7_numbering_from_an_rfq_still_needs_a_manager():
    r, _j = _job()
    with pytest.raises(jobs.JobError):
        purchase_orders.number_for_rfq(r["number"], "OPVENDOR", OP)


def test_l8_self_approval_by_plus_address_is_still_flagged():
    po = _po([FULL], user={"email": "same.person@alliedweldingsystems.com", "role": "MANAGER"})
    out = purchase_orders.approve(po, {"email": "same.person+2@alliedweldingsystems.com", "role": "MANAGER"})
    assert out["approved_by_creator"] is True


def test_over_the_limit_refusal_leaves_a_clean_draft_on_every_approval_route(monkeypatch):
    _limit(monkeypatch, 50.0)
    r, j = _job()
    vendor = f"CLEAN-{uuid.uuid4().hex[:4]}"
    po = purchase_orders.create(j["id"], vendor, BOSS)
    purchase_orders.update(po["id"], {"lines": [FULL]}, BOSS)
    for call in (lambda: purchase_orders.approve(po["id"], BOSS), lambda: purchase_orders.number_for_rfq(r["number"], vendor, BOSS),
                 lambda: __import__("app.services.assistant.board_tools", fromlist=["x"]).approve_po({"po_id": po["id"]}, BOSS)):
        with pytest.raises(Exception):
            call()
        got = purchase_orders.get(po["id"])
        assert got["status"] == "DRAFT" and not got["po_number"]
    assert purchase_orders.approve(po["id"], ADMIN)["status"] == "APPROVED"


def test_lines_cannot_be_raised_after_approval_by_any_route(monkeypatch, client, auth_enforced):
    _limit(monkeypatch, 500.0)
    po = _po([{"quantity": 1, "item": "X", "description": "d", "unit_cost": 100}])
    purchase_orders.approve(po, BOSS)
    op = _h("op-a18@alliedweldingsystems.com", "OPERATOR")
    for body in ({"lines": [{"quantity": 1, "item": "X", "description": "d", "unit_cost": 9999}]}, {"lines": [{"quantity": 50, "item": "X", "description": "d", "unit_cost": 100}]},
                 {"lines": [{"quantity": 1, "item": "X", "description": "d", "unit_cost": 100, "amount": 99999}]}):
        assert client.patch(f"/api/v1/purchase-orders/{po}", json=body, headers=op).status_code == 400
    c = client.patch(f"/api/v1/purchase-orders/{po}/customs", json={"lines": [{"line": 1, "amount": 99999, "unit_cost": 1, "quantity": 99, "origin": "China"}]}, headers=op)
    assert c.status_code == 200 and purchase_orders.get(po)["total"] == 100.0 and purchase_orders.get(po)["lines"][0]["unit_cost"] == 100


def test_the_flag_is_set_for_same_person_in_any_letter_case_and_a_second_person_clears_it():
    po = _po([FULL], user={"email": "Casey.Boss@alliedweldingsystems.com", "role": "MANAGER"})
    assert purchase_orders.approve(po, {"email": " casey.boss@ALLIEDWELDINGSYSTEMS.com ", "role": "MANAGER"})["approved_by_creator"] is True
    po2 = _po([FULL], user=BOSS)
    assert purchase_orders.approve(po2, BOSS2)["approved_by_creator"] is False


def test_a_resolve_send_cannot_approve_a_draft():
    po = _po([FULL])
    with pytest.raises(jobs.JobError):
        purchase_orders.resolve_send(po, True, BOSS)
    assert purchase_orders.get(po)["status"] == "DRAFT"
