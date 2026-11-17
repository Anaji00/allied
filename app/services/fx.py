"""Live USD/CAD/CNY exchange rates from a free public service (Frankfurter, European Central Bank rates).

No key, and no Allied data is sent: the request only asks for today's rates. Rates are kept for FX_CACHE_HOURS.
If the service is switched off, unreachable or sends something odd, the fixed rates in settings are used,
so a quote is never blocked by it. The age and source of the rate in use are available from `status()`.
"""

import threading
import time
from typing import Any, Dict, Optional

import httpx

from app.config import settings
from app.core.logging import logger

_lock = threading.Lock()
_cache: Dict[str, Any] = {"at": float("-inf"), "cad": None, "cny": None, "date": None}   # -inf: never fetched (monotonic() can be small just after a reboot)


def _fetch() -> Optional[Dict[str, Any]]:
    try:
        r = httpx.get(settings.FX_URL, params={"base": "USD", "symbols": "CAD,CNY"}, timeout=8)
        r.raise_for_status()
        body = r.json()
        cad, cny = float(body["rates"]["CAD"]), float(body["rates"]["CNY"])
    except Exception as exc:  # noqa: BLE001 - any failure means "use the settings"
        logger.warning(f"Live exchange rates unavailable ({type(exc).__name__}); using the fixed rates.")
        return None
    if not (0.2 < cad < 5 and 1 < cny < 20):             # a wildly different number is a bad reply, not a rate
        logger.warning("Live exchange rates looked wrong; using the fixed rates.")
        return None
    return {"cad": cad, "cny": cny, "date": str(body.get("date") or "")}


def _live() -> Optional[Dict[str, Any]]:
    if not settings.FX_LIVE_RATES:
        return None
    with _lock:
        fresh = time.monotonic() - _cache["at"] < max(1, settings.FX_CACHE_HOURS) * 3600
        if fresh:                                         # also true right after a failed try: don't ask again every quote
            return dict(_cache) if _cache["cad"] else None
        _cache["at"] = time.monotonic()                   # claimed now: other quotes use what we have while this one asks, and a failure waits out the cache time
    got = _fetch()                                        # the web call is made without holding the lock
    with _lock:
        if got:
            _cache.update(got)
        return dict(_cache) if _cache["cad"] else None


def cad_to_usd() -> float:
    live = _live()
    return round(1 / live["cad"], 6) if live else settings.CAD_TO_USD_RATE


def cny_to_usd() -> float:
    live = _live()
    return round(1 / live["cny"], 6) if live else settings.CNY_TO_USD_RATE


def status() -> Dict[str, Any]:
    live = _live()
    return {"source": "live" if live else "settings", "as_of": live["date"] if live else None,
            "cad_to_usd": cad_to_usd(), "cny_to_usd": cny_to_usd(), "enabled": settings.FX_LIVE_RATES}
