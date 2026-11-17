"""Adversarial round 5: label-shape heuristics, create-item shapes, date/number cells, malformed copilot/voice input, packet confirm and regex timing. Fakes and temporary database only."""
import time

import pytest

from app.services import monday_push
from app.services.assistant import agent, board_tools, voice
from app.services.coordination import boards, jobs, packets
from tests.test_adversary_copilot import _board, _item, live, only  # noqa: F401
from tests.test_board_changes import OP, FakeMonday, _empty_staging  # noqa: F401

NL = chr(10)


def _status_board(labels, extra=None):
    cols = [{"id": "status", "title": "Status", "type": "status", "labels": labels}, {"id": "notes", "title": "Notes", "type": "text"}] + (extra or [])
    return _board("Open RFQs", [{"id": "1", "name": "HECLA", "values": {"status": "", "notes": "old"}}], cols)


# ============================================================ label shape heuristics

@pytest.mark.parametrize("labels,good", [({"0": "Red", "1": "Green", "2": "Blue"}, "Red"), ({"0": "Steel", "1": "Gold", "2": "Silver"}, "Steel")])
def test_an_index_board_with_colour_word_labels_still_takes_its_labels(only, labels, good):
    a = _status_board(labels)
    assert monday_push.stage_edit(a, "1", {"Status": good}, OP)
    with pytest.raises(ValueError):
        monday_push.stage_edit(a, "1", {"Status": "1"}, OP)


def test_numeric_labels_with_less_common_monday_colours_are_labels(only):
    a = _status_board({"1": "grass-green", "2": "bright-blue", "3": "dark-purple"})
    assert monday_push.stage_edit(a, "1", {"Status": "1"}, OP)


def test_common_label_shapes_still_work(only):
    for labels, good, bad in (({"Stuck": "", "Done": ""}, "done", "1"), ({"0": "Working on it", "1": "Done"}, "Done", "1"), ({"Done": "done-green", "Stuck": "stuck-red"}, "stuck", "done-green"),
                              ({"1": "red", "2": "green"}, "1", "red")):
        a = _status_board(labels)
        assert monday_push.stage_edit(a, "1", {"Status": good}, OP)
        with pytest.raises(ValueError):
            monday_push.stage_edit(a, "1", {"Status": bad}, OP)


# ============================================================ create-item and date/number cells

PEOPLE = [{"id": "eng", "title": "Engineer", "type": "people"}]


def test_a_new_row_value_that_cannot_be_sent_is_refused_too(live, only):
    a = _status_board({"Stuck": "", "Done": ""}, PEOPLE)
    with pytest.raises(ValueError):
        monday_push.stage_new_item(a, "NEW ROW", {"Engineer": "Paul Wang"}, OP)


def test_the_copilot_refuses_an_unsendable_edit_before_the_confirm_card(only):
    _status_board({"Stuck": "", "Done": ""}, PEOPLE)
    with pytest.raises(ValueError):
        board_tools._check_edit({"board": "Open RFQs", "item_id": "1", "column": "Engineer", "value": "Paul Wang"}, OP)


@pytest.mark.parametrize("ctype,value", [("date", "next Friday"), ("date", "2026-13-45"), ("numbers", "abc"), ("numbers", "1,2,3")])
def test_date_and_number_cells_are_checked_when_staged(only, ctype, value):
    cols = [{"id": "c1", "title": "Cell", "type": ctype}]
    a = _board("Open RFQs", [{"id": "1", "name": "HECLA", "values": {}}], cols)
    with pytest.raises(ValueError):
        monday_push.stage_edit(a, "1", {"Cell": value}, OP)


def test_valid_dates_and_numbers_are_staged(only):
    for ctype, value in (("date", "2026-11-20"), ("numbers", "12.5"), ("numbers", 7)):
        cols = [{"id": "c1", "title": "Cell", "type": ctype}]
        a = _board("Open RFQs", [{"id": "1", "name": "HECLA", "values": {}}], cols)
        assert monday_push.stage_edit(a, "1", {"Cell": value}, OP)


