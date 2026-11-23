"""One strict reader for money and quantities typed by people: plain numbers only. No exponent notation (1e30), no NaN or Infinity, at most 15 digits before the point, a comma only as a proper thousands
separator (1,250.00 is fine; 12,50 and 1,2,3 are refused, because they are decimal commas or typos and would be silently misread), an optional leading currency sign."""
import re
from decimal import Decimal, InvalidOperation
from typing import Any, Optional

MAX_DIGITS = 15
_PLAIN = re.compile(r"[+-]?(?:\d+|\d{1,3}(?:,\d{3})+)(?:\.\d{1,6})?")


def safe_text(value: Any) -> Any:
    """A text cell that starts like a spreadsheet formula (= + - @ or a control character) is written with a leading apostrophe, so Excel shows it and never runs it."""
    if isinstance(value, str) and value[:1] in ("=", "+", "-", "@", "\t", "\r", "\n"):
        return "'" + value
    return value


def parse_amount(value: Any, what: str = "amount", allow_negative: bool = False, none_ok: bool = False) -> Optional[Decimal]:
    """The number, or ValueError with a plain sentence naming `what`. None (or blank) is None when none_ok, else an error."""
    if value is None or (isinstance(value, str) and not value.strip()):
        if none_ok:
            return None
        raise ValueError(f"The {what} is missing.")
    if isinstance(value, bool):
        raise ValueError(f"The {what} is not a number.")
    if isinstance(value, (int, float, Decimal)):
        text = format(Decimal(str(value)), "f") if not isinstance(value, Decimal) else format(value, "f")
    else:
        text = str(value).strip()
    text = re.sub(r"^([+-]?)\s*[$€£¥]\s*", r"\1", text)
    if not _PLAIN.fullmatch(text):
        raise ValueError(f"The {what} is not a plain number (write it like 1250.00 or 1,250.00; no exponents, and no comma as a decimal point).")
    try:
        d = Decimal(text.replace(",", ""))
    except InvalidOperation:
        raise ValueError(f"The {what} is not a number.")
    if not d.is_finite():
        raise ValueError(f"The {what} is not a number.")
    if len(text.split(".")[0].replace(",", "").lstrip("+-").lstrip("0")) > MAX_DIGITS:
        raise ValueError(f"The {what} is too large to be believable (more than {MAX_DIGITS} digits).")
    if d < 0 and not allow_negative:
        raise ValueError(f"The {what} must be zero or more.")
    return d
