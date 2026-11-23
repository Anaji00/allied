"""Priority 2: the vendor payment terms engine (Decimal math, tranches add to 100.00 and to the PO total, triggers) and the engineer gates (hard blocks, approver list, manager override with a reason).
Temporary database; nothing is paid or sent."""
import uuid
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.core.database import SessionLocal
from app.models.db_models import JobDB, PurchaseOrderDB
from app.services import gates, payments
from tests.conftest import staff_token

MGR = {"email": "mgr.pay@allied.test", "role": "MANAGER"}


def _po(total="1000.00", created_by="drafter@allied.test"):
    tag = uuid.uuid4().hex[:6]
    with SessionLocal() as db:
        job = JobDB(ref=f"JOB-PAY-{tag}", title="t", customer="C", created_by=created_by)
        db.add(job)
        db.commit()
        po = PurchaseOrderDB(job_id=job.id, vendor=f"V{tag}", lines=[{"qty": 1, "item": "x", "unit_cost": total, "amount": total}], status="SENT", created_by=created_by)
        db.add(po)
        db.commit()
        return job.id, po.id


@pytest.mark.parametrize("text,want", [("30/50/20", ["30.00", "50.00", "20.00"]), ("30/70", ["30.00", "70.00"]), ("50-50", ["50.00", "50.00"]), ("33.33/33.33/33.34", ["33.33", "33.33", "33.34"]),
                                       ("100", ["100.00"]), ("25 25 25 25", ["25.00"] * 4), ([10, 90], ["10.00", "90.00"])])
def test_terms_parse_and_add_up_to_exactly_100(text, want):
    assert [f"{p:.2f}" for p in payments.parse_terms(text)] == want


@pytest.mark.parametrize("bad", ["", "30/50", "30/50/21", "0/100", "-10/110", "abc", "33.333/66.667", "50/50/0", "10/10/10/10/10/10/40", "nan/100", "inf"])
def test_terms_that_do_not_add_up_or_are_not_numbers_are_refused_with_a_plain_sentence(bad):
    with pytest.raises(payments.PaymentError) as e:
        payments.parse_terms(bad)
    assert e.value.status == 422 and e.value.message


@pytest.mark.parametrize("total,terms", [("1000.00", "30/50/20"), ("0.10", "33.33/33.33/33.34"), ("12345.67", "30/70"), ("999999.99", "50/50"), ("0.03", "34/33/33"), ("7.77", "1/1/98")])
def test_the_tranches_always_add_up_to_the_total_to_the_cent(total, terms):
    pcts = payments.parse_terms(terms)
    amounts = payments.split_amount(Decimal(total), pcts)
    assert sum(amounts) == Decimal(total) and all(a >= 0 for a in amounts)


def test_the_default_triggers_follow_the_workflow():
    assert payments.default_triggers(3) == ["PO_ISSUED", "GATE2_APPROVED", "DELIVERY_CONFIRMED"]
    assert payments.default_triggers(2) == ["PO_ISSUED", "GATE2_APPROVED"]


def test_a_schedule_is_made_from_the_po_total_and_triggers_open_tranches_once_in_order():
    job_id, po_id = _po("1000.00")
    s = payments.create_schedule(po_id, "30/50/20", MGR, currency="usd")
    assert s["total"] == "1000.00" and s["terms_code"] == "30/50/20" and [t["amount"] for t in s["tranches"]] == ["300.00", "500.00", "200.00"] and s["currency"] == "USD"
    assert all(t["status"] == "PENDING" for t in s["tranches"])
    assert [t["seq"] for t in payments.fire(po_id, "PO_ISSUED", MGR)] == [1]
    assert payments.fire(po_id, "PO_ISSUED", MGR) == []                                    # once
    assert payments.fire(po_id, "DELIVERY_CONFIRMED", MGR)[0]["amount"] == "200.00"
    got = payments.get_for_po(po_id)
    assert [t["status"] for t in got["tranches"]] == ["DUE", "PENDING", "DUE"]             # Gate 2 has not happened: the middle payment is not due


