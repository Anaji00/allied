"""Exchange rates for showing amounts in US dollars. Read only: it converts nothing and stores nothing."""
from typing import Any, Dict

from fastapi import APIRouter, Depends

from app.config import settings
from app.core.security import require_viewer
from app.services import fx

router = APIRouter()


def _rate(value: Any):
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return round(1 / v, 6) if v > 0 else None


@router.get("", summary="Approximate US-dollar value of one unit of each known currency (for showing 'about $X' beside the original amount)")
def rates(_user: dict = Depends(require_viewer)) -> Dict[str, Any]:
    st = fx.status()
    to_usd: Dict[str, Any] = {"USD": 1}
    for code, per_usd in (settings.FX_RATES or {}).items():           # other currencies in the settings: dollars per unit = 1 / units per dollar; a bad or zero rate is null, never a guess
        code = str(code).upper()
        if code != "USD":
            to_usd[code] = _rate(per_usd)
    to_usd["CAD"], to_usd["CNY"] = st["cad_to_usd"], st["cny_to_usd"]        # the live rate when there is one, else the settings
    return {"source": st["source"], "as_of": st["as_of"], "approximate": True, "to_usd": to_usd,
            "note": "These rates are approximate. Show a converted figure only as 'about', beside the original amount; never use it in a stored total or a document. A currency not listed here has no rate."}
