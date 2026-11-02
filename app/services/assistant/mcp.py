"""Third-party tools over MCP (Model Context Protocol, streamable HTTP).

Each server in ASSISTANT_MCP_SERVERS adds its tools to the assistant as
"<server>__<tool>". The calls are made from this server, as the signed-in
user, so permissions and confirmation stay here: a tool is a read only if
the server marks it readOnlyHint or it is listed in read_only_tools;
everything else needs the user's confirmation. Secrets go in headers as
"env:NAME" so they live in the environment, not in the setting.
"""

import hashlib
import json
import os
import re
import threading
import time
from typing import Any, Dict, List, Optional

import httpx

from app.config import settings
from app.core.logging import logger

_PROTOCOL = "2025-06-18"
_cache: Dict[str, Any] = {"at": 0.0, "raw": None, "tools": {}, "status": {}}
_lock = threading.Lock()
_sessions: Dict[str, str] = {}


def servers() -> List[Dict[str, Any]]:
    raw = (settings.ASSISTANT_MCP_SERVERS or "").strip()
    if not raw:
        return []
    try:
        listed = json.loads(raw)
    except ValueError:
        logger.error("ASSISTANT_MCP_SERVERS is not valid JSON; no MCP tools loaded.")
        return []
    out = []
    for s in listed if isinstance(listed, list) else []:
        name, url = str(s.get("name") or "").strip(), str(s.get("url") or "").strip()
        if not name or not url.startswith(("https://", "http://localhost", "http://127.0.0.1")):
            logger.error(f"MCP server {name or '?'} skipped: needs a name and an https URL.")
            continue
        out.append({**s, "name": "".join(c if c.isalnum() else "_" for c in name)[:24]})
    return out


def _headers(server: Dict[str, Any]) -> Dict[str, str]:
    out = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream",
           "MCP-Protocol-Version": _PROTOCOL}
    for k, v in (server.get("headers") or {}).items():
        v = str(v)
        out[k] = os.environ.get(v[4:], "") if v.startswith("env:") else v
    if _sessions.get(server["name"]):
        out["Mcp-Session-Id"] = _sessions[server["name"]]
    return out


def _read(response: httpx.Response) -> Dict[str, Any]:
    if "text/event-stream" in response.headers.get("content-type", ""):
        last = None
        for line in response.text.splitlines():
            if line.startswith("data:"):
                try:
                    msg = json.loads(line[5:].strip())
                except ValueError:
                    continue
                if "result" in msg or "error" in msg:
                    last = msg
        return last or {}
    return response.json() if response.content else {}


def _rpc(server: Dict[str, Any], method: str, params: Optional[Dict[str, Any]] = None, notify: bool = False) -> Any:
    body: Dict[str, Any] = {"jsonrpc": "2.0", "method": method, "params": params or {}}
    if not notify:
        body["id"] = int(time.time() * 1000) % 1_000_000_000
    response = httpx.post(server["url"], json=body, headers=_headers(server), timeout=settings.AI_TIMEOUT_SECONDS)
    if response.headers.get("mcp-session-id"):
        _sessions[server["name"]] = response.headers["mcp-session-id"]
    if response.status_code == 404 and _sessions.pop(server["name"], None):
        raise ConnectionResetError("MCP session expired")
    response.raise_for_status()
    if notify:
        return None
    msg = _read(response)
    if msg.get("error"):
        raise RuntimeError(f"{server['name']}: {msg['error'].get('message', 'error')}")
    return msg.get("result") or {}


def _connect(server: Dict[str, Any]) -> None:
    _rpc(server, "initialize", {"protocolVersion": _PROTOCOL, "capabilities": {},
                                "clientInfo": {"name": "allied-operations", "version": "1.0"}})
    _rpc(server, "notifications/initialized", notify=True)


def _call(server: Dict[str, Any], method: str, params: Optional[Dict[str, Any]] = None) -> Any:
    if not _sessions.get(server["name"]):
        _connect(server)
    try:
        return _rpc(server, method, params)
    except ConnectionResetError:
        _connect(server)
        return _rpc(server, method, params)


def _safe_name(server: str, tool: str) -> str:
    """Tool names the model API accepts (letters, digits, _ and -, at most 64); a name that had
    to be shortened gets a short hash so two long names can't collide."""
    raw = re.sub(r"[^A-Za-z0-9_-]", "_", f"{server}__{tool}")
    return raw if len(raw) <= 64 else f"{raw[:55]}_{hashlib.sha1(raw.encode()).hexdigest()[:8]}"


def _list_tools(server: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Every page of tools/list (servers page long lists with nextCursor)."""
    found, cursor = [], None
    for _ in range(20):
        page = _call(server, "tools/list", {"cursor": cursor} if cursor else None) or {}
        found += page.get("tools") or []
        cursor = page.get("nextCursor")
        if not cursor:
            break
    return found


def _make_tool(server: Dict[str, Any], t: Dict[str, Any]):
    from app.services.assistant.tools import Tool

    name = t.get("name") or ""
    # A server's own "read-only" label is untrusted (MCP spec): a careless or hostile server
    # could label a write that way and skip the confirmation card. It counts only when this
    # server's config says trust_annotations, or the tool is listed in read_only_tools here.
    hinted = bool((t.get("annotations") or {}).get("readOnlyHint")) and bool(server.get("trust_annotations"))
    read_only = hinted or name in (server.get("read_only_tools") or [])
    schema = t.get("inputSchema") or {"type": "object", "properties": {}}

    def run(args: Dict[str, Any], user: Dict[str, Any]) -> Any:
        result = _call(server, "tools/call", {"name": name, "arguments": args})
        text = "\n".join(c.get("text", "") for c in result.get("content") or [] if c.get("type") == "text")
        if result.get("isError"):
            raise RuntimeError(text[:300] or f"{name} failed")
        return text if read_only else {"done": text[:500] or f"{name} done."}

    return Tool(
        name=_safe_name(server["name"], name),
        description=(f"[{server['name']}] " + (t.get("description") or name))[:300],
        params=schema.get("properties") or {}, required=list(schema.get("required") or []),
        run=run, write=not read_only, min_role=server.get("min_role") or "OPERATOR", connector=server["name"],
        # The card shows everything that will be sent, not a cut-off preview.
        summarize=lambda a, n=name, s=server["name"]: f"Run {n} on {s} with {json.dumps(a, indent=1)[:4000]}",
    )


def tools() -> Dict[str, Any]:
    """MCP tools, listed again every 10 minutes (or when the setting changes)."""
    raw = settings.ASSISTANT_MCP_SERVERS or ""
    with _lock:
        if _cache["raw"] == raw and time.monotonic() - _cache["at"] < 600:
            return _cache["tools"]
        found, status = {}, {}
        for server in servers():
            try:
                listed = _list_tools(server)
                allowed = server.get("allowed_tools")
                for t in listed:
                    if allowed and t.get("name") not in allowed:
                        continue
                    tool = _make_tool(server, t)
                    found[tool.name] = tool
                status[server["name"]] = {"ready": True, "tools": len([t for t in found.values() if t.connector == server["name"]])}
            except Exception as exc:  # noqa: BLE001 - one unreachable server must not stop the assistant
                status[server["name"]] = {"ready": False, "detail": f"{type(exc).__name__}: {str(exc)[:160]}"}
                logger.warning(f"MCP server {server['name']} unavailable: {exc}")
        _cache.update(at=time.monotonic(), raw=raw, tools=found, status=status)
        return found


def status() -> Dict[str, Any]:
    tools()
    return dict(_cache["status"])