def test_a_second_schedule_replaces_the_first_until_something_is_paid():
    _, po_id = _po("500.00")
    payments.create_schedule(po_id, "50/50", MGR)
    again = payments.create_schedule(po_id, "30/70", MGR)
    assert payments.get_for_po(po_id)["id"] == again["id"] and again["terms_code"] == "30/70"


def test_a_po_with_no_total_gets_no_schedule_unless_a_total_is_given():
    with SessionLocal() as db:
        job = JobDB(ref=f"JOB-PAY-{uuid.uuid4().hex[:6]}", title="t", customer="C")
        db.add(job)
        db.commit()
        po = PurchaseOrderDB(job_id=job.id, vendor="Vx", lines=[], status="DRAFT")
        db.add(po)
        db.commit()
        pid = po.id
    with pytest.raises(payments.PaymentError):
        payments.create_schedule(pid, "50/50", MGR)
    assert payments.create_schedule(pid, "50/50", MGR, total="80")["total"] == "80.00"


# ------------------------------------------------------------------ gates

@pytest.fixture()
def engineer(monkeypatch):
    monkeypatch.setattr(settings, "ENGINEER_APPROVERS", "athish@allied.test")
    return {"email": "athish@allied.test", "role": "OPERATOR"}


def test_with_no_approver_list_nobody_can_approve():
    job_id, _ = _po()
    g = gates.request(job_id, 1, {"email": "ops@allied.test", "role": "OPERATOR"})
    assert gates.approvers() == []
    with pytest.raises(gates.GateError) as e:
        gates.decide(g["id"], True, {"email": "athish@allied.test", "role": "MANAGER"})
    assert e.value.status == 403


def test_gate_1_blocks_until_an_engineer_approves_and_a_decision_is_final(engineer):
    job_id, _ = _po()
    assert gates.blocked(job_id, 1)
    g = gates.request(job_id, 1, {"email": "ops@allied.test"})
    assert gates.milestone_text(job_id) == "awaiting Gate 1 shop drawing approval"
    with pytest.raises(gates.GateError):
        gates.decide(g["id"], True, {"email": "someone.else@allied.test", "role": "OPERATOR"})
    assert gates.decide(g["id"], True, engineer, "drawing is right")["status"] == "APPROVED"
    assert gates.blocked(job_id, 1) is None and gates.milestone_text(job_id) == "in production, awaiting Gate 2 measurement photos"
    with pytest.raises(gates.GateError) as e:
        gates.decide(g["id"], False, engineer)
    assert e.value.status == 409


def test_a_manager_override_needs_a_reason_and_a_manager_and_is_audited():
    job_id, _ = _po()
    with pytest.raises(gates.GateError):
        gates.override(job_id, 2, "short", MGR)
    with pytest.raises(gates.GateError):
        gates.override(job_id, 2, "the customer is waiting on this one", {"email": "ops@allied.test", "role": "OPERATOR"})
    out = gates.override(job_id, 2, "the customer is waiting on this one", MGR)
    assert out["status"] == "OVERRIDDEN" and out["override_reason"] and gates.blocked(job_id, 2) is None
    with SessionLocal() as db:
        from sqlalchemy import text

        assert db.execute(text("select count(*) from audit_log where action = 'GATE_OVERRIDDEN' and actor_email = :e"), {"e": MGR["email"]}).scalar() >= 1


def test_a_manager_can_revoke_with_a_reason_and_the_block_returns(engineer):
    job_id, _ = _po()
    g = gates.request(job_id, 1, {"email": "ops@allied.test"})
    gates.decide(g["id"], True, engineer)
    with pytest.raises(gates.GateError):
        gates.revoke(g["id"], "short", MGR)
    assert gates.revoke(g["id"], "the drawing was the wrong revision", MGR)["status"] == "REVOKED"
    assert gates.blocked(job_id, 1)


def test_gate_2_approval_makes_the_gate_2_payment_due(engineer):
    job_id, po_id = _po("2000.00")
    payments.create_schedule(po_id, "30/70", MGR)
    g = gates.request(job_id, 2, {"email": "ops@allied.test"})
    gates.decide(g["id"], True, engineer)
    got = payments.get_for_po(po_id)
    assert [t["status"] for t in got["tranches"]] == ["PENDING", "DUE"] and got["tranches"][1]["amount"] == "1400.00"


