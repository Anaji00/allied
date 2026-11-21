"""The lead engine: stored accounts, contacts and leads, a do-not-contact list, outreach drafts, reply/bounce/unsubscribe handling from the mail log, suggestions mined from Allied's own history,
visible-rule scores and follow-up sequences. Rule-based (no AI). Every outward email is a DRAFT in the approval queue: nothing here sends mail. Only the app's own tables are written.
"""

import re
import statistics
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import threading

from sqlalchemy import func

from app.config import settings as app_settings
from app.core.database import SessionLocal
from app.core.logging import logger
from app.models.db_models import (CustomerAliasDB, LeadAccountDB, LeadContactDB, LeadDB, LeadDoNotContactDB, LeadOutreachDB, LeadSettingsDB)

STAGES = ("Found", "Qualified", "Contacted", "Replied", "Quote sent", "Won", "Lost")
SOURCES = ("search", "target", "manual", "import", "history")
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_ADDR = re.compile(r"[\w.+'-]+@[\w.-]+\.[A-Za-z]{2,}")


class LeadError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status, self.message = status, message


DEFAULT_TEMPLATES = {
    "new-prospect": {"title": "New prospect", "subject": "Wear parts for {company}",
                     "body": "{greeting}\n\nI'm writing from Allied Welding Systems. We make wear parts for plants like {company}{bottleneck_line}.\n\nWould a short call or a quote on one part help?\n\n{signature}\n\n{optout}"},
    "win-back": {"title": "Win back a lapsed customer", "subject": "Checking in from Allied Welding Systems",
                 "body": "{greeting}\n\nIt has been a while since your last order with us. Is there anything you need quoted, or anything we got wrong?\n\n{signature}\n\n{optout}"},
    "reorder": {"title": "Reorder reminder", "subject": "Your next order from Allied Welding Systems",
                "body": "{greeting}\n\nIt may be about time for your next order. Would you like us to send a quote for the next one?\n\n{signature}\n\n{optout}"},
    "quote-follow-up": {"title": "Quote follow-up", "subject": "Following up on our quote",
                        "body": "{greeting}\n\nI wanted to follow up on the quote we sent. Can we answer any questions or adjust it?\n\n{signature}\n\n{optout}"},
    "follow-up": {"title": "Check-in", "subject": "Checking in",
                  "body": "{greeting}\n\nI wanted to check in on my last note. If it would help, I can send a quote or answer questions.\n\n{signature}\n\n{optout}"},
}

