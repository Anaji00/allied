"""Every record edited in place, the same way a board cell is: type the text, the app works out the value and says what it understood.

GET fields -> the fields of a job, RFQ, order, purchase order, shipment or part, with their type, whether each can be edited and the choices. PATCH one field
with plain text. The changes go through the services each screen already uses (so their rules hold: a sent PO is locked, a status must be a real one), and
every change is audited. Nothing here touches monday.
"""

import json
import re
from datetime import date
from typing import Any, Dict, List, Optional

from app.core.audit import audit_log
from app.core.database import SessionLocal
from app.services import board_edit, monday_push

KINDS = ("job", "rfq", "order", "po", "shipment", "product", "customer", "vendor")
_MASTER = {"customer": ("CUSTOMERS", "customer_id"), "vendor": ("VENDORS", "vendor_id")}
_EMAIL = re.compile(r"[^@\s,;<>]+@[^@\s,;<>]+\.[^@\s,;<>]{2,}", re.ASCII)


class RecordError(ValueError):
    pass


def _f(field: str, label: str, ftype: str, editable: bool = True, choices: Optional[List[str]] = None) -> Dict[str, Any]:
    return {"field": field, "label": label, "type": ftype, "editable": editable, **({"choices": choices} if choices else {})}


def _choices() -> Dict[str, List[str]]:
    from app.services.coordination import purchase_orders, rfqs, shipments

    return {"complexity": list(rfqs.COMPLEXITY), "status": list(shipments.STATUSES), "mode": ["SEA", "AIR", "TRUCK"], "incoterm": sorted(purchase_orders._INCOTERMS)}


def _schema(kind: str) -> List[Dict[str, Any]]:
    c = _choices()
    if kind == "rfq":
        return [_f("number", "RFQ", "text", False), _f("customer", "Customer", "text"), _f("subject", "Subject", "text", False), _f("contact_email", "Contact", "email"),
                _f("owner_email", "Owner", "email"), _f("vendor", "Vendor(s)", "text"), _f("complexity", "Complexity", "choice", True, c["complexity"]),
                _f("notes", "Notes", "long_text"), _f("status", "Status", "text", False)]
    if kind == "job":
        return [_f("ref", "Job", "text", False), _f("title", "Title", "text"), _f("customer", "Customer", "text"), _f("stage_word", "Stage", "text", False), _f("monday_item_id", "monday item", "text")]
    if kind == "po":
        return [_f("po_number", "PO", "text", False), _f("vendor", "Vendor", "text", False), _f("vendor_email", "Vendor email", "email"), _f("terms", "Terms", "text"),
                _f("ship_via", "Ship via", "text"), _f("good_thru", "Good through", "text"), _f("incoterm", "Incoterm", "choice", True, c["incoterm"]), _f("status_word", "Status", "text", False)]
    if kind == "shipment":
        return [_f("reference", "Reference", "text"), _f("container_no", "Container", "text", False), _f("awb", "AWB", "text", False), _f("forwarder", "Forwarder", "text"),
                _f("carrier", "Carrier", "text"), _f("origin", "From", "text"), _f("destination", "To", "text"), _f("etd", "Departs", "date"), _f("eta_current", "ETA", "date"),
                _f("free_time_ends", "Last free day", "date"), _f("status", "Status", "choice", True, c["status"]), _f("owner_email", "Owner", "email")]
    if kind == "order":
        return [_f("sales_order_number", "Sales order", "text", False), _f("customer_name", "Customer", "text", False), _f("customer_po_number", "Customer's PO", "text", False),
                _f("status_word", "Status", "text", False), _f("shipping_address", "Ship to", "long_text", False), _f("payment_terms", "Terms", "text", False),
                _f("internal_notes", "Internal notes", "long_text")]
    if kind == "product":
        return [_f("item_id", "Part", "text", False), _f("description", "Description", "text"), _f("base_price", "Price", "number"), _f("standard_cost", "Cost", "number"),
                _f("preferred_vendor", "Vendor", "text"), _f("uom", "Unit", "text"), _f("category", "Category", "text")]
    if kind == "customer":
        return [_f("customer_id", "Customer ID", "text", False), _f("customer_name", "Name", "text", False), _f("email", "Email", "email"), _f("contact_name", "Contact", "text"),
                _f("payment_terms", "Terms", "text"), _f("billing_address", "Billing address", "long_text"), _f("shipping_address", "Shipping address", "long_text"),
                _f("sales_rep", "Sales rep", "text"), _f("shipping_method", "Ship via", "text")]
    if kind == "vendor":
        return [_f("vendor_id", "Vendor ID", "text", False), _f("vendor_name", "Name", "text", False), _f("email", "Email", "email"), _f("phone", "Phone", "text"),
                _f("payment_terms", "Terms", "text"), _f("vendor_address", "Address", "long_text")]
    raise RecordError(f"Records are: {', '.join(KINDS)}.")


