"""Add anything on a dime, over HTTP with real accounts: every kind from plain fields, the refusals in plain words, and the monday step, which is only ever
STAGED. Temporary database and catalogue folder only (tests/conftest.py); nothing is sent to monday or to anyone."""
import uuid

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.core.database import SessionLocal
from app.models.db_models import MondayChangeDB
from app.services import monday_push
from app.services.coordination import boards
from tests.conftest import staff_token

TAG = uuid.uuid4().hex[:5]
CUSTOMER = "Owens Corning Starr Composites"


@pytest.fixture()
def api(auth_enforced, monkeypatch):
    from app.main import app

    monkeypatch.setattr(settings, "MONDAY_READ_ONLY", True)
    client = TestClient(app, raise_server_exceptions=False)
    heads = {r: {"Authorization": "Bearer " + staff_token(f"mk-{r.lower()}-{TAG}@alliedweldingsystems.com", r)} for r in ("ADMIN", "MANAGER", "OPERATOR", "VIEWER")}

    def call(method, path, role="OPERATOR", expect=200, **kw):
        r = client.request(method, "/api/v1" + path, headers=heads[role], **kw)
        assert r.status_code == expect, f"{method} {path} as {role}: {r.status_code} {r.text[:400]}"
        return r

    with SessionLocal() as db:
        db.query(MondayChangeDB).delete()
        db.commit()
    return call


@pytest.fixture()
def board():
    bid = str(uuid.uuid4().int % 10**9)
    with boards._lock:
        boards._mirror[bid] = {"board_id": bid, "name": "Test Board", "role": "reference", "read_at": "now", "groups": [{"id": "g1", "title": "New work", "color": ""}],
                               "columns": [{"id": "cust", "title": "Customer", "type": "text"}, {"id": "stat", "title": "Status", "type": "status", "labels": {"New": "", "Open": ""}},
                                           {"id": "who", "title": "Owner", "type": "people"}, {"id": "n", "title": "Notes", "type": "text"}], "items": []}
    yield bid
    with boards._lock:
        boards._mirror.pop(bid, None)


def test_every_kind_is_made_from_plain_fields_and_answers_ref_link_message(api):
    rfq = api("POST", "/create/rfq", json={"customer": CUSTOMER, "subject": f"Flex hose {TAG}", "contact_email": f"b{TAG}@customer-example.test", "items": "2 x flex hose\n1 x clamp"}).json()
    assert rfq["kind"] == "rfq" and rfq["ref"].startswith("RFQ") and rfq["link"].endswith(rfq["ref"]) and rfq["message"]
    job = api("POST", "/create/job", json={"fields": {"customer": CUSTOMER, "title": f"Job {TAG}"}}).json()
    assert job["ref"].startswith("JOB-") and job["link"] == f"/jobs/{job['ref']}"
    po = api("POST", "/create/po", json={"job": job["ref"], "vendor": f"Maple Fab Ltd {TAG}", "items": [{"description": "bracket", "quantity": 4, "unit_cost": 40}]}).json()
    assert po["kind"] == "po" and "approve" in po["message"].lower()
    ship = api("POST", "/create/shipment", json={"reference": f"BK-{TAG}", "forwarder": "Test Forwarder", "eta": "2026-12-20"}).json()
    assert ship["kind"] == "shipment" and ship["ref"].isdigit()
    part = f"ZZ-{TAG.upper()}"
    prod = api("POST", "/create/product", json={"item_id": part, "description": "Test bracket", "base_price": "12.50"}).json()
    assert prod["ref"] == part and "catalogue" in prod["message"]
    api("POST", "/create/product", json={"item_id": part}, expect=400)                        # already there: edit it instead
    order = api("POST", "/create/order", json={"customer": CUSTOMER, "customer_po_number": f"4500{uuid.uuid4().int % 10**6}", "shipping_address": "4837 Highway 81 South, Starr, SC 29684",
                                                 "items": [{"part": "OC262064", "quantity": 1, "unit_price": 1385.0}]}).json()
    assert order["kind"] == "order" and order["ref"]


