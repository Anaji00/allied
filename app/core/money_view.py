"""What each role may SEE of the money in an order or a purchase order. The screens already hide supplier prices, margins and costs from people who should not see them; this makes the server do it too, so a
direct call to the API returns null (not the number) for the same people. Fields are set to None, not removed, so the shapes the frontend expects stay the same.

- Orders (list and one): total expense and gross margin are for managers and administrators only.
- Purchase orders: the lines' unit costs and amounts and the totals are for staff (operators draft the PO, so they need them); a view-only sign-in sees them as null."""
from typing import Any, Dict, Iterable, List

ORDER_KEYS = ("total_expense", "gross_margin_pct", "margin_pct", "gross_margin", "total_cost")
ORDER_LINE_KEYS = ("unit_cost", "cost", "extended_cost", "vendor_cost", "expense")
PO_KEYS = ("total", "approval_limit", "total_cost")
PO_LINE_KEYS = ("unit_cost", "amount", "cost", "price", "extended_cost")


def _role(user: Dict[str, Any]) -> str:
    return str((user or {}).get("role", "")).upper()


def sees_order_money(user: Dict[str, Any]) -> bool:
    return _role(user) in ("MANAGER", "ADMIN")


def sees_po_money(user: Dict[str, Any]) -> bool:
    return _role(user) in ("OPERATOR", "MANAGER", "ADMIN")


def _blank_lines(lines: Any, keys: Iterable[str]) -> None:
    for ln in lines or []:
        if isinstance(ln, dict):
            for k in keys:
                if k in ln:
                    ln[k] = None


def hide_order(order: Dict[str, Any], user: Dict[str, Any]) -> Dict[str, Any]:
    """The order with its cost and margin blanked for anyone but a manager (in place; returns it)."""
    if not isinstance(order, dict) or sees_order_money(user):
        return order
    for k in ORDER_KEYS:
        if k in order:
            order[k] = None
    for key in ("line_items", "lines"):
        _blank_lines(order.get(key), ORDER_LINE_KEYS)
    return order


def hide_orders(rows: List[Dict[str, Any]], user: Dict[str, Any]) -> List[Dict[str, Any]]:
    if not sees_order_money(user):
        for r in rows or []:
            hide_order(r, user)
    return rows


def hide_po(po: Dict[str, Any], user: Dict[str, Any]) -> Dict[str, Any]:
    """The purchase order with its prices and totals blanked for a view-only sign-in (in place; returns it)."""
    if not isinstance(po, dict) or sees_po_money(user):
        return po
    for k in PO_KEYS:
        if k in po:
            po[k] = None
    _blank_lines(po.get("lines"), PO_LINE_KEYS)
    return po


def hide_pos(rows: List[Dict[str, Any]], user: Dict[str, Any]) -> List[Dict[str, Any]]:
    if not sees_po_money(user):
        for r in rows or []:
            hide_po(r, user)
    return rows
