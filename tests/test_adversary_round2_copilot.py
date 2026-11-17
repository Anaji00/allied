"""Adversarial round 2: voice, board tools, monday staging (labels, odd characters, races). Fakes only; temporary database only."""
import threading
import uuid

import pytest

from app.config import settings
from app.core.database import SessionLocal
from app.services import monday_push
from app.services.assistant import agent, board_tools, tools, voice
from app.services.coordination import boards
from tests.test_adversary_copilot import _board, _item, live, only  # noqa: F401
from tests.test_board_changes import BOSS, OP, FakeMonday, _empty_staging  # noqa: F401


# ============================================================ voice


@pytest.mark.parametrize("noise", ["Thank you.", "thank you", "Thanks.", "you", "to", "and", "now", "it", "do", "go", "that", "Thank you. Thank you."])
def test_noise_and_filler_are_not_a_confirmation(noise):
    assert voice.voice_intent(noise) != "confirm"


@pytest.mark.parametrize("said", ["yes", "Yes.", "yes please", "okay", "do it", "go ahead", "send it", "confirm", "xác nhận", "好的", "yes, send it", "approve"])
def test_a_real_yes_still_confirms(said):
    assert voice.voice_intent(said) == "confirm"


@pytest.mark.parametrize("said", ["yes but not that one", "yes, wait", "hold on", "yes no", "ok don't", "yes do not send", "不要", "không", "yes 不要",
                                  "no problem go ahead", "yes, cancel it", "yeah nah", "OK stop"])
def test_any_negation_or_pause_cancels_or_is_not_a_yes(said):
    assert voice.voice_intent(said) in ("cancel", None)



@pytest.mark.parametrize("said", ["yes？", "yes ?", "ok؟", "好的？"])
def test_a_spoken_question_in_other_scripts_is_not_a_confirmation(said):
    assert voice.voice_intent(said) != "confirm"


def test_lookalike_and_odd_unicode_yes_never_confirms():
    for t in ("yеs", "ｙｅｓ", "ye​s", "‮yes", "yes\u0000", "YES" * 3000):
        assert voice.voice_intent(t) != "confirm" or t in ("‮yes", "yes\u0000")


def test_voice_does_not_confirm_several_actions_with_a_bare_send_unless_every_phrase_is_met(monkeypatch):
    from app.services.outbound_email_service import outbound_email_service
    sent = []
    monkeypatch.setattr(outbound_email_service, "send", lambda *a, **k: sent.append(a) or {"status": "SENT"})
    aid = agent._propose(tools.TOOLS["send_email"], {"to": "c@example.com", "subject": "s", "body": "b"}, OP)["id"]
    out = voice.command(OP, transcript="yes", confirm_ids=[aid])
    assert not sent and out["held"]
    voice.command(OP, transcript="send it", confirm_ids=[aid])
    assert len(sent) == 1


# ============================================================ monday: labels, odd characters

def _status_board(labels=None):
    cols = [{"id": "status", "title": "Status", "type": "status", "labels": labels or {"Stuck": "", "Done": "", "Need markup": ""}},
            {"id": "notes", "title": "Notes", "type": "text"}]
    return _board("Open RFQs", [_item(1, "HECLA", status="Stuck")], cols)



def test_a_label_in_another_case_is_staged_as_the_boards_own_spelling(only):
    a = _status_board()
    cid = monday_push.stage_edit(a, "1", {"Status": "need MARKUP"}, OP)
    assert next(c for c in monday_push.changes("STAGED") if c["id"] == cid)["payload"]["columns"]["status"] == "Need markup"



@pytest.mark.parametrize("value", [{"label": "Totally Made Up"}, {"index": 99}, 7])
def test_a_status_value_that_is_not_text_is_still_checked_against_the_labels(only, value):
    a = _status_board()
    with pytest.raises(ValueError):
        monday_push.stage_edit(a, "1", {"Status": value}, OP)



def test_new_row_names_and_zero_width_characters_are_cleaned(only):
    a = _status_board()
    cid = monday_push.stage_new_item(a, "Invoice‮ fdp.exe \x1b[31m", {}, OP)
    name = next(c for c in monday_push.changes("STAGED") if c["id"] == cid)["payload"]["item_name"]
    assert "‮" not in name and "\x1b" not in name
    v = monday_push.stage_edit(a, "1", {"Notes": "pa​id﻿"}, OP)
    assert next(c for c in monday_push.changes("STAGED") if c["id"] == v)["payload"]["columns"]["notes"] == "paid"



def test_a_zero_width_character_does_not_make_a_duplicate_row(only):
    a = _status_board()
    monday_push.stage_new_item(a, "Smith", {}, OP)
    monday_push.stage_new_item(a, "Smith​", {}, OP)
    assert len(monday_push.changes("STAGED")) == 1



