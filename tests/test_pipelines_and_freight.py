"""The fulfilment pipelines on SYNTHETIC emails (made up here, no real sample): UPS tracking check digit, Mountain Movers quote options / BOL / pickup and delivery, the forwarder weekly digest, and the
after-delivery freight close (variance, staged SO-to-invoice payload, delivery payments). Temporary database and output folder; nothing is written to Sage."""
import json
import uuid
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.core.database import SessionLocal
from app.models.db_models import JobDB, PurchaseOrderDB, ShipmentDB
from app.services import freight_close, payments, pipelines
from tests.conftest import staff_token

MGR = {"email": "mgr.pipe@allied.test", "role": "MANAGER"}


def _ship(container=None, ref=None):
    tag = uuid.uuid4().hex[:5].upper()
    with SessionLocal() as db:
        s = ShipmentDB(forwarder="Fwd", mode="SEA", container_no=container or f"MSKU{uuid.uuid4().int % 10**7:07d}", reference=ref or f"BK{tag}", status="DEPARTED")
        db.add(s)
        db.commit()
        return s.id, s.container_no, s.reference


def test_ups_numbers_are_checked_by_their_check_digit():
    good = pipelines.ups_make("A1B2C3D4E5F6G7H")
    assert good.startswith("1Z") and len(good) == 18 and pipelines.ups_valid(good) and pipelines.ups_valid(good.lower()) and pipelines.ups_valid(" ".join([good[:6], good[6:]]))
    bad = good[:-1] + str((int(good[-1]) + 1) % 10)
    assert not pipelines.ups_valid(bad) and not pipelines.ups_valid("1Z") and not pipelines.ups_valid("") and not pipelines.ups_valid(None) and not pipelines.ups_valid("2Z" + good[2:])


def test_a_pipeline_is_stored_with_a_valid_ups_number_and_refuses_a_bad_one():
    sid, _, _ = _ship()
    tn = pipelines.ups_make("123456789012345")
    out = pipelines.set_pipeline(sid, "us_office_parcel", MGR, tracking_no=tn.lower())
    assert out["pipeline"] == "US_OFFICE_PARCEL" and out["tracking_no"] == tn and out["carrier"] == "UPS"
    with pytest.raises(pipelines.PipelineError) as e:
        pipelines.set_pipeline(sid, "US_OFFICE_PARCEL", MGR, tracking_no=tn[:-1] + "0" if tn[-1] != "0" else tn[:-1] + "1")
    assert e.value.status == 422
    assert pipelines.get(sid)["tracking_no"] == tn
    for pipe in ("", "FEDEX", "us"):
        with pytest.raises(pipelines.PipelineError):
            pipelines.set_pipeline(sid, pipe, MGR)
    with pytest.raises(pipelines.PipelineError) as e:
        pipelines.set_pipeline(99999999, "US_MOUNTAIN_MOVERS", MGR)
    assert e.value.status == 404


MM = """Subject: Quote for pickup at Zibo dock

Hello Allied,

Option 1: Standard LTL, $1,234.56  transit 5-7 business days
Option 2: Expedited, $2,980.00 - 3 days
Quote 3: Hot shot / white glove $4,500 1 day

Your BOL number: MM-882931
Pickup date: Nov 20, 2026
Delivered on 2026-11-25
Pickup: 04/05/26
"""


def test_mountain_movers_options_bol_and_dates_are_read_flagged_and_an_ambiguous_date_is_not_used():
    out = pipelines.parse_mountain_movers(MM)
    assert [(o["option"], o["service"], o["price"], o["transit_days"]) for o in out["quote_options"]] == [(1, "Standard LTL", "1234.56", "5-7"), (2, "Expedited", "2980.00", "3"), (3, "Hot shot / white glove", "4500.00", "1")]
    assert out["bol_numbers"] == ["MM-882931"]
    assert {(e["kind"], e["date"]) for e in out["events"]} == {("pickup", "2026-11-20"), ("delivery", "2026-11-25")}
    assert out["ambiguous_dates"] and out["ambiguous_dates"][0]["text"] == "04/05/26" and out["check"] == pipelines.CHECK
    empty = pipelines.parse_mountain_movers("thanks, call me")
    assert empty["quote_options"] == [] and empty["events"] == [] and empty["bol_numbers"] == []


