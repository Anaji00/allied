"""Vendors' answers to follow-ups, read into one-click updates.

When a vendor answers "ships 12 Nov" or "shipped yesterday, photos attached", this finds the
order (by Allied's PO number, else the only open order with that vendor), reads the new date,
and puts a proposed update in the confirm queue: "KWS says PO 9277 ships 12 Nov. Update the
order?" A person confirms with one click; nothing changes before that, and nothing is written
to monday. A reply with no clear date becomes an alert with the vendor's words instead.

The AI reads the reply when it is on; a plain date reader is the fallback. Either way every
date is checked in code: it must appear in the email and fall in a sensible window.
"""

import re
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Set

from app.config import settings
from app.core.logging import logger
from app.services.coordination import notify as alerts

# "PO 9277", "P.O. #9277", "Allied PO 9277"; never "P.O. Box 9277" in a signature.
_PO = re.compile(r"\b(?:allied\s+)?p\.?\s*o\.?(?!\s*box)\s*(?:#|no\.?|number)?\s*:?\s*(\d{3,8})\b", re.I)
_SHIPPED = re.compile(r"\b(?:has|have|was|were|been)\s+(?:been\s+)?shipped\b|\bshipped\s+(?:out|on|today|yesterday)\b"
                      r"|\b(?:departed|loaded on|on board|sailed)\b", re.I)
_PHOTOS = re.compile(r"\b(?:photos?|pictures?|pics|images?)\b", re.I)
_MONTH = r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?"
_DATES = re.compile(rf"\b\d{{4}}-\d{{1,2}}-\d{{1,2}}\b|\b\d{{1,2}}/\d{{1,2}}(?:/\d{{2,4}})?\b"
                    rf"|\b\d{{1,2}}(?:st|nd|rd|th)?\s+{_MONTH}(?:\s+\d{{4}})?\b|\b{_MONTH}\s+\d{{1,2}}(?:st|nd|rd|th)?(?:,?\s+\d{{4}})?\b",
                    re.I)
# Free mail: the whole address identifies the vendor, not the domain.
_FREE = {"gmail.com", "yahoo.com", "hotmail.com", "outlook.com", "163.com", "126.com", "qq.com", "foxmail.com",
         "naver.com", "live.com", "icloud.com", "aol.com"}


def _who(address: str) -> str:
    address = (address or "").strip().strip("<>").lower()
    domain = address.rsplit("@", 1)[-1] if "@" in address else ""
    return address if domain in _FREE else domain


def _vendor_keys() -> Dict[str, Set[str]]:
    """Each vendor name -> the address keys its email comes from."""
    from app.services.coordination import vendors
    from app.services.coordination.autopilot import vendor_email

    out: Dict[str, Set[str]] = {}
    for j in vendors.overview()["jobs"]:
        email = vendor_email(j["vendor"])
        if email:
            out.setdefault(j["vendor"], set()).add(_who(email))
    return out


def is_vendor_reply(sender: str, subject: str) -> bool:
    """From a vendor on file, or an answer to one of Allied's follow-ups ("Allied PO 9277: ship date?")."""
    if re.search(r"allied\s+po\s*\d", subject or "", re.I):
        return True
    key = _who(sender)
    return bool(key) and any(key in keys for keys in _vendor_keys().values())


def match_job(sender: str, subject: str, body: str) -> Optional[Dict[str, Any]]:
    """The open order the reply is about: by PO number (from that vendor if several share it),
    else the only open order with this vendor. None if it can't be told."""
    from app.services.coordination import vendors

    jobs = vendors.overview()["jobs"]
    keys = _vendor_keys()
    key = _who(sender)
    pos = set(_PO.findall(f"{subject}\n{body[:4000]}"))
    by_po = [j for j in jobs if j.get("po_number") and str(j["po_number"]).strip() in pos]
    if len(by_po) > 1:
        by_po = [j for j in by_po if key in keys.get(j["vendor"], set())]
    if len(by_po) == 1:
        return by_po[0]
    if not pos:
        mine = [j for j in jobs if key in keys.get(j["vendor"], set())]
        if len(mine) == 1:
            return mine[0]
    return None


