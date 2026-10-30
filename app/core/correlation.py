"""Per-request correlation id, shared via a context variable.

A correlation id is assigned by the HTTP middleware and needs to reach audit
writes that happen several layers down in the service code. Threading it
through every function signature would touch most of the codebase, so it is
published here instead and read by `audit_event` when the caller does not pass
one explicitly.

contextvars are task-local, so concurrent requests cannot see each other's id,
and the value is restored when the request finishes.
"""

from contextvars import ContextVar, Token
from typing import Optional

_correlation_id: ContextVar[Optional[str]] = ContextVar("allied_correlation_id", default=None)


def set_correlation_id(value: str) -> Token:
    """Sets the id for the current task. Returns a token for `reset_correlation_id`."""
    return _correlation_id.set(value)


def reset_correlation_id(token: Token) -> None:
    """Restores the previous id once a request completes."""
    _correlation_id.reset(token)


def get_correlation_id() -> str:
    """Returns the current request's correlation id, or "" outside a request."""
    return _correlation_id.get() or ""
