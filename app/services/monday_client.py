"""Monday.com v2 GraphQL API Client for board synchronization and notifications.

Behaviour that matters in production:

- Rate limits. monday enforces a per-minute complexity budget and request
  limits. A limit answers HTTP 429 (with Retry-After) or a GraphQL error whose
  extensions carry a retry hint. Both are retried with backoff, bounded, so a
  burst of webhooks slows down instead of failing.
- Versioning. The API-Version header is pinned in configuration; the old
  hardcoded "2024-01" had been retired by monday.
- Simulation. Board writes are skipped unless MONDAY_SIMULATION_MODE is off and
  a key is set. Skipped calls are kept in a short log so the console can show
  what would have been posted.
"""

import json
import re
import threading
import time
from collections import deque
from datetime import datetime, timezone
from typing import Any, Deque, Dict, List, Optional

import httpx

from app.config import settings
from app.core.exceptions import MondayAPIError
from app.core.logging import logger

# GraphQL error codes monday returns for limits that clear on their own.
_MUTATION = re.compile(r"(?:^|[\s{}])mutation\b")


def _code_only(query: str) -> str:
    """The document with strings and comments blanked, in one pass, so neither can hide the other."""
    out, i, n = [], 0, len(query)
    while i < n:
        if query.startswith('"""', i):
            j = query.find('"""', i + 3)
            i = n if j < 0 else j + 3
            out.append(" ")
        elif query[i] == '"':
            i += 1
            while i < n and query[i] != '"':
                i += 2 if query[i] == "\\" else 1
            i += 1
            out.append(" ")
        elif query[i] == "#":
            j = query.find("\n", i)
            i = n if j < 0 else j
        else:
            out.append(query[i])
            i += 1
    return "".join(out)


def is_mutation(query: str) -> bool:
    """True when a GraphQL document changes anything (mutation, or anything not plainly a query).

    Strings and comments are removed first, so a search term like "mutation"
    is not mistaken for one, and an unrecognisable document counts as a write.
    """
    text = _code_only(query or "").strip()
    if _MUTATION.search(text):
        return True
    return not (text.startswith("{") or text.startswith("query"))


class MondayReadOnlyError(MondayAPIError):
    """A write was refused because MONDAY_READ_ONLY is on."""


_RETRYABLE_ERROR_CODES = {
    "COMPLEXITY_BUDGET_EXHAUSTED",
    "ComplexityException",
    "RATE_LIMIT_EXCEEDED",
    "Rate Limit Exceeded",
    "maxConcurrencyExceeded",
    "IP_RATE_LIMIT_EXCEEDED",
    "DAILY_LIMIT_EXCEEDED",
}
_MAX_ATTEMPTS = 4
_MAX_BACKOFF_SECONDS = 30.0
_TIMEOUT = httpx.Timeout(15.0, connect=5.0)


class MondayRetryableError(MondayAPIError):
    """A limit or transient failure; the same call may succeed later."""

    def __init__(self, message: str, retry_after: float = 0.0, details: Optional[Dict[str, Any]] = None):
        super().__init__(message, details)
        self.retry_after = retry_after