def test_missing_or_hostile_fields_are_refused_in_plain_words_and_roles_hold(api):
    for kind, body in (("rfq", {}), ("rfq", {"customer": CUSTOMER, "items": 5}), ("job", {"title": "x"}), ("po", {"vendor": "V"}), ("po", {"job": "JOB-9999999", "vendor": "V"}),
                       ("shipment", {}), ("shipment", {"reference": "x", "eta": "soon"}), ("order", {"customer": CUSTOMER}), ("product", {}), ("board_row", {})):
        r = api("POST", f"/create/{kind}", json=body, expect=400 if kind != "po" or "job" not in body else 404) if not (kind == "po" and body.get("job")) else api("POST", f"/create/{kind}", json=body, expect=404)
        assert r.json()["detail"]
    api("POST", "/create/banana", json={}, expect=422)
    api("POST", "/create/rfq", json={"customer": CUSTOMER, "subject": "x"}, role="VIEWER", expect=403)
    api("POST", "/create/rfq", json={"fields": "text"}, expect=400)
    api("POST", "/create/rfq", json={"customer": ["a"], "subject": "x"}, expect=400)


def test_a_board_row_is_added_with_its_cells_staged_not_sent(api, board):
    got = api("POST", "/create/board_row", json={"board_id": board, "name": "Quick row", "group": "new", "cells": {"Status": "op", "Notes": "hello"}}).json()
    assert got["staged"] and got["change_id"]
    row = next(c for c in monday_push.changes("STAGED") if c["id"] == got["change_id"])
    assert row["kind"] == "create_item" and row["payload"]["group_id"] == "g1" and row["payload"]["fields"]["stat"] == "Open"


def test_to_monday_stages_a_row_with_the_matching_cells_and_lists_what_it_left_out(api, board):
    got = api("POST", "/create/rfq", json={"customer": CUSTOMER, "subject": f"Hose {TAG}{uuid.uuid4().hex[:3]}", "to_monday": {"board_id": board, "group": "new work"}}).json()
    m = got["monday"]
    assert m["staged"] and m["cells"] >= 1 and any(x["fact"] == "Subject" for x in m["left_out"]) and "nothing is sent" in m["message"]
    row = next(c for c in monday_push.changes("STAGED") if c["id"] == m["change_id"])
    assert row["payload"]["fields"]["cust"] == CUSTOMER and row["payload"]["group_id"] == "g1" and row["kind"] == "create_item"
    again = api("POST", f"/actions/rfq/{got['ref']}/to-monday", json={"board_id": board, "group": "new work"}).json()
    assert again["change_id"] == m["change_id"]                                                   # the same row, not a second one
    bad = api("POST", "/create/rfq", json={"customer": CUSTOMER, "subject": f"Hose2 {TAG}{uuid.uuid4().hex[:3]}", "to_monday": {"board_id": "424242424"}}).json()
    assert bad["ref"] and bad["monday"]["staged"] is False and "not staged for monday" in bad["message"]       # the record is made; only the board step failed
    api("POST", "/create/rfq", json={"customer": CUSTOMER, "subject": "x", "to_monday": "board"}, expect=400)
    api("POST", f"/actions/rfq/{got['ref']}/to-monday", json={"board_id": board}, role="VIEWER", expect=403)
    api("POST", f"/actions/rfq/{got['ref']}/to-monday", json={"board_id": board, "group": "nope"}, expect=400)
    api("POST", f"/actions/rfq/{got['ref']}/to-monday", json={}, expect=400)


