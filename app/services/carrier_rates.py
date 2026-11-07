"""Carrier prices that Allied keeps up to date itself, plus the live carrier lookups that plug in beside them.

Freight prices change all the time, so no carrier's price lives in the code. Prices sit on a rate card the
team edits from the app (a small file in the data folder). Each line says when someone last checked it,
and the quote says so too: "estimate, not reviewed", "checked 2026-11-02" or "40 days old: confirm".
A carrier with a live price service (FedEx today) is asked for a real price first; any other carrier
that gets one later is added to `carrier_providers.PROVIDERS` and appears the same way. Nothing here
books a shipment or spends money.
"""

import json
import os
import re
import tempfile
import threading
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.config import settings

REGIONS = ("any", "domestic", "canada", "international")
_lock = threading.Lock()

# What the app starts with until someone enters real prices: the old built-in estimates. as_of is empty, so
# every quote from them says "estimate, not reviewed".
_STARTER = [
    {"id": "expeditors-ocean", "carrier": "Expeditors International", "service": "Ocean Direct LCL + Port Drayage",
     "regions": ["international"], "rate_usd": 1450.0, "per_lb_usd": 0.0, "transit_days": 18},
    {"id": "dhl-air", "carrier": "DHL Global Forwarding", "service": "Air Cargo Priority (Zibo -> Chicago O'Hare)",
     "regions": ["international"], "rate_usd": 3850.0, "per_lb_usd": 0.0, "transit_days": 4},
    {"id": "dayross-canada", "carrier": "Day & Ross Cross-Border", "service": "Direct LTL (US Midwest -> Alberta Hub)",
     "regions": ["canada"], "rate_usd": 580.0, "per_lb_usd": 0.0, "transit_days": 3},
    {"id": "tforce-ltl", "carrier": "TForce Freight", "service": "Standard Regional LTL",
     "regions": ["any"], "rate_usd": 420.0, "per_lb_usd": 0.0, "transit_days": 2},
    {"id": "fedex-freight", "carrier": "FedEx Freight Priority", "service": "Guaranteed Morning Delivery",
     "regions": ["any"], "rate_usd": 610.0, "per_lb_usd": 0.0, "transit_days": 1},
]


class RateCardError(ValueError):
    """Something a person can read about a rate line that isn't valid."""


def _path() -> Path:
    return Path(settings.DATA_DIR) / "carrier_rates.json"


def _line(raw: Dict[str, Any]) -> Dict[str, Any]:
    return {"id": raw["id"], "carrier": raw["carrier"], "service": raw.get("service", ""), "regions": raw.get("regions") or ["any"],
            "rate_usd": float(raw.get("rate_usd", 0)), "per_lb_usd": float(raw.get("per_lb_usd", 0) or 0),
            "transit_days": int(raw.get("transit_days", 3)), "as_of": raw.get("as_of") or None,
            "note": raw.get("note", ""), "enabled": bool(raw.get("enabled", True))}


def load() -> List[Dict[str, Any]]:
    """The rate card: the saved file, or the starter lines if none has been saved. A damaged file falls back to the starter."""
    p = _path()
    try:
        if p.is_file():
            return [_line(r) for r in json.loads(p.read_text(encoding="utf-8"))]
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return [_line(r) for r in _STARTER]