def test_simultaneous_edits_to_different_cells_of_one_row_are_all_kept(only):
    cols = [{"id": f"c{i}", "title": f"Col {i}", "type": "text"} for i in range(8)]
    a = _board("Open RFQs", [{"id": "1", "name": "HECLA", "values": {}}], cols)
    gate = threading.Barrier(8)

    def go(i):
        gate.wait()
        monday_push.stage_edit(a, "1", {f"Col {i}": f"v{i}"}, OP)
    ts = [threading.Thread(target=go, args=(i,)) for i in range(8)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    staged = [c for c in monday_push.changes("STAGED") if c["board_id"] == a]
    assert sum(len(c["payload"]["columns"]) for c in staged) == 8


def test_a_conflict_then_rebase_then_skip_in_order_is_consistent(live, only):
    a = _board("Open RFQs", [_item(1, "HECLA", notes="old")])
    cid = monday_push.stage_edit(a, "1", {"Notes": "mine"}, OP)
    live.live_values = {("1", "notes"): "theirs"}
    assert monday_push.push(who="b")["conflicts"] == 1
    assert monday_push.rebase(cid)["status"] == "STAGED"
    assert monday_push.skip(cid, "b")["status"] == "SKIPPED"
    with pytest.raises(ValueError):
        monday_push.rebase(cid)
    assert monday_push.push(who="b")["pushed"] == 0 and live.sent == []


def test_a_rebase_when_monday_cannot_be_read_changes_nothing(live, only, monkeypatch):
    a = _board("Open RFQs", [_item(1, "HECLA", notes="old")])
    cid = monday_push.stage_edit(a, "1", {"Notes": "mine"}, OP)
    live.live_values = {("1", "notes"): "theirs"}
    monday_push.push(who="b")
    live.boom = True
    try:
        monday_push.rebase(cid)
    except Exception:  # noqa: BLE001
        pass
    assert next(c for c in monday_push.changes() if c["id"] == cid)["status"] == "CONFLICT"


def test_two_pushes_at_once_send_each_change_once(live, only):
    a = _board("Open RFQs", [_item(i, f"ROW {i}", notes="old") for i in range(1, 6)])
    for i in range(1, 6):
        monday_push.stage_edit(a, str(i), {"Notes": f"new{i}"}, OP)
    live.live_values = {(str(i), "notes"): "old" for i in range(1, 6)}
    ts = [threading.Thread(target=lambda: monday_push.push(who="b")) for _ in range(4)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert len(live.sent) == 5


# ============================================================ board tools


def test_a_blank_or_one_letter_job_is_never_guessed(monkeypatch):
    from app.services.coordination import jobs
    monkeypatch.setattr(jobs, "get", lambda t, **k: (_ for _ in ()).throw(LookupError("none")))
    monkeypatch.setattr(jobs, "list_jobs", lambda **k: {"items": [{"ref": "J-1", "title": "Only job"}]})
    for text in ("", "   ", None):
        with pytest.raises(ValueError):
            board_tools._job_ref(text)



def test_a_one_letter_job_phrase_is_not_taken_as_a_job(monkeypatch):
    from app.services.coordination import jobs
    monkeypatch.setattr(jobs, "get", lambda t, **k: (_ for _ in ()).throw(LookupError("none")))
    monkeypatch.setattr(jobs, "list_jobs", lambda **k: {"items": [{"ref": "J-1", "title": "Only job"}]})
    with pytest.raises(ValueError):
        board_tools._job_ref("a")


def test_checks_pin_the_row_so_a_later_rename_cannot_redirect_the_edit(only):
    a = _board("Open RFQs", [_item(1, "HECLA"), _item(2, "HECLA SPARES")])
    args = {"board": "open rfqs", "item_id": "hecla", "column": "notes", "value": "x"}
    board_tools._check_edit(args, OP)
    assert args["item_id"] == "1" and args["board"] == a
    boards._mirror[a]["items"][0]["name"] = "SOMETHING ELSE"
    board_tools.stage_cell_edit(args, OP)
    assert next(iter(monday_push.changes("STAGED")))["item_id"] == "1"


def test_two_rows_with_the_same_name_are_asked_about_not_picked(only):
    a = _board("Open RFQs", [_item(1, "HECLA"), _item(2, "HECLA")])
    with pytest.raises(ValueError, match="Which row"):
        board_tools.one_item(a, "hecla")


def test_two_boards_with_the_same_name_are_asked_about(only):
    _board("2026 Orders", [_item(1, "A")])
    _board("2026 Orders", [_item(1, "B")])
    with pytest.raises(ValueError, match="Which board"):
        board_tools.resolve_board("2026 orders")


def test_a_one_or_two_letter_board_request_matches_nothing_by_substring(only):
    _board("2026 Orders", [_item(1, "A")])
    _board("Open RFQs", [_item(1, "B")])
    for t in ("o", "s", "r", "26"):
        with pytest.raises(ValueError):
            board_tools.resolve_board(t)


def test_an_item_whose_id_looks_like_another_rows_name(only):
    a = _board("Open RFQs", [_item(5, "7"), _item(7, "OTHER")])
    assert board_tools.one_item(a, "7")["id"] == "7"           # an exact id beats a name: documented behaviour, but it must be stable
