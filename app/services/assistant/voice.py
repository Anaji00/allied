"""Speak a task to the assistant: it is written out, done by the same agent, and answered in one
short sentence to be read aloud, with the cards on screen.

- Speech to text: Groq's Whisper (already set up); a typed transcript works too, so the screen's
  own dictation can use this door.
- The spoken reply is text for the phone or desktop to read aloud (VOICE_SPOKEN_REPLY=browser,
  free). The full answer and the cards stay on screen.
- Confirming by voice: when changes are waiting, "confirm" (or "yes, go ahead") carries them out and
  "cancel" drops them. The two high-impact kinds need their own words so a stray "yes" can't do
  them: sending an email needs "send", and pushing to monday needs "monday". Everything still goes
  through the same checks as a click: the person's own role, their own proposals, the read-only guard.
"""

import json
import re
from typing import Any, Dict, List, Optional

from app.config import settings
from app.core.database import SessionLocal
from app.models.db_models import AssistantActionDB
from app.services.assistant import agent

_CONFIRM = re.compile(r"^\W*(?:yes|yeah|yep|ok(?:ay)?|sure|confirm(?:ed)?|do it|go ahead|approve|send it|please do|that'?s right|"
                      r"xác nhận|đồng ý|được|vâng|确认|好的|可以|是的)\b", re.I)
_CANCEL = re.compile(r"^\W*(?:no|nope|cancel|stop|never ?mind|don'?t|hủy|huỷ|không|thôi|取消|不要|不)\b", re.I)
_PRINT = re.compile(r"\bprint\b|\bin ra\b|打印", re.I)


def voice_intent(transcript: str) -> Optional[str]:
    """confirm, cancel or print when the words are plainly that; otherwise None (a new request)."""
    text = (transcript or "").strip()
    if not text or len(text.split()) > 14:        # a long sentence is a task, not an answer
        return None
    if _CANCEL.search(text):
        return "cancel"
    if _PRINT.search(text) and len(text.split()) <= 6:
        return "print"
    return "confirm" if _CONFIRM.search(text) else None


def _plain(text: str) -> str:
    text = re.sub(r"[`*_#>|]+", "", text or "")
    text = re.sub(r"^\s*[-•]\s*", "", text, flags=re.M)
    return re.sub(r"\s+", " ", text).strip()


def _short(text: str, words: int) -> str:
    out = _plain(text).split(" ")
    if len(out) <= words:
        return " ".join(out)
    cut = " ".join(out[:words])
    end = max(cut.rfind(". "), cut.rfind("? "), cut.rfind("! "))
    return (cut[:end + 1] if end > words * 3 else cut.rstrip(",;:") + "...").strip()


def spoken(reply: str, actions: List[Dict[str, Any]]) -> str:
    """One short thing to say: the answer's opening, or what is waiting for a yes."""
    limit = settings.VOICE_REPLY_MAX_WORDS
    if actions:
        n = len(actions)
        first = _short(actions[0]["summary"].split("\n", 1)[0], 18)
        more = f", and {n - 1} more" if n > 1 else ""
        return f"I've lined up {n} change{'s' if n != 1 else ''}: {first}{more}. Say confirm to go ahead, or cancel."
    # Tables and rules of dashes don't read aloud: say the prose, and point at the screen.
    lines = (reply or "").splitlines()
    prose = [ln for ln in lines if "|" not in ln and not re.fullmatch(r"[\s\-–—=_*:]{3,}", ln)]
    dropped = len(prose) < len(lines) and any("|" in ln or re.fullmatch(r"[\s\-–—=_*:]{3,}", ln) for ln in lines)
    prose = [re.sub(r"^\s*[-•*]\s+", "", ln) for ln in prose]      # bullets read as "dash" aloud
    text = _short(" ".join(prose), limit - (6 if dropped else 0))
    if dropped:
        text = (text + " " if text else "") + "The details are on screen."
    return text


def _need_phrase(tool: str, transcript: str) -> Optional[str]:
    """For the high-impact kinds, the word the user must have said; None when nothing extra is needed."""
    low = (transcript or "").lower()
    if tool == "post_monday_update" and "monday" not in low:
        return 'say "confirm push to monday"'
    if tool == "send_email" and not re.search(r"\bsend\b|gửi|发送|發送", low):
        return 'say "send it"'
    return None


