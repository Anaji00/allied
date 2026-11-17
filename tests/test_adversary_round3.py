"""Adversarial round 3: the round-2 fixes themselves, odd label sets, lookalike folding, total layouts, cross-feature combinations. Fakes and temporary database only."""
import uuid

import pytest

from app.services import monday_push
from app.services.assistant import agent, board_tools, tools, voice
from app.services.coordination import boards, jobs, packets, purchase_orders
from tests.test_adversary_copilot import _board, _item, live, only  # noqa: F401
from tests.test_adversary_po import BOSS, _age, _approved_po, _args, _job, mail  # noqa: F401
from tests.test_board_changes import OP, FakeMonday, _empty_staging  # noqa: F401

VQ = "QUOTATION\nVendor Co\nOrder Number: 12345\n"


# ============================================================ PO claim token and the address

def test_a_vendor_who_already_has_the_po_is_not_sent_it_again_after_a_reopen():
    po, _ = _approved_po()
    a = purchase_orders.begin_send(po["id"])
    _age(po["id"], 11)
    purchase_orders.resolve_send(po["id"], False, BOSS)
    b = purchase_orders.begin_send(po["id"])
    purchase_orders.finish_send(po["id"], True, a)           # send A's mail did go
    purchase_orders.finish_send(po["id"], False, b)          # send B failed
    assert purchase_orders.get(po["id"])["status"] != "APPROVED"


def test_a_stale_token_cannot_settle_a_newer_claim_and_the_right_token_can():
    po, _ = _approved_po()
    a = purchase_orders.begin_send(po["id"])
    _age(po["id"], 11)
    purchase_orders.resolve_send(po["id"], False, BOSS)
    b = purchase_orders.begin_send(po["id"])
    purchase_orders.finish_send(po["id"], False, a)
    assert purchase_orders.get(po["id"])["status"] == "SENDING"
    purchase_orders.finish_send(po["id"], True, b)
    assert purchase_orders.get(po["id"])["status"] == "SENT"


def test_a_draft_that_names_another_address_than_the_record_is_refused_not_redirected(mail):
    po, _ = _approved_po()
    purchase_orders.update(po["id"], {"vendor_email": "corrected@vendor-example.com"}, BOSS)
    with pytest.raises(ValueError, match="draft it again"):
        tools._send_vendor_po(_args(po, to="attacker@example.com"), BOSS)
    assert mail.sent == [] and purchase_orders.get(po["id"])["status"] == "APPROVED"
    tools._send_vendor_po(_args(po, to="Corrected@Vendor-Example.com"), BOSS)
    assert mail.sent[0]["to"] == "corrected@vendor-example.com"


def test_a_failed_send_with_a_stale_token_leaves_the_po_sendable_exactly_once(mail):
    po, _ = _approved_po()
    mail.boom = True
    with pytest.raises(RuntimeError):
        tools._send_vendor_po(_args(po), BOSS)
    mail.boom = False
    tools._send_vendor_po(_args(po), BOSS)
    with pytest.raises(ValueError):
        tools._send_vendor_po(_args(po), BOSS)
    assert len(mail.sent) == 1


@pytest.mark.parametrize("typed", ["PO# {n} (rev 2)", "0{n}", "{n}/A"])
def test_a_number_buried_in_text_or_padded_is_still_taken(typed):
    from app.services.sage_sdk_writer import SageSDKWriter
    from app.services.coordination import vendors
    SageSDKWriter.reset_sequences()
    n = int(SageSDKWriter._next_number("PO")) + 1
    vendors.create({"vendor": "TYPED BY HAND", "description": "x", "po_number": typed.format(n=n), "ordered_on": "2026-11-01", "status": "WAITING ON PI"}, BOSS)
    po, _ = _approved_po("HELEN")
    assert po["po_number"] != str(n)


def test_a_customer_po_number_equal_to_the_next_number_is_skipped():
    from app.core.database import SessionLocal
    from app.models.db_models import OrderDB
    from app.services.sage_sdk_writer import SageSDKWriter
    SageSDKWriter.reset_sequences()
    n = int(SageSDKWriter._next_number("PO")) + 1
    with SessionLocal() as db:
        db.add(OrderDB(order_id=f"O{uuid.uuid4().hex[:8]}", customer_po_number=str(n), sales_order_number=f"8{uuid.uuid4().int % 10**5}", customer_id="C",
                       customer_name="Z", status="NEW"))
        db.commit()
    po, _ = _approved_po("HELEN")
    assert po["po_number"] != str(n)


# ============================================================ vendor-name folding: real vendors merged, lookalikes not folded

