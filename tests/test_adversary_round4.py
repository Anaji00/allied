"""Adversarial round 4: used-number matching, label-name reading, the push-time label recheck and the mirror update after a push. Fakes and temporary database only."""
import pytest

from app.services import monday_push
from app.services.coordination import boards, purchase_orders, vendors
from app.services.sage_sdk_writer import SageSDKWriter
from tests.test_adversary_copilot import _board, _item, live, only  # noqa: F401
from tests.test_adversary_po import BOSS, _approved_po  # noqa: F401
from tests.test_board_changes import OP, FakeMonday, _empty_staging  # noqa: F401


def _status_board(labels, extra=None):
    cols = [{"id": "status", "title": "Status", "type": "status", "labels": labels}, {"id": "notes", "title": "Notes", "type": "text"}] + (extra or [])
    return _board("Open RFQs", [{"id": "1", "name": "HECLA", "values": {"status": "", "notes": "old"}}], cols)


# ============================================================ _used_numbers

@pytest.mark.parametrize("typed", ["５０１２３", "PO ٥٠١٢٣"])
def test_a_po_number_typed_in_other_digits_is_still_taken(typed):
    SageSDKWriter.reset_sequences()
    n = int(SageSDKWriter._next_number("PO")) + 1
    digits = "".join(chr(ord(c) - ord("0") + ord("０")) for c in str(n)) if typed.startswith("５") else "".join(chr(ord(c) - ord("0") + 0x0660) for c in str(n))
    vendors.create({"vendor": "TYPED", "description": "x", "po_number": digits, "ordered_on": "2026-11-01", "status": "WAITING ON PI"}, BOSS)
    po, _ = _approved_po("HELEN")
    assert po["po_number"] != str(n)


def test_a_long_block_of_used_numbers_is_skipped_and_a_retry_works():
    SageSDKWriter.reset_sequences()
    n = int(SageSDKWriter._next_number("PO")) + 1
    for i in range(60):
        vendors.create({"vendor": f"BLOCK {i}", "description": "x", "po_number": str(n + i), "ordered_on": "2026-11-01", "status": "WAITING ON PI"}, BOSS)
    try:
        po, _ = _approved_po("BLOCKED HELEN")
    except Exception:  # noqa: BLE001 - a plain refusal the first time is acceptable...
        po, _ = _approved_po("BLOCKED HELEN 2")                        # ...but the next try must get past the block
    assert int(po["po_number"]) >= n + 60 or int(po["po_number"]) < n


def test_dates_and_short_runs_in_customer_numbers_do_not_exhaust_numbering():
    from app.core.database import SessionLocal
    from app.models.db_models import OrderDB
    import uuid
    with SessionLocal() as db:
        db.add(OrderDB(order_id=f"O{uuid.uuid4().hex[:8]}", customer_po_number="2026-11-14 / 1 / 2 / 3 / 12345678901234567890", sales_order_number=f"7{uuid.uuid4().int % 10**5}",
                       customer_id="C", customer_name="Z", status="NEW"))
        db.commit()
    po, _ = _approved_po("DATES CO")
    assert po["po_number"]


# ============================================================ _label_names

def test_numeric_label_names_with_colours_are_labels(only):
    a = _status_board({"1": "red", "2": "green", "3": "blue"})
    assert monday_push.stage_edit(a, "1", {"Status": "1"}, OP)
    with pytest.raises(ValueError):
        monday_push.stage_edit(a, "1", {"Status": "red"}, OP)


def test_an_empty_label_slot_index_is_not_a_label(only):
    a = _status_board({"0": "Working on it", "1": "Done", "5": ""})
    with pytest.raises(ValueError):
        monday_push.stage_edit(a, "1", {"Status": "5"}, OP)


def test_an_exact_label_beats_a_case_insensitive_one(only):
    a = _status_board({"Done": "", "DONE": ""})
    cid = monday_push.stage_edit(a, "1", {"Status": "DONE"}, OP)
    assert next(c for c in monday_push.changes("STAGED") if c["id"] == cid)["payload"]["columns"]["status"] == "DONE"


def test_unicode_composition_does_not_make_a_valid_label_invalid(only):
    a = _status_board({"Été": ""})
    assert monday_push.stage_edit(a, "1", {"Status": "Été"}, OP)


# ============================================================ the push-time label recheck