def _decide_by_voice(ids: List[str], decision: str, transcript: str, user: Dict[str, Any]) -> Dict[str, Any]:
    done, held = [], []
    for action_id in dict.fromkeys(ids):
        try:
            with SessionLocal() as db:
                row = db.get(AssistantActionDB, action_id)
                tool = row.tool if row is not None else ""
            if decision == "confirm":
                need = _need_phrase(tool, transcript)
                if need:
                    held.append({"id": action_id, "tool": tool, "needs": need})
                    continue
            done.append(agent.decide(action_id, decision == "confirm", user))
        except (LookupError, ValueError, PermissionError) as exc:
            done.append({"id": action_id, "status": "SKIPPED", "error": str(exc)})
    return {"done": done, "held": held}


def _summary_of(results: Dict[str, Any], decision: str) -> str:
    ok = [r for r in results["done"] if r.get("status") in ("DONE", "CANCELLED")]
    bad = [r for r in results["done"] if r.get("status") not in ("DONE", "CANCELLED")]
    parts = []
    if ok:
        if decision == "cancel":
            parts.append(f"Cancelled {len(ok)}. Nothing was changed.")
        else:
            said = "; ".join(str((r.get("result") or {}).get("done") or r["status"]).rstrip(".") for r in ok[:3])
            parts.append(f"Done: {said}.")
    for r in bad:
        parts.append(f"Couldn't: {r.get('error') or (r.get('result') or {}).get('error') or r.get('status')}.")
    for h in results["held"]:
        parts.append(f"I held one back: {h['needs']}.")
    return " ".join(parts) or "Nothing was waiting."


def command(user: Dict[str, Any], audio: Optional[bytes] = None, filename: str = "", transcript: str = "",
            messages: Optional[List[Dict[str, Any]]] = None, confirm_ids: Optional[List[str]] = None) -> Dict[str, Any]:
    """Runs one spoken (or typed-by-dictation) turn."""
    from app.services.ai import speech
    from app.services.ai.providers import AIUnavailableError
    from app.services.coordination import eng_notes

    if not settings.VOICE_AGENT_ENABLED:
        raise AIUnavailableError("The voice agent is switched off (VOICE_AGENT_ENABLED).")
    language = None
    if audio:
        suffix = eng_notes.check_audio(audio, filename)
        heard = speech.transcribe(audio, suffix=suffix)
        transcript = (heard.get("text") or "").strip()
        language = heard.get("language")
    transcript = (transcript or "").strip()
    if not transcript:
        raise ValueError("I couldn't hear anything. Try again, or type it.")

    intent = voice_intent(transcript) if confirm_ids else None
    base: Dict[str, Any] = {"transcript": transcript, "voice_intent": intent, "language": language or "en"}
    if intent in ("confirm", "cancel"):
        results = _decide_by_voice(confirm_ids or [], intent, transcript, user)
        text = _summary_of(results, intent)
        return {**base, "reply_text": text, "spoken_text": _short(text, settings.VOICE_REPLY_MAX_WORDS),
                "tool_actions_executed": results["done"], "held": results["held"], "action_cards": [], "plan": None,
                "audio_base64": None, "spoken": _spoken_block(text, base["language"])}
    if intent == "print":
        text = "Printing it now."
        return {**base, "reply_text": text, "spoken_text": text, "tool_actions_executed": [], "action_cards": [], "plan": None,
                "audio_base64": None, "spoken": _spoken_block(text, base["language"])}

    convo = [m for m in (messages or []) if m.get("role") in ("user", "assistant")][-8:] + [{"role": "user", "content": transcript}]
    result = agent.chat(convo, user)
    say = spoken(result["reply"], result.get("actions") or [])
    return {**base, "reply_text": result["reply"], "spoken_text": say,
            "tool_actions_executed": [s for s in result.get("steps") or [] if s.get("ok") and not s.get("proposed")],
            "action_cards": result.get("cards") or [], "plan": result.get("plan"), "actions": result.get("actions") or [],
            "audio_base64": None, "spoken": _spoken_block(say, base["language"]), "model": result.get("model")}


def _spoken_block(text: str, language: str) -> Dict[str, Any]:
    """What the screen should say aloud, and in what language. Empty when the reply is text only."""
    if settings.VOICE_SPOKEN_REPLY != "browser":
        return {"mode": "off"}
    return {"mode": "browser", "text": text, "lang": {"vi": "vi-VN", "zh": "zh-CN", "en": "en-US"}.get((language or "en")[:2], "en-US")}
