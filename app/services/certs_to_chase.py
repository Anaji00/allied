"""Purchase orders that need a mill test report or certificate and do not have one on file. Read only; no prices.

A PO line is marked `cert_required` when the customs details are filled in. A certificate counts as "on file" when a file whose name looks like one (MTR, mill test, heat, cert, CoC, test report) is
attached to the PO, or to the vendor line of that PO. This is a name check on attached files, not a reading of the file: a person still checks the certificate itself."""
import re
from datetime import date, datetime, timezone
from typing import Any, Dict, List

from app.core.database import SessionLocal
from app.models.db_models import ItemFileDB, JobDB, OutboundMailDB, PurchaseOrderDB, VendorJobDB

LIVE = ("APPROVED", "SENDING", "SENT", "ACKNOWLEDGED")
# A file name counts as a certificate only on whole words: MTR, mill test / mill cert, heat cert / heat number, CoC, certificate of conformance, analysis or compliance, material or mill test report.
# "Heat_exchanger_drawing", "Certificate_of_insurance", "certified_drawing", "wheat" and "smtrack" are not certificates.
_CERT_NAME = re.compile(r"(?i)(?<![a-z0-9])(?:mtrs?|mtcs?|mill[\s_-]*(?:test|cert\w*)|heat[\s_-]*(?:cert\w*|number|no|test)|coc|certificate[\s_-]*of[\s_-]*(?:conformance|conformity|analysis|compliance)|"
                        r"(?:material|mill)[\s_-]*test[\s_-]*reports?|test[\s_-]*reports?)(?![a-z0-9])")
_NOT_A_CERT = re.compile(r"(?i)insurance|origin|drawing|\bdwg\b|photo|invoice|packing")
_YES = {"true", "1", "yes", "y", "required"}


def _needs(line: Any) -> bool:
    if not isinstance(line, dict):
        return False
    v = line.get("cert_required")
    return v is True or v == 1 or str(v or "").strip().casefold() in _YES


def _on(at: Any):
    if at is None:
        return None
    return (at if at.tzinfo else at.replace(tzinfo=timezone.utc)).date()


def chase_list() -> Dict[str, Any]:
    from app.services.coordination import notify

    today = notify.today()
    out: List[Dict[str, Any]] = []
    with SessionLocal() as db:
        pos = db.query(PurchaseOrderDB).filter(PurchaseOrderDB.status.in_(LIVE)).order_by(PurchaseOrderDB.id).all()
        jobs = {j.id: j.ref for j in db.query(JobDB).filter(JobDB.id.in_([p.job_id for p in pos]))} if pos else {}
        for po in pos:
            need = [ln for ln in (po.lines or []) if _needs(ln)]
            if not need:
                continue
            refs = {str(po.id)} | ({str(po.po_number)} if po.po_number else set())
            vj = [str(v.id) for v in db.query(VendorJobDB).filter(VendorJobDB.purchase_order_id == po.id)]
            files = db.query(ItemFileDB).filter(ItemFileDB.deleted_at.is_(None), ((ItemFileDB.item_type.in_(("po", "purchase_order"))) & (ItemFileDB.item_ref.in_(refs)))
                                                | ((ItemFileDB.item_type == "vendor_job") & (ItemFileDB.item_ref.in_(vj or ["-"])))).all()
            if any(_CERT_NAME.search(f.filename or "") and not _NOT_A_CERT.search(f.filename or "") for f in files):
                continue
            since = _on(po.sent_at) or _on(po.approved_at) or _on(po.created_at)
            asked = None
            if po.po_number:
                m = (db.query(OutboundMailDB).filter(OutboundMailDB.reference == str(po.po_number), OutboundMailDB.status == "SENT", OutboundMailDB.subject.ilike("%cert%"))
                     .order_by(OutboundMailDB.created_at.desc()).first())
                asked = _on(m.created_at).isoformat() if m is not None and m.created_at else None
            out.append({"po_id": po.id, "po_number": po.po_number, "job_id": po.job_id, "job_ref": jobs.get(po.job_id), "vendor": po.vendor, "line_count": len(need),
                        "sent_on": _on(po.sent_at).isoformat() if po.sent_at else None, "days_waiting": max(0, (today - since).days) if since else None, "last_asked_on": asked})
    out.sort(key=lambda r: -(r["days_waiting"] or 0))
    return {"total": len(out), "items": out, "note": "A certificate counts as on file when a file with a name like MTR, mill test, heat, cert or CoC is attached to the PO. A person still checks the certificate itself."}
