"""Blind-shipment check on outgoing drafts. A vendor must never learn who Allied's customer is, and a customer must never see a vendor's name:
before a draft is approved, its subject, body and attachment names are searched for the other party's name. It only WARNS (the warning joins the
draft's "why" list, and an automatic draft waits for a person); nothing is blocked or rewritten."""
import re
import unicodedata
from typing import Any, Dict, Iterable, List

_OWN = {"allied", "welding", "systems", "allied welding systems"}      # Allied's own name is not a leak
_VENDOR_TOOLS = {"send_vendor_po", "send_vendor_price_request"}


_STOP_FIRST = {"the", "american", "national", "general", "first", "global", "united", "international", "industrial", "industries", "company", "group", "north", "south",
               "east", "west", "new", "great", "royal", "pacific", "atlantic", "metal", "steel", "precision", "all", "top", "quality", "best", "good", "big", "premier",
               "superior", "standard", "universal", "advanced", "modern", "central", "allied", "custom", "quick", "fast", "pure", "prime", "star", "sun", "blue", "red",
               "green", "gold", "silver", "iron", "city", "state", "county", "union", "service", "services", "supply", "parts", "tool", "tools", "machine", "machining",
               "casting", "castings", "products", "systems", "technologies", "technology", "manufacturing", "engineering", "materials", "ceramic", "ceramics"}


def _fold(text: Any) -> str:
    """Lower case, accents gone, look-alike letters (Cyrillic/Greek) written as Latin, invisible characters removed, punctuation as single spaces."""
    from app.services.coordination.jobs import _GREEK_CAPITALS, _LOOKALIKE

    t = unicodedata.normalize("NFKD", str(text or "").translate(_GREEK_CAPITALS)).casefold()
    t = "".join(_LOOKALIKE.get(ch, ch) for ch in t if unicodedata.category(ch) != "Cf" and not unicodedata.combining(ch))
    return " ".join(re.sub(r"[\W_]+", " ", t).split())


def _plain(text: Any) -> str:
    return " " + _fold(text) + " "


def _shouty(name: str) -> bool:
    """A name whose first word is written in capitals ("KWS Ltd", "IKO Southwest"): a short capitalised word like that is a company's own short name."""
    first = str(name or "").split()[0] if str(name or "").split() else ""
    letters = [c for c in first if c.isalpha()]
    return len(letters) >= 2 and all(c.isupper() for c in letters)


def _variants(name: str) -> List[tuple]:
    """How a company is really written, as (text, shortest allowed length): the full name, without its legal ending, its first two words, and its first
    word. The first word is looked for when it is distinctive (4+ letters and not an everyday word), or when it is written in capitals (3 letters, "KWS")."""
    from app.services.coordination.jobs import norm_name

    full, short = _fold(name), norm_name(name)
    words = short.split()
    shouty = _shouty(name)
    out = [(full, 0), (short, 3 if (shouty and len(words) == 1) else 0)]
    if len(words) >= 3:
        out.append((" ".join(words[:2]), 0))
    if len(words) >= 2 and words[0] not in _STOP_FIRST and (len(words[0]) >= 4 or (shouty and len(words[0]) >= 3)):
        out.append((words[0], 3 if shouty else 0))
    seen, uniq = set(), []
    for v, n in out:
        if v and v not in seen:
            seen.add(v)
            uniq.append((v, n))
    return uniq


def _capitalised_in(text: Any, name: str) -> bool:
    """True when the name's first word appears in the text written with its own capital letters ("Vulcan", not "vulcan")."""
    first = re.sub(r"^\W+|\W+$", "", str(name or "").split()[0]) if str(name or "").split() else ""
    return bool(first) and re.search(r"(?<!\w)" + re.escape(first) + r"(?!\w)", str(text or "")) is not None


