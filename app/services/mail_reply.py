"""Reply drafts for the shared mailbox, and a person's own AI settings for them.

A reply is always a DRAFT in the approval queue: nothing here sends mail. Order of preference: a rule template (the secretary's delivery and complaint replies), then the AI only when the person
opted in AND asked for it AND neither the sender nor the words are on their never-send-to-AI list, else a plain template (subject, blank body). The AI-use log keeps who, when, which mail, which
model and what came of it; never the message text and never the prompt.
"""

import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from app.core.database import SessionLocal
from app.core.logging import logger
from app.models.db_models import AssistantActionDB, MailAiSettingsDB, MailAiUseDB, MailLogDB

MAX_LIST = 50
MAX_ITEM = 100
TEXT_LIMIT = 1500
_ADDR = re.compile(r"[\w.+'-]+@[\w.-]+\.[A-Za-z]{2,}")
_QUOTE_START = re.compile(r"^\s*(on .{5,120} wrote:|-{2,}\s*original message\s*-{2,}|from:\s.+@.+|_{5,})\s*$", re.I)
_SIG_START = re.compile(r"^\s*(--\s*|sent from my .{0,40}|regards,?|best regards,?|kind regards,?|thanks,?)\s*$", re.I)
_OUR_SO = re.compile(r"\b(?:SO|sales order)[\s#:-]*\d{4,}\b", re.I)


class ReplyError(Exception):
    """An error with a plain sentence and the status it should answer with."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status, self.message = status, message


# ---------------------------------------------------------------- a person's settings
def _clean_list(value: Any, name: str, lower: bool = True) -> List[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ReplyError(422, f"{name} must be a list.")
    out = []
    for v in value:
        text = " ".join(str(v).split())[:MAX_ITEM]
        text = text.lower() if lower else text
        if text and text not in out:
            out.append(text)
    if len(out) > MAX_LIST:
        raise ReplyError(422, f"{name} can hold at most {MAX_LIST} entries.")
    return out


def _settings_dict(row: Optional[MailAiSettingsDB]) -> Dict[str, Any]:
    return {"ai_opt_in": bool(row.ai_opt_in) if row else False, "auto_draft": bool(row.auto_draft) if row else False, "style_note": (row.style_note if row else "") or "",
            "exclude_senders": list(row.exclude_senders or []) if row else [], "exclude_words": list(row.exclude_words or []) if row else []}


def get_settings(email: str) -> Dict[str, Any]:
    with SessionLocal() as db:
        return _settings_dict(db.get(MailAiSettingsDB, (email or "").strip().lower()))


def put_settings(email: str, data: Dict[str, Any]) -> Dict[str, Any]:
    """Saves this person's settings (only their own; everything off until they turn it on). The AI is never used for "draft automatically" unless they opted in too."""
    key = (email or "").strip().lower()
    style = " ".join(str(data.get("style_note") or "").split())
    if len(style) > 500:
        raise ReplyError(422, "The style note can be at most 500 characters.")
    senders = _clean_list(data.get("exclude_senders"), "The sender list")
    words = _clean_list(data.get("exclude_words"), "The word list")
    opt_in, auto = bool(data.get("ai_opt_in", False)), bool(data.get("auto_draft", False))
    if auto and not opt_in:
        raise ReplyError(422, "Turn AI replies on before asking for drafts to be made automatically.")
    with SessionLocal() as db:
        row = db.get(MailAiSettingsDB, key)
        if row is None:
            row = MailAiSettingsDB(user_email=key)
            db.add(row)
        row.ai_opt_in, row.auto_draft, row.style_note, row.exclude_senders, row.exclude_words = opt_in, auto, style, senders, words
        row.updated_at = datetime.now(timezone.utc)
        db.commit()
        return _settings_dict(row)


# ---------------------------------------------------------------- what may reach the AI
_FORWARD = re.compile(r"^\s*(-{3,}\s*forwarded message\s*-{3,}.*|begin forwarded message:?|am .{5,200} schrieb.*|le .{5,200} a [eé]crit.*|(from|von|de)\s*:.*)\s*$", re.I)
_MONEY = re.compile(r"[$€£¥]\s*\d[\d,]*(?:\.\d+)?|\b\d[\d,]*(?:\.\d+)?\s*(?:usd|cad|eur|dollars)\b", re.I)
_MARGIN = re.compile(r"\b(?:margin|markup|cost)\b[^.\n]{0,24}", re.I)
_ORDER_NO = re.compile(r"\b(?:so|po|order|sales order|invoice)\s*[-#:]?\s*#?\s*\d{4,}\b", re.I)


def clean_for_ai(text: str, limit: int = TEXT_LIMIT) -> str:
    """The new part of a message only: quoted lines, forwarded blocks, header blocks and the signature end it; our order numbers, money amounts and margins are taken out; the length is capped.
    Attachments are never part of it."""
    out = []
    for line in str(text or "").splitlines():
        if line.lstrip().startswith(">"):
            continue
        if _QUOTE_START.match(line) or _SIG_START.match(line) or _FORWARD.match(line):
            break
        out.append(line)
    cleaned = "\n".join(out)
    cleaned = _ORDER_NO.sub("[order number]", _OUR_SO.sub("[order number]", cleaned))
    cleaned = _MARGIN.sub("[figure]", _MONEY.sub("[amount]", cleaned))
    return cleaned.strip()[:limit]


