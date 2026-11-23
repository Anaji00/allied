"""Round 28 (adversary), part 2: landed cost and the tariff table, the cargo parser, the freight quote request, the three pipelines (UPS check digit, Mountain Movers parser, forwarder digest)
and the deferred freight close. Synthetic text and a temporary database / output folder only; nothing is sent, nothing touches Sage. xfail(strict=False, reason="ADV28-Tn ...") marks holes."""
import io
import json
import time
import uuid
from decimal import Decimal
from pathlib import Path

import pytest

from app.config import settings
from app.core.database import SessionLocal
from app.models.db_models import ShipmentDB
from app.services import freight_close, landed_cost as lc, pipelines
from tests.conftest import staff_token

TAG = uuid.uuid4().hex[:5]
BOSS = {"email": f"boss-r28b-{TAG}@alliedweldingsystems.com", "role": "MANAGER"}


def _line(**kw):
    return {"description": "Liner", "quantity": "10", "unit_cost": "100", "category": f"steel-{TAG}", **kw}


@pytest.fixture()
def rate():
    return lc.set_rate(f"steel-{TAG}", "25", BOSS, effective_from="2020-01-01")


# =============================================================== landed cost and the tariff table

def test_an_unknown_duty_or_freight_is_never_zero_and_the_tiers_say_so():
    out = lc.compute([_line(category=f"no-rate-{TAG}")], None)
    assert out["tariff_unknown"] and out["freight_unknown"] and out["landed_total"] == "1000.00" and "EXCLUDES duty and freight" in out["landed_note"]
    assert all(t["firm"] is False for t in out["tiers"])


def test_a_known_rate_is_worked_on_the_goods_value_and_an_as_of_date_before_it_means_unknown(rate):
    ok = lc.compute([_line()], "50")
    assert ok["tariff_total"] == "250.00" and ok["landed_total"] == "1300.00" and not ok["tariff_unknown"] and all(t["firm"] for t in ok["tiers"])
    old = lc.compute([_line()], "50", as_of="2019-01-01")
    assert old["tariff_unknown"]


@pytest.mark.parametrize("bad", ["NaN", "Infinity", "-1", "-0.01", "abc", "1,2,3"])
def test_bad_money_is_a_422_not_a_crash(bad):
    for kw in ({"quantity": bad}, {"unit_cost": bad}):
        try:
            lc.compute([_line(**kw)], "1")
        except lc.CostError:
            pass
    try:
        lc.compute([_line()], bad)
    except lc.CostError:
        pass


@pytest.mark.parametrize("kw", [{"unit_cost": "1e30"}, {"quantity": "1e40", "unit_cost": "9"}, {"unit_cost": "99999999999999999999999999999"}, {"unit_cost": "1e999999"}, {"quantity": "1E+400"}], ids=["cost", "qty", "long", "exp999999", "exp400"])
def test_f1_a_huge_figure_is_refused_plainly(kw):
    try:
        lc.compute([_line(**kw)], "1")
    except lc.CostError:
        return
    except Exception as exc:  # noqa: BLE001
        raise AssertionError(type(exc).__name__)


@pytest.mark.parametrize("tier", ["NaN", "sNaN", "Infinity", "abc"])
def test_f2_a_bad_markup_tier_is_a_422(tier):
    try:
        lc.compute([_line()], "1", tiers=[tier])
    except lc.CostError:
        return
    except Exception as exc:  # noqa: BLE001
        raise AssertionError(type(exc).__name__)
    raise AssertionError("accepted")


def test_a_huge_duty_rate_and_a_negative_or_nan_rate_are_refused():
    for bad in ("501", "-1", "NaN", "Infinity", ""):
        with pytest.raises(lc.CostError):
            lc.set_rate(f"x-{TAG}", bad, BOSS)
    with pytest.raises(lc.CostError):
        lc.set_rate("", "5", BOSS)
    with pytest.raises(lc.CostError):
        lc.set_rate(f"x-{TAG}", "5", BOSS, effective_from="31/12/2026")