def _read(kind: str, ref: str, manager: bool = False) -> Dict[str, Any]:
    if kind == "rfq":
        from app.services.coordination import rfqs

        return rfqs.get(ref)
    if kind == "job":
        from app.models.db_models import JobDB
        from app.services.coordination import jobs

        out = jobs.get(ref, staff=True)
        with SessionLocal() as db:
            row = db.query(JobDB).filter(JobDB.ref == out["ref"]).first()
            out["monday_item_id"] = (row.monday_item_id if row else None) or ""
        return out
    if kind == "po":
        from app.services.coordination import purchase_orders

        po = purchase_orders.by_number(str(ref)) or (purchase_orders.get(int(ref)) if str(ref).isdigit() and int(ref) < 10**6 else None)         # the number people read first, then the id
        if not po:
            raise LookupError(f"No purchase order {ref}.")
        return po
    if kind == "shipment":
        from app.services.coordination import shipments

        s = next((x for x in shipments.list_shipments() if str(x["id"]) == str(ref) or str(x.get("reference") or "").casefold() == str(ref).casefold()), None)
        if s is None:
            raise LookupError(f"No shipment {ref}.")
        return s
    if kind == "order":
        from app.services.database_service import DatabaseService

        with SessionLocal() as db:
            o = DatabaseService.get_order_by_so(db, str(ref))
        if not o:
            raise LookupError(f"No order {ref}.")
        return o
    if kind == "product":
        from app.services.csv_import_service import csv_import_service

        it = csv_import_service.matcher.items_cache.get(str(ref))
        if it is None:
            raise LookupError(f"No part {ref} in the catalogue.")
        return {**dict(it), "item_id": str(ref)}
    if kind in _MASTER:
        from app.core.container import get_entity_matcher

        cache = get_entity_matcher().customers_cache if kind == "customer" else get_entity_matcher().vendors_cache
        key = str(ref)
        it = cache.get(key) or next((v for k, v in cache.items() if k.casefold() == key.casefold() or str(v.get(_MASTER[kind][1]) or "").casefold() == key.casefold()), None)
        if it is None:
            raise LookupError(f"No {kind} {ref} in the master list.")
        return dict(it)
    raise RecordError(f"Records are: {', '.join(KINDS)}.")


MANAGER_FIELDS = {"product": {"base_price", "standard_cost", "preferred_vendor"}}


def check_role(kind: str, field: str, user: Dict[str, Any]) -> None:
    if field in MANAGER_FIELDS.get(kind, ()) and user.get("role") not in ("MANAGER", "ADMIN"):
        raise PermissionError("Only a manager can change a part's price, cost or vendor.")