def test_a_decimal_point_in_a_name_is_not_a_single_letter_abbreviation():
    assert jobs.norm_name("Grade 4.5 Steel") != jobs.norm_name("Grade 45 Steel")


def test_corporate_words_only_count_at_the_end_and_never_empty_a_name():
    assert jobs.norm_name("Co-Steel Industries") != jobs.norm_name("Steel Industries")
    assert jobs.norm_name("LLC") != jobs.norm_name("Inc") or jobs.norm_name("LLC") != ""


def test_greek_capital_lookalikes_fold_to_latin():
    assert jobs.norm_name("ΚΕΝ") == jobs.norm_name("KEN")


@pytest.mark.parametrize("fake", ["ѕhanghai", "Sһanghai"])
def test_more_cyrillic_lookalikes_fold(fake):
    assert jobs.norm_name(fake + " Steel") == jobs.norm_name("Shanghai Steel")


def test_two_real_vendors_with_one_word_difference_stay_separate():
    for a, b in (("Shanghai Steel", "Shanghai Steel Works"), ("KWS Ltd", "KWS Metals"), ("Zibo Ceramic Co", "Zibo Ceramics Co"), ("HELEN", "HELEN Ceramics")):
        assert jobs.norm_name(a) != jobs.norm_name(b)


def test_known_aliases_still_fold():
    assert jobs.norm_name("K.W.S. Ltd.") == jobs.norm_name("kws limited") == "kws"
    assert jobs.norm_name("КWS") == "kws"


# ============================================================ voice

@pytest.mark.parametrize("tool", ["approve_po", "send_vendor_po", "create_sales_order", "approve_design"])
def test_money_actions_need_more_than_a_bare_yes_by_voice(tool):
    assert voice._need_phrase(tool, "okay") is not None


@pytest.mark.parametrize("said", ["yes but no", "vâng nhưng không", "yes, wait", "ok, hold on a second", "ok " * 20, "yes " * 15, "好的但是不要",
                                  "yes don’t", "yes dôn't", "oui mais non", "yes​ but no"])
def test_rambles_and_mixed_language_hedges_never_confirm(said):
    assert voice.voice_intent(said) != "confirm"


def test_voice_stop_while_a_po_confirm_waits_cancels_it_and_leaves_the_po_alone():
    _, j = _job()
    po = purchase_orders.create(j["id"], "STOPCO", BOSS)
    aid = agent._propose(tools.TOOLS["approve_po"], {"po_id": po["id"]}, BOSS)["id"]
    out = voice.command(BOSS, transcript="stop", confirm_ids=[aid])
    assert out["voice_intent"] == "cancel"
    assert purchase_orders.get(po["id"])["status"] == "DRAFT"
    again = voice.command(BOSS, transcript="yes", confirm_ids=[aid])                       # a stale yes after the stop
    assert purchase_orders.get(po["id"])["status"] == "DRAFT" and again["tool_actions_executed"][0]["status"] == "SKIPPED"


def test_voice_yes_for_a_po_whose_design_is_not_approved_fails_plainly():
    _, j = _job(approved=False)
    po = purchase_orders.create(j["id"], "NODESIGN", BOSS)
    aid = agent._propose(tools.TOOLS["approve_po"], {"po_id": po["id"]}, BOSS)["id"]
    out = voice.command(BOSS, transcript="yes", confirm_ids=[aid])
    assert purchase_orders.get(po["id"])["status"] == "DRAFT" and out["tool_actions_executed"][0]["status"] in ("FAILED", "SKIPPED")


# ============================================================ monday: odd label sets, renamed labels, own edits

@pytest.mark.parametrize("labels", [["Done", "Stuck"], "Done,Stuck", 5])
def test_an_odd_label_container_does_not_crash_staging(only, labels):
    cols = [{"id": "status", "title": "Status", "type": "status", "labels": labels}, {"id": "notes", "title": "Notes", "type": "text"}]
    a = _board("Open RFQs", [{"id": "1", "name": "HECLA", "values": {}}], cols)
    try:
        monday_push.stage_edit(a, "1", {"Status": "Done"}, OP)
    except ValueError:
        pass


def test_a_label_index_is_not_a_label(only):
    cols = [{"id": "status", "title": "Status", "type": "status", "labels": {"0": "Working on it", "1": "Done"}}]
    a = _board("Open RFQs", [{"id": "1", "name": "HECLA", "values": {}}], cols)
    cid = None
    try:
        cid = monday_push.stage_edit(a, "1", {"Status": "1"}, OP)
    except ValueError:
        return
    assert next(c for c in monday_push.changes("STAGED") if c["id"] == cid)["payload"]["columns"]["status"] == "Done"


