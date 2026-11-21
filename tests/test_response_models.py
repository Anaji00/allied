"""Every route the frontend builds a typed client for has a named response model, and what it really answers fits that model unchanged. Temporary database; AI and packet services are faked."""
import json
import os
import uuid

import pytest

from app.api.v1 import view_models as vm
from app.main import app
from app.services.ai import features
from app.services.coordination import packets
from tests.test_contract_screens import CUSTOMER, TAG, api  # noqa: F401

ROUTES = {
    ("get", "/api/v1/ball/summary"): "BallSummary",
    ("post", "/api/v1/packets"): "PacketRead",
    ("get", "/api/v1/packets"): "WaitingPacket",
    ("post", "/api/v1/packets/{packet}/attach"): "PacketAttached",
    ("post", "/api/v1/ai/exceptions/{exception_id}/explain"): "ExplainResponse",
    ("post", "/api/v1/ai/items/suggest"): "SuggestItemResponse",
    ("get", "/api/v1/jobs/{job}/page"): "JobPage",
    ("get", "/api/v1/jobs/{job}/automation"): "AutomationLog",
    ("get", "/api/v1/conversations"): "Conversations",
    ("get", "/api/v1/sage/export/overview"): "SageOverview",
    ("get", "/api/v1/my-day"): "MyDay",
    ("get", "/api/v1/brief"): "Brief",
    ("get", "/api/v1/chase"): "Chase",
    ("get", "/api/v1/prep/{kind}/{ref}"): "PrepOut",
    ("get", "/api/v1/since"): "SinceOut",
}


def _ref(schema):
    ok = schema["paths"]
    return ok


def test_every_listed_route_names_its_response_model_in_the_openapi():
    spec = app.openapi()
    for (method, path), name in ROUTES.items():
        body = spec["paths"][path][method]["responses"]["200"]["content"]["application/json"]["schema"]
        text = json.dumps(body)
        assert name in text, f"{method.upper()} {path} should answer with {name}, not a free-form object: {text[:200]}"
        assert "additionalProperties" not in body or "$ref" in text


def test_what_each_route_really_answers_fits_its_model_and_nothing_is_dropped(api):
    rfq = api("POST", "/create/rfq", json={"customer": CUSTOMER, "subject": f"Model {TAG}{uuid.uuid4().hex[:3]}"}).json()
    ref = api("POST", f"/jobs/from-rfq/{rfq['id']}").json()["ref"]
    api("POST", f"/jobs/{ref}/design/approve", role="MANAGER")
    api("POST", f"/jobs/{ref}/purchase-orders", json={"vendor": f"V {TAG}", "lines": [{"description": "x", "quantity": 2, "unit_cost": 2500, "revision": "A"}]})
    api("POST", f"/actions/job/{ref}/share", json={"body": f"comment {TAG}"})
    api("PUT", "/ball", json={"item_type": "rfq", "item_ref": rfq["ref"], "ball_with": "vendor", "ball_person": "x@y.test"})
    samples = {}
    checks = [("ball_summary", "/ball/summary", vm.BallSummary, "MANAGER", {}), ("page", f"/jobs/{ref}/page", vm.JobPage, "MANAGER", {}), ("page_viewer", f"/jobs/{ref}/page", vm.JobPage, "VIEWER", {}),
              ("conversations", "/conversations", vm.Conversations, "MANAGER", {}), ("overview", "/sage/export/overview", vm.SageOverview, "MANAGER", {}),
              ("automation", f"/jobs/{ref}/automation", vm.AutomationLog, "MANAGER", {}), ("my_day", "/my-day", vm.MyDay, "MANAGER", {}), ("brief", "/brief", vm.Brief, "MANAGER", {}),
              ("chase", "/chase", vm.Chase, "MANAGER", {}), ("prep", f"/prep/job/{ref}", vm.PrepOut, "MANAGER", {}), ("since", "/since", vm.SinceOut, "MANAGER", {"params": {"kind": "job", "ref": ref}}),
              ("packets_list", "/packets", None, "MANAGER", {})]
    for name, path, model, role, kw in checks:
        got = api("GET", path, role=role, **kw).json()
        samples[name] = got
        if model is not None:
            fitted = model.model_validate(got).model_dump(mode="json", exclude_unset=True)
            assert fitted == got, f"{name}: the model changed what the route answers"
    assert samples["page_viewer"]["staff"] is False and "purchase_orders" not in samples["page_viewer"]["job"]
    assert samples["page"]["job"]["purchase_orders"][0]["vendor"] == f"V {TAG}"
    assert samples["packets_list"] == []
    out = os.environ.get("SAMPLES_OUT")
    if out:
        with open(out, "w", encoding="utf-8") as f:
            json.dump(samples, f, indent=1, default=str)


