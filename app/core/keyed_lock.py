"""One at a time per key: the same customer PO, the same sales order.

Some actions check "has this been done?" and then do it (raise the order, ship it). Two
callers at the same instant both pass the check and both act. Holding a lock for that key
across the check and the act makes the second wait, see the first one's result, and stop.
The app runs as one process (see README, Known limits), so an in-process lock is enough.
"""

import threading
from contextlib import contextmanager
from typing import Dict, Iterator, Tuple

_guard = threading.Lock()
_locks: Dict[Tuple[str, str], threading.RLock] = {}


@contextmanager
def keyed_lock(area: str, key: str) -> Iterator[None]:
    k = (area, (key or "").strip().upper())
    with _guard:
        lock = _locks.setdefault(k, threading.RLock())
    with lock:
        yield
    # Locks are tiny and keyed by real orders, so they are kept rather than tracked for reuse.