def test_label_sets_with_odd_entries_still_check_and_never_crash(only):
    cols = [{"id": "status", "title": "Status", "type": "status", "labels": {"": "", "Done": None, "Stuck": "", "  Spaced  ": "", "Été": ""}}]
    a = _board("Open RFQs", [{"id": "1", "name": "HECLA", "values": {}}], cols)
    assert monday_push.stage_edit(a, "1", {"Status": "done"}, OP)
    with pytest.raises(ValueError):
        monday_push.stage_edit(a, "1", {"Status": "Nope"}, OP)


def test_a_status_with_hundreds_of_labels_is_checked_fast(only):
    import time
    cols = [{"id": "status", "title": "Status", "type": "status", "labels": {f"Label {i}": "" for i in range(5000)}}]
    a = _board("Open RFQs", [{"id": "1", "name": "HECLA", "values": {}}], cols)
    t = time.time()
    for _ in range(20):
        monday_push.stage_edit(a, "1", {"Status": "label 4999"}, OP)
    assert time.time() - t < 3


def test_a_label_renamed_after_staging_is_not_pushed_as_the_old_label(live, only):
    cols = [{"id": "status", "title": "Status", "type": "status", "labels": {"Stuck": "", "Done": ""}}]
    a = _board("Open RFQs", [{"id": "1", "name": "HECLA", "values": {"status": "Stuck"}}], cols)
    monday_push.stage_edit(a, "1", {"Status": "Done"}, OP)
    live.live_values = {("1", "status"): "Stuck"}
    boards._mirror[a]["columns"][0]["labels"] = {"Stuck": "", "Complete": ""}           # the board was read again: 'Done' is gone
    monday_push.push(who="boss")
    assert live.sent == [] or live.sent[0][3].get("status", {}).get("label") != "Done"


def test_a_second_edit_after_a_push_does_not_conflict_with_the_first(live, only):
    a = _board("Open RFQs", [_item(1, "HECLA", notes="old")])
    monday_push.stage_edit(a, "1", {"Notes": "first"}, OP)
    live.live_values = {("1", "notes"): "old"}
    assert monday_push.push(who="boss")["pushed"] == 1
    live.live_values = {("1", "notes"): "first"}                          # monday now shows what this app just wrote
    monday_push.stage_edit(a, "1", {"Notes": "second"}, OP)
    assert monday_push.push(who="boss")["conflicts"] == 0


def test_the_board_choice_message_stays_short_with_many_boards(only):
    for i in range(300):
        _board(f"Customer Orders Archive Board Number {i} for Allied", [_item(1, "A")])
    with pytest.raises(ValueError) as exc:
        board_tools.resolve_board("orders")
    assert len(str(exc.value)) < 1500


def test_tool_specs_for_a_wide_question_stay_within_a_sane_size():
    import json
    u = {"email": "adm@x.com", "role": "ADMIN"}
    t = tools.for_message(u, "approve the design job, edit the board row cell, send email about vendor rfq shipment mtr lead exception audit note automation spec")
    assert len(json.dumps([x.spec() for x in t.values()])) < 30000


# ============================================================ total reader: odd layouts

def test_amount_due_after_a_deposit_is_not_the_document_total():
    page = VQ + "Grand Total 1,000.00\nDeposit received 600.00\nAmount Due 400.00"
    assert packets.extract("vendor_quote", page)["total"] == "1,000.00"


def test_the_currency_next_to_the_amount_wins_not_any_code_on_the_line():
    got = packets.extract("vendor_quote", VQ + "Total: $1,000.00 (approx. CNY 7,200.00)")
    assert got["total"] == "1,000.00" and got.get("currency") in ("$", "USD", None)


@pytest.mark.parametrize("line", ["Total Tax Inclusive: 1,180.00", "Total (incl. deposit): 1,180.00"])
def test_a_total_whose_label_mentions_tax_or_deposit_is_still_found(line):
    assert packets.extract("vendor_quote", VQ + line).get("total") == "1,180.00"


@pytest.mark.parametrize("page,want", [("Total\n1,000.00", None), ("Total:\t1,000.00", "1,000.00"), ("TOTAL USD1,000.00", "1,000.00"), ("Totals 1,000.00", None),
                                       ("Totally free 5.00", None), ("Total 1,000.00 Total 2,000.00", "1,000.00"), ("Total: 1000.00", "1000.00"), ("Total: 1,000.0", None)])
def test_odd_total_layouts_never_give_a_wrong_number(page, want):
    got = packets.extract("vendor_quote", VQ + page).get("total")
    assert got == want or got is None
