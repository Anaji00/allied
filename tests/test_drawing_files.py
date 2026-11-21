"""Comparing two drawings already attached to a job (by file number), the thread comment, the vendor-safe overlay attachment and the copilot tool.
Drawings are made in the test as small PDFs; the throwaway database and upload folder only (tests/conftest.py)."""
import uuid

import pymupdf
import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.services import actions, drawing_files
from app.services.coordination import files, updates
from tests.conftest import staff_token
from tests.test_drawing_compare import _drawing

TAG = uuid.uuid4().hex[:5]
OP = {"email": f"df-operator-{TAG}@alliedweldingsystems.com", "role": "OPERATOR"}
MGR = {"email": f"df-manager-{TAG}@alliedweldingsystems.com", "role": "MANAGER"}


@pytest.fixture()
def api(auth_enforced, monkeypatch):
    from app.main import app

    monkeypatch.setattr(settings, "MONDAY_READ_ONLY", True)
    client = TestClient(app, raise_server_exceptions=False)
    heads = {r: {"Authorization": "Bearer " + staff_token(f"df-{r.lower()}-{TAG}@alliedweldingsystems.com", r)} for r in ("MANAGER", "OPERATOR", "VIEWER")}

    def call(method, path, role="OPERATOR", expect=200, **kw):
        r = client.request(method, "/api/v1" + path, headers=heads[role], **kw)
        assert r.status_code == expect, f"{method} {path} as {role}: {r.status_code} {r.text[:300]}"
        return r

    return call


@pytest.fixture()
def job(api):
    rfq = api("POST", "/rfqs", json={"customer": "Owens Corning Starr Composites", "subject": f"Drawings {TAG}{uuid.uuid4().hex[:3]}", "contact_email": f"b{TAG}@customer-example.test"}).json()
    ref = api("POST", f"/jobs/from-rfq/{rfq['id']}").json()["ref"]
    a = files.add("rfq", rfq["number"], "rev_a.pdf", _drawing(dim="250", rev="A"), OP)
    b = files.add("rfq", rfq["number"], "rev_b.pdf", _drawing(dim="260", rev="B"), OP)
    c = files.add("rfq", rfq["number"], "notes.dwg", b"AC1015" + b"\x00" * 100, OP) if "dwg" in files.allowed() else None
    return {"ref": ref, "rfq": rfq["number"], "a": a["id"], "b": b["id"], "dwg": c["id"] if c else None}


def test_two_attached_files_are_compared_by_number_with_a_summary(api, job):
    r = api("POST", "/drawings/compare-files", json={"file_a": job["a"], "file_b": job["b"]}).json()
    assert r["verdict"] in ("minor differences", "different") and r["file_a"]["name"] == "rev_a.pdf" and "Drawing comparison: rev_a.pdf against rev_b.pdf" in r["summary"]
    assert any("revision" in w.lower() for w in r["warnings"]) and r["overlay_png_base64"]
    png = api("GET", "/drawings/compare-files/overlay.png", params={"file_a": job["a"], "file_b": job["b"]})
    assert png.headers["content-type"] == "image/png" and png.content[:4] == b"\x89PNG"


def test_missing_hidden_unreadable_and_bad_requests_are_plain(api, job):
    api("POST", "/drawings/compare-files", json={"file_a": job["a"], "file_b": 987654}, expect=404)
    api("POST", "/drawings/compare-files", json={"file_a": job["a"], "file_b": job["a"]}, expect=400)
    api("POST", "/drawings/compare-files", json={"file_a": "x", "file_b": job["b"]}, expect=400)
    api("POST", "/drawings/compare-files", json={"file_a": job["a"], "file_b": job["b"], "page_a": 0}, expect=400)
    api("POST", "/drawings/compare-files", json={"file_a": True, "file_b": job["b"]}, expect=400)
    if job["dwg"]:
        assert "DWG" in api("POST", "/drawings/compare-files", json={"file_a": job["a"], "file_b": job["dwg"]}, expect=400).json()["detail"]
    files.remove(job["b"], MGR)
    gone = api("POST", "/drawings/compare-files", json={"file_a": job["a"], "file_b": job["b"]}, expect=404).json()["detail"]
    assert "gone" in gone
    api("POST", "/drawings/compare-files", json={"file_a": job["a"], "file_b": job["b"]}, role="VIEWER", expect=403)
    api("GET", "/drawings/compare-files/overlay.png", params={"file_a": 0, "file_b": 1}, expect=422)


def test_the_result_can_be_posted_to_the_items_thread(api, job):
    r = api("POST", "/drawings/compare-files", json={"file_a": job["a"], "file_b": job["b"], "post_to_thread": True}).json()
    assert r["thread"]["posted"] and r["thread"]["item_ref"] == job["rfq"]
    notes = [u["body"] for u in updates.thread("rfq", job["rfq"])]
    assert any(n.startswith("Drawing comparison: rev_a.pdf against rev_b.pdf") and "Verdict" in n for n in notes)
    plain = api("POST", "/drawings/compare-files", json={"file_a": job["a"], "file_b": job["b"]}).json()
    assert "thread" not in plain


def test_the_overlay_can_be_attached_to_a_draft_for_a_vendor_only_when_both_are_design_files(api, job):
    ctx = actions.context("job", job["ref"])
    pair = next(f for f in ctx["files"] if f["type"] == "drawing_overlay")
    assert pair["id"] == f"{job['a']}:{job['b']}"
    spec = {"type": "drawing_overlay", "id": pair["id"]}
    api("POST", f"/actions/job/{job['ref']}/email-draft", json={"to": "v@vendor-example.test", "audience": "vendor", "attach": [spec], "body": "Please compare."}, expect=400)       # not design files yet
    mine = api("POST", f"/actions/job/{job['ref']}/email-draft", json={"audience": "internal", "to": f"boss{TAG}@alliedweldingsystems.com", "attach": [spec], "body": "Look"}).json()
    assert mine["attachments"] and mine["attachments"][0].startswith("Drawing comparison")
    for fid in (job["a"], job["b"]):
        api("PATCH", f"/files/{fid}", json={"design": True}, role="MANAGER")
    ok = api("POST", f"/actions/job/{job['ref']}/email-draft", json={"to": "v@vendor-example.test", "audience": "vendor", "attach": [spec], "body": "Please compare."}).json()
    assert ok["drafted"]
    built = actions.resolve_attachments([spec], "vendor", OP)
    assert built[0]["mime"] == "image/png" and built[0]["content"][:4] == b"\x89PNG"
    api("PATCH", f"/files/{job['b']}", json={"design": False}, role="MANAGER")
    with pytest.raises(actions.ActionError):
        actions.resolve_attachments([spec], "vendor", OP)                                       # checked again when it is approved
    with pytest.raises(actions.ActionError):
        actions.resolve_attachments([spec], "customer", OP)                                     # a customer never gets one


def test_the_copilot_tool_compares_the_two_latest_drawings_by_voice_without_a_card(api, job):
    from app.services.assistant import agent
    from app.services.assistant.tools import TOOLS

    t = TOOLS["compare_drawings"]
    assert not t.write and t.allowed(OP)
    said = agent.run_tool(t, {"job": job["ref"]}, OP)
    assert "rev_a.pdf" in said and "rev_b.pdf" in said and "Verdict" in said
    assert "Could not" in agent.run_tool(t, {}, OP) and "Could not" in agent.run_tool(t, {"job": "JOB-9999-9999"}, OP)
    assert "Could not" in agent.run_tool(t, {"file_a": job["a"], "file_b": 987654}, OP)
    assert not t.allowed({"email": "v@x.test", "role": "VIEWER"})
