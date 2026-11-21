"""Organizing data beside the records: spellings and families, tags, vendor contacts, owners, pinned views. New tables only; the records themselves are never changed. Temporary database."""
import uuid

import pytest
from fastapi.testclient import TestClient

from app.core.database import SessionLocal
from tests.conftest import staff_token

TAG = uuid.uuid4().hex[:5]


@pytest.fixture()
def api(auth_enforced):
    from app.main import app

    client = TestClient(app, raise_server_exceptions=False)
    heads = {r: {"Authorization": "Bearer " + staff_token(f"org-{r.lower()}-{TAG}@alliedweldingsystems.com", r)} for r in ("MANAGER", "OPERATOR", "VIEWER")}

    def call(method, path, role="OPERATOR", expect=200, **kw):
        r = client.request(method, "/api/v1" + path, headers=heads[role], **kw)
        assert r.status_code == expect, f"{method} {path} as {role}: {r.status_code} {r.text[:300]}"
        return r.json() if r.content else None

    return call


def test_spellings_resolve_by_the_exact_normalised_name_and_two_companies_are_never_merged(api):
    kc = api("POST", "/aliases", "MANAGER", 201, json={"company": f"CertainTeed {TAG}", "plant": "Kansas City", "family": f"CertainTeed {TAG}"})
    oc = api("POST", "/aliases", "MANAGER", 201, json={"company": f"Owens Corning {TAG}", "plant": "Amarillo", "family": f"Owens Corning {TAG}"})
    for s in (f"CT-KC {TAG}", f"ct kc  {TAG}".upper(), f"CT-Kansas City {TAG}"):
        api("POST", f"/aliases/{kc['id']}/spellings", "MANAGER", 201, json={"spelling": s})
    for s in (f"OC-AMARLLO {TAG}", f"OC-AMARILLO {TAG}"):
        api("POST", f"/aliases/{oc['id']}/spellings", "MANAGER", 201, json={"spelling": s})
    for probe in (f"ct-kc {TAG}", f"CT KC {TAG}", f"ct-kansas city {TAG}"):
        got = api("GET", "/aliases/resolve", params={"name": probe})
        assert got["company"] == f"CertainTeed {TAG}" and got["plant"] == "Kansas City" and got["matched_by"] == "alias"
    assert api("GET", "/aliases/resolve", params={"name": f"oc-amarillo {TAG}"})["company"] == f"Owens Corning {TAG}"
    unknown = api("GET", "/aliases/resolve", params={"name": f"CT-KCX {TAG}"})                      # a near miss is not matched
    assert unknown["company"] is None and unknown["matched_by"] is None
    assert api("GET", "/aliases/resolve", params={"name": f"certainteed {TAG}"})["matched_by"] == "exact name"
    clash = api("POST", f"/aliases/{oc['id']}/spellings", "MANAGER", 409, json={"spelling": f"CT KC {TAG}"})            # already means CertainTeed
    assert "already means CertainTeed" in clash["detail"] and "never merged" in clash["detail"]
    api("POST", f"/aliases/{kc['id']}/spellings", "MANAGER", 201, json={"spelling": f"CT-KC {TAG}"})                    # the same spelling again is fine
    listed = {f["id"]: f for f in api("GET", "/aliases")}
    assert len(listed[kc["id"]]["spellings"]) == 2 and f"OC-AMARILLO {TAG}" in listed[oc["id"]]["spellings"]
    api("POST", "/aliases", "OPERATOR", 403, json={"company": "x"})
    api("GET", "/aliases", "VIEWER", 403)
    api("DELETE", f"/aliases/{kc['id']}/spellings", "MANAGER", params={"spelling": f"ct kc {TAG}"})
    assert api("GET", "/aliases/resolve", params={"name": f"CT-KC {TAG}"})["company"] is None
    api("PATCH", f"/aliases/{oc['id']}", "MANAGER", json={"plant": "Amarillo TX"})
    api("DELETE", f"/aliases/{oc['id']}", "MANAGER")
    assert api("GET", "/aliases/resolve", params={"name": f"OC-AMARILLO {TAG}"})["company"] is None
    api("POST", "/aliases", "MANAGER", 422, json={"company": ""})