def _po_gone_out(rfq: Dict[str, Any]) -> bool:
    """True when a purchase order on the job made from this RFQ has been sent (or is being sent)."""
    from app.core.database import SessionLocal
    from app.models.db_models import JobDB, PurchaseOrderDB

    with SessionLocal() as db:
        job = db.query(JobDB).filter(JobDB.rfq_id == rfq.get("id")).first()
        return bool(job) and db.query(PurchaseOrderDB).filter(PurchaseOrderDB.job_id == job.id, PurchaseOrderDB.status.in_(("SENDING", "SENT", "ACKNOWLEDGED"))).count() > 0


def why_locked(kind: str, rec: Dict[str, Any], f: Dict[str, Any]) -> str:
    """The plain reason this field can't be changed now because of the record's state; "" when it can."""
    if not f["editable"]:
        return "It is worked out or fixed by the app."
    if kind == "po" and rec.get("status") not in ("DRAFT", "APPROVED"):
        return "This purchase order has been sent or cancelled, so it can't be changed."
    if kind == "rfq" and rec.get("closed_at"):
        return "This RFQ is closed."
    if kind == "rfq" and f["field"] == "customer" and _po_gone_out(rec):
        return "The customer is fixed once a purchase order has gone to a vendor."
    if kind == "job" and f["field"] == "customer" and (rec.get("sales_order") or {}).get("number"):
        return "The customer is fixed once the sales order is made."
    if kind == "job" and f["field"] == "customer" and any(p.get("status") in ("SENDING", "SENT", "ACKNOWLEDGED") for p in (rec.get("purchase_orders") or [])):
        return "The customer is fixed once a purchase order has gone to a vendor."
    if kind == "job" and rec.get("stage") == "CLOSED":
        return "This job is closed."
    return ""


def fields(kind: str, ref: str, role: str = "MANAGER") -> Dict[str, Any]:
    """The fields of one record with their current values: [{field, label, type, editable, choices?, value, why_locked}]."""
    sc = _schema(kind)
    rec = _read(kind, ref)
    out = []
    for f in sc:
        why = why_locked(kind, rec, f) or ("Only a manager can change this." if f["field"] in MANAGER_FIELDS.get(kind, ()) and role not in ("MANAGER", "ADMIN") else "")
        out.append({**f, "value": "" if rec.get(f["field"]) is None else rec.get(f["field"]), "editable": f["editable"] and not why, "why_locked": why})
    return {"kind": kind, "ref": ref, "fields": out}


def _field(kind: str, name: str) -> Dict[str, Any]:
    f = next((x for x in _schema(kind) if x["field"] == name or x["label"].casefold() == str(name).casefold()), None)
    if f is None:
        raise RecordError(f"There is no field '{str(name)[:30]}' on a {kind}. Fields: {', '.join(x['label'] for x in _schema(kind))}.")
    if not f["editable"]:
        raise RecordError(f"{f['label']} can't be changed here.")
    return f


def understand(f: Dict[str, Any], text: str, today: Optional[date] = None) -> Dict[str, Any]:
    """What typed text means for a field of this type: {value, understood}. Blank is refused (clearing is not done from here)."""
    raw = monday_push._strip_odd(str(text if text is not None else ""))
    if not raw and f["field"] == "monday_item_id":                                    # an empty link is "unlink": the one field that can be cleared from here
        return {"value": "", "understood": "monday item: none (unlinked)"}
    if not raw:
        raise RecordError(f"Type something for {f['label']}. Clearing a field is not done from here yet.")
    if len(raw) > 2000:
        raise RecordError("That is longer than 2,000 characters. Shorten it; it is not cut for you.")
    t, label = f["type"], f["label"]
    if t == "date":
        d = board_edit.parse_date(raw, today)
        return {"value": d.isoformat(), "understood": f"{label}: {board_edit._say(d)}"}
    if t == "choice":
        v = board_edit.match_label(raw, f.get("choices") or [], label)
        return {"value": v, "understood": f"{label}: {v}" + (f" (you typed '{raw}')" if v.casefold() != raw.casefold() else "")}
    if t == "email":
        v = raw.strip("<>").lower()
        if "@" not in v:                                                  # a name: the one person here it fits (never a guess between two)
            from app.services.coordination import updates

            words = [w for w in re.split(r"[\s._-]+", v) if w]
            hit = [p for p in updates.people() if words and all(w in updates.fold(p["name"]) or w in p["email"].split("@")[0] for w in words)] if words else []
            if len(hit) != 1:
                raise RecordError(f"'{raw[:30]}' " + ("matches no one here." if not hit else "fits more than one person: " + ", ".join(p["email"] for p in hit[:5]) + ". Use the email."))
            v = hit[0]["email"]
        if not _EMAIL.fullmatch(v):
            raise RecordError(f"'{raw[:30]}' is not an email address for {label}.")
        return {"value": v, "understood": f"{label}: {v}"}
    if t == "number":
        v = board_edit._number(raw, label)
        if v.startswith("-"):
            raise RecordError(f"{label} can't be negative.")
        return {"value": v, "understood": f"{label}: {v}"}
    return {"value": " ".join(raw.split()) if t == "text" else raw, "understood": f"{label}: {raw[:80]}"}