# ---------------------------------------------------------------- records: fields and PATCH
def test_records_list_their_fields_and_change_one_from_typed_text(api):
    rfq = api("POST", "/create/rfq", json={"customer": CUSTOMER, "subject": f"Rec {TAG}{uuid.uuid4().hex[:3]}"}).json()["ref"]
    got = api("GET", f"/records/rfq/{rfq}/fields").json()
    by = {f["field"]: f for f in got["fields"]}
    assert by["customer"]["editable"] and not by["number"]["editable"] and by["complexity"]["choices"] == ["SIMPLE", "STANDARD", "COMPLEX"] and by["customer"]["value"] == CUSTOMER
    r = api("PATCH", f"/records/rfq/{rfq}", json={"field": "complexity", "text": "comp"}).json()
    assert r["now"] == "COMPLEX" and r["understood"] == "Complexity: COMPLEX (you typed 'comp')"
    assert api("PATCH", f"/records/rfq/{rfq}", json={"field": "Contact", "text": "Bob@Example.TEST"}).json()["now"] == "bob@example.test"
    for body, why in (({"field": "contact_email", "text": "nope"}, "matches no one"), ({"field": "number", "text": "x"}, "can't be changed"), ({"field": "zzz", "text": "x"}, "no field"),
                      ({"field": "complexity", "text": "banana"}, "not a label"), ({"field": "notes", "text": " "}, "Type something"), ({"field": "notes", "text": "x" * 2001}, "2,000")):
        assert why in api("PATCH", f"/records/rfq/{rfq}", json=body, expect=400).json()["detail"]
    api("PATCH", f"/records/rfq/{rfq}", json={"field": "notes", "text": ["a"]}, expect=400)
    api("PATCH", f"/records/rfq/{rfq}", json={"field": "notes", "text": "ok"}, role="VIEWER", expect=403)
    api("GET", f"/records/rfq/{rfq}/fields", role="VIEWER")
    api("PATCH", "/records/rfq/RFQ-9999-9999", json={"field": "notes", "text": "x"}, expect=404)
    api("PATCH", f"/records/banana/{rfq}", json={"field": "notes", "text": "x"}, expect=422)


def test_shipment_dates_and_choices_job_title_po_lock_and_a_part_are_editable(api):
    from app.services.coordination import shipments

    s = api("POST", "/create/shipment", json={"reference": f"BK-{TAG}{uuid.uuid4().hex[:3]}", "forwarder": "FW"}).json()
    r = api("PATCH", f"/records/shipment/{s['ref']}", json={"field": "eta_current", "text": "2026-12-20"}).json()
    assert r["now"] == "2026-12-20" and "Sunday 20 Dec 2026" in r["understood"]
    assert api("PATCH", f"/records/shipment/{s['ref']}", json={"field": "status", "text": "depart"}).json()["now"] == "DEPARTED"
    api("PATCH", f"/records/shipment/{s['ref']}", json={"field": "eta_current", "text": "someday"}, expect=400)
    job = api("POST", "/create/job", json={"customer": CUSTOMER, "title": "Old title"}).json()
    assert api("PATCH", f"/records/job/{job['ref']}", json={"field": "title", "text": "New title"}).json()["now"] == "New title"
    po = api("POST", "/create/po", json={"job": job["ref"], "vendor": f"Maple Fab {TAG}", "items": [{"description": "bracket", "quantity": 1, "unit_cost": 40, "revision": "A"}]}).json()
    assert api("PATCH", f"/records/po/{po['ref']}", json={"field": "terms", "text": "Net 30"}).json()["now"] == "Net 30"
    assert api("PATCH", f"/records/po/{po['ref']}", json={"field": "incoterm", "text": "fob"}).json()["now"] == "FOB"
    api("POST", f"/purchase-orders/{po['ref']}/cancel", json={"reason": "test"}, role="MANAGER")
    locked = api("GET", f"/records/po/{po['ref']}/fields").json()["fields"]
    assert not any(f["editable"] for f in locked)
    api("PATCH", f"/records/po/{po['ref']}", json={"field": "terms", "text": "Net 60"}, expect=400)
    part = f"ZR-{TAG.upper()}{uuid.uuid4().hex[:3].upper()}"
    api("POST", "/create/product", json={"item_id": part, "description": "A part", "base_price": "10"})
    api("PATCH", f"/records/product/{part}", json={"field": "base_price", "text": "$12.50"}, expect=403)                      # a part's price is a manager's to change
    r = api("PATCH", f"/records/product/{part}", role="MANAGER", json={"field": "base_price", "text": "$12.50"}).json()
    assert str(r["now"]).startswith("12.5")
    api("PATCH", f"/records/product/{part}", role="MANAGER", json={"field": "base_price", "text": "cheap"}, expect=400)
    api("GET", "/records/product/NOT-A-PART/fields", expect=404)


