"""AI features: each one reads, drafts or suggests, and records what it did.

None of these create orders, send email, change Sage or approve anything.
Results that someone must act on are stored as SUGGESTED for review; the rest
are stored as INFO so usage and quality can be measured.
"""

import json
import os
import re
import tempfile
import threading
import time
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field

from app.config import settings
from app.core.logging import logger
from app.services.ai import store
from app.services.ai.providers import AIUnavailableError, get_provider, to_json

LANGUAGES = {"en": "English", "vi": "Vietnamese", "zh": "Simplified Chinese (Mandarin)"}

_COMPANY = (
    "You work for Allied Welding Systems, a US supplier of wear-resistant steel plate, "
    "clad plate, ceramic-lined hose and fabricated wear parts, with yards in Lanett AL, "
    "Edmonton AB and a partner mill in Zibo, China."
)


def _run(feature: str, title: str, call, *, input: Any, confidence=None, needs_review=None,
         reference=None, actor="system"):
    """Calls the provider, times it, stores the result, returns (result, suggestion_id)."""
    provider = get_provider()
    started = time.perf_counter()
    result = call(provider)
    duration = int((time.perf_counter() - started) * 1000)
    conf = confidence(result) if callable(confidence) else confidence
    review = needs_review(result) if callable(needs_review) else bool(needs_review)
    suggestion_id = store.record(
        feature,
        title(result) if callable(title) else title,
        provider=provider.name,
        model=provider.model,
        input=to_json(input),
        output=to_json(result),
        confidence=conf,
        needs_review=review,
        reference=reference(result) if callable(reference) else reference,
        duration_ms=duration,
        actor=actor,
    )
    return result, suggestion_id


def _below_bar(confidence: Optional[float]) -> bool:
    return confidence is None or confidence < settings.AI_CONFIDENCE_THRESHOLD


_CJK = re.compile(r"[぀-ヿ㐀-䶿一-鿿가-힯]")


def _text_in(provider, system: str, prompt: str, language: str) -> str:
    """Free text in one language. Small local models sometimes drift into
    Chinese partway through Vietnamese or English; that output is retried once
    with a firmer instruction and refused if it drifts again."""
    out = provider.text(system, prompt)
    if language == "zh" or not _CJK.search(out):
        return out
    lang = LANGUAGES.get(language, "English")
    out = provider.text(
        system + f" Write ONLY in {lang}. Do not use any Chinese characters.", prompt
    )
    if _CJK.search(out):
        raise AIUnavailableError(
            f"The local model mixed Chinese into the {lang} text. Try again, or use a larger model "
            "(e.g. AI_TEXT_MODEL=qwen3:14b, slower on this machine)."
        )
    return out


# ---------------------------------------------------------------------------
# Inbound email: sort it and read the order out of it
# ---------------------------------------------------------------------------


class TriageLine(BaseModel):
    description: str = Field(description="The item as the customer wrote it")
    quantity: float = Field(description="Quantity ordered; 0 if not stated")
    unit: str = Field(description="EA, SHT, FT, LB, etc.; blank if not stated")
    unit_price: float = Field(description="Price per unit if the customer stated one, else 0")


class EmailTriage(BaseModel):
    category: Literal["order", "quote_request", "supplier_invoice", "delivery_question", "complaint", "other", "junk"]
    language: str = Field(description="ISO 639-1 code of the email's language, e.g. en, vi, zh")
    summary: str = Field(description="One sentence in English saying what the sender wants")
    customer_name: str = Field(description="Company placing the order or asking; blank if unknown")
    po_number: str = Field(description="Customer purchase order number exactly as written; blank if none")
    requested_date: str = Field(description="Requested delivery date as written; blank if none")
    lines: List[TriageLine]
    uncertain_fields: List[str] = Field(description="Fields you could not read with certainty")
    confidence: float = Field(ge=0, le=1, description="0-1: how sure you are the category and fields are right")