class MondayClient:
    """Synchronous HTTP client for Monday.com GraphQL API operations."""

    def __init__(self, api_key: Optional[str] = None, api_url: Optional[str] = None):
        self.api_key = api_key or settings.MONDAY_API_KEY
        self.api_url = api_url or settings.MONDAY_API_URL
        self.headers = {
            # monday takes the raw token, not "Bearer <token>".
            "Authorization": self.api_key,
            "Content-Type": "application/json",
            "API-Version": settings.MONDAY_API_VERSION,
        }
        self._client: Optional[httpx.Client] = None
        self._client_lock = threading.Lock()
        self.simulated_calls: Deque[Dict[str, Any]] = deque(maxlen=100)

    @property
    def live(self) -> bool:
        # Read at call time so tests and config reloads take effect.
        return bool(self.api_key) and not settings.MONDAY_SIMULATION_MODE

    @property
    def writable(self) -> bool:
        """Live and allowed to change data. Reads need only `live`."""
        return self.live and not settings.MONDAY_READ_ONLY

    def _http(self) -> httpx.Client:
        """One pooled connection per process instead of a TLS handshake per call."""
        with self._client_lock:
            if self._client is None:
                self._client = httpx.Client(timeout=_TIMEOUT)
            return self._client

    # ------------------------------------------------------------------
    # Transport
    # ------------------------------------------------------------------

    def _post_once(self, payload: Dict[str, Any], idempotency_key: Optional[str] = None) -> Dict[str, Any]:
        # Second guard, at the only place a request leaves: nothing that writes
        # is sent while read-only is on, whatever the caller did.
        if settings.MONDAY_READ_ONLY and is_mutation(payload.get("query") or ""):
            raise MondayReadOnlyError("monday is read-only (MONDAY_READ_ONLY); the change was not sent.")
        headers = dict(self.headers)
        if idempotency_key:
            # monday caches the response for 30 minutes under this key, so a
            # retried mutation (ours or the inbox's) cannot post twice.
            headers["Idempotency-Key"] = idempotency_key[:255]
        try:
            response = self._http().post(self.api_url, json=payload, headers=headers)
        except httpx.TimeoutException as exc:
            raise MondayRetryableError(f"monday request timed out: {exc}", retry_after=2.0)
        except httpx.TransportError as exc:
            raise MondayRetryableError(f"monday unreachable: {exc}", retry_after=2.0)

        # 409: the same idempotency key is still in flight.
        if response.status_code in (409, 429) or response.status_code >= 500:
            retry_after = _parse_retry_after(response.headers.get("Retry-After"))
            raise MondayRetryableError(
                f"monday returned HTTP {response.status_code}", retry_after=retry_after
            )
        if response.status_code in (401, 403):
            raise MondayAPIError(
                f"monday rejected the API token (HTTP {response.status_code}). "
                f"Check MONDAY_API_KEY and its board permissions."
            )
        if response.status_code >= 400:
            raise MondayAPIError(f"monday returned HTTP {response.status_code}: {response.text[:300]}")

        try:
            result = response.json()
        except ValueError:
            raise MondayAPIError(f"monday returned a non-JSON body: {response.text[:200]}")

        errors = result.get("errors") or ([{"message": result["error_message"], "extensions": {"code": result.get("error_code")}}] if result.get("error_message") else None)
        if errors:
            codes = {str((e.get("extensions") or {}).get("code") or e.get("error_code") or "") for e in errors}
            if codes & _RETRYABLE_ERROR_CODES:
                hints = [
                    (e.get("extensions") or {}).get("retry_in_seconds")
                    for e in errors
                ]
                retry_after = max((_parse_retry_after(str(h)) for h in hints if h), default=5.0)
                raise MondayRetryableError(f"monday limit hit: {sorted(codes)}", retry_after=retry_after)
            raise MondayAPIError(f"Monday API Error: {errors}", details={"errors": errors})
        return result

    def execute_query(
        self,
        query: str,
        variables: Optional[Dict[str, Any]] = None,
        idempotency_key: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Executes a GraphQL query/mutation, retrying limits and transient failures."""
        if self.live and settings.MONDAY_READ_ONLY and is_mutation(query):
            # Treated like simulation, so callers carry on: recorded, not sent.
            self.simulated_calls.append(
                {
                    "at": datetime.now(timezone.utc).isoformat(),
                    "query": " ".join(query.split())[:160],
                    "variables": variables or {},
                    "blocked": "read-only",
                }
            )
            logger.warning("[READ-ONLY] monday change blocked, not sent: " + " ".join(query.split())[:120])
            return {"data": {"simulation": True, "read_only": True}}
        if not self.live:
            self.simulated_calls.append(
                {
                    "at": datetime.now(timezone.utc).isoformat(),
                    "query": " ".join(query.split())[:160],
                    "variables": variables or {},
                }
            )
            logger.info("[SIMULATION] Monday GraphQL call recorded, not sent.")
            return {"data": {"simulation": True}}

        payload: Dict[str, Any] = {"query": query}
        if variables:
            payload["variables"] = variables

        last_exc: Optional[Exception] = None
        for attempt in range(1, _MAX_ATTEMPTS + 1):
            try:
                return self._post_once(payload, idempotency_key)
            except MondayRetryableError as exc:
                last_exc = exc
                if attempt == _MAX_ATTEMPTS:
                    break
                delay = min(_MAX_BACKOFF_SECONDS, max(exc.retry_after, 2 ** attempt))
                logger.warning(f"{exc.message}; retrying in {delay:.0f}s (attempt {attempt}/{_MAX_ATTEMPTS})")
                time.sleep(delay)
        raise MondayAPIError(f"Failed to communicate with Monday.com: {last_exc}")

    # ------------------------------------------------------------------
    # Operations
    # ------------------------------------------------------------------

    def post_pulse_update(
        self, pulse_id: Optional[int], message: str, idempotency_key: Optional[str] = None
    ) -> bool:
        """Posts an update/comment to a pulse thread with status & SO/PO details."""
        if not pulse_id:
            # Several callers have no board item (email intake, repricing);
            # posting to item 0 or a made-up id must never reach the API.
            logger.debug("No monday item for this update; skipped.")
            return False
        query = """
        mutation ($itemId: ID!, $body: String!) {
            create_update (item_id: $itemId, body: $body) {
                id
            }
        }
        """
        try:
            self.execute_query(query, {"itemId": str(pulse_id), "body": message}, idempotency_key)
            logger.info(f"Posted update to Monday pulse #{pulse_id}")
            return True
        except Exception as exc:
            logger.warning(f"Could not post update to pulse #{pulse_id}: {exc}")
            return False

    def update_column_values(
        self,
        board_id: int,
        item_id: int,
        column_values: Dict[str, Any],
        idempotency_key: Optional[str] = None,
    ) -> bool:
        """Updates custom column values on a Monday item (e.g. Sage SO#, Sync Status)."""
        values = {k: v for k, v in column_values.items() if k}
        if not (board_id and item_id and values):
            return False
        query = """
        mutation ($boardId: ID!, $itemId: ID!, $columnValues: JSON!) {
            change_multiple_column_values (board_id: $boardId, item_id: $itemId, column_values: $columnValues, create_labels_if_missing: true) {
                id
            }
        }
        """
        try:
            self.execute_query(
                query,
                {
                    "boardId": str(board_id),
                    "itemId": str(item_id),
                    # The JSON scalar is passed as a JSON-encoded string.
                    "columnValues": json.dumps(values),
                },
                idempotency_key,
            )
            return True
        except Exception as exc:
            logger.warning(f"Could not update Monday columns on item #{item_id}: {exc}")
            return False

    def get_item(self, item_id: int) -> Optional[Dict[str, Any]]:
        """Fetches one item with its column values, keyed by column id.

        Webhook payloads only describe the column that changed, so the rest of
        the order (customer, PO number, lines) has to be read from the item.
        Returns None in simulation or when the item no longer exists.
        """
        if not self.live or not item_id:
            return None
        query = """
        query ($ids: [ID!]) {
            items (ids: $ids) {
                id
                name
                board { id }
                group { id title }
                column_values { id type text value }
            }
        }
        """
        result = self.execute_query(query, {"ids": [str(item_id)]})
        items = (result.get("data") or {}).get("items") or []
        if not items:
            return None
        item = items[0]
        item["columns"] = {cv["id"]: cv for cv in item.get("column_values") or []}
        return item

    def fetch_board_items(self, board_id: int, limit: int = 50) -> Dict[str, Any]:
        """Fetches items and their column values from a board, following the cursor.

        items_page returns at most `limit` items plus a cursor; the first page
        alone silently dropped everything past it.
        """
        first = """
        query ($boardId: [ID!], $limit: Int!) {
            boards(ids: $boardId) {
                name
                items_page(limit: $limit) {
                    cursor
                    items { id name column_values { id text type } }
                }
            }
        }
        """
        more = """
        query ($cursor: String!, $limit: Int!) {
            next_items_page(cursor: $cursor, limit: $limit) {
                cursor
                items { id name column_values { id text type } }
            }
        }
        """
        try:
            result = self.execute_query(first, {"boardId": [str(board_id)], "limit": limit})
            boards = (result.get("data") or {}).get("boards") or []
            if not boards:
                return result
            page = boards[0].get("items_page") or {}
            items: List[Dict[str, Any]] = list(page.get("items") or [])
            cursor = page.get("cursor")
            # Bounded so a huge board cannot pin a worker indefinitely.
            for _ in range(200):
                if not cursor:
                    break
                nxt = (self.execute_query(more, {"cursor": cursor, "limit": limit}).get("data") or {}).get(
                    "next_items_page"
                ) or {}
                items.extend(nxt.get("items") or [])
                cursor = nxt.get("cursor")
            boards[0]["items_page"] = {"cursor": None, "items": items}
            return result
        except Exception as exc:
            logger.error(f"Failed to fetch Monday board items for board #{board_id}: {exc}")
            raise MondayAPIError(f"Could not fetch Monday data: {exc}")


def _projects_query(subitems: bool) -> tuple:
    # Formula columns have no `text`; their result comes as display_value (API 2025-04 and later).
    values = "column_values { id text value column { title } ... on FormulaValue { display_value } }"
    sub = f"subitems {{ id name board {{ id }} {values} }}" if subitems else ""
    fields = f"id name created_at group {{ id title }} {values} {sub}"
    return (
        "query ($boardId: [ID!], $limit: Int!) { boards(ids: $boardId) { id name "
        f"items_page(limit: $limit) {{ cursor items {{ {fields} }} }} }} }}",
        "query ($cursor: String!, $limit: Int!) { next_items_page(cursor: $cursor, limit: $limit) "
        f"{{ cursor items {{ {fields} }} }} }}",
    )


def fetch_projects_board(client: "MondayClient", board_id: int, with_subitems: bool = True) -> List[Dict[str, Any]]:
    """Every item on a projects board (with subitems when milestones are subitems)."""
    first, more = _projects_query(with_subitems)
    limit = 100 if with_subitems else 500
    result = client.execute_query(first, {"boardId": [str(board_id)], "limit": limit})
    boards = (result.get("data") or {}).get("boards") or []
    if not boards:
        # monday answers a wrong id, or a board the token cannot see, with no
        # boards rather than an error. Treating that as "empty board" would
        # delete every project from the mirror.
        raise MondayAPIError(f"Board {board_id} was not found, or the API token cannot see it.")
    page = boards[0].get("items_page") or {}
    items = list(page.get("items") or [])
    cursor = page.get("cursor")
    for _ in range(100):
        if not cursor:
            break
        nxt = (client.execute_query(more, {"cursor": cursor, "limit": limit}).get("data") or {}).get("next_items_page") or {}
        items.extend(nxt.get("items") or [])
        cursor = nxt.get("cursor")
    if cursor:
        raise MondayAPIError(f"Board {board_id} has more than {100 * limit} items; read stopped part way.")
    return items


def fetch_user_emails(client: "MondayClient") -> Dict[str, str]:
    """monday person id -> email, for people columns (which hold ids, not emails)."""
    result = client.execute_query("query { users(limit: 1000) { id email } }")
    return {str(u["id"]): u.get("email") or "" for u in (result.get("data") or {}).get("users") or []}


def _parse_retry_after(value: Optional[str]) -> float:
    try:
        return max(0.0, float(value)) if value else 5.0
    except ValueError:
        return 5.0