def test_a_new_row_with_a_label_renamed_in_case_is_sent_in_the_new_spelling(live, only):
    a = _status_board({"Stuck": "", "Done": ""})
    monday_push.stage_new_item(a, "NEW ROW", {"Status": "Done"}, OP)
    boards._mirror[a]["columns"][0]["labels"] = {"Stuck": "", "DONE": ""}
    monday_push.push(who="boss")
    assert live.sent and live.sent[0][3]["status"] == {"label": "DONE"}


# ============================================================ malformed copilot / voice input

@pytest.mark.parametrize("bad", ["hello", None, 5, ["a"]])
def test_malformed_history_entries_are_ignored(bad):
    try:
        voice.command(OP, transcript="what is the status", messages=[bad, {"role": "user", "content": "x"}])
    except AttributeError:
        raise
    except Exception:  # noqa: BLE001 - the AI being off is fine; a crash on the shape is not
        pass


def test_a_huge_confirm_list_is_refused_quickly():
    t = time.time()
    try:
        voice.command(OP, transcript="yes", confirm_ids=[f"id{i}" for i in range(20000)])
    except ValueError:
        return
    assert time.time() - t < 1.0


def test_confirm_ids_as_a_string_do_not_confirm_by_character():
    out = voice.command(OP, transcript="yes", confirm_ids="abc")
    assert all(r["status"] == "SKIPPED" for r in out["tool_actions_executed"])


def test_a_megabyte_transcript_is_not_a_confirmation_and_is_handled():
    t = time.time()
    assert voice.voice_intent("yes " * 300000) is None
    assert time.time() - t < 2


# ============================================================ packet confirm and timing

def _doc():
    from app.core.database import SessionLocal
    from app.models.db_models import JobDocumentDB
    import uuid
    with SessionLocal() as db:
        r = JobDocumentDB(job_id=None, kind="vendor_quote", label="Vendor quotation", page_from=1, page_to=1, fields={"total": "10.00"}, source_sha=uuid.uuid4().hex)
        db.add(r)
        db.commit()
        return r.id


def test_confirm_refuses_a_valid_until_that_is_not_a_date():
    with pytest.raises(jobs.JobError):
        packets.confirm(_doc(), {"valid_until": "sometime soon"}, {"email": "boss@x.com", "role": "MANAGER"})


def test_confirm_keeps_the_currency_typed_with_the_total():
    out = packets.confirm(_doc(), {"total": "EUR 5,000.00"}, {"email": "boss@x.com", "role": "MANAGER"})
    f = out["fields"]
    assert f.get("currency") in ("EUR",) or "EUR" in str(f.get("total"))


def test_confirm_refuses_odd_totals_and_unknown_keys():
    for k, v in (("total", "1e999"), ("total", "-5"), ("total", "nan"), ("total", "1.234,50"), ("date", "13/45/26"), ("po_number", "<script>"), ("__class__", "x"), ("password", "x")):
        with pytest.raises((jobs.JobError, LookupError)):
            packets.confirm(_doc(), {k: v}, {"email": "boss@x.com", "role": "MANAGER"})


@pytest.mark.parametrize("kind,key", [("allied_po", "Allied Welding Systems" + NL + "purchase order no"), ("allied_po", "Allied Welding Systems date issued"),
                                      ("allied_quote", "Allied Welding Systems Quotation" + NL + "quote number:"), ("allied_quote", "Allied Welding Systems Quotation" + NL + "valid until"),
                                      ("vendor_quote", "Quotation" + NL + "order number:"), ("vendor_quote", "Quotation" + NL + "order date:"),
                                      ("customer_po", "purchase order number"), ("customer_po", "date"), ("allied_so", "sales order number:"), ("packing_list", "sales order date:")])
@pytest.mark.parametrize("filler", [" ", NL, " " + NL, " \t:#"])
def test_no_extractor_backtracks_on_long_filler_after_its_label(kind, key, filler):
    text = key + filler * 15000 + "x"
    t = time.time()
    packets.classify(text)
    packets.extract(kind, text)
    assert time.time() - t < 1.5