def _apply(kind: str, ref: str, f: Dict[str, Any], value: str, user: Dict[str, Any]) -> None:
    name = f["field"]
    if kind == "rfq":
        from app.services.coordination import rfqs

        rfqs.update(ref, {name: value}, user)
    elif kind == "po":
        from app.services.coordination import purchase_orders

        po = _read("po", ref)
        purchase_orders.update(po["id"], {name: value}, user)
    elif kind == "shipment":
        from app.services.coordination import shipments

        s = _read("shipment", ref)
        shipments.update(s["id"], {("eta" if name == "eta_current" else name): value}, user.get("email", ""), source="manual")
    elif kind == "job" and name == "monday_item_id":
        from app.services import jobs_board

        jobs_board.set_link(_read("job", ref)["ref"], value or None)
    elif kind == "job":
        from app.models.db_models import JobDB
        from app.services.coordination import jobs

        with SessionLocal() as db:
            row = jobs._find(db, ref)
            setattr(row, name, value[:256])
            db.commit()
    elif kind == "order":
        from app.models.db_models import OrderDB

        with SessionLocal() as db:
            so = str(ref).removeprefix("SO-")
            row = db.query(OrderDB).filter(OrderDB.sales_order_number.in_([so, f"SO-{so}"])).first()
            if row is None:
                raise LookupError(f"No order {ref}.")
            row.internal_notes = value
            db.commit()
    elif kind in ("product", "customer", "vendor"):
        import csv
        import io

        from app.services.csv_import_service import csv_import_service

        table, id_col = ("ITEMS", "item_id") if kind == "product" else _MASTER[kind]
        key = str(ref) if kind == "product" else str(_read(kind, ref).get(id_col) or ref)
        buf = io.StringIO()
        w = csv.DictWriter(buf, fieldnames=[id_col, name], lineterminator="\n")
        w.writeheader()
        w.writerow({id_col: key, name: value})
        res = csv_import_service.import_csv(buf.getvalue(), table, actor_email=user.get("email", "system"), actor_role=user.get("role", "SYSTEM"))
        if res.records_updated + res.records_created < 1:
            raise RecordError("; ".join(res.errors)[:300] or "The part was not changed.")