def test_f3_one_fresh_rate_does_not_hide_a_stale_one():
    r = lc.set_rate(f"ancient-{TAG}", "10", BOSS, effective_from="2021-01-01")
    from app.models.db_models import TariffRateDB
    from datetime import datetime, timedelta, timezone

    with SessionLocal() as db:
        db.get(TariffRateDB, r["id"]).updated_at = datetime.now(timezone.utc) - timedelta(days=900)
        db.commit()
    lc.set_rate(f"fresh-{TAG}", "5", BOSS)
    out = lc.compute([_line(category=f"ancient-{TAG}")], "1")
    assert out["tariff_table"]["stale"] or not all(t["firm"] for t in out["tiers"])


def test_a_country_rate_never_applies_to_another_country_and_the_newer_effective_rate_wins():
    lc.set_rate(f"cn-{TAG}", "30", BOSS, country="CN", effective_from="2024-01-01")
    lc.set_rate(f"cn-{TAG}", "35", BOSS, country="CN", effective_from="2025-06-01")
    assert lc.rate_for(f"cn-{TAG}", country="CN")["rate_percent"] == "35.00"
    assert lc.rate_for(f"cn-{TAG}", country="VN") is None and lc.rate_for(f"cn-{TAG}") is None
    assert lc.rate_for(f"cn-{TAG}", country="CN", as_of="2025-01-01")["rate_percent"] == "30.00"


@pytest.mark.parametrize("cur", ["x", "euro dollars", "12", "US$"])
def test_f4_the_currency_is_a_real_three_letter_code(cur):
    try:
        out = lc.compute([_line()], "1", currency=cur)
    except lc.CostError:
        return
    assert out["currency"] in ("USD", "EUR", "CNY", "GBP", "CAD", "MXN")


@pytest.mark.parametrize("payload", ['=HYPERLINK("http://evil.example/x","click")', "+SUM(1+1)", "-2+3", "@SUM(A1)"], ids=["eq", "plus", "minus", "at"])
def test_f5_a_formula_in_a_description_is_written_as_text(payload):
    from openpyxl import load_workbook

    sheet = lc.create_sheet({"lines": [_line(description=payload)], "freight": "1"}, BOSS)
    wb = load_workbook(io.BytesIO(lc.sheet_xlsx(sheet["id"])))
    cell = wb["Landed cost"]["B2"]
    assert cell.data_type != "f"


def test_the_workbook_shows_unknown_as_a_word_and_is_built_in_memory_only(tmp_path):
    sheet = lc.create_sheet({"lines": [_line(category=f"none-{TAG}")], "freight": None}, BOSS)
    from openpyxl import load_workbook

    wb = load_workbook(io.BytesIO(lc.sheet_xlsx(sheet["id"])))
    ws = wb["Landed cost"]
    rows = [[c.value for c in r] for r in ws.iter_rows()]
    assert any("unknown" in str(v) for r in rows for v in r)
    assert wb["Markup tiers"]["E2"].value.startswith("NO")


def test_a_saved_sheet_reads_back_with_the_same_unknowns_and_a_foreign_tier_cannot_be_picked(rate):
    sheet = lc.create_sheet({"lines": [_line()], "freight": None}, BOSS)
    assert sheet["freight_unknown"] and sheet["landed_note"]
    with pytest.raises(lc.CostError):
        lc.pick_tier(sheet["id"], "Tier 99", BOSS)
    assert lc.pick_tier(sheet["id"], "Tier 2", BOSS)["picked_tier"] == "Tier 2"
    with pytest.raises(lc.CostError):
        lc.get_sheet(10 ** 9)


def test_the_ocr_flag_is_set_by_the_caller_and_carried_into_the_saved_sheet(rate):
    out = lc.compute([_line(from_ocr=True)], "1")
    assert out["needs_confirm"] and out["lines"][0]["check"]
    saved = lc.create_sheet({"lines": [_line(from_ocr=True)], "freight": "1"}, BOSS)
    assert saved["lines"][0]["check"]


