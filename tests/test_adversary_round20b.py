"""Round 20b (adversary): re-attack of the round-20 fixes. Temporary database only. xfail(strict=False, reason="ADV20b-Tn ...") marks open holes."""
import uuid

import pytest

import tests.test_adversary_round20 as r20
from tests.test_adversary_round20 import BOSS, OP, TODAY, board, world, _clean, api  # noqa: F401
from app.core.database import SessionLocal
from app.services import actions, board_edit, create_anything, records
from app.services.coordination import rfqs


def _u(board, col, text):
    return board_edit.understand(board, col, text, TODAY)


def test_t1_a_second_different_rfq_with_the_same_subject_is_not_swallowed():
    sub = f"Liners {uuid.uuid4().hex[:5]}"
    a = create_anything.create("rfq", {"customer": "SameCo", "subject": sub, "items": [{"description": "liner A", "quantity": 1}]}, OP)
    b = create_anything.create("rfq", {"customer": "SameCo", "subject": sub, "items": [{"description": "hose B", "quantity": 9}]}, OP)
    assert a["ref"] != b["ref"] or [i["description"] for i in rfqs.get(a["ref"])["items"]] == ["liner A", "hose B"]


@pytest.mark.parametrize("text", ["1,234,5", "1,23,456", ",5", "1,234.5.6", "12,345.", "1,2345", "12,34", "1 234,50", "1'234.50", "1,,234"])
def test_odd_comma_numbers_are_refused(board, text):
    with pytest.raises(ValueError):
        _u(board, "Qty", text)


@pytest.mark.parametrize("text", ["un done", "isnt done", "not yet done", "unfinished", "disapproved", "non approved", "never approved"])
def test_more_negations_are_not_matched_to_a_label(board, text):
    try:
        got = _u(board, "Status", text)["value"]
    except ValueError:
        return
    assert got not in ("Done", "Approved"), (text, got)


@pytest.mark.parametrize("to", ["x@alliedweldingsystems.com.evil.test", "x@evil.test.alliedweldingsystems.com", "a@alliedweldingsystems.com, b@outside-example.test",
                                "a@alliedweldingsystems.com;b@outside-example.test", "a@allied-weldingsystems.com", "a@alliedweldingsystems.co", "\"a@alliedweldingsystems.com\"@outside-example.test",
                                "a@outside-example.test@alliedweldingsystems.com", "a@alliedweldingsystems.com\nbcc: b@outside-example.test"])
def test_an_internal_draft_to_a_lookalike_or_mixed_address_is_refused(world, to):
    try:
        out = actions.email_draft("po", str(world["po"]["id"]), BOSS, to=to, audience="internal", body="hi")
    except actions.ActionError:
        return
    assert all(a.lower().endswith("@alliedweldingsystems.com") and a.count("@") == 1 for a in out["to"]), out["to"]


def test_an_allied_address_in_any_case_is_still_allowed(world):
    out = actions.email_draft("po", str(world["po"]["id"]), BOSS, to="Someone@AlliedWeldingSystems.com", audience="internal", body="hi")
    assert out["drafted"]


def test_t2_the_rfq_customer_is_locked_once_a_po_has_gone_out(world):
    from app.models.db_models import PurchaseOrderDB

    with SessionLocal() as db:
        db.get(PurchaseOrderDB, world["po"]["id"]).status = "SENT"
        db.commit()
    with pytest.raises(records.RecordError):
        records.patch("rfq", world["rfq"]["number"], "customer", "Another Customer Ltd", BOSS)


def test_parts_cost_edit_by_operator_is_refused_over_http_and_by_copilot(api):
    part = f"B20-{uuid.uuid4().hex[:6].upper()}"
    assert api("POST", "/create/product", "MANAGER", json={"item_id": part, "description": "p", "base_price": "10"}).status_code == 200
    for field in ("base_price", "standard_cost", "preferred_vendor"):
        assert api("PATCH", f"/records/product/{part}", "OPERATOR", json={"field": field, "text": "5"}).status_code == 403
    from app.services.assistant import tools

    with pytest.raises((PermissionError, ValueError)):
        tools.TOOLS["edit_record"].check({"kind": "product", "ref": part, "field": "standard_cost", "text": "5"}, OP)


def test_a_po_number_that_equals_another_pos_id_goes_to_the_number(world):
    po = world["po"]
    assert actions.context("po", str(po["po_number"]))["ref"] == str(po["po_number"])
    assert records._read("po", str(po["id"]))["id"] == po["id"]
