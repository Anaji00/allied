"""The record of what AI suggested and what people did with it."""

from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from app.core.audit import audit_log
from app.core.database import SessionLocal
from app.core.logging import logger
from app.models.db_models import AiSuggestionDB

# Run when a suggestion of that feature is approved, with the stored row's dict.
# Lets a feature turn an approval into its effect (e.g. remember an item match).
_on_approve: Dict[str, Callable[[Dict[str, Any]], None]] = {}


def on_approve(feature: str):
    def register(fn: Callable[[Dict[str, Any]], None]):
        _on_approve[feature] = fn
        return fn

    return register


def record(
    feature: str,
    title: str,
    *,
    provider: str,
    model: str,
    input: Any,
    output: Any,
    confidence: Optional[float] = None,
    needs_review: bool = True,
    reference: Optional[str] = None,
    duration_ms: Optional[int] = None,
    actor: str = "system",
) -> int:
    with SessionLocal() as db:
        row = AiSuggestionDB(
            feature=feature,
            status="SUGGESTED" if needs_review else "INFO",
            title=title[:256],
            reference=(reference or None) and str(reference)[:128],
            provider=provider,
            model=model or None,
            confidence=confidence,
            needs_review=needs_review,
            input=input,
            output=output,
            duration_ms=duration_ms,
            created_by=actor,
        )
        db.add(row)
        db.commit()
        return row.id


def _as_dict(r: AiSuggestionDB) -> Dict[str, Any]:
    return {
        "id": r.id,
        "feature": r.feature,
        "status": r.status,
        "title": r.title,
        "reference": r.reference,
        "provider": r.provider,
        "model": r.model,
        "confidence": float(r.confidence) if r.confidence is not None else None,
        "needs_review": r.needs_review,
        "input": r.input,
        "output": r.output,
        "duration_ms": r.duration_ms,
        "created_at": r.created_at.isoformat() if r.created_at else None,
        "created_by": r.created_by,
        "reviewed_by": r.reviewed_by,
        "reviewed_at": r.reviewed_at.isoformat() if r.reviewed_at else None,
        "review_note": r.review_note,
    }


def get(suggestion_id: int) -> Optional[Dict[str, Any]]:
    with SessionLocal() as db:
        row = db.get(AiSuggestionDB, suggestion_id)
        return _as_dict(row) if row else None


def list_recent(feature: Optional[str] = None, status: Optional[str] = None, limit: int = 50) -> List[Dict[str, Any]]:
    with SessionLocal() as db:
        q = db.query(AiSuggestionDB).order_by(AiSuggestionDB.id.desc())
        if feature:
            q = q.filter(AiSuggestionDB.feature == feature)
        if status:
            q = q.filter(AiSuggestionDB.status == status.upper())
        return [_as_dict(r) for r in q.limit(limit).all()]


def counts() -> Dict[str, Any]:
    from sqlalchemy import func

    with SessionLocal() as db:
        rows = (
            db.query(AiSuggestionDB.feature, AiSuggestionDB.status, func.count())
            .group_by(AiSuggestionDB.feature, AiSuggestionDB.status)
            .all()
        )
    by_feature: Dict[str, Dict[str, int]] = {}
    for feature, status, n in rows:
        by_feature.setdefault(feature, {})[status] = n
    waiting = sum(v.get("SUGGESTED", 0) for v in by_feature.values())
    return {"waiting_for_review": waiting, "by_feature": by_feature}


def review(suggestion_id: int, approve: bool, actor: Dict[str, Any], note: str = "") -> Optional[Dict[str, Any]]:
    """Approves or rejects a SUGGESTED row. Returns the row, or None if not reviewable."""
    with SessionLocal() as db:
        # One conditional UPDATE, so of two reviews racing on the same row only
        # one changes it, and only that one runs the approval follow-up.
        changed = (
            db.query(AiSuggestionDB)
            .filter(AiSuggestionDB.id == suggestion_id, AiSuggestionDB.status == "SUGGESTED")
            .update(
                {
                    AiSuggestionDB.status: "APPROVED" if approve else "REJECTED",
                    AiSuggestionDB.reviewed_by: actor.get("email", "unknown"),
                    AiSuggestionDB.reviewed_at: datetime.now(timezone.utc),
                    AiSuggestionDB.review_note: note or None,
                },
                synchronize_session=False,
            )
        )
        db.commit()
        if not changed:
            return None
        result = _as_dict(db.get(AiSuggestionDB, suggestion_id))

    audit_log(
        action="AI_APPROVED" if approve else "AI_REJECTED",
        resource_type=f"AI_{result['feature'].upper()}",
        resource_id=str(suggestion_id),
        actor_email=actor.get("email", "unknown"),
        actor_role=actor.get("role", "UNKNOWN"),
        detail=f"{result['title']}" + (f" | {note}" if note else ""),
    )
    if approve and result["feature"] in _on_approve:
        try:
            _on_approve[result["feature"]](result)
        except Exception as exc:  # noqa: BLE001 - the approval itself stands
            logger.error(f"Follow-up for approved AI suggestion #{suggestion_id} failed: {exc}")
    return result