_TRIAGE_SYSTEM = (
    f"{_COMPANY} You sort incoming email for the order desk and read orders out of it. "
    "Copy numbers, PO numbers and part descriptions exactly as written; never invent a value. "
    "Leave a field blank (or 0) when the email does not state it, and list it in uncertain_fields. "
    "An email is an 'order' only if the sender is placing a purchase order now; asking for a price is "
    "'quote_request'; a bill from one of our suppliers is 'supplier_invoice'. "
    "Set confidence below 0.8 whenever anything needed to process the order is missing or ambiguous."
)


def _attachment_text(attachments: List[Dict[str, Any]]) -> str:
    parts = []
    for att in attachments or []:
        name = att.get("filename") or "attachment"
        text = att.get("text")
        content = att.get("content")
        if not text and isinstance(content, (bytes, bytearray)) and name.lower().endswith(".pdf"):
            try:
                from app.services.ai.drawings import pdf_text

                text = "\n".join(pdf_text(bytes(content)))
            except Exception as exc:  # noqa: BLE001 - an unreadable attachment is noted, not fatal
                text = f"(could not read: {exc})"
        if text:
            parts.append(f"--- {name} ---\n{text[:12000]}")
    return "\n\n".join(parts)


def triage_email(sender: str, subject: str, body: str, attachments=None, reference=None, actor="system"):
    prompt = (
        f"From: {sender}\nSubject: {subject}\n\n{body[:20000]}\n\n"
        + (_attachment_text(attachments or []) or "(no readable attachments)")
    )
    return _run(
        "email_triage",
        lambda r: f"{r.category.replace('_', ' ').title()}: {subject[:200]}",
        lambda p: p.structured(_TRIAGE_SYSTEM, prompt, EmailTriage),
        input={"sender": sender, "subject": subject, "body": body[:4000]},
        confidence=lambda r: r.confidence,
        # Orders and complaints always get a human look; the rest only when unsure.
        needs_review=lambda r: r.category in ("order", "complaint") or _below_bar(r.confidence),
        reference=reference,
        actor=actor,
    )


# ---------------------------------------------------------------------------
# Outbound email drafts (never sent from here)
# ---------------------------------------------------------------------------

DraftKind = Literal["order_acknowledgement", "supplier_chaser", "delivery_update", "invoice_dispute", "general_reply"]


class EmailDraft(BaseModel):
    subject: str
    body: str
    states_commitment: bool = Field(
        description="True if the draft states a price, a delivery date, a quantity or any promise"
    )


_MONTHS = (
    "jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec|tháng|月"
)
# Money, a date or a number in the email = it states something binding. Decided
# by rule, not by asking the model whether it promised anything.
_COMMITMENT = re.compile(rf"[$€£¥]|\d|\b({_MONTHS})", re.I)


def draft_email(kind: str, facts: str, language: str = "en", recipient: str = "", reference=None, actor="system"):
    lang = LANGUAGES.get(language, "English")
    system = (
        f"{_COMPANY} You draft business email for a person to check and send. Write in {lang}. "
        "Use only the facts given; never invent prices, dates, quantities or promises. "
        "Where a needed fact is missing, write [TO CONFIRM] instead. Plain, polite, under 150 words. No emoji. "
        "Reply in exactly this form and nothing else:\nSubject: <subject line>\n\n<email body>"
    )
    prompt = f"Kind of email: {kind.replace('_', ' ')}\nRecipient: {recipient or 'not given'}\nFacts:\n{facts}"

    # Plain text, not JSON: a long email body inside JSON made small local
    # models run on for minutes padding the output.
    def call(p):
        out = _text_in(p, system, prompt, language).strip()
        match = re.match(r"^\s*(subject|chủ đề|主题)\s*[:：]\s*(.+?)\s*(?:\n|$)(.*)", out, re.I | re.S)
        subject, body = (match.group(2), match.group(3).strip()) if match else (kind.replace("_", " ").title(), out)
        return EmailDraft(subject=subject.strip(), body=body, states_commitment=bool(_COMMITMENT.search(body)))

    return _run(
        "email_draft",
        lambda r: f"Draft: {r.subject[:200]}",
        call,
        input={"kind": kind, "facts": facts, "language": language, "recipient": recipient},
        # Anything that promises something is a person's decision to send.
        needs_review=lambda r: r.states_commitment or "[TO CONFIRM]" in r.body,
        reference=reference,
        actor=actor,
    )