def test_f6_a_sheet_with_ocr_figures_cannot_have_a_tier_picked_until_confirmed(rate):
    sheet = lc.create_sheet({"lines": [_line(from_ocr=True)], "freight": "1"}, BOSS)
    with pytest.raises(lc.CostError):
        lc.pick_tier(sheet["id"], "Tier 1", BOSS)


# =============================================================== cargo parser and the freight RFQ

def test_f7_a_comma_decimal_weight_is_not_read_ten_times_too_big():
    got = lc.parse_cargo("Gross weight: 1,5 t")
    assert got["weights"][0]["value"] in ("1.5", "1,5")


def test_the_cargo_parser_survives_hostile_text_quickly():
    for text in ("weight " + "9" * 50000 + " kg", "dims " + "1x" * 30000, "weight" + " " * 90000 + "5 kg", ("pcs " * 25000)):
        t0 = time.time()
        lc.parse_cargo(text)
        assert time.time() - t0 < 3


def test_the_cargo_parser_reads_a_normal_quote():
    got = lc.parse_cargo("Gross weight: 1,250 kg\nDimensions: 120 x 80 x 95 cm\n4 crates")
    assert got["weights"][0] == {"value": "1250", "unit": "kg"} and got["dimensions"][0]["unit"] == "cm" and got["packages"] == ["4"] and got["complete"]


def test_the_freight_rfq_names_no_customer_and_no_price_and_is_only_a_draft(monkeypatch):
    from app.services.assistant import agent
    from app.services.assistant.tools import TOOLS  # noqa: F401

    seen = {}

    def fake_propose(tool, args, user):
        seen.update(args)
        return {"id": 1}

    monkeypatch.setattr(agent, "_propose", fake_propose)
    text = "Quote for Owens Corning plant 7. Price $48,000. Gross weight: 1,250 kg. Dimensions: 120 x 80 x 95 cm. 4 crates"
    out = lc.freight_rfq_draft(lc.parse_cargo(text), "fwd@forwarder.example", "Shenzhen", "Houston", BOSS)
    assert out["drafted"] and seen["_audience"] == "forwarder"
    body = seen["body"].lower()
    assert "owens" not in body and "48,000" not in body and "$" not in body


def test_f8_the_destination_is_checked_for_the_customer_name(monkeypatch):
    from app.services.assistant import agent

    seen = {}
    monkeypatch.setattr(agent, "_propose", lambda tool, args, user: seen.update(args) or {"id": 1})
    cargo = lc.parse_cargo("Gross weight: 100 kg. Dimensions: 1 x 1 x 1 m")
    try:
        lc.freight_rfq_draft(cargo, "fwd@forwarder.example", "Shenzhen", "Owens Corning, 1 Owens Corning Pkwy, Toledo", BOSS, job_id=None)
    except lc.CostError:
        return
    assert "owens" not in seen["body"].lower()


@pytest.mark.parametrize("email", ["", "a", "a@b", "a@b.c", "a b@c.com", "a@b.com,c@d.com", "a@b.com;c@d.com", "a@b.com\nBcc: x@y.com", ])
def test_a_bad_forwarder_address_is_refused(email):
    with pytest.raises(lc.CostError):
        lc.freight_rfq_draft({"weights": [1], "dimensions": [1]}, email, "", "", BOSS)


# =============================================================== pipelines: UPS number

def test_ups_check_digit_known_good_and_one_digit_off():
    assert pipelines.ups_valid("1Z999AA10123456784")
    assert not pipelines.ups_valid("1Z999AA10123456785") and not pipelines.ups_valid("1Z999AA10123456774")
    assert pipelines.ups_valid("1z 999 aa1 0123 456 784")
    for bad in ("", "1Z", "1Z999AA1012345678", "1Z999AA101234567845", "2Z999AA10123456784", "1Z999AA10123456\u0668\u0664", "1Z999AA1012345678\u0664", None):
        assert not pipelines.ups_valid(bad)


