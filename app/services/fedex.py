"""FedEx: configuration, a connection check, and live parcel price quotes.

What this does: asks FedEx's Rates and Transit Times API for the price and delivery time of
FedEx Ground and Express parcel services to a US or Canadian address. It only READS prices.
It never books a shipment, prints a label or spends anything.

What it does not do: FedEx's rates API does not quote FedEx Freight (heavy LTL freight), so
Allied's "FedEx Freight" figure stays an estimate. And it has not yet been run against FedEx:
use the connection check with the sandbox credentials first (see README, FedEx).

Settings: FEDEX_ENABLED, FEDEX_ENV (sandbox or production), FEDEX_CLIENT_ID / FEDEX_CLIENT_SECRET
(from a project on developer.fedex.com), FEDEX_ACCOUNT_NUMBER, and the shipper's address.
"""

import re
import threading
import time
from typing import Any, Dict, List, Optional

import httpx

from app.config import settings
from app.core.logging import logger

_BASE = {"sandbox": "https://apis-sandbox.fedex.com", "production": "https://apis.fedex.com"}
_token: Dict[str, Any] = {"value": None, "expires": 0.0}
_lock = threading.Lock()
_US_STATE = re.compile(r",\s*([A-Z]{2})\.?\s+(\d{5})(?:-\d{4})?\b")
_CA_POSTAL = re.compile(r"\b([ABCEGHJ-NPRSTVXY]\d[A-Z])\s?(\d[A-Z]\d)\b", re.I)


class FedExError(Exception):
    """Something a person can read: what went wrong talking to FedEx."""


def base_url() -> str:
    return _BASE.get((settings.FEDEX_ENV or "sandbox").lower(), _BASE["sandbox"])


def missing() -> List[str]:
    need = {"FEDEX_CLIENT_ID": settings.FEDEX_CLIENT_ID, "FEDEX_CLIENT_SECRET": settings.FEDEX_CLIENT_SECRET,
            "FEDEX_ACCOUNT_NUMBER": settings.FEDEX_ACCOUNT_NUMBER, "FEDEX_SHIPPER_POSTAL": settings.FEDEX_SHIPPER_POSTAL}
    return [k for k, v in need.items() if not (v or "").strip()]


def status() -> Dict[str, Any]:
    gaps = missing()
    return {"enabled": settings.FEDEX_ENABLED, "env": (settings.FEDEX_ENV or "sandbox").lower(), "ready": settings.FEDEX_ENABLED and not gaps,
            "missing": gaps, "base_url": base_url(),
            "detail": ("FedEx is switched off." if not settings.FEDEX_ENABLED else
                       f"Waiting for {', '.join(gaps)}." if gaps else f"Ready ({(settings.FEDEX_ENV or 'sandbox').lower()}). Prices only: no shipments or labels."),
            "limits": "Parcel services (Ground, Express). FedEx Freight (LTL) isn't available from FedEx's rates API."}


def _get_token() -> str:
    with _lock:
        if _token["value"] and time.monotonic() < _token["expires"] - 60:
            return _token["value"]
    try:
        r = httpx.post(f"{base_url()}/oauth/token", timeout=15,
                       data={"grant_type": "client_credentials", "client_id": settings.FEDEX_CLIENT_ID,
                             "client_secret": settings.FEDEX_CLIENT_SECRET},
                       headers={"Content-Type": "application/x-www-form-urlencoded"})
    except httpx.HTTPError as exc:
        raise FedExError(f"Couldn't reach FedEx ({type(exc).__name__}).")
    if r.status_code in (400, 401, 403):
        raise FedExError("FedEx refused the credentials. Check FEDEX_CLIENT_ID and FEDEX_CLIENT_SECRET, and that they match "
                         f"the {(settings.FEDEX_ENV or 'sandbox').lower()} environment.")
    if r.status_code >= 300:
        raise FedExError(f"FedEx answered with an error ({r.status_code}).")
    try:
        token = (r.json() or {}).get("access_token")
        body = r.json()
    except (ValueError, AttributeError):
        raise FedExError("FedEx sent back a login reply I couldn't read.")
    if not token:
        raise FedExError("FedEx didn't send back an access token.")
    with _lock:
        _token.update(value=token, expires=time.monotonic() + float(body.get("expires_in") or 3600))
    return token


def check() -> Dict[str, Any]:
    """Log in to FedEx and say whether it worked. Reads nothing, buys nothing."""
    s = status()
    if not s["ready"]:
        return {**s, "ok": False}
    try:
        _get_token()
        return {**s, "ok": True, "detail": f"Connected to FedEx ({s['env']})."}
    except FedExError as exc:
        return {**s, "ok": False, "detail": str(exc)}


