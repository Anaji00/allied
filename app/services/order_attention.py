"""One count per kind of thing on the Orders page that needs a person, and a total for the sidebar badge. Reads only."""

from datetime import datetime, timedelta, timezone
from typing import Dict

from app.core.database import SessionLocal
from app.models.db_models import ExceptionDB, MailLogDB, PackingSlipDB


def counts(email_days: int = 14) -> Dict[str, int]:
    since = datetime.now(timezone.utc) - timedelta(days=email_days)
    with SessionLocal() as db:
        open_ex = [r.exception_type or "" for r in db.query(ExceptionDB).filter(ExceptionDB.status == "OPEN").all()]
        slips = db.query(PackingSlipDB).filter(PackingSlipDB.status == "FAILED").count()
        mail = db.query(MailLogDB).filter(MailLogDB.needs_action.is_(True), MailLogDB.handled_at.is_(None), MailLogDB.created_at >= since).count()
    bills = sum(1 for t in open_ex if t.startswith("INVOICE_"))
    out = {"orders_to_check": len(open_ex) - bills, "slips_failed": slips, "emails_unread": mail, "bills_to_check": bills}
    out["total"] = sum(out.values())
    return out