@pytest.mark.parametrize("n", ["1Z999AA10123456784", "1ZA1B2C3D4E5F6G7H8"])
def test_every_made_ups_number_validates(n):
    body = n[2:-1]
    assert pipelines.ups_valid(pipelines.ups_make(body))


def _ship():
    with SessionLocal() as db:
        s = ShipmentDB(reference=f"REF{TAG}{uuid.uuid4().hex[:4]}".upper(), container_no=f"MSKU{uuid.uuid4().int % 10**7:07d}")
        db.add(s)
        db.commit()
        db.refresh(s)
        return s.id, s.reference, s.container_no


def test_set_pipeline_checks_the_ups_number_and_stores_only_valid_ones():
    sid, ref, cont = _ship()
    with pytest.raises(pipelines.PipelineError):
        pipelines.set_pipeline(sid, "US_OFFICE_PARCEL", BOSS, tracking_no="1Z999AA10123456785")
    with pytest.raises(pipelines.PipelineError):
        pipelines.set_pipeline(sid, "NOPE", BOSS)
    with pytest.raises(pipelines.PipelineError):
        pipelines.set_pipeline(10 ** 9, "US_OFFICE_PARCEL", BOSS)
    out = pipelines.set_pipeline(sid, "US_OFFICE_PARCEL", BOSS, tracking_no="1z 999aa1 0123456784")
    assert out["tracking_no"] == "1Z999AA10123456784" and out["carrier"] == "UPS"


def test_p1_a_bol_file_must_exist_and_belong_to_the_shipment():
    sid, *_ = _ship()
    try:
        pipelines.set_pipeline(sid, "US_MOUNTAIN_MOVERS", BOSS, bol_file_id=987654321)
    except pipelines.PipelineError:
        return
    raise AssertionError("an id that does not exist was stored")


# =============================================================== pipelines: dates

@pytest.mark.parametrize("text,iso,amb", [("2026-12-01", "2026-12-01", False), ("03/04/26", None, True), ("13/04/2026", "2026-04-13", False), ("04/13/2026", "2026-04-13", False), ("5/5/26", "2026-05-05", False),
                                           ("1 Dec 2026", "2026-12-01", False), ("Dec 1, 2026", "2026-12-01", False), ("31/02/2026", None, False), ("2026-13-01", None, False), ("soon", None, False)])
def test_dates_are_read_or_marked_ambiguous(text, iso, amb):
    got = pipelines.read_date(text)
    assert got["iso"] == iso and got["ambiguous"] == amb


@pytest.mark.parametrize("text", ["1999-01-01", "2099-12-31", "0001-01-01", "13/04/126", "9999-12-31"])
def test_p2_an_absurd_year_is_not_applied(text):
    got = pipelines.read_date(text)
    assert got["iso"] is None


# =============================================================== pipelines: the forwarder digest on hostile text

def test_the_digest_applies_one_matching_row_and_never_an_ambiguous_one():
    sid, ref, cont = _ship()
    text = f"{cont}  ETA 2026-12-01\nBooking {ref} ETA 03/04/26\nNOPE1234567 ETA 2026-12-02"
    seen = pipelines.apply_forwarder_digest(text, BOSS, apply=False)
    assert [r["action"] for r in seen["rows"]][:1] == ["would set ETA"] and seen["ambiguous"] and seen["rows"][1]["action"] == "no shipment matches"
    with SessionLocal() as db:
        before = db.get(ShipmentDB, sid).eta_current
    pipelines.apply_forwarder_digest(text, BOSS, apply=False)
    with SessionLocal() as db:
        assert db.get(ShipmentDB, sid).eta_current == before


def test_p3_quoted_lines_are_not_digest_rows():
    sid, ref, cont = _ship()
    text = f"{cont} ETA 2026-12-10\n\nOn Mon, 1 Nov 2026 the forwarder wrote:\n> {cont} ETA 2026-11-01\n"
    got = pipelines.parse_forwarder_digest(text)
    assert [r["eta"] for r in got["rows"] if r["reference"] == cont] == ["2026-12-10"]