def test_a_new_row_with_a_label_removed_since_staging_is_not_sent(live, only):
    a = _status_board({"Stuck": "", "Done": ""})
    monday_push.stage_new_item(a, "NEW ROW", {"Status": "Done"}, OP)
    boards._mirror[a]["columns"][0]["labels"] = {"Stuck": "", "Complete": ""}
    monday_push.push(who="boss")
    assert live.sent == []


def test_a_label_renamed_in_case_is_sent_in_the_boards_new_spelling(live, only):
    a = _status_board({"Stuck": "", "Done": ""})
    monday_push.stage_edit(a, "1", {"Status": "Done"}, OP)
    live.live_values = {("1", "notes"): "old", ("1", "status"): ""}
    boards._mirror[a]["columns"][0]["labels"] = {"Stuck": "", "DONE": ""}
    monday_push.push(who="boss")
    assert live.sent and live.sent[0][3]["status"] == {"label": "DONE"}


def test_a_failed_recheck_leaves_a_retryable_failed_change_and_sends_nothing(live, only):
    a = _status_board({"Stuck": "", "Done": ""})
    cid = monday_push.stage_edit(a, "1", {"Status": "Done"}, OP)
    boards._mirror[a]["columns"][0]["labels"] = {"Stuck": ""}
    out = monday_push.push(who="boss", force=True)
    assert out["failed"] == 1 and live.sent == []
    assert next(c for c in monday_push.changes() if c["id"] == cid)["status"] == "FAILED"
    assert monday_push.skip(cid, "b")["status"] == "SKIPPED"


# ============================================================ the mirror after a push

PEOPLE = [{"id": "eng", "title": "Engineer", "type": "people"}]


def test_a_value_that_cannot_be_sent_is_refused_not_staged_and_mirrored(live, only):
    a = _status_board({"Stuck": "", "Done": ""}, PEOPLE)
    live.live_values = {("1", "notes"): "old", ("1", "eng"): ""}
    try:
        monday_push.stage_edit(a, "1", {"Notes": "x", "Engineer": "Paul Wang"}, OP)
    except ValueError:
        return
    monday_push.push(who="boss")
    sent = live.sent[0][3] if live.sent else {}
    shows = boards._mirror[a]["items"][0]["values"].get("eng")
    assert not (shows == "Paul Wang" and "eng" not in sent)


def test_clearing_a_cell_is_refused_or_really_sent(live, only):
    a = _status_board({"Stuck": "", "Done": ""})
    live.live_values = {("1", "notes"): "old", ("1", "status"): ""}
    try:
        monday_push.stage_edit(a, "1", {"Notes": "", "Status": "Done"}, OP)
    except ValueError:
        return
    monday_push.push(who="boss")
    sent = live.sent[0][3] if live.sent else {}
    shows = boards._mirror[a]["items"][0]["values"].get("notes")
    assert not (shows == "" and "notes" not in sent)


def test_after_a_push_the_mirror_shows_text_not_a_monday_value_dict(live, only):
    a = _status_board({"Stuck": "", "Done": ""})
    monday_push.stage_edit(a, "1", {"Status": "Done"}, OP)
    live.live_values = {("1", "notes"): "old", ("1", "status"): ""}
    assert monday_push.push(who="boss")["pushed"] == 1
    assert boards._mirror[a]["items"][0]["values"]["status"] == "Done"


def test_a_failed_push_does_not_touch_the_mirror(live, only):
    a = _status_board({"Stuck": "", "Done": ""})
    monday_push.stage_edit(a, "1", {"Notes": "mine"}, OP)
    live.live_values = {("1", "notes"): "old"}
    live.update_column_values = lambda *x, **k: False
    assert monday_push.push(who="boss")["failed"] == 1
    assert boards._mirror[a]["items"][0]["values"]["notes"] == "old"


def test_a_conflict_leaves_the_mirror_alone(live, only):
    a = _status_board({"Stuck": "", "Done": ""})
    monday_push.stage_edit(a, "1", {"Notes": "mine"}, OP)
    live.live_values = {("1", "notes"): "theirs"}
    assert monday_push.push(who="boss")["conflicts"] == 1
    assert boards._mirror[a]["items"][0]["values"]["notes"] == "old"


def test_a_push_for_an_item_no_longer_in_the_mirror_does_not_crash(live, only):
    a = _status_board({"Stuck": "", "Done": ""})
    monday_push.stage_edit(a, "1", {"Notes": "mine"}, OP)
    live.live_values = {("1", "notes"): "old"}
    boards._mirror[a]["items"] = []
    assert monday_push.push(who="boss")["pushed"] == 1