def found(text: Any, names: Iterable[str], min_len: int = 4) -> List[str]:
    """The names that appear in the text, in the usual short forms, ignoring case, accents, look-alike letters, invisible characters and letter
    spacing. A name shorter than min_len letters is not looked for, unless it is a capitalised short name ("KWS") or the customer of this very job."""
    hay = _plain(text)
    squashed = hay.replace(" ", "")
    out = []
    for name in names:
        if name in out or _fold(name) in _OWN:
            continue
        for v, floor in _variants(name):
            flat = v.replace(" ", "")
            if v in _OWN or len(flat) < (floor if floor and floor < min_len else min_len):
                continue
            if f" {v} " in hay or (len(flat) >= 8 and flat in squashed):          # a long name is also found with its spaces or letters spread out
                if floor == 0 and " " not in v and len(_variants(name)) > 1 and v != _fold(name) and not _capitalised_in(text, name):
                    continue                                                          # a lone first word counts only when it is written like a name, not as an ordinary word
                out.append(name)
                break
    return out


def _matcher():
    from app.core.container import get_entity_matcher

    return get_entity_matcher()


def vendor_names() -> List[str]:
    """Every vendor Allied knows: the vendor list, the vendors on its purchase orders and on the vendor board."""
    names = {str(v.get("vendor_name") or "").strip() for v in _matcher().vendors_cache.values()}
    try:
        from app.core.database import SessionLocal
        from app.models.db_models import PurchaseOrderDB, VendorJobDB

        with SessionLocal() as db:
            names |= {str(r[0] or "").strip() for r in db.query(PurchaseOrderDB.vendor).distinct().all()}
            names |= {str(r[0] or "").strip() for r in db.query(VendorJobDB.vendor).distinct().all()}
    except Exception:  # noqa: BLE001 - the list is a help, not a gate
        pass
    return sorted(n for n in names if n)


def customer_names(extra: Iterable[str] = ()) -> List[str]:
    names = {str(c.get("customer_name") or "").strip() for c in _matcher().customers_cache.values()} | {str(e or "").strip() for e in extra}
    return sorted(n for n in names if n)


def _is_vendor_address(to: Any) -> bool:
    """True when ANY address in the To field is a vendor's ("a@x.com, b@y.com", "Name <a@x.com>", "a@x.com; b@y.com")."""
    mine = set(re.findall(r"[^\s<>,;:()\"']+@[^\s<>,;:()\"']+", str(to or "").casefold()))
    return bool(mine) and any(str(v.get("email") or "").strip().casefold() in mine for v in _matcher().vendors_cache.values())


def check(tool: str, args: Dict[str, Any]) -> List[str]:
    """Warnings for one draft: the customer's name going to a vendor, or a vendor's name going to anyone who is not a vendor. Never raises."""
    try:
        files = " ".join(str(a.get("name") or "") for a in (args.get("_attachments") or []) if isinstance(a, dict))
        text = " ".join([str(args.get("subject") or ""), str(args.get("body") or ""), files])
        if tool in _VENDOR_TOOLS or (tool == "send_email" and _is_vendor_address(args.get("to"))):
            hinted = []                                    # the customer of this RFQ or job: looked for even when short ("3M"); a bad hint never switches the check off
            for key, finder in (("_rfq_id", lambda i: __import__("app.services.coordination.rfqs", fromlist=["x"]).get(int(i)).get("customer")),
                                ("_job_id", lambda i: __import__("app.services.coordination.jobs", fromlist=["x"]).get(int(i)).get("customer"))):
                try:
                    if args.get(key):
                        hinted.append(str(finder(args[key]) or ""))
                except Exception:  # noqa: BLE001
                    pass
            hits = list(dict.fromkeys(found(text, [h for h in hinted if h], min_len=2) + found(text, customer_names())))
            return [f"Blind shipment: this goes to a vendor but names the customer ({', '.join(hits)}). Take it out before you send." for _ in [0] if hits]
        if tool == "send_email":
            hits = found(text, vendor_names())
            return [f"This goes to a customer or other outside party but names a vendor ({', '.join(hits)}). Customers must never see who makes the part." for _ in [0] if hits]
    except Exception:  # noqa: BLE001 - a failed check must never stop a draft from being queued
        return []
    return []