# ---------------------------------------------------------------------------
# Translation with the engineering glossary
# ---------------------------------------------------------------------------


class Translation(BaseModel):
    translation: str
    unsure_terms: List[str] = Field(description="Technical terms you were not sure how to translate")


def glossary() -> List[Dict[str, Any]]:
    path = settings.AI_GLOSSARY_PATH or (settings.DATA_DIR / "ai_glossary.json")
    try:
        return json.loads(path.read_text(encoding="utf-8")).get("terms", [])
    except FileNotFoundError:
        return []
    except ValueError as exc:
        logger.error(f"AI glossary at {path} is not valid JSON: {exc}")
        return []


def _glossary_rules(text: str, source: str, target: str) -> List[str]:
    """Approved glossary entries whose source term appears in the text."""
    lowered = text.lower()
    rules = []
    for term in glossary():
        if not term.get("approved"):
            continue  # engineering has not signed it off yet
        src, dst = term.get(source), term.get(target)
        if src and dst and src.lower() in lowered:
            rules.append(f'"{src}" -> "{dst}"')
    return rules


def translate(text: str, target: str, source: str = "en", actor="system"):
    if target not in LANGUAGES or source not in LANGUAGES:
        raise ValueError(f"Supported languages: {', '.join(LANGUAGES)}")
    rules = _glossary_rules(text, source, target)
    system = (
        f"{_COMPANY} You translate engineering and shop-floor text from {LANGUAGES[source]} "
        f"to {LANGUAGES[target]} for welding and fabrication staff. Keep numbers, units, part numbers, "
        "drawing numbers and standard names (ASTM, AWS, GB) exactly as written. Translate meaning, not word by word. "
        + ("Use these approved term translations exactly: " + "; ".join(rules) + ". " if rules else "")
        + "If you are unsure how to translate a technical term, put the original term in double square "
        "brackets right after your translation of it, like [[weld]]. "
        "Reply with the translation only: no notes, no quotes, no explanations."
    )

    # Plain text, not JSON: small local models break out of a JSON string when
    # the translation is long or in another script, leaking fragments into it.
    def call(p):
        out = _text_in(p, system, text, target)
        out = re.sub(r"^(translation|翻译|bản dịch)\s*[:：]\s*", "", out.strip(), flags=re.I).strip().strip('"“”')
        unsure = sorted(set(re.findall(r"\[\[([^\]]+)\]\]", out)))
        return Translation(translation=out, unsure_terms=unsure)

    return _run(
        "translation",
        f"{LANGUAGES[source]} -> {LANGUAGES[target]}: {text[:80]}",
        call,
        input={"text": text[:4000], "source": source, "target": target, "glossary_rules": rules},
        needs_review=False,
        actor=actor,
    )


# ---------------------------------------------------------------------------
# Phone calls: summarise a transcript into a message for the right person
# ---------------------------------------------------------------------------


class CallSummary(BaseModel):
    caller_name: str
    company: str
    reason: Literal["order_status", "new_order", "quote_request", "delivery", "complaint", "supplier", "other"]
    language: str = Field(description="ISO 639-1 code of the language spoken")
    summary: str = Field(description="Two or three sentences in English")
    po_numbers: List[str] = Field(description="Any PO, sales order or quote numbers mentioned, exactly as said")
    action_items: List[str]
    callback_needed: bool
    urgency: Literal["low", "normal", "high"]


def summarize_call(transcript: str, from_number: str = "", reference=None, actor="system"):
    system = (
        f"{_COMPANY} You turn a phone call transcript into a short message for the right person. "
        "Report only what was said; never invent names, numbers or promises. A caller placing an order, "
        "changing an order, asking for a price or complaining always needs a callback."
    )
    prompt = f"Caller number: {from_number or 'unknown'}\nTranscript:\n{transcript[:30000]}"
    return _run(
        "call_summary",
        lambda r: f"Call from {r.caller_name or from_number or 'unknown'}: {r.reason.replace('_', ' ')}",
        lambda p: p.structured(system, prompt, CallSummary),
        input={"from_number": from_number, "transcript": transcript[:8000]},
        # A summary that names a job waits for a person before it is noted on
        # that job: the transcript is whatever the caller sent.
        needs_review=lambda r: r.callback_needed or r.reason in ("new_order", "quote_request", "complaint")
        or any(o.get("monday_item_id") for o in find_orders(r.po_numbers)),
        reference=reference or from_number,
        actor=actor,
    )


