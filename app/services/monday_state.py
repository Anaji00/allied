"""Is this job / quote request / order on monday? One answer for every screen's chip: "On monday" (with the board, item and group), "Waiting to send" (a row is saved in the staged changes but not
pushed) or "Not on monday". Reading only: the app's own records, the saved copy of the boards and the staged changes; nothing is sent or changed."""

from typing import Any, Dict, List, Optional

LABELS = {"on_monday": "On monday", "waiting": "Waiting to send", "none": "Not on monday"}


def _state(state: str, board_id: Optional[str] = None, item_id: Optional[str] = None, group: Optional[str] = None, name: Optional[str] = None) -> Dict[str, Any]:
    return {"state": state, "label": LABELS[state], "board_id": board_id, "item_id": item_id, "group": group, "item_name": name}


class Lookup:
    """Built once per request (one pass over the saved boards and the staged rows), then asked for each row."""

    def __init__(self) -> None:
        from app.core.database import SessionLocal
        from app.models.db_models import MondayChangeDB, RfqDB
        from app.services import jobs_board
        from app.services.coordination import boards

        self.links = jobs_board.stored_links()
        self.items: Dict[tuple, Dict[str, Any]] = {}
        self.board_names: Dict[str, str] = {}
        with boards._lock:
            for bid, b in boards._mirror.items():
                self.board_names[str(bid)] = b.get("name") or ""
                for it in b.get("items", []):
                    self.items[(str(bid), str(it["id"]))] = it
        self.staged_jobs, self.staged_names = set(), []
        self.rfq_items: Dict[str, str] = {}
        with SessionLocal() as db:
            for c in db.query(MondayChangeDB).filter(MondayChangeDB.kind == "create_item", MondayChangeDB.status == "STAGED").limit(2000):
                pl = c.payload or {}
                if pl.get("link_job"):
                    self.staged_jobs.add(str(pl["link_job"]))
                self.staged_names.append((str(pl.get("item_name") or c.summary or "").casefold(), str(c.board_id or "")))
            for number, item_id in db.query(RfqDB.number, RfqDB.monday_item_id).filter(RfqDB.monday_item_id.isnot(None)):
                self.rfq_items[number] = str(item_id)
        self.rfq_board = ""
        try:
            from app.config import settings

            self.rfq_board = str(getattr(settings, "MONDAY_RFQ_BOARD_ID", "") or "")
        except Exception:  # noqa: BLE001
            pass

    def _on(self, board_id: str, item_id: str) -> Dict[str, Any]:
        it = self.items.get((board_id, item_id)) or {}
        return _state("on_monday", board_id or None, item_id, it.get("group_title"), it.get("name"))

    def job(self, ref: Optional[str]) -> Dict[str, Any]:
        if not ref:
            return _state("none")
        link = self.links.get(ref)
        if link:
            return self._on(link["board_id"], link["item_id"])
        return _state("waiting") if ref in self.staged_jobs else _state("none")

    def rfq(self, number: Optional[str]) -> Dict[str, Any]:
        item = self.rfq_items.get(str(number or ""))
        if item:
            return self._on(self.rfq_board, item)
        if number and any(str(number).casefold() in name for name, _b in self.staged_names):
            return _state("waiting")
        return _state("none")

    def order(self, sales_order: Optional[str], customer_po: Optional[str], job_ref: Optional[str] = None) -> Dict[str, Any]:
        """An order is on monday when its job is, or when a row on a board carries its sales order or customer PO number in its name."""
        if job_ref:
            s = self.job(job_ref)
            if s["state"] != "none":
                return s
        needles = [n.casefold() for n in (str(sales_order or "").removeprefix("SO-"), str(customer_po or "")) if len(n.strip()) >= 4]
        for (bid, iid), it in self.items.items():
            name = str(it.get("name") or "").casefold()
            if needles and any(n in name for n in needles):
                return self._on(bid, iid)
        if needles and any(n in name for name, _b in self.staged_names for n in needles):
            return _state("waiting")
        return _state("none")


def safe_lookup() -> Optional[Lookup]:
    """A Lookup, or None when the boards cannot be read (the chip then says nothing rather than breaking the list)."""
    try:
        return Lookup()
    except Exception:  # noqa: BLE001
        return None
