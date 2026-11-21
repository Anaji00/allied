"""Plain English for the raw status codes the screens show, so the UI never has to map them.

word(kind, status) always returns text: a known code gets a sentence-case phrase, anything else (monday labels, a new code) is tidied
(underscores to spaces, first letter up) rather than shown as a code or dropped."""
from typing import Any, Dict, Optional

_WORDS: Dict[str, Dict[str, str]] = {
    "order": {
        "RECEIVED": "Received", "VALIDATED": "Checked", "PROCESSING": "Being processed", "SYNCED_TO_SAGE": "Made; not in a Sage file yet",
        "VALIDATION_ERROR": "Needs a person: a check failed", "SAGE_ERROR": "Needs a person: Sage did not take it", "SHIPPED": "Shipped",
    },
    "shipment": {
        "BOOKED": "Booked", "CARGO_READY": "Ready at the vendor", "GATE_IN": "At the port", "DEPARTED": "On its way", "IN_TRANSIT": "On its way",
        "ARRIVED": "Arrived", "CUSTOMS": "In customs", "CLEARED": "Cleared customs", "DELIVERED": "Delivered", "DELAYED": "Running late", "EXCEPTION": "Needs a person",
    },
    "po": {
        "DRAFT": "Draft", "APPROVED": "Approved, not sent", "SENDING": "Sending now", "SENT": "Sent to the vendor", "ACKNOWLEDGED": "Vendor confirmed", "CANCELLED": "Cancelled",
    },
    "rfq": {
        "NEW": "New", "ACKNOWLEDGED": "Acknowledged", "CLARIFYING": "Asking the customer", "WAITING_VENDOR": "Waiting for the vendor", "PRICING": "Being priced",
        "QUOTE_SENT": "Quote sent", "WON": "Won", "LOST": "Lost", "NO_BID": "Not bidding",
    },
    "draft": {
        "PENDING": "Waiting for your OK", "DONE": "Done", "FAILED": "Failed", "CANCELLED": "Cancelled", "EXPIRED": "Expired, never sent", "RUNNING": "Running",
    },
    "slip": {"QUEUED": "Waiting to be made", "MAKING": "Being made", "READY": "Ready to print", "FAILED": "Needs a person: could not be made", "PRINTED": "Printed"},
    "exception": {"OPEN": "Needs a person", "RESOLVED": "Settled", "DISMISSED": "Set aside"},
    "severity": {"LOW": "Minor", "MEDIUM": "Worth a look", "HIGH": "Important", "CRITICAL": "Urgent"},
    "bill": {
        "RECONCILED_APPROVED": "Matches the order, approved", "HELD": "Held for a person", "REJECTED": "Does not match, held", "PENDING": "Waiting to be checked",
        "DISCREPANCY_FLAGGED": "Does not match the order, held", "DUPLICATE_INVOICE": "Already entered once", "VENDOR_MISMATCH": "Not the vendor on the order, held",
        "UNMATCHED_NO_PO": "No order found for it, a person looks",
    },
    "vendor_job": {"CANCELLED": "Cancelled", "DONE": "Done", "SHIPPED": "Shipped", "IN_PRODUCTION": "Being made", "WORKING_ON_IT": "Being made", "STUCK": "Stuck"},
    "job_stage": {
        "RFQ": "Request received", "RESEARCH": "Researching", "ENGINEERING": "In engineering", "DESIGN_APPROVED": "Design approved", "VENDOR_QUOTE": "Vendor price in",
        "CUSTOMER_QUOTED": "Quoted to the customer", "CUSTOMER_ORDERED": "Customer ordered", "PO_ISSUED": "Purchase order out", "SO_CREATED": "Sales order made",
        "SHIPPED": "Shipped", "CLOSED": "Closed",
    },
    "exception_type": {
        "UNMATCHED_SKU": "Part not in our catalogue", "INVOICE_COST_VARIANCE": "Bill is higher than the order", "STALE_VENDOR_PRICE": "Vendor price is out of date",
        "VENDOR_NOACK": "Vendor has not confirmed the PO", "ORDER_EXCEPTION": "Order needs a person", "COMPLIANCE_MTR_FAILURE": "Mill certificate failed a check",
        "MONDAY_EVENT_FAILED": "A monday change did not go through", "EXPEDITED_FREIGHT": "Rush freight asked for",
    },
    "sage_import": {"new": "New", "changed": "Changed", "unchanged": "Already here", "problems": "Needs a look"},
    "sage_paid": {"PAID": "Paid in Sage", "PART_PAID": "Part paid in Sage", "UNPAID": "Unpaid in Sage"},
    "alert": {"info": "For your information", "warning": "Behind", "critical": "Urgent"},
}


def tidy(code: Any) -> str:
    text = " ".join(str(code or "").replace("_", " ").replace("-", " ").split())
    return (text[:1].upper() + text[1:].lower()) if text else ""


def word(kind: str, status: Any) -> str:
    """The plain-English phrase for a status code of this kind ("order", "shipment", "po", "rfq", "draft", "slip", "exception", "bill", "vendor_job", "job_stage", "alert")."""
    raw = str(status or "").strip()
    table = _WORDS.get(kind, {})
    return table.get(raw.upper()) or table.get(raw) or tidy(raw)


def with_word(kind: str, row: Dict[str, Any], field: str = "status") -> Dict[str, Any]:
    """The same dict with status_word added next to the raw code (the code is kept)."""
    row["status_word"] = word(kind, row.get(field))
    return row


# What a person should do about each kind of exception, written once: no AI needed, the same words every time.
EXPLAIN_TEXT = {
    "UNMATCHED_SKU": ("A part on the order is not in our catalogue, so it cannot be priced or sent to a vendor yet.", "Find the right catalogue item (or add the part), then re-run the order."),
    "INVOICE_COST_VARIANCE": ("The vendor's bill is higher than the purchase order it belongs to.", "Check the bill against the PO; ask the vendor to correct it or approve the difference."),
    "STALE_VENDOR_PRICE": ("The price we have on file for this vendor is old, so the order's cost may be wrong.", "Ask the vendor for a current quote and update the price."),
    "VENDOR_NOACK": ("The vendor has not confirmed the purchase order.", "Follow up with the vendor and ask them to confirm the price and date."),
    "ORDER_EXCEPTION": ("This order stopped because it needs a person to look at it.", "Open the order, read what it flagged, fix it or approve it."),
    "COMPLIANCE_MTR_FAILURE": ("The mill certificate for this material failed a check.", "Do not accept the material: return it to the supplier or approve a concession."),
    "MONDAY_EVENT_FAILED": ("A change from monday could not be applied in the app.", "Open the monday change, check what it asked for, and apply it by hand if it is right."),
    "EXPEDITED_FREIGHT": ("Someone asked for rush freight, which costs more.", "Confirm the extra cost is worth it before booking the shipment."),
}


def explain_text(exception_type: Any):
    """(what went wrong, what to do) for a known exception type, or None."""
    return EXPLAIN_TEXT.get(str(exception_type or "").strip().upper())