@pytest.mark.parametrize("text,iso,amb", [("2026-11-20", "2026-11-20", False), ("Nov 20, 2026", "2026-11-20", False), ("20 Nov 2026", "2026-11-20", False), ("11/20/26", "2026-11-20", False), ("20/11/2026", "2026-11-20", False),
                                          ("03/04/26", None, True), ("2026-02-30", None, False), ("soon", None, False), ("13/13/2026", None, False)])
def test_dates_are_read_only_when_they_have_one_meaning(text, iso, amb):
    assert pipelines.read_date(text) == {"iso": iso, "ambiguous": amb}


def test_the_forwarder_digest_moves_only_the_one_shipment_a_row_names_and_never_an_ambiguous_date():
    sid, container, ref = _ship()
    other, c2, _ = _ship()
    text = f"""Weekly update
{container}  ETA 2026-12-01  vessel EVER GIVEN
Booking {ref} ETA: 3 Dec 2026 (duplicate line for the same shipment)
ZZZZ0000000 ETA 2026-12-09 unknown container
{c2} ETA 04/05/26
no date on this line {c2}
"""
    preview = pipelines.apply_forwarder_digest(text, MGR, apply=False)
    assert preview["applied"] is False and not any(r["action"] == "ETA set" for r in preview["rows"])
    actions = {r["reference"]: r["action"] for r in preview["rows"]}
    assert actions["ZZZZ0000000"] == "no shipment matches" and actions[container] == "would set ETA" and preview["ambiguous"][0]["reference"] == c2
    with SessionLocal() as db:
        assert db.get(ShipmentDB, sid).eta_current is None
    applied = pipelines.apply_forwarder_digest(text, MGR, apply=True)
    assert [r["action"] for r in applied["rows"] if r["reference"] == container] == ["ETA set"]
    with SessionLocal() as db:
        assert db.get(ShipmentDB, sid).eta_current in ("2026-12-01", "2026-12-03") and db.get(ShipmentDB, other).eta_current is None


def _po_with_schedule():
    tag = uuid.uuid4().hex[:6]
    with SessionLocal() as db:
        job = JobDB(ref=f"JOB-FR-{tag}", title="t", customer="C")
        db.add(job)
        db.commit()
        po = PurchaseOrderDB(job_id=job.id, po_number=f"PO-FR-{tag}", vendor=f"V{tag}", lines=[{"qty": 1, "unit_cost": "1000", "amount": "1000"}], status="SENT")
        db.add(po)
        db.commit()
        ids = po.id
    payments.create_schedule(ids, "50/50", MGR, triggers=["PO_ISSUED", "DELIVERY_CONFIRMED"])
    return ids, f"PO-FR-{tag}"


@pytest.mark.parametrize("actual,quoted,flag,pct", [("1000", "1000", False, "0.00"), ("1100", "1000", False, "10.00"), ("1100.01", "1000", True, "10.00"), ("800", "1000", True, "-20.00"), ("50", None, True, None)])
def test_freight_variance_is_flagged_past_the_limit_and_when_there_was_no_quote(actual, quoted, flag, pct):
    v = freight_close.variance(Decimal(actual), Decimal(quoted) if quoted is not None else None)
    assert v["flag"] is flag and (pct is None or v["percent"] == pct or abs(Decimal(v["percent"]) - Decimal(pct)) < Decimal("0.01"))


def test_confirming_delivery_stages_the_payload_flags_variance_opens_the_delivery_payment_and_repeats_safely(settings_output=None):
    from app.config import settings

    sid, _, _ = _ship()
    pipelines.set_pipeline(sid, "CN_FREIGHT_FORWARDER", MGR)
    po, number = _po_with_schedule()
    with SessionLocal() as db:
        db.get(ShipmentDB, sid).reference = f"PO {number}"                                      # the shipment is booked under the PO
        db.commit()
    with pytest.raises(freight_close.FreightError):
        freight_close.confirm_delivery(sid, MGR, "SO-12889", "1500", "1000", [po + 9999])     # a PO that is not this shipment's is refused
    out = freight_close.confirm_delivery(sid, MGR, "SO-12889", "1,500.00", "1000", [po])
    assert out["variance"]["flag"] is True and out["payload"]["convert_to_invoice"] is True and "freight_variance" not in out["payload"] and out["payload"]["freight_line"] == {"description": "Freight", "amount": "1500.00"}
    assert [t["trigger"] for t in out["payments_now_due"]] == ["DELIVERY_CONFIRMED"]
    path = settings.OUTPUT_DIR / "sage_staged" / out["payload_file"]
    assert path.exists() and json.loads(path.read_text())["note"].endswith("Nothing was written to Sage.") and not list(path.parent.glob(".sf.*.tmp"))
    again = freight_close.confirm_delivery(sid, MGR, "SO-12889", "1000.00", None, [po])
    assert again["variance"]["flag"] is False and again["payments_now_due"] == [] and again["payload_file"] == out["payload_file"]
    row = pipelines.get(sid)
    assert row["freight_cost_actual"] == "1000.00" and row["freight_cost_quoted"] == "1000.00" and row["variance_flag"] is False and row["freight_populated_at"]
    for bad in ({"so_number": "", "freight_actual": "5"}, {"so_number": "S", "freight_actual": "abc"}, {"so_number": "S", "freight_actual": "-5"}):
        with pytest.raises(freight_close.FreightError):
            freight_close.confirm_delivery(sid, MGR, bad["so_number"], bad["freight_actual"])
    sid2, _, _ = _ship()
    with pytest.raises(freight_close.FreightError) as e:
        freight_close.confirm_delivery(sid2, MGR, "SO-1", "5")
    assert e.value.status == 409


