"""Design and purchase-order approvals are written to the audit trail. Temporary database only."""
import uuid

from app.core.database import SessionLocal
from app.models.db_models import AuditLogDB
from app.services.coordination import jobs, purchase_orders, rfqs

BOSS = {"email": "boss-audit@alliedweldingsystems.com", "role": "MANAGER"}


def _actions():
    with SessionLocal() as db:
        return [(r.action, r.actor_email) for r in db.query(AuditLogDB).all()]


def test_design_and_po_approval_leave_an_audit_entry():
    r = rfqs.create({"customer": "Owens Corning", "subject": f"aud {uuid.uuid4().hex[:6]}", "items": [{"description": "liner", "quantity": 2}]}, BOSS, announce=False)
    j = jobs.from_rfq(r["id"], BOSS)
    jobs.approve_design(j["id"], BOSS)
    jobs.approve_design(j["id"], BOSS)                               # a repeat is not a second entry
    po = purchase_orders.create(j["id"], f"V-{uuid.uuid4().hex[:4]}", BOSS)
    purchase_orders.update(po["id"], {"lines": [{"quantity": 1, "item": "X", "description": "d", "unit_cost": 5}]}, BOSS)
    purchase_orders.approve(po["id"], BOSS)
    acts = _actions()
    assert acts.count(("DESIGN_APPROVED", BOSS["email"])) == 1
    assert ("PO_APPROVED", BOSS["email"]) in acts