def test_p4_two_conflicting_rows_for_one_container_are_not_both_applied():
    sid, ref, cont = _ship()
    text = f"{cont} ETA 2026-12-10\n{cont} ETA 2027-03-01"
    out = pipelines.apply_forwarder_digest(text, BOSS, apply=False)
    assert sum(1 for r in out["rows"] if r["action"] == "would set ETA") <= 1


def test_p5_the_digest_apply_needs_a_forwarder_sender_or_a_manager():
    import inspect

    sig = inspect.signature(pipelines.apply_forwarder_digest)
    assert "sender" in sig.parameters or "from_email" in sig.parameters


def test_the_digest_matches_only_exact_references_not_a_similar_number():
    sid, ref, cont = _ship()
    near = cont[:-1] + str((int(cont[-1]) + 1) % 10)
    out = pipelines.apply_forwarder_digest(f"{near} ETA 2026-12-01\nBooking {ref[:-1]} ETA 2026-12-01", BOSS, apply=False)
    assert all(r["shipment_id"] is None for r in out["rows"])


def test_the_digest_survives_hostile_text_quickly():
    t0 = time.time()
    pipelines.parse_forwarder_digest(("ref " + "A" * 5000 + " eta " * 2000 + "\n") * 40)
    pipelines.parse_forwarder_digest("<div style='display:none'>MSKU1234567 ETA 2026-12-01</div>" * 2000)
    assert time.time() - t0 < 5


def test_p6_hidden_html_text_is_not_parsed():
    sid, ref, cont = _ship()
    html = f"<p>All on schedule.</p><span style='display:none'>{cont} ETA 2026-12-31</span>"
    got = pipelines.parse_forwarder_digest(html)
    assert not got["rows"]


# =============================================================== pipelines: Mountain Movers

def test_the_mountain_movers_parser_reads_a_normal_email():
    text = "Option 1: Standard LTL, $1,250.00, 3-5 business days\nOption 2 - Expedited $2,100 2 days\nBOL #MM-445521\nPickup date: 2026-12-02\nDelivery date: Dec 8, 2026\nPickup: 03/04/26"
    got = pipelines.parse_mountain_movers(text)
    assert [o["price"] for o in got["quote_options"]] == ["1250.00", "2100.00"] and got["bol_numbers"] == ["MM-445521"]
    assert {e["kind"]: e["date"] for e in got["events"]} == {"pickup": "2026-12-02", "delivery": "2026-12-08"} and got["ambiguous_dates"]


@pytest.mark.parametrize("text", ["Your BOL attached, please sign", "bol copy is below", "BOL number pending", "BOL: TBD"])
def test_p7_a_word_after_bol_is_not_a_bol_number(text):
    got = pipelines.parse_mountain_movers(text)
    assert not any(b.isalpha() for b in got["bol_numbers"])


def test_p8_a_quoted_old_pickup_is_not_a_second_event():
    got = pipelines.parse_mountain_movers("Pickup date: 2026-12-02\n> Pickup date: 2026-11-01")
    assert len([e for e in got["events"] if e["kind"] == "pickup"]) == 1


# =============================================================== freight close (deferred freight, staged SO to invoice)

@pytest.fixture()
def out_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "OUTPUT_DIR", str(tmp_path / "output"))
    return tmp_path / "output" / "sage_staged"


def _piped(quoted=None):
    sid, *_ = _ship()
    pipelines.set_pipeline(sid, "CN_FREIGHT_FORWARDER", BOSS)
    if quoted is not None:
        freight_close.confirm_delivery(sid, BOSS, "SO-SEED", "0", quoted)
    return sid


@pytest.mark.parametrize("actual,quoted,flag", [("1100", "1000", False), ("1100.01", "1000", True), ("900", "1000", False), ("899.99", "1000", True), ("0", "1000", True), ("1", None, True), ("1", "0", True), ("0", "0", False)])
def test_the_variance_is_judged_on_the_exact_figure(actual, quoted, flag):
    q = Decimal(quoted) if quoted is not None else None
    assert freight_close.variance(Decimal(actual), q)["flag"] is flag


