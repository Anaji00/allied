"""My day: ONE list of what is waiting on the signed-in person, whatever it is: approvals, drafts to send, problems, alerts, items where they hold the ball, mentions,
unread notes and shipments that need a look. Every row says what it is in a sentence, where it came from, what to click, and how old and how urgent it is.

Built from the sources each screen already uses (drafts, alerts, the ball, comments, engineering notes, orders, jobs, shipments), so nothing here is a second copy of the
rules. A snoozed alert is left out (the alert list does that). A viewer sees nothing; an approval is shown only to someone who may make it. Reading only.
"""

from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional

from app.config import settings
from app.core.logging import logger

_SEV = {"critical": 3, "high": 3, "warning": 2, "medium": 2, "normal": 1, "info": 0, "low": 0}
_APPROVAL_TOOLS = {"approve_po", "approve_design"}
NOTICE_DAYS = 2                                       # an information-only alert older than this is folded into one "older notices" row


def _days(iso: Any) -> int:
    try:
        at = datetime.fromisoformat(str(iso))
    except (TypeError, ValueError):
        return 0
    at = at if at.tzinfo else at.replace(tzinfo=timezone.utc)
    return max(0, (datetime.now(timezone.utc) - at).days)


def _tidy(text: str) -> str:
    """Wording fixes for sentences built from several places: "today old" -> "from today", "for today" -> "since today", internal names spelt out."""
    t = str(text or "")
    for old, new in (("today old", "from today"), (" for today", " since today"), ("rfq_board", "Open RFQs board"), ("rfq board", "Open RFQs board")):
        t = t.replace(old, new)
    t = t.replace(".)", ")").replace("( ", "(").replace(" )", ")")
    while t.count(")") > t.count("(") and t.rstrip().endswith(")"):                      # a stray closing bracket from a sentence built in two places
        t = t.rstrip()[:-1].rstrip()
    if t.count("(") > t.count(")"):                                                       # an opening bracket that never closed
        t = t + ")"
    return t


_KIND_WORD = {"vendor": "the vendor", "customer": "the customer", "carrier": "the carrier", "forwarder": "the forwarder", "us": "us"}


def _person(name: Any) -> str:
    """A person's name written with capitals: "helen" -> "Helen", "kim roe" -> "Kim Roe". An email address or a name that already has capitals is left as it is."""
    n = str(name or "").strip()
    return n.title() if n and n == n.lower() and "@" not in n else n


def _age(days: int) -> str:
    return "today" if days <= 0 else "1 day" if days == 1 else f"{days} days"


