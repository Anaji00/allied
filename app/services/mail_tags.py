"""Tags for each email, by rule, before any AI: cheap, predictable, and easy to change.

- Topic tags from keywords in the subject and the start of the body: rfq, po, invoice,
  payment, shipping, photos, drawing, quality, urgent, complaint. MAIL_TAG_RULES adds or
  replaces them (JSON: {"tag": ["keyword", ...]}).
- Noise tags from the email's own headers and sender: newsletter, auto-reply, bounce,
  calendar, notification. Noise is logged quietly: no AI read, no alert, hidden from the
  feed unless asked. MAIL_QUIET_SENDERS adds senders or domains to treat as noise.
- A thread key, so every reply in one conversation shares one alert a day.
"""

import json
import re
from email.message import Message
from typing import Dict, List, Optional

from app.config import settings
from app.core.logging import logger

TOPICS: Dict[str, List[str]] = {
    "rfq": [r"\brfq\b", r"request for (?:a )?quot", r"\bplease quote\b", r"\bquotation\b", r"\bpricing\b", r"\bbudgetary\b"],
    "po": [r"purchase order", r"\bp\.?o\.?\s*#?\s*\d{3,}", r"\border confirmation\b"],
    "invoice": [r"\binvoice\b", r"\bproforma\b", r"\bpro forma\b", r"\bp\.?i\.?\s*#?\s*\d", r"\bstatement of account\b"],
    "payment": [r"\bpayment\b", r"\bwire\b", r"\bremittance\b", r"\bdeposit\b", r"\bpaid\b"],
    "shipping": [r"\bshipped\b", r"\bship date\b", r"\beta\b", r"\betd\b", r"\bb/?l\b", r"bill of lading", r"\bcontainer\b",
                 r"\bvessel\b", r"\btracking\b", r"\bfreight\b", r"\bcrat(?:e|ing)\b"],
    "photos": [r"\bphotos?\b", r"\bpictures?\b", r"\bpics\b"],
    "drawing": [r"\bdrawings?\b", r"\bdwg\b", r"\brev(?:ision)?\.?\s*[a-z0-9]\b", r"\bsketch\b", r"\bstep file\b"],
    "quality": [r"\bmtr\b", r"mill cert", r"\bncr\b", r"\binspection\b", r"\bnon-?conform"],
    "urgent": [r"\burgent\b", r"\basap\b", r"\brush\b", r"\bline (?:is )?down\b", r"\bemergency\b", r"\bimmediately\b"],
    "complaint": [r"\bcomplain", r"\bdefect", r"\bcracked\b", r"\bwrong (?:part|size|item)\b", r"\bdamaged\b", r"\brejected\b"],
}
NOISE = ("newsletter", "auto-reply", "bounce", "calendar", "notification")
_NOREPLY = re.compile(r"^(?:no-?reply|do-?not-?reply|notifications?|mailer-daemon|postmaster|alerts?|news|marketing)[@+.-]", re.I)
_AUTO_SUBJECT = re.compile(r"^(?:automatic reply|auto(?:matic)?[- ]?reply|out of (?:the )?office|ooo\b|abwesenheit|autoreply)", re.I)
_BOUNCE_SUBJECT = re.compile(r"undeliverable|delivery status notification|mail delivery (?:failed|subsystem)|returned mail", re.I)
_CALENDAR_SUBJECT = re.compile(r"^(?:invitation|updated invitation|accepted|declined|tentative|canceled event)\b.*:", re.I)
_PREFIX = re.compile(r"^\s*(?:(?:re|fw|fwd|aw|wg|sv|tr|rv)\s*(?:\[\d+\])?\s*:\s*|\[ext(?:ernal)?\]\s*)+", re.I)


def _rules() -> Dict[str, List[str]]:
    rules = dict(TOPICS)
    if settings.MAIL_TAG_RULES:
        try:
            extra = json.loads(settings.MAIL_TAG_RULES)
            rules.update({str(k).lower(): [re.escape(str(w).lower()) if not str(w).startswith("re:") else str(w)[3:]
                                           for w in v] for k, v in extra.items()})
        except (ValueError, AttributeError) as exc:
            logger.warning(f"MAIL_TAG_RULES is not valid JSON ({exc}); using the built-in tags only.")
    return rules


def topics(subject: str, body: str) -> List[str]:
    text = f"{subject}\n{body[:3000]}".lower()
    return [tag for tag, words in _rules().items() if any(re.search(w, text) for w in words)]


def noise(msg: Message, sender: str, subject: str) -> Optional[str]:
    """Why this email is noise (a noise tag), or None if a person might need it."""
    sender = (sender or "").lower()
    quiet = {s.strip().lower().lstrip("@") for s in (settings.MAIL_QUIET_SENDERS or "").split(",") if s.strip()}
    if sender in quiet or sender.rsplit("@", 1)[-1] in quiet:
        return "notification"
    if (msg.get("Auto-Submitted") or "no").lower() != "no" or msg.get("X-Autoreply") or msg.get("X-Autorespond") \
            or _AUTO_SUBJECT.search(subject or ""):
        return "auto-reply"
    if _BOUNCE_SUBJECT.search(subject or "") or sender.startswith(("mailer-daemon@", "postmaster@")):
        return "bounce"
    if _CALENDAR_SUBJECT.search(subject or "") or any(p.get_content_type() == "text/calendar" for p in msg.walk()):
        return "calendar"
    if msg.get("List-Unsubscribe") or msg.get("List-Id") or (msg.get("Precedence") or "").lower() in ("bulk", "list", "junk"):
        return "newsletter"
    if _NOREPLY.search(sender):
        return "notification"
    return None


def thread_key(msg: Message, subject: str, message_id: str) -> str:
    """One key per conversation: the first message of the thread (a reply names it first in
    References), so the opening email and every reply share it. Without ids, the bare subject."""
    refs = (msg.get("References") or "").split() or (msg.get("In-Reply-To") or "").split()
    if refs:
        return refs[0][:200]
    if message_id and not message_id.startswith("noid-"):
        return message_id[:200]
    return "subj:" + _PREFIX.sub("", subject or "").strip().lower()[:190]