def _save(lines: List[Dict[str, Any]]) -> None:
    p = _path()
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=p.parent, prefix=".carrier_rates.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(lines, f, indent=2)
        os.replace(tmp, p)                      # all or nothing: a crash never leaves half a file
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def check_fields(data: Dict[str, Any], partial: bool = False) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    if "carrier" in data or not partial:
        name = str(data.get("carrier") or "").strip()
        if not name or len(name) > 80:
            raise RateCardError("Give the carrier a name (up to 80 characters).")
        out["carrier"] = name
    if "service" in data:
        out["service"] = str(data["service"] or "").strip()[:120]
    if "regions" in data:
        regions = data["regions"] if isinstance(data["regions"], list) else [data["regions"]]
        if not regions or any(r not in REGIONS for r in regions):
            raise RateCardError(f"Where it applies must be one or more of: {', '.join(REGIONS)}.")
        out["regions"] = list(dict.fromkeys(regions))
    for key, label, top in (("rate_usd", "The price", 1_000_000), ("per_lb_usd", "The price per pound", 1000)):
        if key in data:
            try:
                v = float(data[key] or 0)
            except (TypeError, ValueError):
                raise RateCardError(f"{label} must be a number.")
            if not 0 <= v <= top:
                raise RateCardError(f"{label} must be between 0 and {top:,}.")
            out[key] = round(v, 2)
    if "transit_days" in data:
        try:
            d = int(data["transit_days"])
        except (TypeError, ValueError):
            raise RateCardError("Transit days must be a whole number.")
        if not 0 <= d <= 120:
            raise RateCardError("Transit days must be between 0 and 120.")
        out["transit_days"] = d
    if "as_of" in data:
        try:
            out["as_of"] = date.fromisoformat(str(data["as_of"])).isoformat() if data["as_of"] else None
        except ValueError:
            raise RateCardError("The checked date must look like 2026-11-05.")
    if "note" in data:
        out["note"] = str(data["note"] or "")[:300]
    if "enabled" in data:
        out["enabled"] = bool(data["enabled"])
    return out


def _slug(text: str, taken: set) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:40] or "carrier"
    out, n = base, 2
    while out in taken:
        out, n = f"{base}-{n}", n + 1
    return out


def add(data: Dict[str, Any]) -> Dict[str, Any]:
    fields = check_fields(data)
    with _lock:
        lines = load()
        new = _line({"id": _slug(f"{fields['carrier']} {fields.get('service', '')}", {r["id"] for r in lines}),
                     **{"rate_usd": 0, "transit_days": 3}, **fields})
        lines.append(new)
        _save(lines)
    return new


def update(line_id: str, data: Dict[str, Any]) -> Dict[str, Any]:
    fields = check_fields(data, partial=True)
    with _lock:
        lines = load()
        for r in lines:
            if r["id"] == line_id:
                r.update(fields)
                if any(k in fields for k in ("rate_usd", "per_lb_usd")) and "as_of" not in fields:
                    r["as_of"] = date.today().isoformat()   # a person just typed a price: that is a check
                _save(lines)
                return r
    raise LookupError(f"No rate line '{line_id}'.")


def confirm(line_id: str) -> Dict[str, Any]:
    """'I checked this price today': stamps today's date without changing the price."""
    return update(line_id, {"as_of": date.today().isoformat()})


def remove(line_id: str) -> None:
    with _lock:
        lines = load()
        kept = [r for r in lines if r["id"] != line_id]
        if len(kept) == len(lines):
            raise LookupError(f"No rate line '{line_id}'.")
        _save(kept)


def age_days(line: Dict[str, Any], today: Optional[date] = None) -> Optional[int]:
    if not line.get("as_of"):
        return None
    return ((today or date.today()) - date.fromisoformat(line["as_of"])).days


def describe(line: Dict[str, Any], today: Optional[date] = None) -> str:
    """The plain note put after the service on a quote."""
    days = age_days(line, today)
    if days is None:
        return "estimate, not reviewed"
    if days > settings.CARRIER_RATE_STALE_DAYS:
        return f"rate card, {days} days old: confirm before booking"
    return f"rate card, checked {line['as_of']}"


def view() -> List[Dict[str, Any]]:
    out = []
    for r in load():
        days = age_days(r)
        out.append({**r, "days_old": days, "status": describe(r), "stale": days is None or days > settings.CARRIER_RATE_STALE_DAYS})
    return out


def matching(is_canada: bool, is_international: bool) -> List[Dict[str, Any]]:
    """Enabled lines that apply to this destination."""
    want = {"any"} | ({"canada"} if is_canada else set()) | ({"international"} if is_international else set())
    if not is_canada and not is_international:
        want.add("domestic")
    return [r for r in load() if r["enabled"] and want & set(r["regions"])]


def price(line: Dict[str, Any], weight_lbs: Optional[float]) -> float:
    return round(line["rate_usd"] + (line["per_lb_usd"] * float(weight_lbs) if weight_lbs and line["per_lb_usd"] else 0), 2)
