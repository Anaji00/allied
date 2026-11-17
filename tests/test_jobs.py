"""Jobs: one record from RFQ to shipment; the stage comes from what exists; only a manager approves a design. Test data only."""
import uuid

from app.services.coordination import rfqs
from tests.conftest import staff_token

BOSS = {"email": "boss@alliedweldingsystems.com", "role": "MANAGER"}


def _rfq():
    return rfqs.create({"customer": f"Cust{uuid.uuid4().hex[:5]}", "subject": "hey make me this part",
                        "items": [{"description": "liner", "quantity": 2}]}, BOSS, announce=False)


def test_job_from_rfq_is_made_once_and_staged(client):
    r = _rfq()
    a = client.post(f"/api/v1/jobs/from-rfq/{r['id']}")
    b = client.post(f"/api/v1/jobs/from-rfq/{r['number']}")
    assert a.status_code == 200 and a.json()["id"] == b.json()["id"] and a.json()["ref"].startswith("JOB-")
    j = client.get(f"/api/v1/jobs/{a.json()['ref']}").json()
    assert j["stage"] == "RFQ" and j["done"] == ["RFQ"] and j["rfq"]["number"] == r["number"] and j["purchase_orders"] == []
    assert any(i["id"] == j["id"] for i in client.get("/api/v1/jobs?stage=RFQ").json()["items"])
    assert client.get("/api/v1/jobs/JOB-1999-9999").status_code == 404


def test_only_a_manager_approves_the_design(client, auth_enforced):
    r = _rfq()
    boss = {"Authorization": f"Bearer {staff_token('boss2@alliedweldingsystems.com', 'MANAGER')}"}
    op = {"Authorization": f"Bearer {staff_token('op2@alliedweldingsystems.com', 'OPERATOR')}"}
    job = client.post(f"/api/v1/jobs/from-rfq/{r['id']}", headers=op).json()
    assert client.post(f"/api/v1/jobs/{job['id']}/design/approve", headers=op).status_code in (401, 403)
    ok = client.post(f"/api/v1/jobs/{job['id']}/design/approve", headers=boss).json()
    assert ok["design"]["approved_by"] == "boss2@alliedweldingsystems.com" and "DESIGN_APPROVED" in ok["done"]