_MONTHS = {m: i for i, m in enumerate(("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), 1)}


def _parse(text: str, today: date) -> Optional[date]:
    """One date as vendors write it: 2026-11-12, 11/12, 11/12/26, 12 Nov, Nov 12th, November 12, 2026.
    Without a year it is the nearest such date to today (so "12 Jan" in November is next January,
    "Dec 28" in January last month). Slashes are read month first (US), unless the first number is over 12."""
    t = text.strip().lower().replace(",", " ")
    try:
        if m := re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})", t):
            return date(int(m[1]), int(m[2]), int(m[3]))
        year = None
        if m := re.fullmatch(r"(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?", t):
            a, b = int(m[1]), int(m[2])
            month, day = (b, a) if a > 12 else (a, b)
            year = int(m[3]) if m[3] else None
        elif m := re.fullmatch(r"(\d{1,2})(?:st|nd|rd|th)?\s+([a-z]{3})[a-z]*\.?(?:\s+(\d{4}))?", t):
            day, month, year = int(m[1]), _MONTHS.get(m[2][:3]), int(m[3]) if m[3] else None
        elif m := re.fullmatch(r"([a-z]{3})[a-z]*\.?\s+(\d{1,2})(?:st|nd|rd|th)?(?:\s+(\d{4}))?", t):
            month, day, year = _MONTHS.get(m[1][:3]), int(m[2]), int(m[3]) if m[3] else None
        else:
            return None
        if not month:
            return None
        if year is not None:
            return date(year + 2000 if year < 100 else year, month, day)
        # No year written: the year that puts the date nearest today ("Jan 5" in December is
        # next month; "Dec 28" in January is last week). A tie goes to the future.
        options = []
        for year in (today.year - 1, today.year, today.year + 1):
            try:
                options.append(date(year, month, day))
            except ValueError:  # 29 Feb outside a leap year
                continue
        return min(options, key=lambda d: (abs((d - today).days), d < today)) if options else None
    except ValueError:  # 31 Feb and the like
        return None


def _dates_in(text: str, today: date) -> List[date]:
    found = []
    for m in _DATES.finditer(text):
        d = _parse(m.group(0), today)
        if d and today - timedelta(days=45) <= d <= today + timedelta(days=365) and d not in found:
            found.append(d)
    return found


def read(sender: str, subject: str, body: str, today: Optional[date] = None) -> Dict[str, Any]:
    """What the vendor said: ship_date, shipped (and when), photos, a one-line summary, and
    whether it is unclear. Dates the AI gives must also be found in the text."""
    today = today or alerts.today()
    text = f"{subject}\n{body}"
    in_text = _dates_in(body, today) or _dates_in(subject, today)
    out: Dict[str, Any] = {"ship_date": None, "shipped": bool(_SHIPPED.search(body)), "shipped_on": None,
                           "photos": bool(_PHOTOS.search(body)), "summary": " ".join(body.split())[:200], "by": "rules"}
    ai = None
    if settings.AI_PROVIDER not in ("", "off"):
        try:
            from app.services.ai import features

            ai, _ = features.read_vendor_reply(sender, subject, body, today.isoformat())
        except Exception as exc:  # noqa: BLE001 - the plain reader below still works
            logger.info(f"AI could not read the vendor reply ({type(exc).__name__}); using the date reader.")
    if ai is not None:
        def grounded(value: Optional[str]) -> Optional[date]:
            try:
                d = date.fromisoformat(value) if value else None
            except ValueError:
                return None
            return d if d in in_text else None  # never a date the email doesn't contain

        out.update(shipped=ai.shipped, photos=ai.photos or out["photos"], summary=ai.summary[:300], by="ai")
        out["ship_date"] = grounded(ai.ship_date) if not ai.unclear else None
        out["shipped_on"] = grounded(ai.shipped_on)
        out["unclear"] = ai.unclear or (not ai.shipped and out["ship_date"] is None)
    else:
        future = [d for d in in_text if d >= today]
        if out["shipped"]:
            past = [d for d in in_text if d <= today]
            out["shipped_on"] = past[0] if len(past) == 1 else None
        elif len(future) == 1:
            out["ship_date"] = future[0]
        out["unclear"] = not out["shipped"] and out["ship_date"] is None
    return out


def handle(email_id: str, sender: str, subject: str, body: str, attachments: Optional[List[Dict[str, Any]]] = None,
           dry_run: bool = False) -> Optional[Dict[str, Any]]:
    """Turns a vendor's reply into a proposed update for a person to confirm. None if the
    email isn't a vendor reply (it then goes through the usual order/RFQ reading)."""
    from app.services.coordination import autopilot
    from app.services.coordination.autopilot import Intent, reviewers

    if not is_vendor_reply(sender, subject):
        return None
    job = match_job(sender, subject, body)
    said = read(sender, subject, body)
    photos = said["photos"] or any(str(a.get("content_type") or a.get("filename") or "").lower().endswith(
        ("jpg", "jpeg", "png", "heic", "image/jpeg", "image/png")) for a in attachments or [])
    quote = " ".join(body.split())[:400]
    if job is None:
        intent = Intent("alert", f"vendor-reply:unmatched:{email_id}", f"Vendor reply from {sender} not matched to an order",
                        to=reviewers(), title=f"Vendor reply to match: {subject[:120]}",
                        body=f"From {sender}. No open order matched (no PO number, or several orders).\n\n\"{quote}\"")
        return {"job_id": None, "did": autopilot.carry_out("vendor_reply", "suggest", [intent], dry_run), "read": said}
    po = job.get("po_number") or job["id"]
    today = alerts.today()
    why = [f"{job['vendor']} wrote: \"{quote}\"", f"Read by {'the AI, checked against the email' if said['by'] == 'ai' else 'the date reader'}",
           f"Order: {job.get('description') or '-'}, PO {po}; promised before: {job.get('promised_on') or 'none'}"]
    if _who(sender) not in _vendor_keys().get(job["vendor"], set()):
        why.append(f"Check the sender: {sender} isn't the address on file for {job['vendor']}; it matched by PO number.")
    if photos:
        why.append("The vendor says photos are attached: check them against the drawing.")
    args: Dict[str, Any] = {"job_id": job["id"], "_job_id": job["id"], "_email_id": email_id, "_why": why}
    if not job.get("acknowledged_on"):
        args["acknowledged_on"] = today.isoformat()
    if said["shipped"]:
        args["shipped_on"] = (said["shipped_on"] or today).isoformat()
        args["note"] = f"{job['vendor']} replied {today.isoformat()}: shipped. {said['summary']}"[:500]
        what = f"{job['vendor']} says PO {po} has shipped" + (f" ({args['shipped_on']})" if said["shipped_on"] else "")
    elif said["ship_date"]:
        args["promised_on"] = said["ship_date"].isoformat()
        args["note"] = f"{job['vendor']} replied {today.isoformat()}: ships {args['promised_on']}. {said['summary']}"[:500]
        what = f"{job['vendor']} says PO {po} ships {said['ship_date']:%d %b}"
    else:
        intent = Intent("alert", f"vendor-reply:{job['id']}:{email_id}", f"{job['vendor']} replied about PO {po}, no clear date",
                        to=reviewers(job.get("owner_email")), title=f"{job['vendor']} replied about PO {po}: read it",
                        body=f"{said['summary']}\n\nNo clear ship date, so nothing was changed.\n\n\"{quote}\""
                             + ("\n\nPhotos are attached." if photos else ""))
        return {"job_id": job["id"], "did": autopilot.carry_out("vendor_reply", "suggest", [intent], dry_run), "read": said}
    intent = Intent("draft", f"vendor-reply:{job['id']}:{email_id}", f"{what}. Update the order?",
                    to=reviewers(job.get("owner_email")), tool="update_vendor_job", args=args)
    did = autopilot.carry_out("vendor_reply", "suggest", [intent], dry_run)
    if not dry_run:
        autopilot.tell_reviewers(did)
    return {"job_id": job["id"], "did": did, "read": said}