def _act(label: str, method: str, path: str, body: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    return {"label": label, "method": method, "path": path, "body": body or {}}


def _row(kind: str, ref: str, sentence: str, reason: str, action: Dict[str, Any], link: str, days: int, severity: str = "normal", secondary: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    return {"id": f"{kind}:{ref}", "kind": kind, "sentence": " ".join(str(sentence).split())[:300], "reason": reason, "primary_action": action, "other_actions": secondary or [],
            "link": link or "/", "age": _age(days), "age_days": days, "severity": severity}


def _link(kind: str, ref: str) -> str:
    return {"rfq": f"/orders?kind=rfq&ref={ref}", "job": f"/jobs/{ref}", "shipment": f"/jobs?shipment={ref}", "order": f"/orders?kind=so&ref={ref}",
            "vendor_job": "/vendors", "project": "/projects", "board_item": "/boards"}.get(kind, "/")


def items(user: Dict[str, Any]) -> Dict[str, Any]:
    role = user.get("role") or "VIEWER"
    email = (user.get("email") or "").strip().lower()
    if role == "VIEWER" or not email:
        return {"total": 0, "need_action": 0, "badge": 0, "counts": {}, "items": []}
    rows: List[Dict[str, Any]] = []

    def part(name: str, fn) -> None:
        try:
            rows.extend(fn())
        except Exception as exc:  # noqa: BLE001 - one broken source never blanks the day
            logger.warning(f"My day: {name} unavailable: {type(exc).__name__}")

    manager = role in ("MANAGER", "ADMIN")

    def drafts() -> List[Dict[str, Any]]:
        from app.services.assistant import agent

        out = []
        seen = set()
        for d in sorted(agent.pending(user), key=lambda x: str(x.get("created_at") or "")):                   # oldest first: one row per target, and it keeps its true age
            who = str((d.get("args") or {}).get("to") or "").casefold()
            if (d["summary"].split("\n", 1)[0], who) in seen:
                continue
            seen.add((d["summary"].split("\n", 1)[0], who))
            approval = d.get("tool") in _APPROVAL_TOOLS
            out.append(_row("approval" if approval else "draft", str(d["id"]), d["summary"].split("\n", 1)[0], "A draft is waiting for your OK" + (f" ({str(d['why'][0]).strip().rstrip('.')})" if d.get("why") else ""),
                            _act("Approve" if approval else "Send it", "POST", f"/api/v1/assistant/actions/{d['id']}", {"confirm": True}), "/inbox", _days(d.get("created_at")),
                            secondary=[_act("Dismiss", "POST", f"/api/v1/assistant/actions/{d['id']}", {"confirm": False})]))
        return out

    def approvals() -> List[Dict[str, Any]]:
        if not manager:
            return []
        from app.core.database import SessionLocal
        from app.models.db_models import PurchaseOrderDB
        from app.services.coordination import jobs, purchase_orders

        out = []
        for j in jobs.list_jobs("", "", "", 100, 0)["items"]:
            if any(n.get("key") == "approve_design" for n in j.get("next_steps", [])):
                out.append(_row("approval", f"design:{j['ref']}", f"Approve the design on {j['ref']} {j['title']}", "The vendor's price is in; the design needs a manager before a purchase order can be approved",
                                _act("Approve design", "POST", f"/api/v1/jobs/{j['ref']}/design/approve"), _link("job", j["ref"]), _days(j.get("updated_at"))))
        limit = float(getattr(settings, "PO_APPROVAL_LIMIT", 0.0) or 0.0)
        with SessionLocal() as db:
            drafts_ = [(r.id, r.po_number, r.vendor, r.job_id, r.lines, r.created_at) for r in db.query(PurchaseOrderDB).filter(PurchaseOrderDB.status == "DRAFT").limit(100)]
        for pid, number, vendor, job_id, lines, made in drafts_:
            if not jobs.design_approved(job_id) or (limit and purchase_orders.lines_total(lines) > limit and role != "ADMIN"):
                continue
            out.append(_row("approval", f"po:{pid}", f"Approve the purchase order to {vendor}", "A drafted purchase order is waiting for a manager's approval",
                            _act("Approve PO", "POST", f"/api/v1/purchase-orders/{pid}/approve"), "/orders?kind=po&ref=" + str(pid), _days(made.isoformat() if made else None)))
        return out

    def problems() -> List[Dict[str, Any]]:
        from app.core.database import SessionLocal
        from app.models.db_models import OrderDB

        out = []
        with SessionLocal() as db:
            for o in db.query(OrderDB).filter(OrderDB.status.in_(("VALIDATION_ERROR", "SAGE_ERROR"))).order_by(OrderDB.created_at.desc()).limit(50):
                if manager or (o.created_by or "").lower() == email:
                    out.append(_row("problem", o.sales_order_number, f"Order {o.sales_order_number} for {o.customer_name} needs a person: " + ("a check failed" if o.status == "VALIDATION_ERROR" else "Sage did not take it"),
                                    "An order that could not go through", _act("Open", "LINK", _link("order", o.sales_order_number)), _link("order", o.sales_order_number),
                                    _days(o.created_at.isoformat() if o.created_at else None), "high"))
        return out

    def alerts() -> List[Dict[str, Any]]:
        from app.services.coordination import notify

        out, old_info = [], []
        for n in notify.inbox(email, open_only=True, limit=100):
            d = _days(n["created_at"])
            sev = str(n["severity"]).lower()
            if sev in ("info", "low"):
                if d > NOTICE_DAYS:
                    old_info.append((n, d))
                    continue
                out.append(_row("alert", str(n["id"]), n["title"], f"For your information, {_age(d)} old: nothing to do but dismiss it", _act("Dismiss", "POST", f"/api/v1/alerts/{n['id']}/acknowledge"),
                                n.get("link") or "/alerts", d, "low"))
                continue
            why = "An urgent alert" if sev in ("critical", "high") else "A warning"
            out.append(_row("alert", str(n["id"]), n["title"], f"{why} for you, {_age(d)} old", _act("Mark handled", "POST", f"/api/v1/alerts/{n['id']}/acknowledge"),
                            n.get("link") or "/alerts", d, "high" if sev in ("critical", "high") else "normal", secondary=[_act("Snooze a day", "POST", f"/api/v1/alerts/{n['id']}/snooze", {"hours": 24})]))
        if old_info:
            oldest = max(d for _n, d in old_info)
            out.append(_row("notice", "older", f"{len(old_info)} older notice{'s' if len(old_info) != 1 else ''}", f"For your information only, up to {_age(oldest)} old: nothing to do but dismiss them",
                            _act("Dismiss them all", "POST", "/api/v1/my-day/dismiss-notices"), "/alerts", oldest, "low"))
        return out

    def balls() -> List[Dict[str, Any]]:
        from app.services.coordination import ball

        out = []
        for b in ball.list_open("me", email, include_derived=False)[:40]:
            d = int(b.get("days_waiting") or 0)
            mine = (b.get("ball_person") or "").lower() == email
            holder = "you" if mine else _person(b.get("ball_name") or b.get("ball_label") or "someone")
            label = b.get("label") or b["item_ref"]
            label = label[:1].upper() + label[1:]                                               # "a board item" -> "A board item"
            if holder.islower():                                                                  # a plain lowercase name from a board cell: "helen" -> "Helen"
                holder = holder.title()
            same = holder.casefold() in label.casefold()                                          # "KINGCERA PO 9234 waiting on KINGCERA" says nothing new
            sentence = f"{label}: the ball is with you" if mine else (f"{label}: waiting on {_KIND_WORD.get(str(b.get('ball_with') or ''), 'the other side')} {holder}" if same else f"{label}: waiting on {holder}")
            row = _row("ball", f"{b['item_type']}:{b['item_ref']}", sentence + (f" ({b['note']})" if b.get("note") else ""),
                       f"Waiting on you for {_age(d)}" if mine else f"You own this and it has waited on {holder} for {_age(d)}", _act("Open", "LINK", _link(str(b["item_type"]), str(b["item_ref"]))),
                       _link(str(b["item_type"]), str(b["item_ref"])), d, "high" if b.get("overdue") or d >= settings.BALL_NUDGE_DAYS else "normal")
            row["ball_with"] = {"kind": b.get("ball_with"), "label": b.get("ball_label"), "name": holder, "person": b.get("ball_person"), "mine": mine}
            out.append(row)
        return out

    def mentions() -> List[Dict[str, Any]]:
        from app.services.coordination import updates

        out = []
        for m in updates.mentions_of(email, 7, 20):
            out.append(_row("mention", str(m["id"]), f"{m['author_name']} mentioned you: {m['body'][:140]}", f"A comment on {m['item_type'].replace('_', ' ')} {m['item_ref']}",
                            _act("Open", "LINK", _link(m["item_type"], m["item_ref"])), _link(m["item_type"], m["item_ref"]), _days(m["created_at"])))
        return out

    def notes() -> List[Dict[str, Any]]:
        from app.services.coordination import eng_notes

        return [_row("note", str(n["id"]), f"Engineering note: {str(n.get('text') or '')[:140]}", "An engineering note you have not read", _act("Open", "LINK", "/boards"), "/boards", _days(n.get("created_at")))
                for n in eng_notes.list_notes(for_email=email, days=14) if email in [u.lower() for u in n.get("unread", [])]][:20]

    def ships() -> List[Dict[str, Any]]:
        from app.services.coordination import notify, shipments

        on = notify.today()
        out = []
        for s in shipments.list_shipments(include_finished=False):
            if (s.get("owner_email") or "").lower() == email:
                fl = shipments.flags(s, on)
                if fl:
                    out.append(_row("shipment", str(s["id"]), f"Shipment {s.get('reference') or s.get('container_no') or s['id']}: {', '.join(fl)}", "A shipment of yours that needs a look",
                                    _act("Open", "LINK", _link("shipment", str(s["id"]))), _link("shipment", str(s["id"])), 0, "high" if "late" in fl else "normal"))
        return out

    def follows() -> List[Dict[str, Any]]:
        from app.services import follow

        out = []
        for f in follow.following(user)["items"]:
            if not f["changed_since_seen"]:
                continue
            what = follow.sentence(user, f["item_kind"], f["item_ref"])
            out.append(_row("follow", f"{f['item_kind']}:{f['item_ref']}", f"{f['title']} changed since you looked" + (f": {what}" if what else ""), "You follow this and it changed after you last looked",
                            _act("Mark seen", "POST", "/api/v1/seen", {"item_kind": f["item_kind"], "item_ref": f["item_ref"]}), f["link"], 0))
        return out

    for name, fn in (("follows", follows), ("drafts", drafts), ("approvals", approvals), ("problems", problems), ("alerts", alerts), ("ball", balls), ("mentions", mentions), ("notes", notes), ("shipments", ships)):
        part(name, fn)
    rows.sort(key=lambda r: (-_SEV.get(r["severity"], 1), -r["age_days"]))
    counts: Dict[str, int] = {}
    for r in rows:
        counts[r["kind"]] = counts.get(r["kind"], 0) + 1
    need = sum(1 for r in rows if r["severity"] != "low")                  # what really needs the person: the headline, the badge and /me/needs all say this number
    for r in rows:                                                          # plain words: no "today old", no "waited ... for today", no internal board names
        r["sentence"], r["reason"] = _tidy(r["sentence"]), _tidy(r["reason"])
    return {"total": len(rows), "need_action": need, "badge": need, "counts": counts, "items": rows}


# ----------------------------------------------------------------------------- the morning brief

def _brief_link(line: str) -> str:
    low = line.casefold()
    if "waiting for your ok" in low:
        return "/inbox"
    if low.startswith("drawing overdue"):
        return "/boards"
    if "waiting on you" in low:
        return "/jobs"
    if "email" in low and "waiting on a person" in low:
        return "/inbox"
    if " po " in low or "late" in low or "will miss" in low:
        return "/orders?kind=po"
    return "/"


def brief(user: Dict[str, Any]) -> Dict[str, Any]:
    """The morning brief as data: a headline and one sentence with a link per thing, in the business timezone (the same lines the morning message uses)."""
    from app.services.coordination import notify, secretary, updates

    email = (user.get("email") or "").strip().lower()
    now = notify.now_local()
    name = next((p["name"].split()[0] for p in updates.people() if p["email"] == email and p["name"] and "@" not in p["name"]), "")
    lines = secretary.brief_for(email) if email and user.get("role") != "VIEWER" else []
    when = f"{now.strftime('%A')} {now.day} {now.strftime('%B')}"
    greeting = "Good morning" if now.hour < 12 else "Good afternoon" if now.hour < 17 else "Good evening"
    need = items(user)["need_action"] if email and user.get("role") != "VIEWER" else 0                 # the same count as the rows on the Today list that need action
    headline = (f"{greeting}{', ' + name if name else ''}. It is {when}. " + (f"{need} thing{'s' if need != 1 else ''} need{'s' if need == 1 else ''} you." if need else "Nothing is waiting on you."))
    return {"headline": headline, "date": now.date().isoformat(), "timezone": str(getattr(now.tzinfo, "key", None) or now.tzname() or ""), "count": len(lines), "need_action": need,
            "items": [{"sentence": ln, "link": _brief_link(ln)} for ln in lines]}


# ----------------------------------------------------------------------------- meeting prep

PREP_KINDS = ("job", "customer", "vendor")


def _emails(queries: List[str], limit: int = 8) -> List[Dict[str, Any]]:
    from app.services import mail_log

    seen: Dict[Any, Dict[str, Any]] = {}
    for q in [x for x in queries if x and len(str(x)) >= 3][:6]:
        for m in mail_log.list_mail(q=str(q), days=120, limit=20, include_quiet=True)["mail"]:
            seen.setdefault(m.get("id"), m)
    rows = sorted(seen.values(), key=lambda m: str(m.get("received_at") or m.get("created_at") or ""), reverse=True)[:limit]
    return [{"at": m.get("received_at") or m.get("created_at"), "from": m.get("sender"), "subject": m.get("subject"), "direction": m.get("direction"), "category": m.get("category"),
             "summary": (m.get("summary") or "")[:200]} for m in rows]


def _po_view(po: Dict[str, Any], manager: bool = False) -> Dict[str, Any]:
    return {"id": po["id"], "po_number": po.get("po_number"), "vendor": po.get("vendor"), "status": po.get("status_word") or po.get("status"), "raw_status": po.get("status"), "total": po.get("total") if manager else None,
            "sent_at": po.get("sent_at"), "good_thru": po.get("good_thru"),
            "revisions": [{"line": r["line"], "revision_sent": r["revision_sent"], "revision_acked": r["revision_acked"], "mismatch": r["mismatch"]} for r in po.get("revisions", [])],
            "drawing_mismatch_lines": po.get("revision_mismatch") or []}


def _comments(item_type: str, ref: str, n: int = 5) -> List[Dict[str, Any]]:
    from app.services.coordination import updates

    return [{"author": c["author_name"], "at": c["created_at"], "body": c["body"][:300]} for c in updates.thread(item_type, ref, include_notes=False)[-n:]]


def prep(kind: str, ref: str, manager: bool = False) -> Dict[str, Any]:
    """Everything to know before a call or meeting about a job, a customer or a vendor, in one place, with a copy-ready text form. Reading only; for staff."""
    from app.core.database import SessionLocal
    from app.models.db_models import OrderDB, PurchaseOrderDB
    from app.services.coordination import jobs, purchase_orders, rfqs, shipments

    if kind not in PREP_KINDS:
        raise ValueError(f"Prepare for one of: {', '.join(PREP_KINDS)}.")
    ref = " ".join(str(ref or "").split())
    if not ref or len(ref) > 120:
        raise ValueError("Say which one.")
    out: Dict[str, Any] = {"kind": kind, "ref": ref, "title": ref, "emails": [], "pos": [], "rfqs": [], "shipments": [], "jobs": [], "ball": None, "comments": []}
    ships = shipments.list_shipments()

    def ship_rows(po_numbers: List[str]) -> List[Dict[str, Any]]:
        refs = {f"PO {n}" for n in po_numbers if n}
        return [{"id": s["id"], "reference": s.get("reference"), "status": s.get("status_word"), "eta": s.get("eta_current") or s.get("eta_original"), "forwarder": s.get("forwarder")}
                for s in ships if s.get("reference") in refs]

    if kind == "job":
        j = jobs.get(ref, staff=True)
        out.update(title=f"{j['ref']} {j['title']}", customer=j["customer"], stage=j["stage_word"], ball=j.get("ball"), next_steps=[n["text"] for n in j.get("next_steps", [])])
        pos = [purchase_orders.get(p["id"]) for p in j["purchase_orders"] if p.get("id")]
        out["pos"] = [_po_view(p, manager) for p in pos]
        if j.get("rfq"):
            r = rfqs.get(j["rfq"]["number"])
            out["rfqs"] = [{"number": r["number"], "subject": r.get("subject"), "status": r.get("status"), "due": r.get("effective_due_at")}]
            out["comments"] = _comments("rfq", r["number"])
        out["shipments"] = ship_rows([p.get("po_number") for p in pos])
        out["emails"] = _emails([j["rfq"]["number"] if j.get("rfq") else "", j["ref"]] + [p["vendor"] for p in pos if p.get("vendor")])
    elif kind == "customer":
        name = ref
        for r in rfqs.list_rfqs(open_only=False, limit=2000):
            if jobs.same_customer(name, r.get("customer") or ""):
                out["rfqs"].append({"number": r["number"], "subject": r.get("subject"), "status": r.get("status"), "due": r.get("effective_due_at")})
        out["rfqs"] = sorted(out["rfqs"], key=lambda r: r["number"], reverse=True)[:15]                  # the newest fifteen
        out["jobs"] = [{"ref": j["ref"], "title": j["title"], "stage": j["stage_word"]} for j in jobs.list_jobs("", "", name, 30, 0)["items"] if jobs.same_customer(name, j.get("customer") or "")]
        with SessionLocal() as db:
            orders = [o for o in db.query(OrderDB).order_by(OrderDB.created_at.desc()).limit(500) if jobs.same_customer(name, o.customer_name or "")]
        out["orders"] = [{"sales_order": o.sales_order_number, "customer_po": o.customer_po_number, "status": o.status, "total": o.total_revenue if manager else None} for o in orders[:10]]
        pos = []
        for jj in out["jobs"][:10]:
            pos += [purchase_orders.get(p["id"]) for p in jobs.get(jj["ref"], staff=True)["purchase_orders"] if p.get("id")]
        out["pos"] = [_po_view(p, manager) for p in pos]
        out["shipments"] = ship_rows([p.get("po_number") for p in pos])
        out["emails"] = _emails([name] + [r["number"] for r in out["rfqs"][:3]])
    else:
        with SessionLocal() as db:
            rows = [r.id for r in db.query(PurchaseOrderDB).order_by(PurchaseOrderDB.id.desc()).limit(500) if jobs.same_vendor_name(ref, r.vendor or "")]
        pos = [purchase_orders.get(i) for i in rows[:15]]
        out["pos"] = [_po_view(p, manager) for p in pos]
        out["shipments"] = ship_rows([p.get("po_number") for p in pos])
        out["emails"] = _emails([ref] + [p["vendor_email"] for p in pos[:1] if p.get("vendor_email")])
        out["open_pos"] = sum(1 for p in pos if p["status"] in ("DRAFT", "APPROVED", "SENDING", "SENT", "ACKNOWLEDGED"))
    out["text"] = _prep_text(out)
    return out


def _prep_text(p: Dict[str, Any]) -> str:
    L = [f"Prep: {p['title']}"]
    if p.get("customer"):
        L.append(f"Customer: {p['customer']}")
    if p.get("stage"):
        L.append(f"Stage: {p['stage']}")
    if p.get("ball"):
        b = p["ball"]
        L.append(f"Ball: {b.get('ball_name') or b.get('ball_with')}" + (f" ({b['note']})" if b.get("note") else "") + (f", waiting {b.get('days_waiting')} days" if b.get("days_waiting") else ""))
    for n in p.get("next_steps", []):
        L.append(f"Next: {n}")
    for r in p["rfqs"]:
        L.append(f"RFQ {r['number']}: {r.get('subject') or ''} ({r.get('status')})")
    for j in p.get("jobs", []):
        L.append(f"Job {j['ref']}: {j['title']} ({j['stage']})")
    for o in p.get("orders", []):
        L.append(f"Order {o['sales_order']} for PO {o['customer_po']}: {o['status']}")
    for po in p["pos"]:
        L.append(f"PO {po['po_number'] or po['id']} to {po['vendor']}: {po['status']}" + (f", good through {po['good_thru']}" if po.get("good_thru") else ""))
        for r in po["revisions"]:
            if r["revision_sent"] or r["revision_acked"]:
                L.append(f"  line {r['line']}: drawing rev {r['revision_sent'] or '-'} sent, vendor says {r['revision_acked'] or 'nothing yet'}" + (" (MISMATCH)" if r["mismatch"] else ""))
    for s in p["shipments"]:
        L.append(f"Shipment {s['reference']}: {s['status']}, ETA {s['eta'] or 'not known'}")
    for c in p["comments"]:
        L.append(f"Comment ({c['author']}): {c['body'][:120]}")
    for m in p["emails"][:5]:
        L.append(f"Email ({str(m['at'] or '')[:10]}) {m['from']}: {m['subject']}")
    return "\n".join(L)


# ----------------------------------------------------------------------------- what changed since

def parse_since(text: str, now: Optional[datetime] = None) -> datetime:
    """"yesterday", "today", "this week", "last week", "3d", "2 days", "6h" or a date (2026-11-10): the moment from which changes are shown (business timezone)."""
    import re
    from datetime import timedelta

    from app.services.coordination import notify

    now = now or notify.now_local()
    t = " ".join(str(text or "yesterday").casefold().split())
    start_today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    if t == "today":
        return start_today
    if t == "yesterday":
        return start_today - timedelta(days=1)
    if t in ("this week", "week"):
        return start_today - timedelta(days=start_today.weekday())
    if t == "last week":
        return start_today - timedelta(days=start_today.weekday() + 7)
    m = re.fullmatch(r"(\d{1,3})\s*(d|day|days|h|hr|hrs|hour|hours|w|week|weeks)", t)
    if m:
        n = int(m.group(1))
        unit = m.group(2)[0]
        return now - (timedelta(hours=n) if unit == "h" else timedelta(days=n * (7 if unit == "w" else 1)))
    try:
        d = datetime.fromisoformat(t)
        return d if d.tzinfo else d.replace(tzinfo=now.tzinfo)
    except ValueError:
        raise ValueError("Say since when: yesterday, today, this week, last week, 3d, 6h or a date like 2026-11-10.") from None


_QUIET_ACTIONS = {"LOGIN", "LOGOUT", "TOKEN_REFRESH", "SAGE_IMPORT_PREVIEW"}


def _utc(iso: Any) -> datetime:
    d = datetime.fromisoformat(str(iso))
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def _words(action: str) -> str:
    return " ".join(str(action or "").replace("_", " ").split()).capitalize()


_MONEY_WORDS = r"(?:total|price|cost|amount|revenue|margin|unit[_ ]cost|unit[_ ]price|base[_ ]price|standard[_ ]cost|value|volume)"
_MONEY_FIELDS = {"base_price", "standard_cost", "preferred_vendor", "total", "total_revenue", "unit_cost", "unit_price"}


def _no_money(detail: str) -> str:
    """An audit detail with amounts taken out, for anyone who is not a manager: "total 5000.0" becomes "total (hidden)", a dollar figure becomes "an amount"; a changed price field shows no before or after."""
    import json
    import re

    d = str(detail or "")
    if d.startswith("{"):
        try:
            j = json.loads(d)
            if str(j.get("field")) in _MONEY_FIELDS:
                return f"{j.get('label') or j['field']} was changed (amounts are for managers)"
        except ValueError:
            pass
    d = re.sub(rf"(?i)\b({_MONEY_WORDS})\b(\W{{0,3}})\$?\s*-?[\d][\d,]*(?:\.\d+)?", r"\1 (hidden)", d)
    return re.sub(r"[$\u00a5\u20ac\u00a3]\s*[\d][\d,]*(?:\.\d+)?", "an amount", d)


def since(kind: str, ref: str, when: str = "yesterday", manager: bool = True) -> Dict[str, Any]:
    """A short list of what changed on a job (or a board) since then, in plain sentences, newest first: the audit trail, comments, files attached, the ball, and (for a board) its activity and staged changes."""
    from app.core.database import SessionLocal
    from app.models.db_models import AuditLogDB, ItemFileDB, MondayChangeDB
    from app.services.coordination import ball, jobs, updates

    if kind not in ("job", "board"):
        raise ValueError("Ask about a job or a board.")
    ref = " ".join(str(ref or "").split())
    if not ref or len(ref) > 64:
        raise ValueError("Say which one.")
    cut = parse_since(when)
    names = {p["email"]: p["name"] for p in updates.people()}
    who = lambda e: names.get(str(e or "").lower(), str(e or "someone"))        # noqa: E731
    out: List[Dict[str, Any]] = []

    def add(at: Any, by: Any, sentence: str, source: str) -> None:
        iso = at.isoformat() if hasattr(at, "isoformat") else str(at or "")
        if iso:
            out.append({"at": iso, "who": who(by), "sentence": " ".join(sentence.split())[:300], "source": source})

    def aware(d: Any) -> Any:
        return d if getattr(d, "tzinfo", None) else d.replace(tzinfo=cut.tzinfo)

    title = ref
    if kind == "job":
        j = jobs.get(ref, staff=True)
        title = f"{j['ref']} {j['title']}".strip()
        keys = {j["ref"], str(j["id"])}
        thread = None
        if j.get("rfq"):
            keys |= {j["rfq"]["number"], str(j["rfq"]["id"])}
            thread = j["rfq"]["number"]
        if (j.get("sales_order") or {}).get("number"):
            keys.add(str(j["sales_order"]["number"]))
        keys |= {str(p["po_number"]) for p in j["purchase_orders"] if p.get("po_number")}
        with SessionLocal() as db:
            for a in db.query(AuditLogDB).filter(AuditLogDB.resource_id.in_(sorted(keys)), AuditLogDB.timestamp >= cut.astimezone(timezone.utc)).order_by(AuditLogDB.id.desc()).limit(200):
                if a.action not in _QUIET_ACTIONS:
                    detail = a.detail if manager else _no_money(a.detail)
                    add(a.timestamp, a.actor_email, f"{_words(a.action)}" + (f": {detail}" if detail else ""), "audit")
            if thread:
                for f in db.query(ItemFileDB).filter(ItemFileDB.item_type == "rfq", ItemFileDB.item_ref == thread, ItemFileDB.created_at >= cut.astimezone(timezone.utc), ItemFileDB.deleted_at.is_(None)):
                    add(f.created_at, f.uploaded_by, f"Attached the file {f.filename}", "file")
        if thread:
            for c in updates.thread("rfq", thread, include_notes=False):
                for x in [c] + c.get("replies", []):
                    if x.get("created_at") and _utc(x["created_at"]) >= cut.astimezone(timezone.utc) and not x.get("removed"):
                        add(x["created_at"], x["author_email"], f"Commented: {x['body'][:140]}", "comment")
            b = ball.get("rfq", thread)
            for h in (b or {}).get("history", []):
                if h.get("at") and _utc(h["at"]) >= cut.astimezone(timezone.utc):
                    add(h["at"], h.get("by"), f"Moved the ball from {h.get('from')} to {h.get('to')}" + (f" ({h['note']})" if h.get("note") else ""), "ball")
    else:
        from app.services import monday_push
        from app.services.coordination import activity, boards

        b = boards._mirror.get(ref)
        title = (b or {}).get("name") or ref
        try:
            for e in activity.feed(ref, days=max(1, int((datetime.now(timezone.utc) - cut.astimezone(timezone.utc)).days) + 1))["changes"]:
                if e["at"] and e["at"] >= cut.astimezone(timezone.utc).isoformat():
                    add(e["at"], e.get("user"), f"{e.get('item') or 'An item'}: " + (f"{e.get('column')} {e.get('from') or 'blank'} -> {e.get('to') or 'blank'}" if e.get("column") else str(e.get("event") or "changed").replace("_", " ")), "monday")
        except LookupError:
            pass                                    # the board's activity has not been read: staged changes below still show
        with SessionLocal() as db:
            for c in db.query(MondayChangeDB).filter(MondayChangeDB.board_id == ref, MondayChangeDB.created_at >= cut.astimezone(timezone.utc)).order_by(MondayChangeDB.id.desc()).limit(100):
                add(c.created_at, c.staged_by, f"Staged ({c.status.lower()}): {c.summary}", "staged")
        if b is None and not out:
            raise LookupError(f"Board {ref} has not been read yet.")
    seen, rows = set(), []
    for r in sorted(out, key=lambda r: r["at"], reverse=True):
        key = (r["at"][:19], r["sentence"])
        if key not in seen:
            seen.add(key)
            rows.append(r)
    rows = rows[:100]
    text = f"Since {cut.strftime('%A %d %b %H:%M')} on {title}:\n" + ("\n".join(f"- {r['at'][:16].replace('T', ' ')} {r['who']}: {r['sentence']}" for r in rows) if rows else "Nothing changed.")
    return {"kind": kind, "ref": ref, "title": title, "since": cut.isoformat(), "count": len(rows), "changes": rows, "text": text}


# ----------------------------------------------------------------------------- who owes us something

CHASE_KINDS = {"vendor_follow_up": "a delivery update", "rfq_vendor_chase": "a price", "quote_follow_up": "an answer on our quote"}
_EXTERNAL = ("vendor", "customer", "carrier", "other")


def chase(user: Dict[str, Any]) -> Dict[str, Any]:
    """Everyone who owes us something, oldest first: a vendor late with a price or a delivery, a customer who has not answered a quote, a carrier or anyone else the ball is with.
    Each row carries the follow-up already drafted for it (if there is one) with a one-call send and dismiss through the usual draft decision (nothing goes out until it is sent)."""
    import json

    from app.core.database import SessionLocal
    from app.models.db_models import AssistantActionDB
    from app.services.assistant import agent
    from app.services.coordination import ball

    rows: List[Dict[str, Any]] = []
    drafts = {d["id"]: d for d in agent.pending(user) if d.get("kind") in CHASE_KINDS}
    full: Dict[str, Dict[str, Any]] = {}
    if drafts:
        with SessionLocal() as db:
            for r in db.query(AssistantActionDB).filter(AssistantActionDB.id.in_(list(drafts))):
                full[r.id] = dict(r.args or {})

    def draft_action(did: str) -> Dict[str, Any]:
        return {"draft_id": did, "send": _act("Send it", "POST", f"/api/v1/chase/{did}", {"action": "send"}), "dismiss": _act("Dismiss", "POST", f"/api/v1/chase/{did}", {"action": "dismiss"})}

    used: set = set()
    holders = [b for b in ball.list_open("all", include_derived=True) if b.get("ball_with") in _EXTERNAL and not b.get("resolved")]
    for b in holders:
        ref = str(b["item_ref"])
        match = next((did for did, a in full.items() if did not in used and (ref in json.dumps(a, default=str) or ref in drafts[did]["summary"])), None)
        if match:
            used.add(match)
        who = _person(b.get("ball_name") or b.get("ball_person") or b.get("ball_label") or "someone")
        d = int(b.get("days_waiting") or 0)
        row = {"id": f"ball:{b['item_type']}:{ref}", "kind": "ball", "who": who, "role": b.get("ball_label"), "owes": b.get("note") or "a reply", "what": b.get("label") or ref, "days_waiting": d,
               "age": _age(d), "since": b.get("since"), "link": _link(str(b["item_type"]), ref), "overdue": bool(b.get("overdue")), "draft_id": None, "send": None, "dismiss": None}
        if match:
            row.update(draft_action(match), draft_summary=drafts[match]["summary"].split("\n", 1)[0], kind="chase")
        rows.append(row)
    for did, d in drafts.items():
        if did in used:
            continue
        a = full.get(did, {})
        days = _days(d.get("created_at"))
        rows.append({"id": f"draft:{did}", "kind": "chase", "who": a.get("vendor_name") or str(a.get("to") or "").split(",")[0] or "someone", "role": CHASE_KINDS[d["kind"]], "owes": CHASE_KINDS[d["kind"]],
                     "what": d["summary"].split("\n", 1)[0], "days_waiting": days, "age": _age(days), "since": d.get("created_at"), "link": "/inbox", "overdue": False,
                     "draft_summary": d["summary"].split("\n", 1)[0], **draft_action(did)})
    rows.sort(key=lambda r: -r["days_waiting"])
    return {"total": len(rows), "with_draft": sum(1 for r in rows if r["draft_id"]), "items": rows}


def chase_decide(draft_id: str, action: str, user: Dict[str, Any]) -> Dict[str, Any]:
    """Send or dismiss one follow-up draft: the usual draft decision, limited to the follow-up kinds, so this route can't be used to send anything else."""
    from app.services.assistant import agent

    if action not in ("send", "dismiss"):
        raise ValueError("The action must be send or dismiss.")
    mine = {d["id"]: d for d in agent.pending(user) if d.get("kind") in CHASE_KINDS}
    if str(draft_id) not in mine:
        raise LookupError("That follow-up is not waiting for you (it may already be sent or dismissed).")
    out = agent.decide(str(draft_id), action == "send", user)
    return {"draft_id": str(draft_id), "action": action, "status": out.get("status"), "result": out.get("result")}


# ----------------------------------------------------------------------------- a call, noted on a job

def call_note_text(summary: Any, from_number: str = "") -> str:
    """The note as it would appear on the job: who called, what it was about, the numbers mentioned and the follow-ups. Only what the summary says."""
    who = summary.caller_name or from_number or "a caller"
    lines = [f"Call from {who}" + (f" ({summary.company})" if summary.company else "") + f", {summary.reason.replace('_', ' ')}: {summary.summary}"]
    if summary.po_numbers:
        lines.append("Numbers mentioned: " + ", ".join(summary.po_numbers[:10]))
    if summary.action_items:
        lines.append("Follow-ups:")
        lines += [f"- {a}" for a in summary.action_items[:10]]
    if summary.callback_needed:
        lines.append("A callback is needed.")
    return "\n".join(lines)[:3800]


def call_to_job(job: str, transcript: str, user: Dict[str, Any], from_number: str = "") -> Dict[str, Any]:
    """Summarise a call (the existing call summary) and put the summary and follow-ups on the job as a DRAFT note: a person confirms it before it is posted. Nothing is sent to anyone."""
    from app.services.ai import features
    from app.services.assistant import agent
    from app.services.assistant.tools import TOOLS
    from app.services.coordination import jobs

    text = str(transcript or "").strip()
    if not text:
        raise ValueError("Give the call: its text, or a recording.")
    if len(text) > 30000:
        raise ValueError("That call is longer than 30,000 characters. Give the main part of it.")
    j = jobs.get(job, staff=True)
    summary, suggestion_id = features.summarize_call(text, str(from_number or "")[:40], reference=j["ref"], actor=user.get("email", "system"))
    note = call_note_text(summary, str(from_number or ""))
    card = agent._propose(TOOLS["add_call_note"], {"job": j["ref"], "note": note}, user)
    return {"job": j["ref"], "action_id": card["id"], "status": "PENDING", "note": note, "suggestion_id": suggestion_id,
            "summary": {"caller": summary.caller_name, "company": summary.company, "reason": summary.reason, "callback_needed": summary.callback_needed, "urgency": summary.urgency,
                        "po_numbers": summary.po_numbers, "action_items": summary.action_items},
            "message": f"A draft note for {j['ref']} is waiting for you to confirm. Nothing was posted or sent.",
            "confirm": {"label": "Post the note", "method": "POST", "path": f"/api/v1/assistant/actions/{card['id']}", "body": {"confirm": True}},
            "dismiss": {"label": "Dismiss", "method": "POST", "path": f"/api/v1/assistant/actions/{card['id']}", "body": {"confirm": False}}}


def dismiss_notices(user: Dict[str, Any]) -> Dict[str, Any]:
    """Dismisses this person's information-only alerts older than the notice age: the ones folded into the "older notices" row."""
    from app.services.coordination import notify

    email = (user.get("email") or "").strip().lower()
    n = 0
    for a in notify.inbox(email, open_only=True, limit=500):
        if str(a["severity"]).lower() in ("info", "low") and _days(a["created_at"]) > NOTICE_DAYS and notify.dismiss_for_me(a["id"], email) == "ok":
            n += 1
    return {"dismissed": n, "message": f"Dismissed {n} older notice{'s' if n != 1 else ''}."}
