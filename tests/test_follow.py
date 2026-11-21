"""Follow and "changed since I looked": own marks only, roles, lists, my-day row. Temporary database."""
import time
import uuid

import pytest
from fastapi.testclient import TestClient

from tests.conftest import staff_token
from tests.test_board_edit import OP, TODAY, _clean, board  # noqa: F401
from tests.test_contract_screens import CUSTOMER

TAG = uuid.uuid4().hex[:5]


@pytest.fixture()
def api(auth_enforced):
    from app.main import app

    client = TestClient(app, raise_server_exceptions=False)
    heads = {r: {"Authorization": "Bearer " + staff_token(f"fl-{r.lower()}-{TAG}@alliedweldingsystems.com", r)} for r in ("MANAGER", "OPERATOR", "VIEWER")}
    other = {"Authorization": "Bearer " + staff_token(f"fl-other-{TAG}@alliedweldingsystems.com", "OPERATOR")}

    def call(method, path, role="OPERATOR", expect=200, who=None, **kw):
        r = client.request(method, "/api/v1" + path, headers=who or heads[role], **kw)
        assert r.status_code == expect, f"{method} {path} as {role}: {r.status_code} {r.text[:300]}"
        return r.json()

    call.other = other
    return call


def _job(api):
    rfq = api("POST", "/create/rfq", json={"customer": CUSTOMER, "subject": f"Follow {TAG}{uuid.uuid4().hex[:3]}"})
    return api("POST", f"/jobs/from-rfq/{rfq['id']}")["ref"]


def test_follow_changed_since_seen_and_one_persons_marks_never_touch_another(api):
    ref = _job(api)
    body = {"item_kind": "job", "item_ref": ref}
    assert api("POST", "/follow", json=body)["followed"] is True
    api("POST", "/follow", json=body)                                                     # a double click is fine
    assert api("GET", "/following")["count"] == 1 and api("GET", "/following")["items"][0]["changed_since_seen"] is False
    time.sleep(1.1)
    api("POST", f"/actions/job/{ref}/share", json={"body": f"news {TAG}"}, role="MANAGER")          # a change after the follow
    api("POST", f"/jobs/{ref}/design/approve", role="MANAGER")
    f = api("GET", "/following")
    assert f["changed"] == 1 and f["items"][0]["changed_since_seen"] and f["items"][0]["changed_at"]
    row = next(r for r in api("GET", "/my-day")["items"] if r["kind"] == "follow")
    assert "changed since you looked" in row["sentence"] and row["primary_action"]["label"] == "Mark seen" and row["primary_action"]["path"] == "/api/v1/seen" and row["primary_action"]["body"] == body
    lst = next(j for j in api("GET", "/jobs", params={"q": ref})["items"] if j["ref"] == ref)
    assert lst["followed"] and lst["changed_since_seen"] and lst["changed_at"]
    assert api("GET", "/following", who=api.other)["count"] == 0                           # nobody else's list
    assert not next(j for j in api("GET", "/jobs", params={"q": ref}, who=api.other)["items"] if j["ref"] == ref)["followed"]
    api("POST", "/seen", json={"item_kind": "job", "item_ref": ref}, who=api.other)        # another person looking changes nothing for me
    assert api("GET", "/following")["changed"] == 1
    assert api("POST", "/seen", json=body)["changed_since_seen"] is False
    assert api("GET", "/following")["changed"] == 0 and not any(r["kind"] == "follow" for r in api("GET", "/my-day")["items"])
    api("POST", "/unfollow", json=body)
    assert api("GET", "/following")["count"] == 0


def test_roles_and_bad_input_get_plain_errors(api):
    ref = _job(api)
    assert api("POST", "/follow", json={"item_kind": "job", "item_ref": ref}, role="VIEWER")["followed"]            # a viewer can open the job page
    assert "Only staff" in api("POST", "/follow", json={"item_kind": "order", "item_ref": "1"}, role="VIEWER", expect=403)["detail"]
    assert "Only staff" in api("POST", "/follow", json={"item_kind": "board_item", "item_ref": "1:2"}, role="VIEWER", expect=403)["detail"]
    api("POST", "/follow", json={"item_kind": "nonsense", "item_ref": "x"}, expect=422)
    api("POST", "/follow", json={"item_kind": "job", "item_ref": "JOB-9999-9999"}, expect=404)
    api("POST", "/follow", json={"item_kind": "job", "item_ref": ""}, expect=422)


def test_a_board_row_can_be_followed_and_its_staged_edit_counts_as_a_change(api, board):
    from app.services import board_edit

    ref = f"{board}:1"
    api("POST", "/follow", json={"item_kind": "board_item", "item_ref": ref})
    time.sleep(1.1)
    board_edit.edit_cell(board, "1", "Status", "done", OP, TODAY)
    rows = api("GET", f"/board-edit/boards/{board}/items")["items"]
    one = next(r for r in rows if r["id"] == "1")
    two = next(r for r in rows if r["id"] == "2")
    assert one["followed"] and one["changed_since_seen"] and one["changed_at"] and not two["followed"] and not two["changed_since_seen"]
    assert api("GET", "/following")["items"][0]["title"].startswith("")