def excluded(settings_: Dict[str, Any], sender: str, subject: str, summary: str) -> Optional[str]:
    """"sender" or "words" when this message is on the person's never-send-to-AI list. Senders match however the address is written (+tags, display names, case) and a domain entry covers its subdomains."""
    from app.services import leads

    addr = leads.norm_address(sender)
    local, _, domain = addr.rpartition("@") if "@" in addr else ("", "", "")
    for s in settings_["exclude_senders"]:
        e = leads.norm_address(s)
        if e.startswith("@") or ("@" not in e and "." in e):
            if domain and leads._domain_covers(e.lstrip("@"), domain):
                return "sender"
        elif "@" in e:
            el, _, ed = e.rpartition("@")
            if local == el and leads._domain_covers(ed, domain):
                return "sender"
    hay = f"{subject} {summary}".lower()
    if any(w in hay for w in settings_["exclude_words"]):
        return "words"
    return None


def _log(user: str, mail_id: int, model: Optional[str], outcome: str) -> None:
    with SessionLocal() as db:
        db.add(MailAiUseDB(user_email=user.lower(), mail_id=mail_id, model=model, outcome=outcome[:40]))
        db.commit()


# ---------------------------------------------------------------- the draft
def _queued(action_id: str) -> Optional[Dict[str, Any]]:
    with SessionLocal() as db:
        row = db.get(AssistantActionDB, action_id)
        if row is None:
            return None
        a = row.args or {}
        return {"action_id": row.id, "to": a.get("to", ""), "subject": a.get("subject", ""), "body": a.get("body", ""), "rule": a.get("_play")}


def _mail(mail_id: int) -> MailLogDB:
    with SessionLocal() as db:
        row = db.get(MailLogDB, mail_id)
        if row is None:
            raise ReplyError(404, "No such email.")
        db.expunge(row)
        return row


def _sender_address(row: MailLogDB) -> str:
    return (_ADDR.search(row.sender or "") or [row.sender or ""])[0].lower()


def _queue(user: Dict[str, Any], play: str, key: str, summary: str, to: str, subject: str, body: str, why: List[str]) -> Optional[str]:
    import uuid

    from app.services.coordination import autopilot
    from app.services.coordination.autopilot import Intent

    intent = Intent("draft", key, summary, to=[user["email"]], tool="send_email", args={"to": to, "subject": subject[:200], "body": body, "_why": why})
    did = autopilot.carry_out(play, "suggest", [intent], explicit=True)
    if not did or str(did[0].get("outcome", "")).startswith("failed"):
        return None
    return uuid.uuid5(uuid.NAMESPACE_URL, f"allied-autopilot:{key}").hex


