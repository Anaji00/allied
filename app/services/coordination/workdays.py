"""Business-day and business-hour arithmetic in BUSINESS_TZ.

Weekends and BUSINESS_HOLIDAYS are skipped; a day runs from
BUSINESS_DAY_START to BUSINESS_DAY_END. Times are returned in UTC.
"""

from datetime import date, datetime, time, timedelta, timezone
from typing import Optional, Set
from zoneinfo import ZoneInfo

from app.config import settings


def _tz() -> ZoneInfo:
    return ZoneInfo(settings.BUSINESS_TZ)


def holidays() -> Set[date]:
    out = set()
    for part in (settings.BUSINESS_HOLIDAYS or "").split(","):
        try:
            out.add(date.fromisoformat(part.strip()))
        except ValueError:
            continue
    return out


def is_workday(d: date) -> bool:
    return d.weekday() < 5 and d not in holidays()


def _local(at: datetime) -> datetime:
    if at.tzinfo is None:
        at = at.replace(tzinfo=timezone.utc)  # stored times are UTC (SQLite drops the zone)
    return at.astimezone(_tz())


def _hours_per_day() -> int:
    return max(1, settings.BUSINESS_DAY_END - settings.BUSINESS_DAY_START)


def _clamp(at: datetime) -> datetime:
    """Moves a local time forward to the next moment inside working hours."""
    start, end = time(settings.BUSINESS_DAY_START), time(settings.BUSINESS_DAY_END)
    while True:
        if not is_workday(at.date()) or at.time() >= end:
            nxt = at.date() + timedelta(days=1)
            at = datetime.combine(nxt, start, at.tzinfo)
            continue
        if at.time() < start:
            at = datetime.combine(at.date(), start, at.tzinfo)
        return at


def add_hours(at: datetime, hours: float) -> datetime:
    """`hours` working hours after `at`."""
    cur = _clamp(_local(at))
    left = timedelta(hours=hours)
    while True:
        end = datetime.combine(cur.date(), time(settings.BUSINESS_DAY_END), cur.tzinfo)
        room = end - cur
        if left <= room:
            return (cur + left).astimezone(timezone.utc)
        left -= room
        cur = _clamp(end)


def add_days(at: datetime, days: int) -> datetime:
    """The end of the working day `days` working days after `at` (a day received counts from tomorrow)."""
    local = _local(at)
    d, n = local.date(), 0
    while n < days:
        d += timedelta(days=1)
        if is_workday(d):
            n += 1
    if days == 0 and not is_workday(d):
        while not is_workday(d):
            d += timedelta(days=1)
    return datetime.combine(d, time(settings.BUSINESS_DAY_END), _tz()).astimezone(timezone.utc)


def hours_between(a: datetime, b: datetime) -> float:
    """Working hours from a to b (negative when b is earlier)."""
    a, b = as_utc(a), as_utc(b)
    if b < a:
        return -hours_between(b, a)
    cur, stop = _clamp(_local(a)), _local(b)
    total = 0.0
    while cur < stop:
        end = datetime.combine(cur.date(), time(settings.BUSINESS_DAY_END), cur.tzinfo)
        total += (min(end, stop) - cur).total_seconds() / 3600
        cur = _clamp(end)
    return total


def days_between(a: datetime, b: datetime) -> float:
    return hours_between(a, b) / _hours_per_day()


def workdays_after(d: date, n: int) -> date:
    while n > 0:
        d += timedelta(days=1)
        if is_workday(d):
            n -= 1
    return d


def now() -> datetime:
    return datetime.now(timezone.utc)


def as_utc(at: Optional[datetime]) -> Optional[datetime]:
    if at is None:
        return None
    return at.replace(tzinfo=timezone.utc) if at.tzinfo is None else at.astimezone(timezone.utc)
