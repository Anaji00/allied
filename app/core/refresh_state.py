"""Refresh tokens: each carries an id (jti); the latest id of each person is kept (never the token itself). Using the PREVIOUS token again within a short grace window (ten parallel refreshes from one page
load) is allowed and answered with the same current token; presenting an older one, or the previous one after the window, is treated as a stolen token and ends all of that person's sessions.
If the table is not there yet (the migration has not run) every function does nothing and sessions behave as they did before."""

import time
import uuid
from typing import Any, Dict, Optional

import jwt
from sqlalchemy.exc import SQLAlchemyError

from app.config import settings
from app.core.database import SessionLocal
from app.core.logging import logger
from app.models.db_models import RefreshStateDB

GRACE_SECONDS = float(getattr(settings, "REFRESH_GRACE_SECONDS", 600.0))        # how long the PREVIOUS refresh token is still honoured (a second tab that has not yet seen the new one)
_revoked_cache: Dict[str, Any] = {}


def _key(email: str) -> str:
    return (email or "").strip().lower()


def new_jti() -> str:
    return uuid.uuid4().hex[:32]


def register(email: str, token: str) -> None:
    """Records a freshly issued refresh token (at sign-in) as this person's latest."""
    try:
        claims = jwt.decode(token, options={"verify_signature": False})
        _set_latest(_key(email), claims.get("jti"), float(claims.get("exp") or 0))
    except Exception as exc:  # noqa: BLE001 - signing in must never fail because of this record
        logger.info(f"Refresh token not recorded: {type(exc).__name__}")


def _set_latest(email: str, jti: Optional[str], exp: float) -> None:
    if not jti:
        return
    try:
        with SessionLocal() as db:
            row = db.get(RefreshStateDB, email)
            if row is None:
                db.add(RefreshStateDB(user_email=email, latest_jti=jti, latest_exp=exp, revoked_before=0.0, updated_at=time.time()))
            else:
                row.prev_jti, row.prev_superseded_at = row.latest_jti, time.time()
                row.latest_jti, row.latest_exp, row.updated_at = jti, exp, time.time()
            db.commit()
    except SQLAlchemyError:
        pass


def _set_latest_exp(email: str, token: str) -> None:
    """Remembers when the token just signed expires, so the same token can be signed again unchanged inside the grace window."""
    try:
        exp = float(jwt.decode(token, options={"verify_signature": False}).get("exp") or 0)
        with SessionLocal() as db:
            row = db.get(RefreshStateDB, _key(email))
            if row is not None:
                row.latest_exp = exp
                db.commit()
    except Exception:  # noqa: BLE001
        pass


def exchange(email: str, presented_jti: Optional[str]) -> Dict[str, Any]:
    """Decides what to do with a refresh token that was presented. Returns one of
    {"action": "issue", "jti": <the new id>}  a normal refresh: the caller signs a new token with this id (already recorded as the latest);
    {"action": "same", "jti": <latest id>, "exp": <its expiry>}  inside the grace window: the caller re-signs the CURRENT token unchanged;
    {"action": "theft"}  an old token was reused: every session of this person has just been ended."""
    email = _key(email)
    new = new_jti()
    now = time.time()
    try:
        for _ in range(5):
            with SessionLocal() as db:
                row = db.get(RefreshStateDB, email)
                if row is None or not row.latest_jti or not presented_jti:                    # nothing recorded yet, or a token from before ids existed: adopt it
                    if row is None:
                        db.add(RefreshStateDB(user_email=email, latest_jti=new, latest_exp=0.0, revoked_before=0.0, updated_at=now))
                        try:
                            db.commit()
                        except SQLAlchemyError:
                            db.rollback()
                            continue
                    else:
                        won = db.query(RefreshStateDB).filter(RefreshStateDB.user_email == email, RefreshStateDB.latest_jti == row.latest_jti).update(
                            {RefreshStateDB.prev_jti: row.latest_jti, RefreshStateDB.prev_superseded_at: now, RefreshStateDB.latest_jti: new, RefreshStateDB.updated_at: now}, synchronize_session=False)
                        db.commit()
                        if not won:
                            continue
                    return {"action": "issue", "jti": new}
                if presented_jti == row.latest_jti:                                           # the normal case: swap the latest atomically (two at once: one wins, the other is read again)
                    won = db.query(RefreshStateDB).filter(RefreshStateDB.user_email == email, RefreshStateDB.latest_jti == presented_jti).update(
                        {RefreshStateDB.prev_jti: presented_jti, RefreshStateDB.prev_superseded_at: now, RefreshStateDB.latest_jti: new, RefreshStateDB.updated_at: now}, synchronize_session=False)
                    db.commit()
                    if won:
                        return {"action": "issue", "jti": new}
                    continue
                if presented_jti == row.prev_jti and row.prev_superseded_at and now - row.prev_superseded_at <= GRACE_SECONDS:
                    return {"action": "same", "jti": row.latest_jti, "exp": row.latest_exp or 0.0}
                row.revoked_before = now                                                      # an older token, or the previous one too late: treated as stolen; tokens carry their issue time to the fraction of a second
                row.prev_jti = None
                db.commit()
                _revoked_cache.pop(email, None)
                return {"action": "theft"}
    except SQLAlchemyError as exc:
        logger.info(f"Refresh state unavailable ({type(exc).__name__}): refreshing without reuse detection")
    return {"action": "issue", "jti": new, "unrecorded": True}


def revoked_before(email: str) -> float:
    """Sessions (access tokens issued before this moment) are dead; 0 when none were ended. Remembered for a short while."""
    email = _key(email)
    hit = _revoked_cache.get(email)
    if hit and time.monotonic() - hit[0] < 15:
        return hit[1]
    value = 0.0
    try:
        with SessionLocal() as db:
            row = db.get(RefreshStateDB, email)
            value = float(row.revoked_before or 0.0) if row else 0.0
    except SQLAlchemyError:
        value = 0.0
    _revoked_cache[email] = (time.monotonic(), value)
    return value
