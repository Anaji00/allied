"""Sign-in throttle and password rules.

Wrong passwords are counted per account and per client address in memory. After
too many in a short time the account (or address) is locked for a while, and a
locked sign-in is refused even with the right password. Counts reset on a
correct sign-in, on restart, and by themselves as the window passes. Unknown
emails are counted the same way, so the answer never tells accounts apart.
"""
import hashlib
import threading
import time
from typing import Dict, List, Optional

from app.core.logging import logger

PAIR_MAX_FAILURES = 5          # one account from one address
ACCOUNT_MAX_FAILURES = 20      # one account from anywhere; a known-good address is let through
WINDOW_SECONDS = 600.0
LOCK_SECONDS = 900.0
MAX_PASSWORD_LENGTH = 128
_TABLE_LIMIT = 5000
_GOOD_PER_ACCOUNT = 5

_lock = threading.Lock()
_failures: Dict[str, List[float]] = {}
_locked_until: Dict[str, float] = {}
_good_addresses: Dict[str, Dict[str, float]] = {}      # account -> addresses that signed in with the right password
_in_flight: Dict[str, List[float]] = {}                # attempts that passed the lock check and are still checking the password (hashing takes time)
IN_FLIGHT_SECONDS = 30.0


def _acct(email: str) -> str:
    return (email or "").strip().lower()[:200]


def _pair(email: str, address: str) -> str:
    return f"pair:{_acct(email)}|{(address or '')[:100]}"


def _account_key(email: str) -> str:
    return f"acct:{_acct(email)}"


def seconds_locked(email: str, address: str = "") -> int:
    """Seconds left on a lock for this account from this address, 0 when free to try.

    The account-wide lock does not stop an address that has signed in to the account
    before, so a stranger cannot keep the real owner out by guessing wrongly elsewhere.
    """
    now = time.monotonic()
    with _lock:
        left = 0.0
        pair_until = _locked_until.get(_pair(email, address), 0.0)
        acct_until = _locked_until.get(_account_key(email), 0.0)
        if pair_until > now:
            left = pair_until - now
        if acct_until > now and (address or "") not in _good_addresses.get(_acct(email), {}):
            left = max(left, acct_until - now)
        return int(left) + 1 if left > 0 else 0


def reserve(email: str, address: str = "") -> int:
    """Called when a sign-in attempt starts. Returns seconds to wait when it must be refused: locked, or so many attempts are already being checked at once that, if they all fail, the lock would be
    reached (twenty at the same instant would otherwise all pass the lock check before the first wrong password is counted). Otherwise the attempt holds a place until it is counted."""
    wait = seconds_locked(email, address)
    if wait:
        return wait
    now = time.monotonic()
    key = _pair(email, address)
    with _lock:
        open_ = [t for t in _in_flight.get(key, []) if now - t < IN_FLIGHT_SECONDS]
        failed = len([t for t in _failures.get(key, []) if now - t < WINDOW_SECONDS])
        if failed + len(open_) >= PAIR_MAX_FAILURES:
            _in_flight[key] = open_
            return 2
        open_.append(now)
        _in_flight[key] = open_
    return 0


def _release(key: str) -> None:
    held = _in_flight.get(key)
    if held:
        held.pop(0)
        if not held:
            _in_flight.pop(key, None)


def record_failure(email: str, address: str = "") -> None:
    now = time.monotonic()
    with _lock:
        _release(_pair(email, address))
        for key, limit in ((_pair(email, address), PAIR_MAX_FAILURES), (_account_key(email), ACCOUNT_MAX_FAILURES)):
            recent = [t for t in _failures.get(key, []) if now - t < WINDOW_SECONDS]
            recent.append(now)
            _failures[key] = recent
            if len(recent) >= limit:
                _locked_until[key] = now + LOCK_SECONDS
                _failures.pop(key, None)
                logger.warning("Sign-in locked for %s after %d wrong passwords (%s)", _acct(email), limit, key.split(":", 1)[0])
        _trim(now)


def _trim(now: float) -> None:
    keep = _TABLE_LIMIT * 4 // 5
    if len(_failures) > 100:
        for key in [k for k, v in _failures.items() if not v or now - v[-1] >= WINDOW_SECONDS]:
            _failures.pop(key, None)
        if len(_failures) > _TABLE_LIMIT:                           # still too many: drop the oldest
            for key in sorted(_failures, key=lambda k: _failures[k][-1])[: len(_failures) - keep]:
                _failures.pop(key, None)
    if len(_locked_until) > _TABLE_LIMIT:
        for key in [k for k, v in _locked_until.items() if v <= now]:
            _locked_until.pop(key, None)
        if len(_locked_until) > _TABLE_LIMIT:
            for key in sorted(_locked_until, key=_locked_until.get)[: len(_locked_until) - keep]:
                _locked_until.pop(key, None)
    if len(_good_addresses) > _TABLE_LIMIT:          # only a correct password adds here, so a stranger cannot flood it
        for key in list(_good_addresses)[: len(_good_addresses) - keep]:
            _good_addresses.pop(key, None)


def record_success(email: str, address: str = "") -> None:
    now = time.monotonic()
    with _lock:
        _release(_pair(email, address))
        _failures.pop(_account_key(email), None)
        _failures.pop(_pair(email, address), None)
        good = _good_addresses.setdefault(_acct(email), {})
        good[address or ""] = now
        while len(good) > _GOOD_PER_ACCOUNT:
            good.pop(min(good, key=good.get))
        _trim(now)


def unlock(email: str) -> None:
    """Clears every count and lock for an account (an admin reset its password)."""
    prefix = f"pair:{_acct(email)}|"
    with _lock:
        for table in (_failures, _locked_until):
            for key in [k for k in table if k == _account_key(email) or k.startswith(prefix)]:
                table.pop(key, None)


def reset() -> None:
    with _lock:
        _failures.clear()
        _locked_until.clear()
        _good_addresses.clear()
        _in_flight.clear()


# ---------------------------------------------------------------- password rules

_COMMON = {
    "password", "password1", "password12", "password123", "passw0rd", "12345678", "123456789", "1234567890",
    "qwertyui", "qwerty123", "qwertyuiop", "11111111", "00000000", "abcd1234", "abc12345", "iloveyou",
    "letmein1", "welcome1", "welcome123", "admin123", "administrator", "changeme", "changeme1",
}


def password_problem(new_password: str, current_password: Optional[str] = None) -> Optional[str]:
    """A plain sentence saying why this password is refused, or None when it is fine."""
    pw = new_password or ""
    if not pw.strip():
        return "The password cannot be only spaces."
    if pw.strip().lower() in _COMMON:
        return "That password is too common. Choose something less guessable."
    if len(set(pw)) < 3:
        return "The password needs more variety than that."
    if current_password is not None and pw == current_password:
        return "The new password must be different from the current one."
    return None


def password_stamp(hashed_password: str) -> str:
    """A short fingerprint of the stored hash. It changes whenever the password does,
    so tokens carrying the old one stop working without a schema change."""
    return hashlib.sha256((hashed_password or "").encode("utf-8")).hexdigest()[:12]
