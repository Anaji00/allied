"""Carriers that can give a live price. FedEx is the first; adding another means one small class here.

A provider has `id`, `name`, `status()` (ready or why not) and `quotes(address, weight_lbs)` which returns
[{name, price, currency, transit}]. A provider that is off, unset or down is skipped and the dispatch
screen still has the rate card, so no one carrier is ever required.
"""

from typing import Any, Dict, List, Optional


class FedExProvider:
    id = "fedex"
    name = "FedEx"
    covers = "Parcel services (Ground, Express). Not FedEx Freight (LTL)."

    def status(self) -> Dict[str, Any]:
        from app.services import fedex

        s = fedex.status()
        return {"id": self.id, "name": self.name, "ready": s["ready"], "detail": s["detail"], "covers": self.covers}

    def quotes(self, address: str, weight_lbs: Optional[float] = None) -> List[Dict[str, Any]]:
        from app.services import fedex

        return [{"name": f"FedEx {q['name']}".replace("FedEx FedEx", "FedEx"), "price": q["price"], "currency": q["currency"],
                 "transit": q.get("transit") or q.get("arrives")} for q in fedex.quote(address, weight_lbs)]


PROVIDERS: List[Any] = [FedExProvider()]


def statuses() -> List[Dict[str, Any]]:
    return [p.status() for p in PROVIDERS]