def patch(kind: str, ref: str, field: str, text: str, user: Dict[str, Any], today: Optional[date] = None) -> Dict[str, Any]:
    """Change one field from typed text. The change is made by the service that owns the record, so its rules apply (a sent PO is locked, and so on)."""
    if kind not in KINDS:
        raise RecordError(f"Records are: {', '.join(KINDS)}.")
    f = _field(kind, field)
    check_role(kind, f["field"], user)
    before = _read(kind, ref)
    why = why_locked(kind, before, f)
    if why:
        raise RecordError(f"{f['label']} can't be changed: {why[0].lower() + why[1:]}")
    got = understand(f, text, today)
    was = before.get(f["field"])
    _apply(kind, ref, f, got["value"], user)
    after = _read(kind, ref)
    now = after.get(f["field"])
    audit_log("RECORD_FIELD_CHANGED", kind, str(ref)[:60], actor_email=user.get("email", ""), actor_role=user.get("role", ""),
              detail=json.dumps({"field": f["field"], "label": f["label"], "before": _plain_value(was), "after": _plain_value(now)}, ensure_ascii=False, default=str)[:3000])
    return {"kind": kind, "ref": ref, "field": f["field"], "label": f["label"], "understood": got["understood"], "was": "" if was is None else was, "now": "" if now is None else now, "becomes": "" if now is None else now,
            "message": f"{got['understood']} (saved)"}


def _plain_value(v: Any) -> Any:
    """A value as it goes in the audit trail: text, a number or yes/no; anything bigger as short text."""
    if v is None or isinstance(v, (bool, int, float)):
        return v
    if isinstance(v, str):
        return v[:1000]
    return json.dumps(v, ensure_ascii=False, default=str)[:1000]


def undo_last(user: Dict[str, Any]) -> Dict[str, Any]:
    """Puts back the person's own most recent record edit, from the old value kept in the audit trail. Refused (and says why) if the record has since been
    changed by someone else, is now locked, or the old value was blank (a field can't be cleared from here). The undo is itself audited, and can't be undone twice."""
    from app.models.db_models import AuditLogDB

    me = (user.get("email") or "").strip().lower()
    with SessionLocal() as db:
        done = set()
        for a in db.query(AuditLogDB).filter(AuditLogDB.action == "RECORD_FIELD_UNDONE", AuditLogDB.actor_email == me).order_by(AuditLogDB.id.desc()).limit(200):
            try:
                done.add(json.loads(a.detail or "{}").get("undoes"))
            except ValueError:
                pass
        mine = None
        for a in db.query(AuditLogDB).filter(AuditLogDB.action == "RECORD_FIELD_CHANGED", AuditLogDB.actor_email == me).order_by(AuditLogDB.id.desc()).limit(50):
            if a.event_id not in done:
                mine = (a.event_id, a.resource_type, a.resource_id, a.detail)
                break
    if mine is None:
        raise LookupError("You have no record edits to undo.")
    event_id, kind, ref, detail = mine
    try:
        d = json.loads(detail or "{}")
        field = d["field"]
    except (ValueError, KeyError):
        raise RecordError("That edit was made before changes were kept, so it can't be put back from here.") from None
    f = _field(kind, field)
    check_role(kind, f["field"], user)
    rec = _read(kind, ref)
    why = why_locked(kind, rec, f)
    if why:
        raise RecordError(f"{f['label']} can't be put back now: {why[0].lower() + why[1:]}")
    if str(_plain_value(rec.get(f["field"])) if rec.get(f["field"]) is not None else "") != str(d.get("after") if d.get("after") is not None else ""):
        raise RecordError(f"{f['label']} has been changed since (it now says '{str(rec.get(f['field']))[:40]}'), so your edit was not undone.")
    before = d.get("before")
    if before in (None, "") and f["field"] != "monday_item_id":
        raise RecordError(f"{f['label']} was blank before, and a field can't be cleared from here.")
    got = understand(f, "" if before is None else str(before))
    _apply(kind, ref, f, got["value"], user)
    audit_log("RECORD_FIELD_UNDONE", kind, str(ref)[:60], actor_email=me, actor_role=user.get("role", ""),
              detail=json.dumps({"undoes": event_id, "field": f["field"], "label": f["label"], "before": d.get("after"), "after": got["value"]}, ensure_ascii=False, default=str)[:3000])
    return {"undone": True, "kind": kind, "ref": ref, "field": f["field"], "label": f["label"], "restored": got["value"], "message": f"{f['label']} is back to {got['value']}."}