def _rule_draft(row: MailLogDB, user: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    from app.services.coordination import secretary

    sender = _sender_address(row)
    fn = {"delivery_question": lambda: secretary.delivery_reply(str(row.id), sender, row.subject, row.summary or ""),
          "complaint": lambda: secretary.complaint_ack(str(row.id), sender, row.subject, row.summary or "")}.get(row.category)
    if fn is None:
        return None
    import uuid

    did = fn()
    if not did:
        return None
    action_id = did.get("id") or uuid.uuid5(uuid.NAMESPACE_URL, f"allied-autopilot:mail-{'reply' if row.category == 'delivery_question' else 'ack'}:{row.id}").hex
    q = _queued(action_id)
    if q is None:
        return None
    reason = "The email asks where an order is." if row.category == "delivery_question" else "The email is a complaint."
    return {**q, "source": "rule", "rule": "delivery_reply" if row.category == "delivery_question" else "complaint_ack", "note": f"{reason} Check every fact in the draft before it is sent."}


def draft_reply(mail_id: int, user: Dict[str, Any], use_ai: bool = False) -> Dict[str, Any]:
    """{source: rule|ai|template, rule, to, subject, body, action_id, note}. Nothing is sent."""
    row = _mail(mail_id)
    me = (user.get("email") or "").lower()
    sender = _sender_address(row)
    subject = f"Re: {row.subject}"[:200] if not row.subject.lower().startswith("re:") else row.subject[:200]
    from app.services import leads as _leads

    stop = _leads.blocked(sender)
    if stop:                                                       # no reply draft is made to someone on the do-not-contact list
        return {"source": "template", "rule": None, "to": sender, "subject": subject, "body": "", "action_id": None, "note": stop + " No draft was made."}
    mine = get_settings(me)
    if use_ai:
        if not mine["ai_opt_in"]:
            _log(me, mail_id, None, "not_opted_in")
            raise ReplyError(409, "AI replies are off for you.")
        why = excluded(mine, row.sender, row.subject, row.summary or "")
        if why == "sender":
            _log(me, mail_id, None, "excluded_sender")
            raise ReplyError(409, "This sender is on your never-send-to-AI list.")
        if why == "words":
            _log(me, mail_id, None, "excluded_words")
            raise ReplyError(409, "This message has a word on your never-send-to-AI list.")
    rule = _rule_draft(row, user)
    if rule:
        return rule
    if use_ai:
        try:
            from app.services.ai import features

            facts = f"Reply to this email.\nFrom: {sender}\nSubject: {row.subject}\nWhat it says: {clean_for_ai(row.summary or '')}"
            if mine["style_note"]:
                facts += f"\nWriting style asked for: {mine['style_note']}"
            result, _sid = features.draft_email("reply", facts, "en", sender, reference=f"mail:{mail_id}", actor=me)
            body = result.body.strip()
            action_id = _queue(user, "mail_ai_reply", f"mail-ai:{mail_id}:{me}", f"AI reply to {sender}: {row.subject[:80]}", sender, result.subject or subject, body,
                               [f"An AI wrote this from the email's summary; check it before sending (it can be wrong). Excluded senders and words were not sent to it."])
            _log(me, mail_id, _model_name(), "drafted")
            note = "Written by the AI from a short summary of the email. Check it before you send it." + (" It states a date, price or promise: check that." if result.states_commitment else "")
            return {"source": "ai", "rule": None, "to": sender, "subject": result.subject or subject, "body": body, "action_id": action_id, "note": note}
        except Exception as exc:  # noqa: BLE001 - the AI being off or busy is not an error for the person
            logger.info(f"Mail AI reply for {mail_id} fell back to the template: {type(exc).__name__}")
            _log(me, mail_id, _model_name(), "ai_unavailable")
            return {"source": "template", "rule": None, "to": sender, "subject": subject, "body": "", "action_id": None,
                    "note": "The AI is not available right now, so this is a plain template: write your reply."}
    return {"source": "template", "rule": None, "to": sender, "subject": subject, "body": "", "action_id": None, "note": "No ready-made reply fits this email: write your own."}


def _model_name() -> Optional[str]:
    try:
        from app.services.ai.providers import get_provider

        return str(get_provider().status().get("model") or "") or None
    except Exception:  # noqa: BLE001
        return None


# ---------------------------------------------------------------- the reply template store (managers edit; merge fields are {customer} {vendor} {po} {job} {date} {first_name} {signature})
MERGE_FIELDS = ("customer", "vendor", "po", "job", "date", "first_name", "signature")
_KEY = re.compile(r"^[a-z0-9][a-z0-9-]{1,58}$")


def _tmpl(r) -> Dict[str, Any]:
    return {"key": r.key, "title": r.title, "subject": r.subject, "body": r.body, "situation": r.situation, "merge_fields": list(MERGE_FIELDS),
            "updated_by": r.updated_by, "updated_at": r.updated_at.isoformat() if r.updated_at else None}


def list_templates() -> List[Dict[str, Any]]:
    from app.models.db_models import MailTemplateDB

    with SessionLocal() as db:
        return [_tmpl(r) for r in db.query(MailTemplateDB).order_by(MailTemplateDB.key)]


def put_template(key: str, data: Dict[str, Any], user: Dict[str, Any]) -> Dict[str, Any]:
    from app.models.db_models import MailTemplateDB

    if not _KEY.match(key or ""):
        raise ReplyError(422, "The template name is lowercase letters, numbers and dashes.")
    subject, body = " ".join(str(data.get("subject") or "").split()), str(data.get("body") or "").strip()
    if not subject or not body or len(subject) > 300 or len(body) > 8000:
        raise ReplyError(422, "A template needs a subject (up to 300 characters) and a body (up to 8000).")
    import string

    try:
        used = [f for _l, f, _s, _c in string.Formatter().parse(subject + " " + body) if f is not None]
    except ValueError:
        raise ReplyError(422, "A template has a stray brace: write {customer} to insert a field.") from None
    bad = [f for f in used if f not in MERGE_FIELDS]
    if bad:
        raise ReplyError(422, f"Unknown merge field {{{bad[0]}}}. Use: " + ", ".join("{" + f + "}" for f in MERGE_FIELDS) + ".")
    with SessionLocal() as db:
        row = db.get(MailTemplateDB, key)
        if row is None:
            row = MailTemplateDB(key=key, title=str(data.get("title") or key)[:200], subject=subject, body=body)
            db.add(row)
        row.title, row.subject, row.body, row.situation = str(data.get("title") or row.title or key)[:200], subject, body, (str(data.get("situation") or "")[:40] or None)
        row.updated_by, row.updated_at = user.get("email"), datetime.now(timezone.utc)
        db.commit()
        return _tmpl(row)


def delete_template(key: str) -> None:
    from app.models.db_models import MailTemplateDB

    with SessionLocal() as db:
        db.query(MailTemplateDB).filter(MailTemplateDB.key == key).delete()
        db.commit()