# ---------------------------------------------------------------- the book
def test_the_book_lists_orders_and_jobs_with_per_cell_type_and_editable(api):
    rfq = api("POST", "/create/rfq", json={"customer": CUSTOMER, "subject": f"Book {TAG}{uuid.uuid4().hex[:3]}"}).json()["ref"]
    job = api("POST", "/create/job", json={"customer": CUSTOMER, "title": f"Book job {TAG}"}).json()["ref"]
    book = api("GET", "/book", params={"area": "orders", "kind": "rfq", "q": rfq}).json()
    row = next(r for r in book["rows"] if r["ref"] == rfq)
    assert row["record_kind"] == "rfq" and row["cells"]["customer"] == {"value": CUSTOMER, "text": CUSTOMER, "type": "text", "editable": True} and not row["cells"]["number"]["editable"]
    assert [c["field"] for c in book["columns"]["rfq"]][:2] == ["number", "customer"] and book["columns"]["rfq"][-1]["choices"]
    jobs = api("GET", "/book", params={"area": "jobs", "q": job}).json()
    assert jobs["rows"][0]["ref"] == job and jobs["rows"][0]["cells"]["title"]["editable"] and jobs["rows"][0]["link"] == f"/jobs/{job}"
    allrows = api("GET", "/book", params={"area": "orders"}).json()
    assert {r["kind"] for r in allrows["rows"]} >= {"rfq"} and set(allrows["columns"]) <= {"rfq", "so", "po"}
    so_rows = api("GET", "/book", params={"area": "orders", "kind": "so"}).json()
    assert all(not r["cells"]["sales_order_number"]["editable"] for r in so_rows["rows"])
    po_rows = api("GET", "/book", params={"area": "orders", "kind": "po"}).json()
    assert all(not any(c["editable"] for c in r["cells"].values()) for r in po_rows["rows"])
    api("GET", "/book", params={"area": "inbox"}, expect=400)
    api("GET", "/book", params={"area": "orders", "kind": "x"}, expect=400)
    api("GET", "/book", params={"limit": 9999}, expect=422)
    api("GET", "/book", role="VIEWER", expect=403)


# ---------------------------------------------------------------- the copilot tools
def test_the_copilot_adds_edits_and_stages_through_confirm_cards(api, board):
    from app.services.assistant import agent
    from app.services.assistant.tools import TOOLS

    op = {"email": f"mk-operator-{TAG}@alliedweldingsystems.com", "role": "OPERATOR"}

    def card(tool, args):
        t = TOOLS[tool]
        assert agent._precheck(t, args, op) == "", args
        return agent._propose(t, args, op)

    a = {"kind": "quote request", "fields": {"customer": CUSTOMER, "subject": f"Spoken {TAG}{uuid.uuid4().hex[:3]}"}, "board": "Test Board", "group": "new work"}
    proposal = card("add_anything", a)
    assert proposal["status"] == "PENDING" and "Add a rfq" in proposal["summary"] and "board Test Board, group New work" in proposal["summary"] and "Customer -> Customer" in proposal["summary"] and "Left out" in proposal["summary"] and "Subject" in proposal["summary"]
    assert monday_push.changes("STAGED") == []                                               # a proposal changes nothing
    done = agent.decide(proposal["id"], True, op)
    assert done["status"] == "DONE" and done["result"]["ref"].startswith("RFQ") and done["result"]["monday"]["staged"]
    ref = done["result"]["ref"]
    edit = card("edit_record", {"kind": "rfq", "ref": ref, "field": "complexity", "text": "comp"})
    assert "COMPLEX" in edit["summary"]
    assert agent.decide(edit["id"], True, op)["status"] == "DONE"
    push = card("put_on_monday", {"kind": "rfq", "ref": ref, "board": "Test Board"})
    assert "staged" in push["summary"] and agent.decide(push["id"], True, op)["status"] == "DONE"
    # asked in words that are not enough, hostile or wrong: refused before a card is made
    for tool, args in (("add_anything", {"kind": "banana", "fields": {}}), ("add_anything", {"kind": "po", "fields": {"vendor": "V"}}), ("add_anything", {"kind": "shipment", "fields": {"eta": "2026-12-20"}}),
                       ("add_anything", {"kind": "rfq", "fields": "customer"}), ("add_anything", {"kind": "rfq", "fields": {"customer": "C"}, "board": "No Such Board"}),
                       ("edit_record", {"kind": "rfq", "ref": ref, "field": "contact_email", "text": "nope"}), ("edit_record", {"kind": "rfq", "ref": "RFQ-0-0", "field": "notes", "text": "x"}),
                       ("edit_record", {"kind": "board row", "ref": "1", "field": "x", "text": "y"}), ("put_on_monday", {"kind": "product", "ref": "X", "board": "Test Board"})):
        assert agent._precheck(TOOLS[tool], dict(args), op) != "", (tool, args)
    viewer = {"email": f"mk-viewer-{TAG}@alliedweldingsystems.com", "role": "VIEWER"}
    assert not TOOLS["add_anything"].allowed(viewer) and not TOOLS["edit_record"].allowed(viewer)