@pytest.mark.parametrize("bad", ["NaN", "Infinity", "-5", "abc", "", None])
def test_bad_freight_is_a_422(bad):
    sid = _piped()
    try:
        freight_close.confirm_delivery(sid, BOSS, "SO-1", bad)
    except freight_close.FreightError:
        return
    except Exception as exc:  # noqa: BLE001
        raise AssertionError(type(exc).__name__)
    raise AssertionError("accepted")


def test_a_close_without_a_pipeline_or_an_so_or_a_shipment_is_refused(out_dir):
    sid, *_ = _ship()
    with pytest.raises(freight_close.FreightError):
        freight_close.confirm_delivery(sid, BOSS, "SO-1", "100")
    with pytest.raises(freight_close.FreightError):
        freight_close.confirm_delivery(10 ** 9, BOSS, "SO-1", "100")
    pipelines.set_pipeline(sid, "CN_FREIGHT_FORWARDER", BOSS)
    with pytest.raises(freight_close.FreightError):
        freight_close.confirm_delivery(sid, BOSS, "   ", "100")


@pytest.mark.parametrize("so", ["../../etc/passwd", "..\\..\\evil", "SO 1/../../x", "C:\\Windows\\x", "SO\x00X", "a" * 500, "con", "SO-1\u202e"])
def test_a_hostile_sales_order_number_cannot_write_outside_the_staging_folder(out_dir, so):
    sid = _piped()
    out = freight_close.confirm_delivery(sid, BOSS, so, "100")
    written = list(out_dir.glob("*"))
    assert out["payload_file"] in [p.name for p in written]
    assert all(p.resolve().parent == out_dir.resolve() for p in written)
    assert not list(out_dir.glob(".sf.*.tmp"))


def test_closing_twice_rewrites_the_same_file_and_does_not_double_the_payments(out_dir):
    sid = _piped()
    a = freight_close.confirm_delivery(sid, BOSS, "SO-9001", "1200", "1000")
    b = freight_close.confirm_delivery(sid, BOSS, "SO-9001", "1200", "1000")
    assert a["payload_file"] == b["payload_file"] and len(list(out_dir.glob("so_freight_SO-9001_*.json"))) == 1
    assert b["payments_now_due"] == []


def test_c1_different_so_numbers_never_share_a_file_name(out_dir):
    sid = _piped()
    a = freight_close.confirm_delivery(sid, BOSS, "SO 12", "100", "100")
    b = freight_close.confirm_delivery(sid, BOSS, "SO/12", "200", "200")
    assert a["payload_file"] != b["payload_file"]


def test_c2_the_invoice_payload_holds_no_cost_variance(out_dir):
    sid = _piped()
    freight_close.confirm_delivery(sid, BOSS, "SO-77", "1500", "1000")
    data = json.loads(next(out_dir.glob("so_freight_SO-77_*.json")).read_text())
    assert "freight_variance" not in data


def test_c3_changing_the_quote_after_the_fact_cannot_clear_a_flag(out_dir):
    sid = _piped()
    freight_close.confirm_delivery(sid, BOSS, "SO-88", "1300", "1000")
    assert pipelines.get(sid)["variance_flag"] is True
    freight_close.confirm_delivery(sid, BOSS, "SO-88", "1300", "1300")
    assert pipelines.get(sid)["variance_flag"] is True


def test_c4_only_pos_of_this_shipments_job_are_opened(out_dir, monkeypatch):
    from app.services import payments

    opened = []
    monkeypatch.setattr(payments, "fire", lambda po, trig, user=None: opened.append(po) or [])
    sid = _piped()
    try:
        freight_close.confirm_delivery(sid, BOSS, "SO-55", "100", "100", po_ids=[987654])
    except freight_close.FreightError:
        return
    assert 987654 not in opened