def find_orders(numbers: List[str]) -> List[Dict[str, Any]]:
    """Read-only lookup of orders by customer PO or sales order number."""
    from app.core.database import SessionLocal
    from app.models.db_models import OrderDB

    wanted = {re.sub(r"[^A-Z0-9]", "", n.upper()) for n in numbers if n}
    if not wanted:
        return []
    found = []
    with SessionLocal() as db:
        for row in db.query(OrderDB).order_by(OrderDB.created_at.desc()).limit(5000):
            keys = {re.sub(r"[^A-Z0-9]", "", str(v or "").upper()) for v in (row.customer_po_number, row.sales_order_number)}
            if keys & wanted:
                found.append({
                    "sales_order_number": row.sales_order_number,
                    "customer_po_number": row.customer_po_number,
                    "customer_name": row.customer_name,
                    "status": row.status,
                    "monday_item_id": row.monday_item_id,
                    "po_date": row.po_date,
                })
    return found


# ---------------------------------------------------------------------------
# Plain-English explanation for a stuck job
# ---------------------------------------------------------------------------


class Explanation(BaseModel):
    explanation: str = Field(description="One or two plain sentences: what went wrong")
    suggested_fix: str = Field(description="One sentence: what a person should do")


def explain_exception(item: Dict[str, Any], actor="system"):
    system = (
        f"{_COMPANY} Explain an operations exception to an office worker who is not technical. "
        "Base it only on the details given. No jargon, no blame, no invented facts."
    )
    prompt = json.dumps({k: item.get(k) for k in (
        "exception_type", "severity", "title", "description", "reference_id", "vendor_or_customer", "variance_amount"
    )}, default=str)
    return _run(
        "exception_explanation",
        f"Explain: {item.get('title', '')[:200]}",
        lambda p: p.structured(system, prompt, Explanation),
        input=item,
        needs_review=False,
        reference=item.get("exception_id"),
        actor=actor,
    )


# ---------------------------------------------------------------------------
# Part-name suggestion when fuzzy matching is unsure
# ---------------------------------------------------------------------------


class ItemChoice(BaseModel):
    item_id: str = Field(description="One of the candidate item ids, or blank if none fits")
    reason: str
    confidence: float = Field(ge=0, le=1)


def candidate_items(raw_text: str, limit: int = 6) -> List[Dict[str, Any]]:
    from rapidfuzz import fuzz, process

    from app.core.container import get_entity_matcher

    items = get_entity_matcher().items_cache
    corpus = {sku: f"{sku} {rec.get('description', '')}" for sku, rec in items.items()}
    hits = process.extract(raw_text, corpus, scorer=fuzz.WRatio, limit=limit)
    return [
        {"item_id": sku, "description": items[sku].get("description", ""), "score": round(score, 1)}
        for _text, score, sku in hits
    ]


def suggest_item(raw_text: str, actor="system"):
    candidates = candidate_items(raw_text)
    if not candidates:
        raise ValueError("The catalogue is empty.")
    system = (
        f"{_COMPANY} Pick the catalogue item a customer meant. Choose only from the candidates given, "
        "by item_id. If none clearly fits, return a blank item_id."
    )
    prompt = f"Customer wrote: {raw_text}\nCandidates:\n" + "\n".join(
        f"- {c['item_id']}: {c['description']}" for c in candidates
    )

    def call(p):
        choice = p.structured(system, prompt, ItemChoice)
        if choice.item_id and choice.item_id not in {c["item_id"] for c in candidates}:
            # A model that names something outside the list is not trusted.
            choice = ItemChoice(item_id="", reason=f"Model named an unknown item ({choice.item_id}).", confidence=0)
        return choice

    result, suggestion_id = _run(
        "item_match",
        lambda r: f"'{raw_text[:80]}' -> {r.item_id or 'no match'}",
        call,
        input={"raw_text": raw_text, "candidates": candidates},
        confidence=lambda r: r.confidence,
        needs_review=lambda r: bool(r.item_id),  # a person confirms before it is remembered
        reference=raw_text[:128],
        actor=actor,
    )
    return result, suggestion_id, candidates


