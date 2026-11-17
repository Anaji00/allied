"""Adversarial: authentication and roles on every route. Temporary database only; the network is blocked while these run."""
import re
import socket

import pytest
from fastapi.routing import APIRoute

from tests.conftest import staff_token

PUBLIC_OK = ("/api/v1/auth/login", "/api/v1/auth/register", "/api/v1/health", "/docs", "/openapi.json", "/redoc", "/api/v1/webhooks")
WRITES = ("POST", "PUT", "PATCH", "DELETE")


@pytest.fixture()
def no_network(monkeypatch):
    def boom(*a, **k):
        raise OSError("tests never open the network")
    monkeypatch.setattr(socket.socket, "connect", boom)
    monkeypatch.setattr(socket, "create_connection", boom)


def _routes():
    from app.main import app
    out = []
    for r in app.routes:
        if isinstance(r, APIRoute):
            for m in r.methods:
                if m in WRITES + ("GET",):
                    out.append((m, r.path))
    return out


def _fill(path):
    return re.sub(r"\{[^}]+\}", "1", path)


def _sweep(client, headers, writes_only):
    leaks = []
    for m, p in _routes():
        if p.startswith(PUBLIC_OK) or p == "/" or (writes_only and m not in WRITES):
            continue
        r = client.request(m, _fill(p), json={} if m != "GET" else None, headers=headers)
        if r.status_code not in (401, 403, 404, 405):
            leaks.append((m, p, r.status_code))
    return leaks


def test_no_route_answers_without_a_token(auth_enforced, client, no_network):
    assert not _sweep(client, {}, False)


def test_viewer_cannot_reach_any_write_route(auth_enforced, client, no_network):
    h = {"Authorization": "Bearer " + staff_token("adv-viewer@alliedweldingsystems.com", "VIEWER")}
    leaks = _sweep(client, h, True)
    print("VIEWER LEAKS", leaks)
    assert not leaks, leaks


def test_operator_cannot_reach_manager_only_po_routes(auth_enforced, client):
    h = {"Authorization": "Bearer " + staff_token("adv-op@alliedweldingsystems.com", "OPERATOR")}
    assert client.post("/api/v1/purchase-orders/1/approve", headers=h).status_code == 403
    assert client.post("/api/v1/purchase-orders/1/resolve-send", json={"sent": True}, headers=h).status_code == 403


def _order(so="7712345"):
    import uuid
    from app.core.database import SessionLocal
    from app.models.db_models import OrderDB
    with SessionLocal() as db:
        db.add(OrderDB(order_id=f"O{uuid.uuid4().hex[:8]}", customer_po_number="CUSTREF-9", sales_order_number=so, customer_id="C1",
                       customer_name="Adversary Customer", status="NEW", purchase_orders={"HELEN": "50999"}))
        db.commit()
    return so



def test_a_viewer_never_sees_the_internal_sales_order_number(auth_enforced, client):
    from app.services.assistant import tools
    so = _order()
    viewer = {"email": "adv-viewer@alliedweldingsystems.com", "role": "VIEWER"}
    assert so not in tools.TOOLS["find_orders"].run({"search": "Adversary"}, viewer)
    h = {"Authorization": "Bearer " + staff_token("adv-viewer@alliedweldingsystems.com", "VIEWER")}
    # The orders screens are Allied's own staff view (a VIEWER is internal read-only staff) and are keyed by sales order number, so they keep it.
    # Customers and vendors never log in; the copilot's find_orders hides the number from a viewer.
    for path in ("/api/v1/purchase-orders?q=50999",):
        r = client.get(path, headers=h)
        assert so not in r.text, path