def test_the_ai_and_packet_routes_answer_through_their_models(api, monkeypatch):
    from app.services.exception_queue_service import exception_queue_service

    class Item:
        exception_id = "EX-1"
        exception_type = "SOMETHING_NEW"

        def model_dump(self, mode="json"):
            return {"exception_id": "EX-1", "title": "t"}

    monkeypatch.setattr(exception_queue_service, "list_exceptions", lambda *a, **k: [Item()])
    monkeypatch.setattr(features, "explain_exception", lambda item, actor="system": (features.Explanation(explanation="A part is late.", suggested_fix="Call the vendor."), "sg-1"))
    got = api("POST", "/ai/exceptions/EX-1/explain", role="VIEWER").json()
    assert got == {"suggestion_id": "sg-1", "result": {"explanation": "A part is late.", "suggested_fix": "Call the vendor."}}
    vm.ExplainResponse.model_validate(got)
    cands = [{"item_id": "A1", "description": "Liner", "score": 91.5}]
    monkeypatch.setattr(features, "suggest_item", lambda raw, actor="system": (features.ItemChoice(item_id="A1", reason="closest", confidence=0.9), "sg-2", cands))
    got = api("POST", "/ai/items/suggest", json={"raw_text": "liner plate"}).json()
    assert got["result"]["item_id"] == "A1" and got["candidates"] == cands
    vm.SuggestItemResponse.model_validate(got)
    doc = {"id": 5, "job_id": None, "kind": "po", "label": "Purchase order", "pages": "1-2", "page_from": 1, "page_to": 2, "fields": {"po_number": "9248"}, "warnings": [], "confirmed": False,
           "confirmed_by": None, "source": "scan.pdf", "packet": "a" * 64, "at": "2026-11-17T10:00:00"}
    monkeypatch.setattr(packets, "unattached", lambda: [{"packet": "a" * 64, "source": "scan.pdf", "documents": [doc]}])
    assert api("GET", "/packets").json()[0]["documents"][0] == doc
    monkeypatch.setattr(packets, "attach", lambda packet, job: {"job_id": 3, "documents": [{**doc, "job_id": 3}]})
    got = api("POST", f"/packets/{'a' * 64}/attach", json={"job": "JOB-1"}).json()
    assert got["job_id"] == 3 and got["documents"][0]["job_id"] == 3
    monkeypatch.setattr(packets, "ingest", lambda pdf, name, user, job=None: {"packet": "b" * 64, "already_read": False, "job_id": None, "documents": [doc]})
    monkeypatch.setattr(packets, "read_upload", lambda f: b"%PDF")
    got = api("POST", "/packets", files={"file": ("p.pdf", b"%PDF-1.4", "application/pdf")}).json()
    assert got["already_read"] is False and got["documents"][0]["fields"] == {"po_number": "9248"}
    vm.PacketRead.model_validate(got)


def test_a_known_exception_kind_is_explained_in_fixed_words_without_the_ai(api, monkeypatch):
    from app.services.exception_queue_service import exception_queue_service

    class Item:
        exception_id = "EX-2"
        exception_type = "UNMATCHED_SKU"

        def model_dump(self, mode="json"):
            return {"exception_id": "EX-2"}

    monkeypatch.setattr(exception_queue_service, "list_exceptions", lambda *a, **k: [Item()])
    monkeypatch.setattr(features, "explain_exception", lambda *a, **k: (_ for _ in ()).throw(AssertionError("the AI must not be asked")))
    got = api("POST", "/ai/exceptions/EX-2/explain", role="VIEWER").json()
    assert got["suggestion_id"] is None and "catalogue" in got["result"]["explanation"] and got["result"]["suggested_fix"]


def test_the_monday_chip_the_ai_flag_last_read_and_per_currency_totals(api):
    from app.services import monday_state

    rows = api("GET", "/book", params={"area": "orders", "kind": "so"}).json()["rows"] + api("GET", "/book", params={"area": "jobs"}).json()["rows"]
    assert all(r["monday"]["state"] in ("on_monday", "waiting", "none") and r["monday"]["label"] == monday_state.LABELS[r["monday"]["state"]] for r in rows)
    st = api("GET", "/automation/status").json()
    assert set(st["ai"]) == {"ready", "provider", "model", "reason"} and (st["ai"]["ready"] or st["ai"]["reason"])
    assert all("last_read" in b for b in api("GET", "/boards").json())
    ov = api("GET", "/sage/export/overview", role="MANAGER").json()
    assert isinstance(ov["ready_to_send"]["sales_totals"], dict)
    assert api("GET", "/sage/export/summary", role="MANAGER").json()["by_currency"] is not None