# ---------------------------------------------------------------- the real-part rule, why_locked, names, dry run
def test_an_order_line_must_be_a_real_catalogue_part_and_the_closest_are_offered(api):
    body = {"customer": CUSTOMER, "customer_po_number": f"4500{uuid.uuid4().int % 10**6}", "shipping_address": "4837 Highway 81 South, Starr, SC 29684"}
    r = api("POST", "/create/order", json={**body, "items": [{"part": "OC26206X", "quantity": 1}]}, expect=400).json()["detail"]
    assert "not a part in the catalogue" in r and "OC262064" in r
    ok = api("POST", "/create/order", json={**body, "items": [{"part": "oc262064", "quantity": 1, "unit_price": 1385}]}).json()
    assert ok["ref"]


def test_locked_fields_say_why_and_a_person_can_be_named(api):
    job = api("POST", "/create/job", json={"customer": CUSTOMER, "title": "Lock test"}).json()
    po = api("POST", "/create/po", json={"job": job["ref"], "vendor": f"Lock Vendor {TAG}", "items": [{"description": "bracket", "quantity": 1, "unit_cost": 40, "revision": "A"}]}).json()
    api("POST", f"/purchase-orders/{po['ref']}/cancel", json={"reason": "test"}, role="MANAGER")
    f = {x["field"]: x for x in api("GET", f"/records/po/{po['ref']}/fields").json()["fields"]}
    assert f["terms"]["editable"] is False and "sent or cancelled" in f["terms"]["why_locked"] and f["po_number"]["why_locked"]
    assert "sent or cancelled" in api("PATCH", f"/records/po/{po['ref']}", json={"field": "terms", "text": "x"}, expect=400).json()["detail"]
    rfq = api("POST", "/create/rfq", json={"customer": CUSTOMER, "subject": f"Owner {TAG}{uuid.uuid4().hex[:3]}"}).json()["ref"]
    me = f"mk-manager-{TAG}@alliedweldingsystems.com"
    assert "matches no one" in api("PATCH", f"/records/rfq/{rfq}", json={"field": "owner_email", "text": "zzqx nobody"}, expect=400).json()["detail"]
    api("POST", "/actions/rfq/x/share", json={"body": "x"}, expect=404) if False else None
    r = api("PATCH", f"/records/rfq/{rfq}", json={"field": "owner_email", "text": me.split("@")[0]}).json()           # a name typed as the start of the person's address
    assert r["becomes"] == me and r["was"] != r["becomes"]


def test_to_monday_dry_run_shows_the_mapping_and_stages_nothing(api, board):
    rfq = api("POST", "/create/rfq", json={"customer": CUSTOMER, "subject": f"Dry {TAG}{uuid.uuid4().hex[:3]}"}).json()["ref"]
    d = api("POST", f"/actions/rfq/{rfq}/to-monday", json={"board_id": board, "group": "new work", "dry_run": True}).json()
    assert d["dry_run"] and not d["staged"] and d["group"] == "New work" and any(c["column"] == "Customer" for c in d["columns"]) and d["left_out"]
    assert monday_push.changes("STAGED") == []


def test_a_bare_yes_creates_nothing(api):
    from app.services.assistant import voice

    assert voice.voice_intent("yes") == "confirm"                                         # it can only answer a card that is already waiting
    from app.services.assistant import agent

    with pytest.raises(LookupError):
        agent.decide("no-such-card", True, {"email": f"mk-operator-{TAG}@alliedweldingsystems.com", "role": "OPERATOR"})