def test_the_api_blocks_the_drafter_from_approving_their_own_job_and_a_viewer_from_everything(monkeypatch, auth_enforced):
    from app.main import app

    monkeypatch.setattr(settings, "ENGINEER_APPROVERS", "athish2@allied.test,drafter2@allied.test")
    c = TestClient(app, raise_server_exceptions=False)
    h = lambda email, role: {"Authorization": "Bearer " + staff_token(email, role)}  # noqa: E731
    job_id, po_id = _po(created_by="drafter2@allied.test")
    r = c.post("/api/v1/gates/request", json={"job_id": job_id, "gate": 1}, headers=h("ops2@allied.test", "OPERATOR"))
    assert r.status_code == 200
    gid = r.json()["id"]
    assert c.post(f"/api/v1/gates/{gid}/decide", json={"approve": True}, headers=h("drafter2@allied.test", "OPERATOR")).status_code == 403
    assert c.post(f"/api/v1/gates/{gid}/decide", json={"approve": True}, headers=h("athish2@allied.test", "OPERATOR")).status_code == 200
    assert c.get(f"/api/v1/gates/job/{job_id}", headers=h("v@allied.test", "VIEWER")).status_code == 403
    assert c.put(f"/api/v1/payments/po/{po_id}/schedule", json={"terms": "30/70"}, headers=h("ops2@allied.test", "OPERATOR")).status_code == 403
    ok = c.put(f"/api/v1/payments/po/{po_id}/schedule", json={"terms": "30/70"}, headers=h("m2@allied.test", "MANAGER"))
    assert ok.status_code == 200 and ok.json()["tranches"][0]["amount"] == "300.00"
    assert c.put(f"/api/v1/payments/po/{po_id}/schedule", json={"terms": "30/60"}, headers=h("m2@allied.test", "MANAGER")).status_code == 422


def test_the_job_gates_route_gives_ids_words_and_what_this_person_may_do(monkeypatch, auth_enforced):
    from app.main import app

    monkeypatch.setattr(settings, "ENGINEER_APPROVERS", "athish3@allied.test")
    c = TestClient(app, raise_server_exceptions=False)
    h = lambda email, role: {"Authorization": "Bearer " + staff_token(email, role)}  # noqa: E731
    job_id, po_id = _po(created_by="drafter3@allied.test")
    first = c.get(f"/api/v1/gates/job/{job_id}", headers=h("ops3@allied.test", "OPERATOR")).json()
    g1 = first["status"]["1"]
    assert g1["id"] is None and g1["status_word"] == "Not asked yet" and g1["can_request"] and g1["request_url"] and not g1["can_approve"] and not g1["can_override"] and first["needs_approver_set"] is False
    gid = c.post("/api/v1/gates/request", json={"job_id": job_id, "gate": 1}, headers=h("ops3@allied.test", "OPERATOR")).json()["id"]
    eng = c.get(f"/api/v1/gates/job/{job_id}", headers=h("athish3@allied.test", "OPERATOR")).json()["status"]["1"]
    assert eng["id"] == gid and eng["status_word"] == "Waiting for the engineer" and eng["can_approve"] and not eng["can_revoke"] and not eng["can_override"]
    drafter = c.get(f"/api/v1/gates/job/{job_id}", headers=h("drafter3@allied.test", "OPERATOR")).json()["status"]["1"]
    assert not drafter["can_approve"]
    mgr = c.get(f"/api/v1/gates/job/{job_id}", headers=h("m3@allied.test", "MANAGER")).json()["status"]["1"]
    assert mgr["can_override"] and not mgr["can_approve"]
    c.post(f"/api/v1/gates/{gid}/decide", json={"approve": True}, headers=h("athish3@allied.test", "OPERATOR"))
    done = c.get(f"/api/v1/gates/job/{job_id}", headers=h("m3@allied.test", "MANAGER")).json()["status"]["1"]
    assert done["status_word"] == "Approved" and done["decided_by_name"] and done["can_revoke"] and not done["can_override"]
    monkeypatch.setattr(settings, "ENGINEER_APPROVERS", "")
    assert c.get(f"/api/v1/gates/job/{job_id}", headers=h("m3@allied.test", "MANAGER")).json()["needs_approver_set"] is True
