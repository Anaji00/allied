"""The engineer gates as hard blocks on a vendor order tied to a job: no production without Gate 1, no crating or pickup (the ship date) without Gate 2; a manager override with a reason opens it.
Temporary database."""
import uuid
from datetime import date

import pytest

from app.config import settings
from app.core.database import SessionLocal
from app.models.db_models import JobDB, PurchaseOrderDB, VendorJobDB
from app.services import gates
from app.services.coordination import vendors

MGR = {"email": "mgr.gate@allied.test", "role": "MANAGER"}
ENG = {"email": "athish.gate@allied.test", "role": "OPERATOR"}


@pytest.fixture(autouse=True)
def on(monkeypatch):
    monkeypatch.setattr(settings, "GATES_ENFORCED", True)
    monkeypatch.setattr(settings, "ENGINEER_APPROVERS", ENG["email"])


def _order(tied=True):
    tag = uuid.uuid4().hex[:6]
    with SessionLocal() as db:
        job = JobDB(ref=f"JOB-G-{tag}", title="t", customer="C")
        db.add(job)
        db.commit()
        po = PurchaseOrderDB(job_id=job.id, vendor=f"V{tag}", lines=[], status="SENT", po_number=f"P{tag}")
        db.add(po)
        db.commit()
        ids = (job.id, po.id)
    line = vendors.create({"vendor": f"V{tag}", "description": "liner", "po_number": f"P{tag}" if tied else f"X{tag}", "ordered_on": "2026-11-01"}, MGR)
    if tied:
        with SessionLocal() as db:
            db.get(VendorJobDB, line["id"]).purchase_order_id = ids[1]
            db.commit()
    return ids[0], line["id"]


def test_production_waits_for_gate_1_and_crating_and_pickup_wait_for_gate_2():
    job_id, line = _order()
    with pytest.raises(ValueError) as e:
        vendors.update(line, {"status": "In production"}, MGR)
    assert "Gate 1" in str(e.value) and "Production cannot start" in str(e.value)
    g1 = gates.request(job_id, 1, MGR)
    gates.decide(g1["id"], True, ENG)
    assert vendors.update(line, {"status": "In production"}, MGR)["status"] == "In production"
    with pytest.raises(ValueError) as e:
        vendors.update(line, {"status": "Crating"}, MGR)
    assert "Gate 2" in str(e.value)
    with pytest.raises(ValueError):
        vendors.update(line, {"shipped_on": "2026-11-20"}, MGR)
    gates.override(job_id, 2, "customer approved the photos by phone", MGR)
    assert vendors.update(line, {"shipped_on": "2026-11-20"}, MGR)["shipped_on"] == "2026-11-20"


def test_an_order_not_tied_to_a_job_and_ordinary_edits_are_not_gated():
    job_id, line = _order(tied=False)
    assert vendors.update(line, {"status": "In production", "shipped_on": "2026-11-20"}, MGR)["shipped_on"] == "2026-11-20"
    job2, line2 = _order()
    assert vendors.update(line2, {"promised_on": "2026-12-01", "description": "liner plate"}, MGR)["promised_on"] == "2026-12-01"


def test_a_revoked_gate_blocks_again_and_the_flag_off_lets_everything_through(monkeypatch):
    job_id, line = _order()
    g1 = gates.request(job_id, 1, MGR)
    gates.decide(g1["id"], True, ENG)
    gates.revoke(g1["id"], "the drawing was the wrong revision", MGR)
    with pytest.raises(ValueError):
        vendors.update(line, {"status": "Production"}, MGR)
    monkeypatch.setattr(settings, "GATES_ENFORCED", False)
    assert vendors.update(line, {"status": "Production"}, MGR)["status"] == "Production"