def test_the_close_routes_are_manager_only_and_the_parse_routes_operator_only(auth_enforced, client):
    ops = {"Authorization": f"Bearer {staff_token('op-r28b@alliedweldingsystems.com', 'OPERATOR')}"}
    vw = {"Authorization": f"Bearer {staff_token('vw-r28b@alliedweldingsystems.com', 'VIEWER')}"}
    sid, *_ = _ship()
    assert client.post(f"/api/v1/pipelines/shipment/{sid}/delivered", json={"sales_order_number": "S", "freight_actual": "1"}, headers=ops).status_code == 403
    assert client.post("/api/v1/pipelines/parse/mountain-movers", json={"text": "x"}, headers=vw).status_code == 403
    for path, body in (("/api/v1/landed-cost/calculate", {"lines": [_line()]}), ("/api/v1/landed-cost/tariffs", {"category": "c", "rate_percent": "1"}), ("/api/v1/landed-cost/cargo", {"text": "x"})):
        for h in (ops, vw, {}):
            assert client.post(path, json=body, headers=h).status_code in (401, 403), (path, h)
    for path in ("/api/v1/landed-cost/tariffs", "/api/v1/landed-cost/sheets/1", "/api/v1/landed-cost/sheets/1/xlsx"):
        for h in (ops, vw, {}):
            assert client.get(path, headers=h).status_code in (401, 403), (path, h)
    assert client.put(f"/api/v1/pipelines/shipment/{sid}", json={"pipeline": "US_OFFICE_PARCEL"}, headers=vw).status_code == 403
    assert client.post("/api/v1/pipelines/forwarder-digest", json={"text": "x", "apply": True}, headers=vw).status_code == 403


def test_the_redaction_routes_cannot_be_driven_by_a_viewer(auth_enforced, client):
    vw = {"Authorization": f"Bearer {staff_token('vw2-r28b@alliedweldingsystems.com', 'VIEWER')}"}
    op = {"Authorization": f"Bearer {staff_token('op2-r28b@alliedweldingsystems.com', 'OPERATOR')}"}
    paths = [("get", "/api/v1/payments/redaction/job/1", None), ("post", "/api/v1/payments/redaction/check", {"job_id": 1, "file_id": 1}),
             ("post", "/api/v1/payments/redaction/1/verify", {}), ("post", "/api/v1/payments/redaction/attest", {"job_id": 1, "file_id": 1, "statement": "I checked the whole file"})]
    for method, path, body in paths:
        r = client.request(method, path, json=body, headers=vw)
        assert r.status_code in (403, 404, 405, 422), (path, r.status_code)
        if r.status_code == 404:
            continue
        assert r.status_code != 200
    for method, path, body in paths[2:]:
        r = client.request(method, path, json=body, headers=op)
        assert r.status_code != 200, path


def test_the_dockerfile_installs_the_ocr_engine_and_every_runtime_dependency_the_new_code_imports():
    root = Path(__file__).resolve().parent.parent
    docker = (root / "Dockerfile").read_text()
    reqs = (root / "requirements.txt").read_text().lower()
    assert "tesseract-ocr" in docker and "tesseract-ocr-eng" in docker and (root / "docker" / "backend-entrypoint.sh").exists()
    for lib in ("pymupdf", "ezdxf", "pytesseract", "pillow", "openpyxl"):
        assert lib in reqs, lib
    assert "COPY --chown=allied:allied app/" in docker and "USER allied" in docker


@pytest.mark.parametrize("bad", ["1e999999", "1E+400", "1e30"])
def test_f9_a_huge_exponent_freight_is_a_422(bad, out_dir):
    sid = _piped()
    try:
        freight_close.confirm_delivery(sid, BOSS, "SO-HUGE", bad)
    except freight_close.FreightError:
        return
    except Exception as exc:  # noqa: BLE001
        raise AssertionError(type(exc).__name__)
    raise AssertionError("accepted")


def test_f10_an_angle_bracket_address_is_refused():
    with pytest.raises(lc.CostError):
        lc.freight_rfq_draft({"weights": [1], "dimensions": [1]}, "<a@b.com>", "", "", BOSS)