def parse_destination(text: str) -> Optional[Dict[str, str]]:
    """A US or Canadian address in free text -> {postalCode, countryCode, stateOrProvinceCode?}; None if not clear."""
    text = text or ""
    us = _US_STATE.search(text)
    if us:
        return {"postalCode": us.group(2), "countryCode": "US", "stateOrProvinceCode": us.group(1)}
    ca = _CA_POSTAL.search(text)
    if ca:
        return {"postalCode": f"{ca.group(1)}{ca.group(2)}".upper(), "countryCode": "CA"}
    return None


def _request_body(dest: Dict[str, str], weight_lbs: float) -> Dict[str, Any]:
    shipper = {"postalCode": settings.FEDEX_SHIPPER_POSTAL, "countryCode": settings.FEDEX_SHIPPER_COUNTRY or "US"}
    if settings.FEDEX_SHIPPER_STATE:
        shipper["stateOrProvinceCode"] = settings.FEDEX_SHIPPER_STATE
    if settings.FEDEX_SHIPPER_CITY:
        shipper["city"] = settings.FEDEX_SHIPPER_CITY
    return {
        "accountNumber": {"value": settings.FEDEX_ACCOUNT_NUMBER},
        "rateRequestControlParameters": {"returnTransitTimes": True},
        "requestedShipment": {
            "shipper": {"address": shipper}, "recipient": {"address": {**dest, "residential": False}},
            "pickupType": "USE_SCHEDULED_PICKUP", "rateRequestType": ["ACCOUNT", "LIST"], "packagingType": "YOUR_PACKAGING",
            "requestedPackageLineItems": [{"weight": {"units": "LB", "value": round(float(weight_lbs), 1)}}],
        },
    }


def parse_quotes(body: Dict[str, Any]) -> List[Dict[str, Any]]:
    """The cheapest-first list of services from a rates reply: name, price, currency, when it arrives."""
    out = []
    for d in ((body.get("output") or {}).get("rateReplyDetails") or []):
        rated = d.get("ratedShipmentDetails") or []
        pick = next((x for x in rated if x.get("rateType") == "ACCOUNT"), None) or (rated[0] if rated else None)
        if not pick:
            continue
        price = pick.get("totalNetCharge") if pick.get("totalNetCharge") is not None else pick.get("totalNetFedExCharge")
        if price is None:
            continue
        commit = d.get("commit") or {}
        out.append({"service": d.get("serviceType"), "name": d.get("serviceName") or d.get("serviceType"),
                    "price": float(price), "currency": pick.get("currency") or (pick.get("shipmentRateDetail") or {}).get("currency") or "USD",
                    "rate_type": pick.get("rateType"),
                    "arrives": (commit.get("dateDetail") or {}).get("dayCxsFormat", "")[:10] or None,
                    "transit": (commit.get("transitDays") or {}).get("description")})
    return sorted(out, key=lambda q: q["price"])


def quote(destination_text: str, weight_lbs: Optional[float] = None) -> List[Dict[str, Any]]:
    """Live FedEx parcel prices to this address. Raises FedExError with a plain sentence."""
    s = status()
    if not s["ready"]:
        raise FedExError(s["detail"])
    dest = parse_destination(destination_text)
    if dest is None:
        raise FedExError("I couldn't read a US ZIP or Canadian postal code from that address, so I can't ask FedEx.")
    weight = float(weight_lbs or settings.FEDEX_DEFAULT_WEIGHT_LBS)
    try:
        r = httpx.post(f"{base_url()}/rate/v1/rates/quotes", json=_request_body(dest, weight), timeout=20,
                       headers={"Authorization": f"Bearer {_get_token()}", "Content-Type": "application/json", "X-locale": "en_US"})
    except httpx.HTTPError as exc:
        raise FedExError(f"Couldn't reach FedEx ({type(exc).__name__}).")
    if r.status_code == 401:
        with _lock:
            _token.update(value=None, expires=0.0)           # the token went stale: log in again next time
        raise FedExError("FedEx asked to sign in again; try once more.")
    if r.status_code >= 300:
        try:
            detail = "; ".join(e.get("message", "") for e in (r.json().get("errors") or []))[:200]
        except ValueError:
            detail = ""
        logger.warning(f"FedEx rates answered {r.status_code}: {detail}")
        raise FedExError(f"FedEx couldn't price that shipment ({r.status_code}){': ' + detail if detail else ''}.")
    try:
        return parse_quotes(r.json())
    except (ValueError, AttributeError, TypeError):
        raise FedExError("FedEx sent back a reply I couldn't read.")
