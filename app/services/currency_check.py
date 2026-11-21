"""Money with its currency, in words, and a flag for an order whose currency does not fit where it ships. Reading only: it never changes an order.

Older orders may carry a currency that came from a vendor's country instead of the customer's (a bug that is fixed); they are flagged here so a person can look, and nothing is rewritten."""

from decimal import Decimal, InvalidOperation
from typing import Any, Dict, Optional, Tuple

from app.config import settings

# code -> (symbol, name in words)
NAMES: Dict[str, Tuple[str, str]] = {"USD": ("$", "US dollars"), "CAD": ("CA$", "Canadian dollars"), "CNY": ("CN\u00a5", "yuan"), "EUR": ("\u20ac", "euros"), "GBP": ("\u00a3", "pounds"),
                                     "MXN": ("MX$", "pesos"), "JPY": ("JP\u00a5", "yen"), "AUD": ("A$", "Australian dollars"), "VND": ("\u20ab", "dong")}
# where goods ship -> the currencies that make sense for a customer there
_FITS = {"US": {"USD"}, "CA": {"CAD", "USD"}}
_COUNTRY = {"US": "the United States", "CA": "Canada"}


def money_text(amount: Any, currency: Any) -> str:
    """"CN\u00a554,242.28 (yuan)", "$8,445.82 (US dollars)". An unknown code is shown as the code. A blank amount is blank."""
    if amount in (None, ""):
        return ""
    code = str(currency or settings.DEFAULT_CURRENCY or "USD").upper()
    symbol, name = NAMES.get(code, (code + " ", code))
    try:
        figure = f"{Decimal(str(amount)):,.2f}"
    except (InvalidOperation, ValueError):
        return str(amount)
    return f"{symbol}{figure} ({name})"


def usd_approx(amount: Any, currency: Any) -> Optional[str]:
    """About how many US dollars, only when a rate is configured for that currency (and it is not already dollars); otherwise None."""
    from app.services.sage_export_service import SageExportService

    code = str(currency or "USD").upper()
    if code == "USD" or amount in (None, ""):
        return None
    try:
        return str(SageExportService.to_usd(Decimal(str(amount)), code))
    except (ValueError, InvalidOperation):
        return None


def check(order: Dict[str, Any]) -> Tuple[bool, str]:
    """(True, the plain reason) when the order's currency does not fit its ship-to country (a US ship-to in yuan); (False, "") otherwise, including when the country is not clear."""
    from app.services.import_paperwork import _country_of

    code = str(order.get("currency") or "").upper()
    where = _country_of(order.get("shipping_address"))
    if not code or where not in _FITS or code in _FITS[where]:
        return False, ""
    return True, f"This order ships to {_COUNTRY[where]} but is in {NAMES.get(code, (code, code))[1]}. Check which currency the customer should be billed in."