# Words that say nothing about which item is meant. An alias made only of these
# (or too short) would match lines for unrelated items, because aliases are
# searched for in every line of every order.
_ALIAS_MIN_CHARS = 4
_COMMON_WORDS = {
    "A", "AN", "THE", "AND", "OR", "OF", "FOR", "WITH", "TO", "IN", "ON", "X", "BY",
    "EA", "EACH", "PC", "PCS", "QTY", "UNIT", "UNITS", "LOT", "SET", "NEW", "SPARE",
    "ITEM", "ITEMS", "PART", "PARTS", "ORDER", "PO", "LINE", "SIZE", "TYPE", "STD",
    "PLATE", "PLATES", "STEEL", "SHEET", "SHEETS", "BAR", "BARS", "PIPE", "HOSE",
    "WEAR", "LINER", "LINERS", "SCREEN", "SCREENS", "BEAM", "BEAMS",
}
_alias_lock = threading.Lock()


def alias_problem(raw: str) -> Optional[str]:
    """Why raw_text cannot become an alias, or None if it can."""
    if len(re.sub(r"\s", "", raw)) < _ALIAS_MIN_CHARS:
        return f"shorter than {_ALIAS_MIN_CHARS} characters"
    words = re.findall(r"[A-Z0-9]+", raw.upper())
    if not words or all(w in _COMMON_WORDS for w in words):
        return "made only of common words"
    return None


@store.on_approve("item_match")
def _remember_alias(row: Dict[str, Any]) -> None:
    """An approved match becomes an alias, so the next order matches without AI."""
    raw = (row.get("input") or {}).get("raw_text", "").strip()
    sku = (row.get("output") or {}).get("item_id", "").strip()
    if not (raw and sku):
        return
    problem = alias_problem(raw)
    if problem:
        # The approval stands; the text is just too vague to match on later.
        logger.warning(f"Not remembering alias '{raw}' -> {sku}: {problem}")
        return
    path = settings.ALIAS_DICT_PATH
    with _alias_lock:
        # Re-read inside the lock so a concurrent approval is not overwritten,
        # and replace the file atomically so a reader never sees half of it.
        aliases = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        aliases[raw.upper()] = sku
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(aliases, f, indent=2, ensure_ascii=False)
            os.replace(tmp, path)
        except BaseException:
            try:
                os.remove(tmp)
            except OSError:
                pass
            raise
    from app.core.container import get_entity_matcher

    get_entity_matcher().load_catalogs()
    logger.info(f"Remembered alias '{raw}' -> {sku}")


@store.on_approve("call_summary")
def _post_call_note(row: Dict[str, Any]) -> None:
    """An approved call summary is posted as a note on each job it names.

    A note so whoever owns the job sees the call; never an order change.
    """
    summary = CallSummary.model_validate(row.get("output") or {})
    from_number = (row.get("input") or {}).get("from_number", "")
    jobs = [o for o in find_orders(summary.po_numbers) if o.get("monday_item_id")]
    if not jobs:
        return
    from app.core.container import get_monday_client

    note = (
        f"Phone call from {summary.caller_name or from_number or 'a caller'}"
        f" ({summary.reason.replace('_', ' ')}): {summary.summary}"
        + (" Callback needed." if summary.callback_needed else "")
    )
    for order in jobs:
        ok = get_monday_client().post_pulse_update(int(order["monday_item_id"]), note)
        logger.info(f"Call note for suggestion #{row.get('id')} on monday item {order['monday_item_id']}: posted={ok}")


__all__ = [
    "AIUnavailableError", "LANGUAGES", "EmailTriage", "EmailDraft", "Translation", "CallSummary",
    "Explanation", "ItemChoice", "triage_email", "draft_email", "translate", "summarize_call",
    "find_orders", "explain_exception", "suggest_item", "candidate_items", "glossary",
]
