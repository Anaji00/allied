"""A burst of simultaneous wrong guesses cannot all pass the lock check before the first one is counted."""
from app.core import login_guard


def test_attempts_in_flight_count_toward_the_lock():
    login_guard.reset()
    email, addr = "burst@alliedweldingsystems.com", "203.0.113.9"
    first = [login_guard.reserve(email, addr) for _ in range(20)]
    assert first[:login_guard.PAIR_MAX_FAILURES] == [0] * login_guard.PAIR_MAX_FAILURES and all(w > 0 for w in first[login_guard.PAIR_MAX_FAILURES:])
    for _ in range(login_guard.PAIR_MAX_FAILURES):
        login_guard.record_failure(email, addr)                       # the five that were checking all fail: now it is a real lock
    assert login_guard.seconds_locked(email, addr) > 0


def test_a_correct_sign_in_gives_its_place_back_and_a_stranger_is_not_affected():
    login_guard.reset()
    email, addr = "ok@alliedweldingsystems.com", "203.0.113.10"
    for _ in range(12):
        assert login_guard.reserve(email, addr) == 0
        login_guard.record_success(email, addr)
    assert login_guard.reserve(email, "198.51.100.1") == 0
    login_guard.reset()