def test_tags_are_added_listed_counted_and_removed_without_touching_the_record(api):
    from app.services.coordination import rfqs

    boss = {"email": f"org-t-{TAG}@alliedweldingsystems.com", "role": "MANAGER"}
    r = rfqs.create({"customer": f"Tag Co {TAG}", "subject": "tagged", "items": [{"description": "liner", "quantity": 1}]}, boss, announce=False)
    before = rfqs.get(r["number"])
    got = api("POST", f"/tags/rfq/{r['number']}", json={"tag": "  Blind  SHIP "}, expect=201)
    assert got["tags"] == ["blind ship"]
    api("POST", f"/tags/rfq/{r['number']}", json={"tag": "blind ship"}, expect=201)                                      # twice is once
    api("POST", f"/tags/rfq/{r['number']}", json={"tag": "senior follow"}, expect=201)
    api("POST", "/tags/board_item/123:456", json={"tag": "blind ship"}, expect=201)
    assert api("GET", f"/tags/rfq/{r['number']}")["tags"] == ["blind ship", "senior follow"]
    assert rfqs.get(r["number"]) == before                                                                                  # the record is exactly as it was
    summary = api("GET", "/tags")
    assert any(t["tag"] == "blind ship" and t["count"] >= 2 for t in summary["used"]) and "air freight" in summary["suggested"]
    assert {i["item_ref"] for i in api("GET", "/tags/items", params={"tag": "blind ship"})} >= {r["number"], "123:456"}
    assert api("GET", "/tags/items", params={"tag": "blind ship", "kind": "board_item"})[0]["item_ref"] == "123:456"
    assert api("DELETE", f"/tags/rfq/{r['number']}", params={"tag": "blind ship"})["tags"] == ["senior follow"]
    api("POST", "/tags/nonsense/x", json={"tag": "a"}, expect=422)
    api("POST", f"/tags/rfq/{r['number']}", json={"tag": "x" * 41}, expect=422)
    api("POST", f"/tags/rfq/{r['number']}", "VIEWER", 403, json={"tag": "a"})


def test_vendor_contacts_are_separate_managed_by_managers_and_searchable(api):
    c = api("POST", "/vendor-contacts", "MANAGER", 201, json={"name": f"Li Wei {TAG}", "company": f"Zibo {TAG}", "country": "China", "email": "LI@zibo-example.test", "phone": "+86 1"})
    assert c["email"] == "li@zibo-example.test"
    api("POST", "/vendor-contacts", "OPERATOR", 403, json={"name": "x"})
    api("POST", "/vendor-contacts", "MANAGER", 422, json={"name": "A", "email": "nope"})
    api("POST", "/vendor-contacts", "MANAGER", 422, json={"name": ""})
    assert [x["id"] for x in api("GET", "/vendor-contacts", params={"q": f"zibo {TAG}"})] == [c["id"]]
    assert api("PATCH", f"/vendor-contacts/{c['id']}", "MANAGER", json={"country": "CN"})["country"] == "CN"
    api("GET", "/vendor-contacts", "VIEWER", 403)
    api("DELETE", f"/vendor-contacts/{c['id']}", "MANAGER")
    assert api("GET", "/vendor-contacts", params={"q": f"zibo {TAG}"}) == []


def test_an_owner_and_ball_holder_are_kept_in_the_app_and_one_person_sees_what_they_own(api):
    assert api("GET", "/owners/order/9001") is None
    got = api("PUT", "/owners/order/9001", json={"owner": f"org-operator-{TAG}@alliedweldingsystems.com", "ball_with": "Doug Hamlin", "note": "waiting on capital"})
    assert got["owner"].startswith("org-operator") and got["ball_with"] == "Doug Hamlin"
    assert api("GET", "/owners/order/9001")["note"] == "waiting on capital"
    assert [o["item_ref"] for o in api("GET", "/owners/mine")] == ["9001"]
    api("PUT", "/owners/order/9001", expect=422, json={})
    api("PUT", "/owners/nonsense/1", expect=422, json={"owner": "x"})
    api("DELETE", "/owners/order/9001")
    assert api("GET", "/owners/order/9001") is None
    api("GET", "/owners/order/9001", "VIEWER", 403)


def test_a_saved_view_can_be_pinned_and_unpinned_by_the_person_alone(api):
    v = api("POST", "/workspace/views", "OPERATOR", 201, json={"name": f"Mine {TAG}", "screen": "orders", "filters": {"status": "OPEN"}})
    api("POST", "/workspace/views", "OPERATOR", 201, json={"name": f"Another {TAG}", "screen": "orders", "filters": {}})
    assert not any(x.get("pinned") for x in api("GET", "/workspace/views"))
    assert api("POST", f"/workspace/views/{v['id']}/pin")["pinned"] is True
    listed = [x for x in api("GET", "/workspace/views") if x["name"].endswith(TAG)]
    assert listed[0]["id"] == v["id"] and listed[0]["pinned"] is True and not listed[1]["pinned"]               # pinned first
    api("POST", "/workspace/views/nope/pin", expect=404)
    assert not any(x.get("pinned") for x in api("GET", "/workspace/views", "MANAGER"))                           # someone else's pins are theirs
    assert api("DELETE", f"/workspace/views/{v['id']}/pin")["pinned"] is False
    assert not any(x.get("pinned") for x in api("GET", "/workspace/views"))