def test_the_book_filters_sorts_and_flags_what_is_waiting_on_me(api):
    me = f"mk-operator-{TAG}@alliedweldingsystems.com"
    a = api("POST", "/create/rfq", json={"customer": "Zeta Co", "subject": f"Sort {TAG}{uuid.uuid4().hex[:3]}"}).json()["ref"]
    b = api("POST", "/create/rfq", json={"customer": "Alpha Co", "subject": f"Sort {TAG}{uuid.uuid4().hex[:3]}"}).json()["ref"]
    api("PATCH", f"/records/rfq/{a}", json={"field": "owner_email", "text": me})
    rows = api("GET", "/book", params={"area": "orders", "kind": "rfq", "sort": "customer", "q": f"Sort {TAG}"}).json()["rows"]
    assert [r["cells"]["customer"]["value"] for r in rows][:2] == ["Alpha Co", "Zeta Co"]
    rows = api("GET", "/book", params={"area": "orders", "kind": "rfq", "sort": "customer", "desc": True, "q": f"Sort {TAG}"}).json()["rows"]
    assert rows[0]["cells"]["customer"]["value"] == "Zeta Co"
    mine = api("GET", "/book", params={"area": "orders", "kind": "rfq", "mine": True}).json()
    assert a in [r["ref"] for r in mine["rows"]] and b not in [r["ref"] for r in mine["rows"]] and all(r["needs_me"] for r in mine["rows"]) and mine["needs_me"] == mine["count"]
    flagged = api("GET", "/book", params={"area": "orders", "kind": "rfq", "q": a}).json()["rows"][0]
    assert flagged["needs_me"] is True
    api("GET", "/book", params={"area": "orders", "kind": "rfq", "sort": "banana"}, expect=400)
    assert api("GET", "/book", params={"area": "orders", "kind": "rfq", "status": "zzzz"}).json()["count"] == 0


# ---------------------------------------------------------------- before/after and undo for record edits
def test_a_record_edit_keeps_old_and_new_in_the_audit_trail_and_my_last_edit_can_be_undone(api):
    import json

    from app.models.db_models import AuditLogDB

    ref = api("POST", "/create/rfq", json={"customer": "Owens Corning Starr Composites", "subject": f"Undo {TAG}{uuid.uuid4().hex[:3]}"}).json()["ref"]
    api("PATCH", f"/records/rfq/{ref}", json={"field": "complexity", "text": "comp"})
    api("PATCH", f"/records/rfq/{ref}", json={"field": "contact_email", "text": "buyer@example.test"})
    with SessionLocal() as db:
        rows = [json.loads(a.detail) for a in db.query(AuditLogDB).filter(AuditLogDB.action == "RECORD_FIELD_CHANGED", AuditLogDB.resource_id == ref).order_by(AuditLogDB.id)]
    assert rows[0] == {"field": "complexity", "label": "Complexity", "before": "STANDARD", "after": "COMPLEX"} and rows[1]["field"] == "contact_email" and rows[1]["after"] == "buyer@example.test"
    api("PATCH", f"/records/rfq/{ref}", json={"field": "notes", "text": "first note"})
    api("PATCH", f"/records/rfq/{ref}", json={"field": "notes", "text": "second note"})
    r = api("POST", "/records/undo").json()
    assert r["undone"] and r["field"] == "notes" and r["restored"] == "first note"
    assert {f["field"]: f["value"] for f in api("GET", f"/records/rfq/{ref}/fields").json()["fields"]}["notes"] == "first note"
    again = api("POST", "/records/undo", expect=400).json()["detail"]                              # the one before that: the first note was blank before
    assert "blank before" in again


def test_undo_refuses_when_someone_changed_it_since_or_it_is_locked_or_there_is_nothing(api):
    ref = api("POST", "/create/rfq", json={"customer": "Owens Corning Starr Composites", "subject": f"Undo2 {TAG}{uuid.uuid4().hex[:3]}"}).json()["ref"]
    api("PATCH", f"/records/rfq/{ref}", json={"field": "complexity", "text": "comp"})
    api("PATCH", f"/records/rfq/{ref}", role="MANAGER", json={"field": "complexity", "text": "simple"})            # someone else changed it afterwards
    r = api("POST", "/records/undo", expect=400).json()["detail"]
    assert "changed since" in r
    api("POST", "/records/undo", role="VIEWER", expect=403)
    fresh = f"nobody-{TAG}"
    with SessionLocal() as db:
        from app.models.db_models import AuditLogDB

        mine = db.query(AuditLogDB).filter(AuditLogDB.action == "RECORD_FIELD_CHANGED", AuditLogDB.actor_email == f"ct-admin-{TAG}@alliedweldingsystems.com").count()
    assert mine == 0
    api("POST", "/records/undo", role="ADMIN", expect=404)                                                         # an account with no edits has nothing to undo