def test_the_api_roles(auth_enforced):
    from app.main import app

    c = TestClient(app, raise_server_exceptions=False)
    h = lambda role: {"Authorization": "Bearer " + staff_token(f"pp-{role.lower()}@allied.test", role)}  # noqa: E731
    sid, container, _ = _ship()
    tn = pipelines.ups_make("111122223333444")
    assert c.put(f"/api/v1/pipelines/shipment/{sid}", json={"pipeline": "US_OFFICE_PARCEL", "tracking_no": tn}, headers=h("VIEWER")).status_code == 403
    assert c.put(f"/api/v1/pipelines/shipment/{sid}", json={"pipeline": "US_OFFICE_PARCEL", "tracking_no": tn}, headers=h("OPERATOR")).status_code == 200
    assert c.put(f"/api/v1/pipelines/shipment/{sid}", json={"pipeline": "US_OFFICE_PARCEL", "tracking_no": "1Z000"}, headers=h("OPERATOR")).status_code == 422
    assert c.post("/api/v1/pipelines/parse/mountain-movers", json={"text": MM}, headers=h("OPERATOR")).json()["bol_numbers"] == ["MM-882931"]
    assert c.post(f"/api/v1/pipelines/shipment/{sid}/delivered", json={"sales_order_number": "SO-9", "freight_actual": "10"}, headers=h("OPERATOR")).status_code == 403
    assert c.post(f"/api/v1/pipelines/shipment/{sid}/delivered", json={"sales_order_number": "SO-9", "freight_actual": "10"}, headers=h("MANAGER")).status_code == 200
    assert c.post("/api/v1/pipelines/forwarder-digest", json={"text": f"{container} ETA 2026-12-02"}, headers=h("OPERATOR")).json()["rows"][0]["action"] == "would set ETA"


def test_the_digest_preview_has_what_a_screen_needs_and_apply_needs_a_trusted_sender(monkeypatch):
    sid, container, ref = _ship()
    other, c2, _ = _ship()
    text = f"{container} ETA 2026-12-02\n{container} ETA 2026-12-02\n{c2} ETA 03/04/26\nZZZZ0000001 ETA 2031-01-01\n"
    op = {"email": "op.digest@allied.test", "role": "OPERATOR"}
    out = pipelines.apply_forwarder_digest(text, op, apply=False, sender="x@random.example")
    row = out["rows"][0]
    assert row["container"] == container and row["new_eta"] == "2026-12-02" and row["old_eta"] is None and row["job"] is None and "listed more than once in this update" in row["flags"]
    assert out["sender_trusted"] is False and out["can_apply"] is False
    assert {a["reference"] for a in out["ambiguous"]} == {c2, "ZZZZ0000001"} and all(a["reason"] for a in out["ambiguous"])
    with pytest.raises(pipelines.PipelineError) as e:
        pipelines.apply_forwarder_digest(text, op, apply=True, sender="x@random.example")
    assert e.value.status == 403
    monkeypatch.setattr(settings, "FORWARDER_DOMAINS", "forwarder-example.test")
    ok = pipelines.apply_forwarder_digest(text, op, apply=True, sender="Ops <ops@forwarder-example.test>")
    assert ok["sender_trusted"] is True and [r["action"] for r in ok["rows"]] == ["ETA set"]