DEFAULT_SETTINGS: Dict[str, Any] = {
    "industries": [], "regions": [], "titles": ["Plant Manager", "Maintenance Manager", "Reliability Engineer", "Purchasing Manager"], "min_spend": 0, "exclude": [],
    "follow_up_days": 4, "sequence_days": [0, 4, 10], "daily_draft_limit": 10, "business_hours": [8, 17], "sequence_mode": "off",
    "signature": "Allied Welding Systems", "phone": "", "postal_address": "", "optout_line": "If you would rather not hear from us, reply \"unsubscribe\" and we will stop.",
    "default_owner": "", "search_enabled": False,
    "weights": {"fit": 25, "history": 30, "timing": 25, "engagement": 15, "recency": 5},
    "templates": DEFAULT_TEMPLATES,
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(d: Any) -> Optional[datetime]:
    if d is None:
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


# ---------------------------------------------------------------- settings
MERGE_FIELDS = ("name", "first_name", "greeting", "title", "company", "bottleneck", "bottleneck_line", "reason", "signature", "phone", "postal_address", "optout", "part", "last_order_line", "roi")


def _check_template_fields(text: str, name: str) -> None:
    """A template may only name the plain merge fields: no attribute access, no indexing, no positional fields (which could reach into objects)."""
    import string

    try:
        fields = [f for _lit, f, _spec, _conv in string.Formatter().parse(text) if f is not None]
    except ValueError:
        raise LeadError(422, f"Template {name} has a stray brace: write {{company}} to insert a field.") from None
    for f in fields:
        if f not in MERGE_FIELDS:
            raise LeadError(422, f"Template {name} uses {{{f}}}, which is not a merge field. Use: " + ", ".join("{" + m + "}" for m in MERGE_FIELDS) + ".")


def get_settings() -> Dict[str, Any]:
    with SessionLocal() as db:
        row = db.get(LeadSettingsDB, 1)
        saved = dict(row.settings or {}) if row else {}
    out = {**DEFAULT_SETTINGS, **saved}
    out["weights"] = {**DEFAULT_SETTINGS["weights"], **(saved.get("weights") or {})}
    out["templates"] = {**DEFAULT_TEMPLATES, **(saved.get("templates") or {})}
    out["search_enabled"] = bool(saved.get("search_enabled", False)) and bool(getattr(app_settings, "LEAD_WEB_SEARCH", False))
    return out


def put_settings(data: Dict[str, Any], user: Dict[str, Any]) -> Dict[str, Any]:
    cur = get_settings()
    new = {k: cur[k] for k in DEFAULT_SETTINGS}
    new["search_enabled"] = bool(cur.get("search_enabled", False))
    for k, v in (data or {}).items():
        if k not in DEFAULT_SETTINGS:
            raise LeadError(422, f"Unknown setting '{k}'.")
        if k in ("industries", "regions", "titles", "exclude"):
            if not isinstance(v, list):
                raise LeadError(422, f"{k} must be a list.")
            v = [" ".join(str(x).split())[:100] for x in v if str(x).strip()][:50]
        elif k in ("follow_up_days", "daily_draft_limit", "min_spend"):
            try:
                v = int(v)
            except (TypeError, ValueError):
                raise LeadError(422, f"{k} must be a number.") from None
            if v < 0 or v > 100000:
                raise LeadError(422, f"{k} is out of range.")
        elif k == "sequence_days":
            if not isinstance(v, list) or not v or any(not isinstance(x, int) or x < 0 or x > 365 for x in v) or v != sorted(v):
                raise LeadError(422, "sequence_days must be increasing day numbers, like [0, 4, 10].")
        elif k == "business_hours":
            if not (isinstance(v, list) and len(v) == 2 and all(isinstance(x, int) and 0 <= x <= 24 for x in v) and v[0] < v[1]):
                raise LeadError(422, "business_hours must be two hours like [8, 17].")
        elif k == "sequence_mode":
            if v not in ("off", "suggest", "auto"):
                raise LeadError(422, "sequence_mode must be off, suggest or auto (auto still only makes drafts).")
        elif k == "weights":
            if not isinstance(v, dict) or any(x not in DEFAULT_SETTINGS["weights"] or not isinstance(n, int) or n < 0 or n > 100 for x, n in v.items()):
                raise LeadError(422, "weights must name fit, history, timing, engagement or recency with whole numbers from 0 to 100.")
            v = {**cur["weights"], **v}
        elif k == "templates":
            if not isinstance(v, dict):
                raise LeadError(422, "templates must be a set of named templates.")
            for name, t in v.items():
                if not isinstance(t, dict) or not str(t.get("subject") or "").strip() or not str(t.get("body") or "").strip() or len(str(t["body"])) > 8000:
                    raise LeadError(422, f"Template {name} needs a subject and a body (up to 8000 characters).")
            for name, t in v.items():
                _check_template_fields(str(t.get("subject")) + " " + str(t.get("body")), name)
            v = {**cur["templates"], **{n: {"title": str(t.get("title") or n)[:100], "subject": str(t["subject"])[:300], "body": str(t["body"])} for n, t in v.items()}}
        elif k == "search_enabled":
            v = bool(v)
        else:
            v = " ".join(str(v).split())[:500] if k != "signature" else str(v)[:1000]
        new[k] = v
    with SessionLocal() as db:
        row = db.get(LeadSettingsDB, 1)
        if row is None:
            row = LeadSettingsDB(id=1, settings={})
            db.add(row)
        row.settings, row.updated_by, row.updated_at = new, user.get("email"), _now()
        db.commit()
    return get_settings()


# ---------------------------------------------------------------- do-not-contact
def _norm_email(e: Any) -> str:
    return " ".join(str(e or "").split()).lower()


def norm_address(raw: Any) -> str:
    """An address written one way: compatibility letters folded (fullwidth), the display name and angle brackets taken off, lower case, no trailing dot, and no +tag in the local part."""
    import unicodedata

    text = unicodedata.normalize("NFKC", str(raw or "")).strip()
    m = re.search(r"<([^<>]+)>", text)
    if m:
        text = m.group(1)
    text = text.strip().strip("<>\"' \t").lower().rstrip(".")
    if "@" in text:
        local, _, domain = text.rpartition("@")
        text = f"{local.split('+', 1)[0]}@{domain.rstrip('.')}"
    return " ".join(text.split())


def _domain_covers(entry: str, domain: str) -> bool:
    """True when `domain` is the entry's domain or a subdomain of it (never a look-alike such as xexample.com or example.com.evil.test)."""
    return bool(entry) and (domain == entry or domain.endswith("." + entry))


def blocked(email: str) -> Optional[str]:
    """A plain reason when this address (or its domain, or a subdomain of it) is on the do-not-contact list, else None. Case, spaces, display names, +tags, fullwidth letters and a trailing dot do not get around it."""
    e = norm_address(email)
    if not e or "@" not in e:
        return None
    local, _, domain = e.rpartition("@")
    with SessionLocal() as db:
        for row in db.query(LeadDoNotContactDB).all():
            if row.kind == "domain" and _domain_covers(row.value, domain):
                hit = True
            elif row.kind == "email":
                rl, _, rd = row.value.rpartition("@")
                hit = rl == local and _domain_covers(rd, domain)
            else:
                hit = False
            if hit:
                return f"{e} is on the do-not-contact list ({row.kind} {row.value}{': ' + row.reason if row.reason else ''})."
    return None


def add_dnc(value: str, user: Optional[Dict[str, Any]] = None, reason: str = "", source: str = "manual") -> Dict[str, Any]:
    v = norm_address(value).lstrip("@")
    if not v or len(v) > 320:
        raise LeadError(422, "Give an email address or a domain.")
    kind = "email" if "@" in v else "domain"
    if kind == "email" and not _EMAIL.match(v):
        raise LeadError(422, "That is not an email address.")
    with SessionLocal() as db:
        row = db.query(LeadDoNotContactDB).filter_by(kind=kind, value=v).first()
        if row is None:
            row = LeadDoNotContactDB(kind=kind, value=v, reason=(reason or "")[:300], source=source, added_by=(user or {}).get("email"))
            db.add(row)
            db.commit()
        return {"id": row.id, "kind": row.kind, "value": row.value, "reason": row.reason, "source": row.source, "added_by": row.added_by, "created_at": row.created_at.isoformat()}


def list_dnc() -> List[Dict[str, Any]]:
    with SessionLocal() as db:
        return [{"id": r.id, "kind": r.kind, "value": r.value, "reason": r.reason, "source": r.source, "added_by": r.added_by, "created_at": r.created_at.isoformat()}
                for r in db.query(LeadDoNotContactDB).order_by(LeadDoNotContactDB.id.desc()).limit(1000)]


def remove_dnc(dnc_id: int) -> None:
    with SessionLocal() as db:
        db.query(LeadDoNotContactDB).filter(LeadDoNotContactDB.id == dnc_id).delete()
        db.commit()


# ---------------------------------------------------------------- seeding the five example accounts (once, on first use)
def seed_targets() -> int:
    from app.services.lead_agent import get_lead_agent

    with SessionLocal() as db:
        if db.query(LeadAccountDB).filter(LeadAccountDB.source == "target").count():
            return 0
        n = 0
        for t in get_lead_agent().get_target_accounts():
            acc = LeadAccountDB(company=t.company, site=t.plant_name, industry=t.industry, location=t.location, bottlenecks=list(t.primary_wear_bottlenecks or []),
                                estimated_annual_spend=float(t.estimated_annual_spend or 0), source="target")
            db.add(acc)
            db.flush()
            contact = LeadContactDB(account_id=acc.id, name=t.key_contact, title=t.contact_title, source="target", consent_note="Example target account from the original list; check before contacting")
            db.add(contact)
            db.flush()
            db.add(LeadDB(account_id=acc.id, contact_id=contact.id, status="Qualified" if str(t.status).upper() == "QUALIFIED" else "Found", source="target", reason=f"Example target account: {t.plant_name}"))
            n += 1
        db.commit()
        return n


# ---------------------------------------------------------------- views
def _lead_dict(l: LeadDB, acc: Optional[LeadAccountDB], con: Optional[LeadContactDB]) -> Dict[str, Any]:
    return {"id": l.id, "account_id": l.account_id, "contact_id": l.contact_id, "name": con.name if con else None, "title": con.title if con else None, "company": acc.company if acc else None,
            "industry": acc.industry if acc else None, "location": acc.location if acc else None, "email": con.email if con else None, "phone": con.phone if con else None,
            "source": l.source, "source_detail": l.source_detail, "profile_url": None, "status": l.status, "owner": l.owner, "reason": l.reason, "score": l.score, "score_parts": l.score_parts or [],
            "next_touch_at": l.next_touch_at.isoformat() if l.next_touch_at else None, "last_touch_at": l.last_touch_at.isoformat() if l.last_touch_at else None, "notes": l.notes,
            "estimated_annual_spend": acc.estimated_annual_spend if acc else None, "do_not_contact": bool(con and con.email and blocked(con.email)),
            "bad_address": bool(con and con.bad_address), "sequence": {"name": l.sequence_name, "step": l.sequence_step, "paused": bool(l.sequence_paused)},
            "monday_item_id": l.monday_item_id, "created_at": l.created_at.isoformat() if l.created_at else None}


def _is_due(l: LeadDB, now: datetime) -> bool:
    return bool(l.next_touch_at and _aware(l.next_touch_at) <= now and l.status in ("Found", "Qualified", "Contacted"))


def list_leads(status: str = "", owner: str = "", due: bool = False, q: str = "", limit: int = 200, offset: int = 0) -> Dict[str, Any]:
    now = _now()                                                  # reading never writes: the five example accounts are added only by seed_targets (POST /leads/seed-examples)
    with SessionLocal() as db:
        query = db.query(LeadDB, LeadAccountDB, LeadContactDB).outerjoin(LeadAccountDB, LeadAccountDB.id == LeadDB.account_id).outerjoin(LeadContactDB, LeadContactDB.id == LeadDB.contact_id).filter(LeadDB.archived_at.is_(None))
        if status:
            if status not in STAGES:
                raise LeadError(422, f"The status must be one of: {', '.join(STAGES)}.")
            query = query.filter(LeadDB.status == status)
        if owner:
            query = query.filter(LeadDB.owner == owner.strip().lower())
        rows = query.order_by(LeadDB.score.desc().nullslast(), LeadDB.id.desc()).all()
        out = []
        needle = " ".join(q.split()).lower()
        for l, a, c in rows:
            d = _lead_dict(l, a, c)
            if needle and needle not in " ".join(str(d.get(k) or "") for k in ("name", "company", "email", "reason", "notes")).lower():
                continue
            if due and not _is_due(l, now):
                continue
            out.append(d)
        due_today = sum(1 for l, _a, _c in rows if _is_due(l, now))
    return {"items": out[offset:offset + max(1, min(limit, 500))], "total": len(out), "due_today": due_today}


def get_lead(lead_id: int) -> Dict[str, Any]:
    with SessionLocal() as db:
        l = db.get(LeadDB, lead_id)
        if l is None or l.archived_at:
            raise LeadError(404, "No such lead.")
        return _lead_dict(l, db.get(LeadAccountDB, l.account_id) if l.account_id else None, db.get(LeadContactDB, l.contact_id) if l.contact_id else None)


# ---------------------------------------------------------------- adding, deduping, changing
def _find_duplicate(db, company: str, name: str, email: str, domain: str) -> Optional[LeadDB]:
    e = _norm_email(email)
    if e:
        hit = db.query(LeadDB).join(LeadContactDB, LeadContactDB.id == LeadDB.contact_id).filter(LeadDB.archived_at.is_(None), LeadContactDB.email == e).first()
        if hit:
            return hit
    if company and name:
        hit = (db.query(LeadDB).join(LeadContactDB, LeadContactDB.id == LeadDB.contact_id).join(LeadAccountDB, LeadAccountDB.id == LeadDB.account_id)
               .filter(LeadDB.archived_at.is_(None), LeadAccountDB.company.ilike(company), LeadContactDB.name.ilike(name)).first())
        if hit:
            return hit
    return None


def add_lead(data: Dict[str, Any], user: Dict[str, Any]) -> Dict[str, Any]:
    """Adds one lead (an account and a contact are made or reused). A duplicate (same email, or same company and name) is not added again: the existing lead comes back with duplicate=True."""
    company = " ".join(str(data.get("company") or "").split())[:256]
    name = " ".join(str(data.get("name") or "").split())[:256]
    email = _norm_email(data.get("email"))
    if not (company or name or email):
        raise LeadError(422, "Give at least a company, a name or an email.")
    if email and not _EMAIL.match(email):
        raise LeadError(422, "That email address does not look right.")
    source = data.get("source") or "manual"
    if source not in SOURCES:
        raise LeadError(422, f"The source must be one of: {', '.join(SOURCES)}.")
    status = data.get("status") or "Found"
    if status not in STAGES:
        raise LeadError(422, f"The status must be one of: {', '.join(STAGES)}.")
    domain = email.rsplit("@", 1)[-1] if email else ""
    with SessionLocal() as db:
        dup = _find_duplicate(db, company, name, email, domain)
        if dup is not None:
            out = get_lead(dup.id)
            return {**out, "duplicate": True}
        acc = None
        if company:
            acc = db.query(LeadAccountDB).filter(LeadAccountDB.archived_at.is_(None), LeadAccountDB.company.ilike(company)).first()
        if acc is None:
            acc = LeadAccountDB(company=company or name or email, site=data.get("site"), industry=data.get("industry"), location=data.get("location"), domain=domain or None,
                                bottlenecks=list(data.get("bottlenecks") or []), estimated_annual_spend=data.get("estimated_annual_spend"), source=source)
            db.add(acc)
            db.flush()
        con = LeadContactDB(account_id=acc.id, name=name or company or email, title=data.get("title"), email=email or None, phone=data.get("phone"), source=source, consent_note=(data.get("consent_note") or None))
        db.add(con)
        db.flush()
        lead = LeadDB(account_id=acc.id, contact_id=con.id, status=status, source=source, source_detail=(data.get("source_detail") or None), reason=data.get("reason"),
                      owner=(data.get("owner") or get_settings()["default_owner"] or user.get("email") or "").lower() or None, notes=data.get("notes"))
        db.add(lead)
        db.commit()
        lid = lead.id
    score_lead(lid)
    return {**get_lead(lid), "duplicate": False}


def add_many(rows: List[Dict[str, Any]], user: Dict[str, Any]) -> Dict[str, Any]:
    out, dups, errs = [], 0, []
    for i, r in enumerate(rows[:500]):
        try:
            got = add_lead(r, user)
            dups += bool(got.get("duplicate"))
            out.append(got)
        except LeadError as exc:
            errs.append({"row": i + 1, "error": exc.message})
    return {"added": sum(1 for x in out if not x["duplicate"]), "duplicates": dups, "errors": errs, "items": out}


def patch_lead(lead_id: int, data: Dict[str, Any]) -> Dict[str, Any]:
    with SessionLocal() as db:
        l = db.get(LeadDB, lead_id)
        if l is None or l.archived_at:
            raise LeadError(404, "No such lead.")
        con = db.get(LeadContactDB, l.contact_id) if l.contact_id else None
        acc = db.get(LeadAccountDB, l.account_id) if l.account_id else None
        for k, v in (data or {}).items():
            if k == "status":
                if v not in STAGES:
                    raise LeadError(422, f"The status must be one of: {', '.join(STAGES)}.")
                l.status = v
                if v in ("Replied", "Won", "Lost"):
                    l.sequence_paused = True
            elif k == "owner":
                l.owner = (str(v or "").strip().lower() or None)
            elif k == "notes":
                l.notes = str(v or "")[:4000]
            elif k in ("next_touch_at", "last_touch_at"):
                try:
                    setattr(l, k, _aware(datetime.fromisoformat(str(v))) if v else None)
                except ValueError:
                    raise LeadError(422, f"{k} must be a date like 2026-12-01.") from None
            elif k == "sequence_paused":
                l.sequence_paused = bool(v)
            elif k in ("name", "title", "phone") and con is not None:
                setattr(con, k, str(v or "")[:256] or None)
            elif k == "email" and con is not None:
                e = _norm_email(v)
                if e and not _EMAIL.match(e):
                    raise LeadError(422, "That email address does not look right.")
                con.email, con.bad_address = e or None, False
            elif k in ("company", "industry", "location") and acc is not None:
                setattr(acc, k, str(v or "")[:256] or None)
            elif k == "estimated_annual_spend" and acc is not None:
                acc.estimated_annual_spend = float(v) if v not in (None, "") else None
            else:
                raise LeadError(422, f"'{k}' cannot be changed here.")
        l.updated_at = _now()
        db.commit()
    score_lead(lead_id)
    return get_lead(lead_id)


def archive_lead(lead_id: int) -> Dict[str, Any]:
    with SessionLocal() as db:
        l = db.get(LeadDB, lead_id)
        if l is None or l.archived_at:
            raise LeadError(404, "No such lead.")
        l.archived_at = _now()
        db.commit()
    return {"archived": True, "id": lead_id}


# ---------------------------------------------------------------- scoring: visible rules, with the reason for every point
def _score_parts(l: LeadDB, acc: Optional[LeadAccountDB], con: Optional[LeadContactDB], st: Dict[str, Any]) -> List[Dict[str, Any]]:
    w = st["weights"]
    parts: List[Dict[str, Any]] = []

    def add(label: str, key: str, fraction: float, why: str) -> None:
        pts = int(round(w[key] * max(0.0, min(1.0, fraction))))
        parts.append({"label": f"{label}: {why}", "points": pts, "max": w[key]})

    inds = [i.lower() for i in st["industries"]]
    fit = 0.0
    why = "no industry or region set to compare"
    if acc is not None:
        hits = []
        if inds and acc.industry and any(i in acc.industry.lower() or acc.industry.lower() in i for i in inds):
            fit += 0.5
            hits.append("industry matches")
        if acc.bottlenecks:
            fit += 0.3
            hits.append("a known wear problem")
        if st["min_spend"] and (acc.estimated_annual_spend or 0) >= st["min_spend"]:
            fit += 0.2
            hits.append("spend over the minimum")
        why = ", ".join(hits) or "nothing matches your profile yet"
    add("Fit", "fit", fit, why)
    hist = {"history": 1.0, "target": 0.4}.get(l.source, 0.0)
    add("History", "history", hist, "we have past orders or quotes with them" if l.source == "history" else "a target account" if l.source == "target" else "no past orders or quotes found")
    timing = 0.0
    twhy = "nothing is due"
    if l.reason and re.search(r"reorder|usual gap|due", l.reason, re.I):
        timing, twhy = 1.0, "a reorder looks due"
    elif l.reason and re.search(r"quote", l.reason, re.I):
        timing, twhy = 0.6, "a quote is waiting for an answer"
    elif l.next_touch_at and _aware(l.next_touch_at) <= _now():
        timing, twhy = 0.5, "a follow-up is due"
    add("Timing", "timing", timing, twhy)
    eng = {"Replied": 1.0, "Quote sent": 1.0, "Contacted": 0.2}.get(l.status, 0.0)
    add("Engagement", "engagement", eng, {"Replied": "they replied", "Quote sent": "a quote is out", "Contacted": "we have written to them"}.get(l.status, "no contact yet"))
    last = _aware(l.last_touch_at or l.created_at)
    days = (_now() - last).days if last else 999
    add("Recency", "recency", 1.0 if days <= 14 else 0.5 if days <= 60 else 0.0, f"last activity {days} days ago" if days < 999 else "no activity yet")
    return parts


def _lead_row(lead_id: int) -> LeadDB:
    with SessionLocal() as db:
        l = db.get(LeadDB, lead_id)
        db.expunge(l)
        return l


def score_lead(lead_id: int) -> Optional[int]:
    st = get_settings()
    with SessionLocal() as db:
        l = db.get(LeadDB, lead_id)
        if l is None:
            return None
        parts = _score_parts(l, db.get(LeadAccountDB, l.account_id) if l.account_id else None, db.get(LeadContactDB, l.contact_id) if l.contact_id else None, st)
        total = sum(p["points"] for p in parts)
        denom = sum(p["max"] for p in parts) or 1
        l.score, l.score_parts = int(round(100 * total / denom)), parts
        db.commit()
        return l.score


# ---------------------------------------------------------------- outreach drafts (never sent)
class _Safe(dict):
    def __missing__(self, key):
        return ""


def _merge(text: str, fields: Dict[str, Any]) -> str:
    return str(text).format_map(_Safe({k: ("" if v is None else v) for k, v in fields.items()}))


def _fields(l: LeadDB, acc: Optional[LeadAccountDB], con: Optional[LeadContactDB], st: Dict[str, Any]) -> Dict[str, Any]:
    raw = (con.name if con and con.name else "")
    generic = (not raw) or raw.lower().startswith(("buyer", "purchasing", "accounts", "sales", "team", "info", "unknown")) or "@" in raw
    first = "" if generic else raw.split()[0]
    greeting = f"Hello {first}," if first else (f"Hello {acc.company} team," if acc and acc.company else "Hello,")
    bott = (acc.bottlenecks[0] if acc and acc.bottlenecks else "")
    footer = (st["optout_line"] + (f"\n{st['postal_address']}" if st["postal_address"] else "")).strip()
    return {"name": con.name if con else "", "first_name": first, "greeting": greeting, "title": con.title if con else "", "company": acc.company if acc else "", "bottleneck": bott,
            "bottleneck_line": f", especially around {bott}" if bott else "", "reason": l.reason or "", "signature": st["signature"] + (f"\n{st['phone']}" if st["phone"] else ""),
            "phone": st["phone"], "postal_address": st["postal_address"], "optout": footer, "part": "", "last_order_line": "", "roi": ""}


def _pick_template(l: LeadDB) -> str:
    r = (l.reason or "").lower()
    if "reorder" in r or "usual gap" in r:
        return "reorder"
    if "quote" in r:
        return "quote-follow-up"
    if "lapsed" in r or "no order" in r or l.source == "history":
        return "win-back"
    return "new-prospect"


def _step_template(l: LeadDB, step: int) -> str:
    """Step 1 is the situation's own template; later steps are a quote follow-up only when a quote exists, else a plain check-in."""
    if step == 0:
        return _pick_template(l)
    return "quote-follow-up" if "quote" in (l.reason or "").lower() else "follow-up"


def draft_outreach(lead_id: int, user: Dict[str, Any], template: str = "", step: int = 0, why: str = "") -> Dict[str, Any]:
    """A draft in the approval queue for this lead, from a template with merge fields. Refused for a do-not-contact or bad address. Nothing is sent."""
    import uuid

    from app.services.coordination import autopilot
    from app.services.coordination.autopilot import Intent

    st = get_settings()
    with SessionLocal() as db:
        l = db.get(LeadDB, lead_id)
        if l is None or l.archived_at:
            raise LeadError(404, "No such lead.")
        acc = db.get(LeadAccountDB, l.account_id) if l.account_id else None
        con = db.get(LeadContactDB, l.contact_id) if l.contact_id else None
        if con is None or not con.email:
            raise LeadError(409, "This lead has no email address yet: add one first.")
        reason = blocked(con.email)
        if reason:
            raise LeadError(409, reason + " No draft was made.")
        if con.bad_address:
            raise LeadError(409, f"{con.email} bounced earlier, so no draft was made. Fix the address first.")
        to_email = con.email
        key = template or _pick_template(l)
        if key == "quote-follow-up" and "quote" not in (l.reason or "").lower():        # never "the quote we sent" when none was
            key = "follow-up"
        t = st["templates"].get(key)
        if t is None:
            raise LeadError(422, f"No template named {key}.")
        f = _fields(l, acc, con, st)
        subject, body = _merge(t["subject"], f), _merge(t["body"], f)
        if st["postal_address"] == "":
            body += "\n\n(Add the company postal address in the lead settings before sending: commercial email must carry one.)"
        sid = f"lead-{lead_id}-{key}-{step}-{uuid.uuid4().hex[:6]}"
        owner = (user.get("email") or l.owner or "").lower()
        intent = Intent("draft", sid, f"Lead outreach to {con.name} at {acc.company if acc else ''}: {t.get('title', key)}", to=[owner] if owner else [], tool="send_email",
                        args={"to": con.email, "subject": subject[:200], "body": body, "_why": [why or f"Lead outreach from template '{key}'" + (f", step {step + 1}" if step else ""), "Nothing is sent until you approve it"], "_lead_id": lead_id})
        did = autopilot.carry_out("lead_outreach", "suggest", [intent], explicit=True)
        if not did or str(did[0].get("outcome", "")).startswith("failed"):
            raise LeadError(500, "The draft could not be made.")
        action_id = uuid.uuid5(uuid.NAMESPACE_URL, f"allied-autopilot:{sid}").hex
        days = int(st["follow_up_days"])
        l.status = "Contacted" if l.status in ("Found", "Qualified") else l.status
        l.last_touch_at = _now()
        l.next_touch_at = _now() + timedelta(days=days)
        l.updated_at = _now()
        db.add(LeadOutreachDB(lead_id=lead_id, kind="draft", template=key, step=step, action_id=action_id, note=(why or "")[:500], by=user.get("email")))
        db.commit()
    score_lead(lead_id)
    return {"lead_id": lead_id, "template": key, "to": to_email, "subject": subject, "body": body, "action_id": action_id, "next_touch_at": get_lead(lead_id)["next_touch_at"]}


def outreach_log(lead_id: int) -> List[Dict[str, Any]]:
    with SessionLocal() as db:
        return [{"id": r.id, "kind": r.kind, "template": r.template, "step": r.step, "action_id": r.action_id, "mail_id": r.mail_id, "note": r.note, "by": r.by, "at": r.at.isoformat()}
                for r in db.query(LeadOutreachDB).filter(LeadOutreachDB.lead_id == lead_id).order_by(LeadOutreachDB.id.desc()).limit(200)]


# ---------------------------------------------------------------- the mail log: replies, bounces, out of office, unsubscribes
_UNSUB = re.compile(r"\b(?:unsubscribe|remove (?:me|us)(?: from)?|stop (?:emailing|contacting|sending|messaging)|do not (?:contact|e-?mail|message) me|do not (?:contact|e-?mail)|don't (?:contact|e-?mail|message)|take (?:me|us) off|opt[- ]?out|cancel my subscription|not interested,? please stop|darse de baja|darme de baja|dar de baja|cease (?:and desist|all (?:e-?mail|contact)|(?:e-?mail|contact))|cancelar (?:la |mi )?suscripci[oó]n|no me (?:escriba|env[ií]e)|abmelden|austragen|nicht mehr (?:kontaktieren|schreiben)|se d[ée]sabonner|ne plus me contacter)\b"
                    r"|^\s*stop[.!\s]*$|d[ée]sabonn|d[ée]sinscri|退订|取消订阅|不要再发|请勿再发|hủy đăng ký|huỷ đăng ký|ngừng gửi|đừng gửi", re.I | re.M)
_OOO = re.compile(r"out of (?:the )?office|automatic reply|auto-?reply|on vacation|away from (?:my )?(?:desk|email)", re.I)
_BACK = re.compile(r"(?:back|return(?:ing)?)(?: to the office)?(?: on)?\s+(?:on\s+)?(\d{4}-\d{2}-\d{2})", re.I)
_QUOTE_STOP = re.compile(r"^\s*(on .{5,200} wrote:?|am .{5,200} schrieb.*|le .{5,200} a [eé]crit.*|-{2,}\s*(original|forwarded) message\s*-{2,}.*|begin forwarded message:?|_{5,}|(from|von|de)\s*:.*)\s*$", re.I)
_MAILER = ("mailer-daemon@", "postmaster@")
_OTHER_PERSON = re.compile(r"colleague|co-?worker|boss|manager|assistant|\bmy team\b|\bhim\b|\bher\b|\bthem\b|on behalf|someone|\bour\b.{0,12}\b(?:buyer|engineer|plant)\b", re.I)


def _mail_system_domains() -> set:
    """The domains a real bounce comes from: the mailbox provider's own and the company's mail domain. A stranger's "mailer-daemon@" does not count."""
    out = {"gmail.com", "googlemail.com", "google.com"}
    host = ".".join((app_settings.IMAP_HOST or "").lower().split(".")[-2:])
    if host:
        out.add(host)
    frm = str(app_settings.EMAIL_FROM or "").rsplit("@", 1)[-1].lower()
    if frm:
        out.add(frm)
    return out


_BARE_STOP = re.compile(r"^\W*(?:please\s+)?(?:stop|remove|unsubscribe|opt out|remove me|remove us|cease)\W*$", re.I)
_NEGATION = re.compile(r"\b(?:not|won't|will not|never|don't|dont|cannot|can't|no one|without)\b", re.I)
_QUESTION = re.compile(r"\bhow\s+(?:do|can|to|would)\b|\bcan\s+i\b|\bwhere\s+(?:do|can)\b", re.I)


def _is_stop_request(top: str, subject: str) -> bool:
    """A stop request is short and about the sender: a bare "stop" / "remove" counts; otherwise the phrase sits in the first 400 characters, is not about someone else ("my colleague Bob"), is not
    negated ("we will not unsubscribe", "please don't stop sending quotes") and is not a question ("how do I unsubscribe from the other newsletter?")."""
    if _BARE_STOP.match(top or "") or _BARE_STOP.match(subject or ""):
        return True
    for src in (subject or "", top[:400]):
        for m in _UNSUB.finditer(src):
            window = src[max(0, m.start() - 60): m.end() + 60]
            before = src[max(0, m.start() - 25): m.start()]
            own_negation = m.group(0).lower().startswith(("do not", "don't", "not interested", "no me", "nicht", "ne plus"))
            if _OTHER_PERSON.search(window):
                continue
            if not own_negation and _NEGATION.search(before):
                continue
            sentence_end = src.find("?", m.end())
            sentence_start = max(src.rfind(".", 0, m.start()), src.rfind("!", 0, m.start()), src.rfind("\n", 0, m.start())) + 1
            if (sentence_end != -1 and (sentence_end - m.end()) < 80 and "." not in src[m.end():sentence_end]) or _QUESTION.search(src[sentence_start:m.start()]):
                continue
            return len(top) <= 600
    return False


def top_reply(text: str, limit: int = 1200) -> str:
    """Only what the person wrote themselves: quoted lines (">"), "On ... wrote:", forwarded blocks and header blocks end it."""
    out = []
    for line in str(text or "").splitlines():
        if line.lstrip().startswith(">") or _QUOTE_STOP.match(line):
            if line.lstrip().startswith(">"):
                continue
            break
        out.append(line)
    return "\n".join(out).strip()[:limit]


def on_mail(mail_id: int, sender: str, subject: str, body: str, trusted: bool, dsn: bool = False) -> Optional[str]:
    """Called for each new email. Marks the matching lead Replied / pauses it / adds to do-not-contact, by rule. Mail that failed the sender check is ignored (a forged reply must not change a lead).
    Rules read only the top of the reply (our own footer, quoted back, never counts); a bounce must come from a mail system; an out-of-office never clears a pause set by a reply, bounce or unsubscribe."""
    if not trusted:
        return None
    addr = norm_address(sender)
    top = top_reply(body)
    text = f"{subject}\n{top}"
    with SessionLocal() as db:
        if addr.startswith(_MAILER) and (dsn or addr.rsplit("@", 1)[-1] in _mail_system_domains()):
            failed = []
            # A real delivery report names the failed recipient in its own fields (Final-Recipient / Original-Recipient): when they are there, only those addresses count, not every address
            # that appears in the quoted original (its From, Cc, signature...). Without them (a plain-text bounce) the addresses in the body are used, as before.
            fields = [ln for ln in (body or "").splitlines() if re.match(r"\s*(final|original)-recipient\s*:", ln, re.I)]
            source = "\n".join(fields) if fields else (body or "")
            for a in dict.fromkeys(norm_address(x) for x in _ADDR.findall(source)):
                if a and not a.startswith(_MAILER):
                    failed.append(a)
            for e in failed[:3]:                                                        # the failed recipient, not every address mentioned
                con = db.query(LeadContactDB).filter(LeadContactDB.email == e).first()
                if con:
                    con.bad_address = True
                    for l in db.query(LeadDB).filter(LeadDB.contact_id == con.id, LeadDB.archived_at.is_(None)):
                        l.sequence_paused = True
                        db.add(LeadOutreachDB(lead_id=l.id, kind="bounce", mail_id=mail_id, note=f"{e} bounced"))
            db.commit()
            return "bounce"
        if not addr or "@" not in addr:
            return None
        domain = addr.rsplit("@", 1)[-1]
        cons = db.query(LeadContactDB).filter(func.lower(LeadContactDB.email) == addr).all()
        if not cons:
            cons = [c for c in db.query(LeadContactDB).filter(LeadContactDB.email.isnot(None)) if norm_address(c.email) == addr]
        if not cons:
            dom_acc = [a.id for a in db.query(LeadAccountDB).filter(LeadAccountDB.domain == domain)]
            cons = db.query(LeadContactDB).filter(LeadContactDB.account_id.in_(dom_acc)).all() if dom_acc else []
        if not cons:
            return None
        ids = [c.id for c in cons]
        leads_ = db.query(LeadDB).filter(LeadDB.contact_id.in_(ids), LeadDB.archived_at.is_(None)).all()
        if not leads_:
            return None
        if _is_stop_request(top, subject):
            add_dnc(addr, None, f"They asked to stop (mail {mail_id})", "mail")
            for l in leads_:
                l.sequence_paused = True
                l.next_touch_at = None
                db.add(LeadOutreachDB(lead_id=l.id, kind="unsubscribe", mail_id=mail_id, note="They asked to stop emailing"))
            db.commit()
            return "unsubscribe"
        if _OOO.search(text):
            m = _BACK.search(text)
            back = None
            if m:
                try:
                    d = datetime.fromisoformat(m.group(1)).replace(tzinfo=timezone.utc)
                    back = d if _now() < d <= _now() + timedelta(days=60) else None          # a date in the past or far ahead is ignored
                except ValueError:
                    back = None
            for l in leads_:
                if l.sequence_paused or l.status not in ("Found", "Qualified", "Contacted"):
                    db.add(LeadOutreachDB(lead_id=l.id, kind="out_of_office", mail_id=mail_id, note="Out of office (nothing changed: already paused or answered)"))
                    continue
                if back:
                    l.next_touch_at = back
                else:
                    l.sequence_paused = True
                db.add(LeadOutreachDB(lead_id=l.id, kind="out_of_office", mail_id=mail_id, note="Out of office" + (f", back {back:%Y-%m-%d}" if back else "")))
            db.commit()
            return "out_of_office"
        for l in leads_:
            if l.status in ("Found", "Qualified", "Contacted"):
                l.status = "Replied"
            l.last_touch_at, l.sequence_paused, l.next_touch_at = _now(), True, None
            db.add(LeadOutreachDB(lead_id=l.id, kind="reply", mail_id=mail_id, note=f"Reply from {addr}"))
        db.commit()
        names = [l.id for l in leads_]
    try:
        from app.services.coordination import notify
        from app.services.coordination.autopilot import reviewers

        notify.notify("lead_replied", f"lead-reply:{names[0]}:{mail_id}", f"A lead replied: {addr}", f"{addr} answered our outreach (mail {mail_id}).", to=reviewers(), link="/leads", severity="warning")
    except Exception as exc:  # noqa: BLE001 - the lead is marked either way
        logger.info(f"Lead reply alert not raised: {type(exc).__name__}")
    for lid in names:
        score_lead(lid)
    return "reply"


# ---------------------------------------------------------------- summary
def summary(manager: bool = True) -> Dict[str, Any]:
    now = _now()
    with SessionLocal() as db:
        leads = db.query(LeadDB).filter(LeadDB.archived_at.is_(None)).all()
        accs = {a.id: a for a in db.query(LeadAccountDB)}
        week = now - timedelta(days=7)
        replied = db.query(LeadOutreachDB).filter(LeadOutreachDB.kind == "reply", LeadOutreachDB.at >= week).count()
        by_source: Dict[str, Dict[str, int]] = {}
        for l in leads:
            s = by_source.setdefault(l.source, {"leads": 0, "won": 0, "lost": 0})
            s["leads"] += 1
            s["won"] += l.status == "Won"
            s["lost"] += l.status == "Lost"
        won, lost = sum(1 for l in leads if l.status == "Won"), sum(1 for l in leads if l.status == "Lost")
        pipeline = sum((accs[l.account_id].estimated_annual_spend or 0) for l in leads if l.account_id in accs and l.status not in ("Won", "Lost"))
    return {"by_stage": {s: sum(1 for l in leads if l.status == s) for s in STAGES}, "due_today": sum(1 for l in leads if _is_due(l, now)), "replied_week": replied, "won": won, "lost": lost,
            "win_rate": round(won / (won + lost), 2) if (won + lost) else None, "pipeline_value": pipeline if manager else None, "by_source": by_source}


def on_mail_by_message(message_id: str, sender: str, subject: str, body: str, trusted: bool, dsn: bool = False) -> Optional[str]:
    """The same as on_mail, finding the mail-log row of this message first (so the lead's log can point at it)."""
    from app.models.db_models import MailLogDB

    with SessionLocal() as db:
        row = db.query(MailLogDB.id, MailLogDB.direction).filter(MailLogDB.message_id == message_id[:512]).first()
    if row is None or row.direction == "out":
        return None
    return on_mail(row.id, sender, subject, body, trusted, dsn)


# ---------------------------------------------------------------- customer name aliases (CT-KC = CertainTeed Kansas City): matching is by an editable list, never a fuzzy merge
def _norm_name(n: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(n or "").lower()).strip()


def alias_map() -> Dict[str, str]:
    with SessionLocal() as db:
        return {_norm_name(a.alias): a.canonical for a in db.query(CustomerAliasDB)}


def canonical(name: str, aliases: Optional[Dict[str, str]] = None) -> str:
    aliases = alias_map() if aliases is None else aliases
    return aliases.get(_norm_name(name), " ".join(str(name or "").split()))


def add_alias(alias: str, canonical_name: str, user: Dict[str, Any]) -> Dict[str, Any]:
    a, c = " ".join(str(alias or "").split())[:256], " ".join(str(canonical_name or "").split())[:256]
    if not a or not c:
        raise LeadError(422, "Give the other name and the name it stands for.")
    with SessionLocal() as db:
        row = db.query(CustomerAliasDB).filter(CustomerAliasDB.alias == a).first()
        if row is None:
            row = CustomerAliasDB(alias=a, canonical=c, created_by=user.get("email"))
            db.add(row)
        else:
            row.canonical = c
        db.commit()
        return {"alias": a, "canonical": c}


def list_aliases() -> List[Dict[str, Any]]:
    with SessionLocal() as db:
        return [{"id": r.id, "alias": r.alias, "canonical": r.canonical} for r in db.query(CustomerAliasDB).order_by(CustomerAliasDB.canonical, CustomerAliasDB.alias)]


# ---------------------------------------------------------------- suggestions mined from Allied's own orders and quotes (read only)
def _suggestion_id(kind: str, company: str) -> str:
    return f"{kind}:{_norm_name(company).replace(' ', '-')}"[:120]


def suggestions(manager: bool = True) -> List[Dict[str, Any]]:
    """Leads Allied already has in its own history, each with the plain sentence that explains it:
    lapsed (no order in 12+ months but 2+ before), quote not won (a quote sent 30+ days ago with no outcome), reorder due (the median gap between a customer's orders says the next is due within 30 days)."""
    from app.models.db_models import OrderDB, RfqDB

    aliases = alias_map()
    now = _now()
    orders: Dict[str, List[datetime]] = {}
    spend: Dict[str, float] = {}
    with SessionLocal() as db:
        for name, at, total in db.query(OrderDB.customer_name, OrderDB.created_at, OrderDB.total_revenue).filter(OrderDB.status.notin_(("CANCELLED",))):
            if name and at:
                key = canonical(name, aliases)
                orders.setdefault(key, []).append(_aware(at))
                spend[key] = spend.get(key, 0.0) + float(total or 0)
        quotes = [(r.customer, r.number, r.subject, r.contact_email, _aware(r.received_at)) for r in db.query(RfqDB).filter(RfqDB.status == "QUOTE_SENT")]
        have = {(l.reason or "")[:60] for l in db.query(LeadDB).filter(LeadDB.archived_at.is_(None))}
    out: List[Dict[str, Any]] = []
    for company, dates in orders.items():
        dates.sort()
        last = dates[-1]
        gap_days = None
        if len(dates) >= 3:
            gaps = [(b - a).days for a, b in zip(dates, dates[1:]) if (b - a).days > 0]
            gap_days = int(statistics.median(gaps)) if gaps else None
        if len(dates) >= 2 and (now - last).days >= 365:
            out.append({"kind": "lapsed", "account": company, "sentence": f"{company} ordered {len(dates)} times, the last on {last:%b %d, %Y}, and nothing in {(now - last).days // 30} months.",
                        "suggested_template": "win-back", "spend": round(spend.get(company, 0.0), 2)})
        elif gap_days:
            due = last + timedelta(days=gap_days)
            days_to = (due - now).days
            if days_to <= 30 and (now - last).days < 365:
                when = f"is due in {days_to} days" if days_to >= 0 else f"was due {-days_to} days ago"
                out.append({"kind": "reorder_due", "account": company, "sentence": f"{company} ordered {len(dates)} times, last on {last:%b %d}, usual gap {gap_days} days: the next order {when}.",
                            "suggested_template": "reorder", "spend": round(spend.get(company, 0.0), 2)})
    for company, number, subject, contact, at in quotes:
        age = (now - at).days if at else 0
        if age >= 30:
            key = canonical(company or "", aliases)
            out.append({"kind": "quote_not_won", "account": key, "sentence": f"Quote {number} to {key} ({subject[:60]}) went out {age} days ago and has no outcome.", "suggested_template": "quote-follow-up",
                        "email": contact or None, "spend": 0.0})
    for s in out:
        s["id"] = _suggestion_id(s["kind"], s["account"])
        s["already_a_lead"] = s["sentence"][:60] in have
        if not manager:
            s["spend"] = None                                                              # what a customer spent is for managers
    return out


def make_lead_from_suggestion(suggestion_id: str, user: Dict[str, Any]) -> Dict[str, Any]:
    hit = next((s for s in suggestions(True) if s["id"] == suggestion_id), None)
    if hit is None:
        raise LeadError(404, "That suggestion is no longer there.")
    return add_lead({"company": hit["account"], "name": f"Buyer at {hit['account']}", "email": hit.get("email"), "source": "history", "source_detail": hit["kind"], "reason": hit["sentence"],
                     "estimated_annual_spend": hit.get("spend") or None}, user)


# ---------------------------------------------------------------- follow-up sequences: drafts only, in business hours, up to a daily limit
def _business_hours_now() -> bool:
    from app.services.coordination import notify

    now = notify.now_local()
    lo, hi = get_settings()["business_hours"]
    return now.weekday() < 5 and lo <= now.hour < hi


_run_lock = threading.Lock()


def run_sequences(user: Dict[str, Any], dry_run: bool = True, ignore_hours: bool = False) -> Dict[str, Any]:
    with _run_lock:                                                                      # two runs at once (a double click) cannot both draft the same step
        return _run_sequences(user, dry_run, ignore_hours)


def _run_sequences(user: Dict[str, Any], dry_run: bool, ignore_hours: bool) -> Dict[str, Any]:
    """Makes the next step's draft for every lead whose sequence is due (Day 0, 4, 10 by default). Stops for a lead that replied, bounced, unsubscribed, is do-not-contact or paused. Drafts only."""
    st = get_settings()
    mode = st["sequence_mode"]
    if mode == "off" and not dry_run:
        return {"mode": "off", "made": [], "would_make": [], "skipped": [], "note": "Sequences are switched off."}
    if not dry_run and not ignore_hours and not _business_hours_now():
        return {"mode": mode, "made": [], "would_make": [], "skipped": [], "note": "Outside business hours: nothing was drafted."}
    now = _now()
    days = st["sequence_days"]
    todo, skipped = [], []
    with SessionLocal() as db:
        for l in db.query(LeadDB).filter(LeadDB.archived_at.is_(None), LeadDB.sequence_name.isnot(None)).all():
            if l.status not in ("Found", "Qualified", "Contacted") or l.sequence_paused:
                skipped.append({"lead_id": l.id, "why": "paused or finished"})
            elif l.sequence_step >= len(days):
                skipped.append({"lead_id": l.id, "why": "sequence complete"})
            elif l.next_touch_at and _aware(l.next_touch_at) > now:
                skipped.append({"lead_id": l.id, "why": "not due yet"})
            else:
                todo.append((l.id, l.sequence_step, l.last_touch_at))
    start_of_day = _now().replace(hour=0, minute=0, second=0, microsecond=0)
    with SessionLocal() as db:
        today = db.query(LeadOutreachDB).filter(LeadOutreachDB.kind == "draft", LeadOutreachDB.at >= start_of_day).count()
    todo = todo[: max(0, int(st["daily_draft_limit"]) - today)]                          # the limit is per day, counted from the drafts already made today
    made, would = [], []
    for lead_id, step, last in todo:
        why = f"Step {step + 1} of {len(days)}: " + (f"no reply since {_aware(last):%d %b}" if last else "first message")
        if dry_run:
            would.append({"lead_id": lead_id, "step": step, "why": why})
            continue
        try:
            got = draft_outreach(lead_id, user, _step_template(_lead_row(lead_id), step), step, why)
            with SessionLocal() as db:
                l = db.get(LeadDB, lead_id)
                l.sequence_step = step + 1
                nxt = days[step + 1] - days[step] if step + 1 < len(days) else None
                l.next_touch_at = _now() + timedelta(days=nxt) if nxt is not None else None
                db.commit()
            made.append({"lead_id": lead_id, "step": step, "action_id": got["action_id"], "why": why})
        except LeadError as exc:
            skipped.append({"lead_id": lead_id, "why": exc.message})
    return {"mode": mode, "made": made, "would_make": would, "skipped": skipped}


def start_sequence(lead_id: int, name: str = "standard") -> Dict[str, Any]:
    with SessionLocal() as db:
        l = db.get(LeadDB, lead_id)
        if l is None or l.archived_at:
            raise LeadError(404, "No such lead.")
        l.sequence_name, l.sequence_step, l.sequence_paused, l.next_touch_at = name[:60], 0, False, _now()
        db.commit()
    return get_lead(lead_id)


# ---------------------------------------------------------------- the monday push: staged, with no default board
def to_monday(lead_id: int, user: Dict[str, Any], board_id: str = "", dry_run: bool = True) -> Dict[str, Any]:
    """Stages the lead as a new row on the board the OWNER chose (nothing is sent: a manager pushes it). Without a chosen board it says so; a similar name already on the board is shown as "maybe the same"."""
    board_id = " ".join(str(board_id or "").split())
    if not board_id:
        raise LeadError(409, "Choose a board first: there is no default board for leads.")
    lead = get_lead(lead_id)
    from app.services import monday_push
    from app.services.coordination import boards

    b = boards._mirror.get(board_id)
    if not b:
        raise LeadError(404, "That board has not been read yet.")
    want = _norm_name(lead["company"] or "")
    maybe = [i for i in b.get("items", []) if want and (_norm_name(i.get("name")) == want or want in _norm_name(i.get("name")))]
    source = {"company": lead["company"], "customer": lead["company"], "contact": lead["name"], "email": lead["email"], "phone": lead["phone"], "notes": lead["notes"]}
    titles = {c["title"].casefold(): c["title"] for c in b.get("columns", [])}
    fields = {titles[k]: v for k, v in source.items() if v and k in titles}
    used = {k for k in source if k in titles}
    left_out = sorted({"company" if k == "customer" else k for k, v in source.items() if v and k not in titles and not (k == "company" and "customer" in titles) and not (k == "customer" and "company" in titles)} - {u if u != "customer" else "company" for u in used})
    out = {"dry_run": dry_run, "board_id": board_id, "item_name": lead["company"] or lead["name"], "fields": fields, "left_out": left_out,
           "maybe_the_same": [{"item_id": str(i["id"]), "name": i.get("name")} for i in maybe][:5], "staged": False}
    if dry_run:
        return out
    if maybe:
        raise LeadError(409, "A row with a similar name is already on that board. Check 'maybe the same' first.")
    cid = monday_push.stage_new_item(board_id, out["item_name"], fields, user)
    return {**out, "staged": True, "change_id": cid, "note": "Staged only: a manager sends it, and it is blocked while monday is read-only."}


# ---------------------------------------------------------------- scheduled: sequences and the weekly digest draft
def _system_user() -> Dict[str, Any]:
    from app.services.coordination.autopilot import reviewers

    return {"email": (reviewers() or [app_settings.ADMIN_EMAIL])[0], "role": "MANAGER"}


def scheduled_run() -> Dict[str, Any]:
    """Called by the background worker every hour or so: with sequences off it does nothing; otherwise it makes the drafts that are due (business hours and the daily limit are checked inside). Both
    "ask me first" (suggest) and "automatic" (auto) only ever make DRAFTS that wait for a person: nothing here sends mail."""
    mode = get_settings()["sequence_mode"]
    if mode == "off":
        return {"mode": "off"}
    return run_sequences(_system_user(), dry_run=False)


def weekly_digest(now: Optional[datetime] = None) -> Dict[str, Any]:
    """A draft, to the person who reviews drafts, of the week in the lead pipeline: stages, what is due, replies, suggestions waiting. One per week (the key carries the ISO week). Internal only."""
    from app.services.coordination import autopilot
    from app.services.coordination.autopilot import Intent

    now = now or _now()
    s = summary(manager=True)
    sug = [x for x in suggestions(True) if not x["already_a_lead"]]
    stages = ", ".join(f"{k} {v}" for k, v in s["by_stage"].items() if v) or "no leads yet"
    won, lost = s["won"], s["lost"]
    body = (f"Lead pipeline, week of {now:%b %d}:\n\n- Leads by stage: {stages}.\n- Due for a follow-up today: {s['due_today']}.\n- Replies this week: {s['replied_week']}.\n"
            f"- Won {won}, lost {lost}" + (f" (win rate {int(s['win_rate'] * 100)}%)" if s.get("win_rate") is not None else "") + ".\n"
            f"- Pipeline value (estimated yearly spend of open leads): {s['pipeline_value']:,.0f}.\n- Suggestions from our own history waiting to be made leads: {len(sug)}"
            + ("".join(f"\n  - {x['sentence']}" for x in sug[:5])) + "\n\nThis is a draft for you only; nothing was sent to a customer.")
    user = _system_user()
    year, week, _ = now.isocalendar()
    intent = Intent("draft", f"lead-digest:{year}w{week}", f"Lead pipeline digest, week {week}", to=[user["email"]], tool="send_email",
                    args={"to": user["email"], "subject": f"Lead pipeline, week {week}", "body": body, "_why": ["The weekly lead summary, made by rule from the pipeline"]})
    did = autopilot.carry_out("lead_digest", "suggest", [intent], explicit=False)
    return {"week": f"{year}w{week}", "outcome": did[0]["outcome"] if did else None, "stages": s["by_stage"]}


# ---------------------------------------------------------------- website title (only when the owner switched the people search on): a public page the user pasted
_BLOCKED_HOSTS = ("localhost", "127.", "0.", "10.", "192.168.", "169.254.", "::1", "fc", "fd")


def _public_host(host: str) -> bool:
    import ipaddress
    import socket

    h = (host or "").strip().lower()
    if not h or h in ("localhost",) or h.endswith((".local", ".internal", ".localhost")):
        return False
    try:
        infos = socket.getaddrinfo(h, None)
    except OSError:
        return False
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast or ip.is_unspecified:
            return False
    return True


def fetch_title(url: str) -> str:
    """The title of a public web page (https or http only; never a private or local address; 200 KB at most; 8 seconds). Only called when the owner has switched LEAD_WEB_SEARCH on."""
    from urllib.parse import urlparse

    import httpx

    parts = urlparse(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise LeadError(422, "Give a web address that starts with http:// or https://.")
    if not _public_host(parts.hostname):
        raise LeadError(422, "That address is not a public website.")
    with httpx.Client(timeout=8.0, follow_redirects=False) as client:
        r = client.get(url, headers={"User-Agent": "AlliedLeadBot/1.0"})
    if r.status_code != 200:
        raise LeadError(422, f"The page answered {r.status_code}.")
    m = re.search(r"<title[^>]*>(.*?)</title>", r.text[:200_000], re.I | re.S)
    return " ".join((m.group(1) if m else "").split())[:200]


def enrich_from_url(lead_id: int, url: str) -> Dict[str, Any]:
    if not (app_settings.LEAD_WEB_SEARCH and get_settings()["search_enabled"]):
        raise LeadError(409, "Looking up web pages is switched off. The owner turns it on with LEAD_WEB_SEARCH and the search setting.")
    title = fetch_title(url)
    with SessionLocal() as db:
        l = db.get(LeadDB, lead_id)
        if l is None or l.archived_at:
            raise LeadError(404, "No such lead.")
        l.notes = ((l.notes or "") + f"\nWebsite: {title or url} ({url[:200]})").strip()[:4000]
        l.updated_at = _now()
        db.commit()
    return get_lead(lead_id)