def test_board_rows_carry_family_company_plant_and_tags_without_being_changed(api):
    from app.services.coordination import boards

    family = api("POST", "/aliases", "MANAGER", 201, json={"company": f"Board Family {TAG}", "plant": "Kansas City", "family": f"Board Family {TAG}"})
    api("POST", f"/aliases/{family['id']}/spellings", "MANAGER", 201, json={"spelling": f"BF-KC {TAG}"})
    board = str(uuid.uuid4().int % 10**9)
    cols = [{"id": "customer", "title": "Customer", "type": "text"}, {"id": "status", "title": "Status", "type": "text"}]
    items = [{"id": "1", "name": "Job one", "values": {"customer": f"BF-KC {TAG}", "status": "x"}}, {"id": "2", "name": f"BF-KC {TAG}", "values": {"customer": "", "status": "y"}},
             {"id": "3", "name": "Unknown", "values": {"customer": f"Nobody {TAG}", "status": "z"}}]
    with boards._lock:
        boards._mirror[board] = {"board_id": board, "name": "B", "role": "projects", "read_at": "now", "groups": [], "columns": cols, "items": items}
    api("POST", f"/tags/board_item/{board}:1", json={"tag": "blind ship"}, expect=201)
    before = [dict(i) for i in items]
    got = {r["id"]: r for r in api("GET", f"/board-edit/boards/{board}/items")["items"]}
    assert got["1"]["family"] == f"Board Family {TAG}" and got["1"]["plant"] == "Kansas City" and got["1"]["company"] == f"Board Family {TAG}" and got["1"]["tags"] == ["blind ship"]
    assert got["2"]["family"] == f"Board Family {TAG}"                                           # no customer cell: the row's own name is used
    assert got["3"]["family"] is None and got["3"]["tags"] == []                                # an unlisted name is simply unknown
    assert got["1"]["cells"]["customer"]["value"] == f"BF-KC {TAG}" and boards._mirror[board]["items"] == before        # the row and the saved board copy are unchanged
    viewer = api("GET", f"/board-edit/boards/{board}/items", "VIEWER")
    assert "family" not in viewer["items"][0] and "tags" not in viewer["items"][0]                                    # staff only


def test_jobs_board_rows_carry_family_and_the_jobs_own_tags(api):
    from tests.test_contract_screens import CUSTOMER

    fam = api("POST", "/aliases", "MANAGER", 201, json={"company": f"JobsCo {TAG}", "family": f"JobsFam {TAG}"})
    api("POST", f"/aliases/{fam['id']}/spellings", "MANAGER", 201, json={"spelling": f"JC {TAG}"})
    rfq = api("POST", "/create/rfq", "OPERATOR", 200, json={"customer": f"JC {TAG}", "subject": f"Jobs board {TAG}"})
    ref = api("POST", f"/jobs/from-rfq/{rfq['id']}", "OPERATOR", 200)["ref"]
    api("POST", f"/tags/job/{ref}", json={"tag": "air freight"}, expect=201)
    rows = {r["id"]: r for r in api("GET", "/board-edit/boards/jobs/items")["items"]}
    assert rows[ref]["family"] == f"JobsFam {TAG}" and rows[ref]["tags"] == ["air freight"]


def test_before_the_migration_has_run_the_screens_still_work_without_the_new_fields(api, monkeypatch):
    from sqlalchemy.exc import OperationalError

    from app.services import organize

    def missing(*a, **k):
        raise OperationalError("select", {}, Exception("no such table: record_tags"))

    monkeypatch.setattr(organize, "tags_of_many", missing)
    monkeypatch.setattr(organize, "resolver", missing)
    rows = api("GET", "/board-edit/boards/jobs/items")["items"]
    assert isinstance(rows, list) and all("family" not in r for r in rows)
    monkeypatch.undo()
    monkeypatch.setattr(organize, "pinned_ids", lambda e: (_ for _ in ()).throw(OperationalError("select", {}, Exception("no such table: view_pins"))) if False else set())
    assert isinstance(api("GET", "/workspace/views"), list)
