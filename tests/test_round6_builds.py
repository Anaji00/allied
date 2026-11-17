"""Researcher's three: drawing revision on PO lines, the blind-shipment check on drafts, the second backup copy. Plus the push-time full cell check.
Temporary database, fake mail and fake monday only."""
import sqlite3

import pytest

from app.config import settings
from app.services import backups, monday_push, order_documents
from app.services.coordination import blind, boards, purchase_orders
from tests.test_adversary_copilot import _board, live, only  # noqa: F401
from tests.test_adversary_po import BOSS, _approved_po, _job  # noqa: F401
from tests.test_board_changes import OP  # noqa: F401


# ============================================================ drawing revision as its own field

def test_a_line_keeps_its_drawing_revision_as_its_own_field():
    lines = order_documents.po_lines([{"quantity": 2, "description": "liner", "revision": " B\x00 "}, {"quantity": 1, "description": "plate"}], strict=True)
    assert lines[0]["revision"] == "B" and "revision" not in lines[1]
    assert order_documents.po_lines([{"description": "x", "revision": "R" * 99}])[0]["revision"] == "R" * 20


def test_the_revision_is_on_the_pdf_the_view_and_the_vendor_email():
    _, j = _job()
    po = purchase_orders.create(j["id"], "REVCO", BOSS)
    po = purchase_orders.update(po["id"], {"lines": [{"quantity": 2, "description": "liner", "unit_cost": "5", "revision": "C"}, {"quantity": 1, "description": "plate", "unit_cost": "1"}]}, BOSS)
    assert po["missing_revision"] == [2]                                  # a warning list, never a block
    assert purchase_orders.pdf(po["id"]).startswith(b"%PDF")
    app = purchase_orders.approve(po["id"], BOSS)
    assert app["status"] == "APPROVED"
    from app.models.db_models import AssistantActionDB
    from app.core.database import SessionLocal
    purchase_orders.update(po["id"], {"vendor_email": "rev@vendor-example.com"}, BOSS)
    d = purchase_orders.email_draft(po["id"], BOSS)
    with SessionLocal() as db:
        body = db.get(AssistantActionDB, d["action_id"]).args["body"]
    assert "drawing revision C" in body


def test_the_revision_text_is_drawn_on_the_pdf():
    import fitz
    pdf = order_documents.vendor_po_pdf({"po_number": "1", "vendor_name": "V", "lines": order_documents.po_lines([{"quantity": 1, "description": "liner", "revision": "D"}])})
    text = "".join(p.get_text() for p in fitz.open(stream=pdf, filetype="pdf"))
    assert "DRAWING REV D" in text


# ============================================================ blind shipment

def test_the_check_finds_a_whole_word_name_and_ignores_short_words_and_allieds_own():
    assert blind.found("Parts for Owens Corning, ship Friday", ["Owens Corning", "Zibo Ceramic"]) == ["Owens Corning"]
    assert blind.found("owens corning", ["Owens Corning"]) == ["Owens Corning"]
    assert blind.found("o w e n s  c o r n i n g", ["Owens Corning"]) == ["Owens Corning"]          # spread-out letters are still the name
    assert blind.found("Allied Welding Systems", ["Allied Welding Systems"]) == []
    assert blind.found("the cat", ["cat"]) == []                          # under four letters is not a company name


def test_a_vendor_mail_naming_the_customer_is_warned(monkeypatch):
    monkeypatch.setattr(blind, "customer_names", lambda extra=(): ["Owens Corning"])
    w = blind.check("send_vendor_price_request", {"to": "v@x.com", "body": "This is for Owens Corning", "_attachments": [{"name": "Owens Corning drawing.pdf"}]})
    assert w and "Owens Corning" in w[0]
    assert blind.check("send_vendor_price_request", {"to": "v@x.com", "body": "A price please", "_attachments": [{"name": "dwg.pdf"}]}) == []


def test_a_customer_mail_naming_a_vendor_is_warned_and_a_vendor_mail_is_not(monkeypatch):
    monkeypatch.setattr(blind, "vendor_names", lambda: ["Zibo Ceramic"])
    monkeypatch.setattr(blind, "_is_vendor_address", lambda to: to == "v@zibo.com")
    assert blind.check("send_email", {"to": "buyer@customer.com", "body": "We buy it from Zibo Ceramic"})
    assert blind.check("send_email", {"to": "v@zibo.com", "body": "Zibo Ceramic order"}) == []


def test_a_failed_check_never_stops_a_draft(monkeypatch):
    monkeypatch.setattr(blind, "vendor_names", lambda: 1 / 0)
    assert blind.check("send_email", {"to": "a@b.com", "body": "x"}) == []


def test_a_leaking_draft_is_queued_with_a_warning_and_never_auto(monkeypatch):
    from app.core.database import SessionLocal
    from app.models.db_models import AssistantActionDB
    from app.services.coordination import autopilot
    import uuid

    monkeypatch.setattr(blind, "customer_names", lambda extra=(): ["Owens Corning"])
    key = f"leak-{uuid.uuid4().hex[:6]}"
    it = autopilot.Intent("draft", key, "Ask a vendor", to=[BOSS["email"]], tool="send_vendor_price_request",
                          args={"to": "v@x.com", "vendor_name": "V", "subject": "Price", "body": "For Owens Corning", "_why": ["a reason"]})
    assert autopilot._draft("test", it, auto=True) == "drafted"
    aid = uuid.uuid5(uuid.NAMESPACE_URL, f"allied-autopilot:{key}").hex
    with SessionLocal() as db:
        row = db.get(AssistantActionDB, aid)
        assert row.status == "PENDING" and any("Blind shipment" in w for w in row.args["_why"]) and "a reason" in row.args["_why"]


# ============================================================ second backup copy

@pytest.fixture()
def two_dirs(tmp_path, monkeypatch):
    from app.core.database import init_database

    init_database()
    monkeypatch.setattr(settings, "BACKUP_DIR", str(tmp_path / "bk"))
    monkeypatch.setattr(settings, "BACKUP_COPY_DIR", str(tmp_path / "other"))
    backups.last.clear()
    return tmp_path


def test_a_backup_is_also_copied_and_the_copy_is_checked(two_dirs):
    out = backups.run()
    assert out["ok"] and out["copy"]["ok"] and (two_dirs / "other" / out["file"]).is_file()
    assert not list((two_dirs / "other").glob("*.part"))
    with sqlite3.connect(f"file:{two_dirs / 'other' / out['file']}?mode=ro&immutable=1", uri=True) as c:
        assert c.execute("pragma integrity_check").fetchone()[0] == "ok"
    line = backups.status()["copy"]
    assert line["state"] == "made" and "Second copy: made" in line["text"]


def test_with_no_copy_folder_the_line_says_so_and_the_backup_still_works(tmp_path, monkeypatch):
    from app.core.database import init_database

    init_database()
    monkeypatch.setattr(settings, "BACKUP_DIR", str(tmp_path / "bk"))
    monkeypatch.setattr(settings, "BACKUP_COPY_DIR", "")
    out = backups.run()
    assert out["ok"] and out["copy"] == {"configured": False, "ok": False}
    assert backups.status()["copy"]["state"] == "not_set_up"


def test_a_copy_folder_that_cannot_be_written_is_reported_not_raised(tmp_path, monkeypatch):
    from app.core.database import init_database

    init_database()
    blocker = tmp_path / "file-not-folder"
    blocker.write_text("x")
    monkeypatch.setattr(settings, "BACKUP_DIR", str(tmp_path / "bk"))
    monkeypatch.setattr(settings, "BACKUP_COPY_DIR", str(blocker / "sub"))
    monkeypatch.setattr(backups, "_tell", lambda out: None)
    out = backups.run()
    assert out["ok"] is False and out["copy"]["ok"] is False and "second copy failed" in out["error"]
    assert (tmp_path / "bk" / out["file"]).is_file()                       # the main backup is still there


def test_a_stale_copy_is_called_not_made(two_dirs):
    (two_dirs / "other").mkdir()
    sqlite3.connect(two_dirs / "other" / "allied-20200101-000000.db").close()
    assert backups.copy_line()["state"] == "not_made"


# ============================================================ push-time full check (any staging path, retry, rebase)

def test_a_change_staged_by_other_code_is_fully_checked_when_pushed(live, only):
    cols = [{"id": "c1", "title": "Due", "type": "date"}, {"id": "notes", "title": "Notes", "type": "text"}]
    a = _board("Open RFQs", [{"id": "1", "name": "HECLA", "values": {"notes": "old", "c1": ""}}], cols)
    cid = monday_push.stage("update_columns", a, "1", {"columns": {"c1": "next Friday"}, "base": {"c1": ""}}, "bad date", source="test", by="x")
    out = monday_push.push(ids=[cid], who="boss")                          # only this change: the test database may hold others staged by earlier tests
    assert out["failed"] == 1 and live.sent == []
    assert "date" in next(c for c in monday_push.changes() if c["id"] == cid)["error"].lower()


# ============================================================ the small items from the senior's list

def test_a_currency_typed_on_confirm_must_be_a_known_one_and_a_yen_sign_is_unknown():
    import uuid
    from app.core.database import SessionLocal
    from app.models.db_models import JobDocumentDB
    from app.services.coordination import jobs, packets

    with SessionLocal() as db:
        r = JobDocumentDB(job_id=None, kind="customer_po", label="PO", page_from=1, page_to=1, fields={"total": "10.00"}, source_sha=uuid.uuid4().hex)
        db.add(r)
        db.commit()
        did = r.id
    u = {"email": "boss@x.com", "role": "MANAGER"}
    assert packets.confirm(did, {"currency": "eur"}, u)["fields"]["currency"] == "EUR"
    with pytest.raises(jobs.JobError):
        packets.confirm(did, {"currency": "DOGE"}, u)
    assert "currency" not in packets.confirm(did, {"currency": "", "total": "¥1,000.00"}, u)["fields"]        # a yen sign alone: unknown, never CNY
    assert packets.confirm(did, {"total": "JPY 1,000.00"}, u)["fields"]["currency"] == "JPY"


@pytest.mark.parametrize("line", ["Total (including shipping & handling) 1,250.00", "Total (incl. freight and handling charges) 1,250.00"])
def test_a_total_with_a_long_including_label_is_found(line):
    from app.services.coordination import packets
    assert packets.extract("vendor_quote", "QUOTATION\nVendor Co\nOrder Number: 12345\n" + line).get("total") == "1,250.00"


def test_a_chinese_ending_written_without_a_space_is_the_ending():
    from app.services.coordination import jobs
    assert jobs.norm_name("淄博陶瓷有限公司") == jobs.norm_name("淄博陶瓷 有限公司") == "淄博陶瓷"
    assert jobs.norm_name("公司") == "公司"


def test_an_ambiguous_valid_until_is_flagged_and_only_the_newest_quote_can_expire():
    from datetime import date
    from app.services.coordination import jobs
    out = {c["key"] for c in jobs.job_checks({"docs": [("vendor_quote", {"valid_until": "03/04/26", "vendor": "A", "date": "1/1/26", "total": "1.00"})]}, today=date(2026, 1, 1))}
    assert "vendor_quote_date_unclear" in out
    old_and_new = [("vendor_quote", {"vendor": "A", "date": "1/1/26", "valid_until": "1/31/26", "total": "1.00"}),
                   ("vendor_quote", {"vendor": "A", "date": "3/1/26", "valid_until": "4/30/26", "total": "2.00"})]
    assert "vendor_quote_expired" not in {c["key"] for c in jobs.job_checks({"docs": old_and_new}, today=date(2026, 3, 15))}
    assert "vendor_quote_date_unclear" not in {c["key"] for c in jobs.job_checks({"docs": [("vendor_quote", {"valid_until": "11/25/26", "total": "1"})]}, today=date(2026, 1, 1))}


def test_only_the_stager_or_a_manager_may_skip_a_staged_change(live, only):
    cols = [{"id": "notes", "title": "Notes", "type": "text"}]
    a = _board("Open RFQs", [{"id": "1", "name": "HECLA", "values": {"notes": "old"}}], cols)
    cid = monday_push.stage_edit(a, "1", {"Notes": "x"}, {"email": "ann@x.com", "role": "OPERATOR"})
    with pytest.raises(PermissionError):
        monday_push.skip(cid, "bob@x.com", role="OPERATOR")
    assert monday_push.skip(cid, "ann@x.com", role="OPERATOR")["status"] == "SKIPPED"
    cid2 = monday_push.stage_edit(a, "1", {"Notes": "y"}, {"email": "ann@x.com", "role": "OPERATOR"})
    assert monday_push.skip(cid2, "boss@x.com", role="MANAGER")["status"] == "SKIPPED"


def test_a_dict_value_must_use_a_shape_monday_knows_for_the_column(only):
    for ctype, bad in (("people", {"nonsense": 1}), ("status", {"x": 1}), ("dropdown", {"x": 1}), ("checkbox", {"x": 1})):
        a = _board("Open RFQs", [{"id": "1", "name": "HECLA", "values": {}}], [{"id": "c1", "title": "Cell", "type": ctype}])
        with pytest.raises(ValueError):
            monday_push.check_cell(a, "Cell", bad)
    a = _board("Open RFQs", [{"id": "1", "name": "HECLA", "values": {}}], [{"id": "c1", "title": "Cell", "type": "people"}])
    assert monday_push.check_cell(a, "Cell", {"personsAndTeams": [{"id": 1, "kind": "person"}]})


def test_a_label_message_says_when_the_board_copy_is_old(only):
    from datetime import datetime, timedelta, timezone
    a = _board("Open RFQs", [{"id": "1", "name": "HECLA", "values": {}}], [{"id": "s", "title": "Status", "type": "status", "labels": {"Done": ""}}])
    boards._mirror[a]["read_at"] = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
    with pytest.raises(ValueError, match="refresh the board"):
        monday_push.check_cell(a, "Status", "Brand New")


def test_the_frontend_wording_asks():
    from datetime import date
    from app.services.coordination import ball, jobs, vendors
    assert vendors._nice(date(date.today().year, 11, 8)) == "Nov 8" and vendors._nice(date(2019, 3, 1)) == "Mar 1, 2019"
    assert ball.KINDS["us"] == "us" and ball.KINDS["vendor"] == "a vendor"
    out = {c["key"] for c in jobs.job_checks({"docs": [], "order_status": "SHIPPED", "shipments": [{"status": "BOOKED"}], "purchase_orders": [{"po_number": "5", "vendor": "V", "status": "APPROVED"}]})}
    assert {"po_not_sent", "shipment_still_booked"} <= out
    assert not {c["key"] for c in jobs.job_checks({"docs": [], "shipments": [], "purchase_orders": [{"po_number": "5", "status": "APPROVED"}]})} & {"po_not_sent"}


# ============================================================ the send ledger and the stuck-send alert

def test_every_mail_is_written_to_the_ledger_and_only_a_real_send_counts(monkeypatch):
    import uuid
    from app.core.database import SessionLocal
    from app.models.db_models import OutboundMailDB
    from app.services.outbound_email_service import outbound_email_service as svc

    ref = f"PO-{uuid.uuid4().hex[:6]}"
    monkeypatch.setattr(settings, "EMAIL_REDIRECT_ALL_TO", "")
    svc._ledger({"message_id": f"M{uuid.uuid4().hex[:10]}", "category": "PURCHASE_ORDER", "reference": ref + "-drafted", "to": ["a@b.com"], "intended_to": ["a@b.com"], "status": "DRAFTED"})
    svc._ledger({"message_id": f"M{uuid.uuid4().hex[:10]}", "category": "PURCHASE_ORDER", "reference": ref + "-red", "to": ["t@b.com"], "intended_to": ["a@b.com"], "status": "SENT", "redirected": True})
    svc._ledger({"message_id": f"M{uuid.uuid4().hex[:10]}", "category": "PURCHASE_ORDER", "reference": ref + "-failed", "to": ["a@b.com"], "intended_to": ["a@b.com"], "status": "FAILED"})
    svc._ledger({"message_id": f"M{uuid.uuid4().hex[:10]}", "category": "PURCHASE_ORDER", "reference": ref + "-ok", "to": ["a@b.com"], "intended_to": ["a@b.com"], "status": "SENT"})
    got = {r for r in svc.really_sent("PURCHASE_ORDER") if r.startswith(ref)}
    assert got == {ref + "-ok"}
    with SessionLocal() as db:
        assert db.query(OutboundMailDB).filter(OutboundMailDB.reference.like(ref + "%")).count() == 4


def test_a_ledger_failure_never_breaks_a_send(monkeypatch):
    from app.services.outbound_email_service import outbound_email_service as svc
    monkeypatch.setattr("app.models.db_models.OutboundMailDB", None)          # the write blows up
    out = svc.send(to="someone@example.org", subject="s", body="b", category="GENERAL", reference="X")
    assert out["status"] in ("DRAFTED", "SENT", "FAILED")


def test_a_duplicate_message_id_does_not_raise():
    from app.services.outbound_email_service import outbound_email_service as svc
    e = {"message_id": "MDUP0000000001", "category": "GENERAL", "to": [], "intended_to": [], "status": "DRAFTED"}
    svc._ledger(e)
    svc._ledger(e)


def test_a_po_sending_for_ten_minutes_is_alerted_once_a_day():
    from datetime import datetime, timedelta, timezone
    from app.core.database import SessionLocal
    from app.models.db_models import PurchaseOrderDB
    po, _ = _approved_po()
    purchase_orders.begin_send(po["id"])
    assert po["po_number"] not in {p["po_number"] for p in purchase_orders.stuck_sending()}          # just started: not stuck
    with SessionLocal() as db:
        db.get(PurchaseOrderDB, po["id"]).sent_at = datetime.now(timezone.utc) - timedelta(minutes=11)
        db.commit()
    assert po["po_number"] in {p["po_number"] for p in purchase_orders.stuck_sending()}
    assert purchase_orders.alert_stuck_sends() >= 1


def test_search_finds_jobs_and_a_waiting_draft_past_its_time_is_not_waiting():
    from datetime import timedelta
    from app.core.database import SessionLocal
    from app.models.db_models import AssistantActionDB
    from app.services import search as search_mod
    from app.services.coordination import jobs as jobs_mod, workdays
    import uuid
    r, j = _job()
    out = search_mod.search(j["ref"])
    hit = next(g for g in out["groups"] if g["type"] == "job")["hits"][0]
    assert hit["ref"] == j["ref"] and hit["link"] == f"/jobs/{j['ref']}" and "sales" not in hit["sub"].lower()
    aid = uuid.uuid4().hex
    with SessionLocal() as db:
        db.add(AssistantActionDB(id=aid, user_email=BOSS["email"], user_role="AUTOPILOT", tool="send_email", args={"_job_id": j["id"], "_play": "x", "to": "a@b.com", "body": "b"},
                                 summary="old draft", status="PENDING", created_at=workdays.now() - timedelta(days=30)))
        db.commit()
    waiting = [w for w in jobs_mod.get(j["id"])["waiting"] if w.get("action_id") == aid]
    assert waiting == []


def test_an_interrupted_send_is_settled_from_the_ledger_when_it_knows():
    import uuid
    from datetime import datetime, timedelta, timezone
    from app.core.database import SessionLocal
    from app.models.db_models import PurchaseOrderDB
    from app.services.outbound_email_service import outbound_email_service as svc

    def stuck(po):
        purchase_orders.begin_send(po["id"])
        with SessionLocal() as db:
            db.get(PurchaseOrderDB, po["id"]).sent_at = datetime.now(timezone.utc) - timedelta(minutes=11)
            db.commit()

    sent_po, _ = _approved_po("LEDGERA")
    stuck(sent_po)
    svc._ledger({"message_id": f"M{uuid.uuid4().hex[:10]}", "category": "PURCHASE_ORDER", "reference": sent_po["po_number"], "to": ["a@b.com"], "intended_to": ["a@b.com"], "status": "SENT"})
    assert purchase_orders.settle_from_ledger(sent_po["id"]) == "SENT"

    failed_po, _ = _approved_po("LEDGERB")
    stuck(failed_po)
    svc._ledger({"message_id": f"M{uuid.uuid4().hex[:10]}", "category": "PURCHASE_ORDER", "reference": failed_po["po_number"], "to": ["a@b.com"], "intended_to": ["a@b.com"], "status": "FAILED"})
    assert purchase_orders.settle_from_ledger(failed_po["id"]) == "APPROVED"

    unknown_po, _ = _approved_po("LEDGERC")
    stuck(unknown_po)
    svc._ledger({"message_id": f"M{uuid.uuid4().hex[:10]}", "category": "PURCHASE_ORDER", "reference": unknown_po["po_number"], "to": ["a@b.com"], "intended_to": ["a@b.com"], "status": "ATTEMPTING"})
    assert purchase_orders.settle_from_ledger(unknown_po["id"]) is None             # a mail that may have left: a manager decides
    nothing_po, _ = _approved_po("LEDGERD")
    stuck(nothing_po)
    assert purchase_orders.settle_from_ledger(nothing_po["id"]) is None


def test_a_purchase_order_is_not_sent_when_the_ledger_cannot_be_written(monkeypatch):
    from app.services.outbound_email_service import outbound_email_service as svc
    monkeypatch.setattr(settings, "SMTP_HOST", "smtp.example.test")
    monkeypatch.setattr(type(svc), "send_enabled", property(lambda self: True))
    monkeypatch.setattr(type(svc), "_ledger", staticmethod(lambda entry: False))
    delivered = []
    monkeypatch.setattr(type(svc), "_deliver", lambda self, msg, rcpts: delivered.append(rcpts))      # on the class: patching the instance would outlive the test
    out = svc.send(to="vendor@example.org", subject="PO", body="b", category="PURCHASE_ORDER", reference="X1")
    assert out["status"] == "FAILED" and delivered == []
    out2 = svc.send(to="boss@example.org", subject="note", body="b", category="GENERAL")           # staff mail is best effort
    assert out2["status"] == "SENT" and delivered


# ============================================================ cancelling a purchase order

def test_a_manager_cancels_a_po_with_a_reason_and_nothing_is_emailed():
    po, _ = _approved_po("CANCELCO")
    with pytest.raises(purchase_orders.jobs.JobError):
        purchase_orders.cancel(po["id"], "customer cancelled", {"email": "op@x.com", "role": "OPERATOR"})
    with pytest.raises(purchase_orders.jobs.JobError):
        purchase_orders.cancel(po["id"], "  ", BOSS)
    out = purchase_orders.cancel(po["id"], "customer cancelled the order", BOSS)
    assert out["status"] == "CANCELLED" and out["cancel_reason"] == "customer cancelled the order" and out["cancelled_at"] and "warning" not in out
    assert purchase_orders.cancel(po["id"], "again", BOSS)["cancel_reason"] == "customer cancelled the order"      # twice changes nothing
    with pytest.raises(purchase_orders.jobs.JobError):
        purchase_orders.update(po["id"], {"terms": "Net 1"}, BOSS)
    with pytest.raises(ValueError):
        purchase_orders.begin_send(po["id"])
    with pytest.raises(purchase_orders.jobs.JobError):
        purchase_orders.email_draft(po["id"], BOSS)


def test_cancelling_a_sent_po_warns_to_tell_the_vendor_and_a_sending_one_is_refused():
    sent, _ = _approved_po("CANCELSENT")
    purchase_orders.begin_send(sent["id"])
    with pytest.raises(purchase_orders.jobs.JobError):
        purchase_orders.cancel(sent["id"], "while sending", BOSS)
    purchase_orders.finish_send(sent["id"], True)
    out = purchase_orders.cancel(sent["id"], "customer cancelled", BOSS)
    assert out["status"] == "CANCELLED" and "tell" in out["warning"].lower()


def test_an_operator_cannot_raise_a_new_po_for_a_cancelled_vendor_and_a_cancelled_po_is_not_an_open_loose_end():
    from app.services.coordination import jobs as jobs_mod
    po, j = _approved_po("CANCELTWO")
    purchase_orders.cancel(po["id"], "mistake", BOSS)
    with pytest.raises(purchase_orders.jobs.JobError):
        purchase_orders.create(j["id"], "CANCELTWO", {"email": "op@x.com", "role": "OPERATOR"})      # only a manager raises a new PO after a cancel
    out = {c["key"] for c in jobs_mod.job_checks({"docs": [], "order_status": "SHIPPED", "purchase_orders": [{"po_number": po["po_number"], "vendor": "X", "status": "CANCELLED"}]})}
    assert "po_not_sent" not in out


def test_a_cancelled_po_is_inert_everywhere_it_used_to_count():
    from app.core.database import SessionLocal
    from app.models.db_models import AssistantActionDB, VendorJobDB
    from app.services.coordination import jobs as jobs_mod
    po, j = _approved_po("INERTCO")
    purchase_orders.update(po["id"], {"vendor_email": "inert@vendor-example.com"}, BOSS)
    draft = purchase_orders.email_draft(po["id"], BOSS)
    assert jobs_mod.po_number_on_job(j["id"], "INERTCO") == po["po_number"]
    with SessionLocal() as db:
        line = db.query(VendorJobDB).filter(VendorJobDB.purchase_order_id == po["id"]).first()
        assert line is not None and line.status != "CANCELLED"
    purchase_orders.cancel(po["id"], "customer cancelled", BOSS)
    assert jobs_mod.po_number_on_job(j["id"], "INERTCO") is None                            # a customer's order never takes a cancelled PO's number
    with SessionLocal() as db:
        assert db.query(VendorJobDB).filter(VendorJobDB.purchase_order_id == po["id"]).first().status == "CANCELLED"
        assert db.get(AssistantActionDB, draft["action_id"]).status == "CANCELLED"        # the waiting vendor email is cancelled too
    from app.services.coordination import vendors
    assert po["po_number"] not in {x.get("po_number") for x in vendors.overview(include_shipped=True)["jobs"]}      # the chaser has nothing to chase


def test_a_cancelled_draft_cannot_be_approved_back_to_life():
    _, j = _job()
    po = purchase_orders.create(j["id"], "DEADDRAFT", BOSS)
    purchase_orders.cancel(po["id"], "raised by mistake", BOSS)
    with pytest.raises(purchase_orders.jobs.JobError):
        purchase_orders.approve(po["id"], BOSS)


# ============================================================ new PO after a cancel, overseas fields, ledger retention, synced folder

def test_a_manager_can_raise_a_new_po_after_a_cancel_but_an_operator_cannot():
    po, j = _approved_po("AGAINCO")
    purchase_orders.cancel(po["id"], "customer cancelled", BOSS)
    with pytest.raises(purchase_orders.jobs.JobError):
        purchase_orders.create(j["id"], "AGAINCO", {"email": "op@x.com", "role": "OPERATOR"})
    again = purchase_orders.create(j["id"], "AGAINCO", BOSS)
    assert again["id"] != po["id"] and again["status"] == "DRAFT" and again["po_number"] is None
    assert purchase_orders.create(j["id"], "AGAINCO", BOSS)["id"] == again["id"]            # still one live PO per vendor per job
    assert purchase_orders.get(po["id"])["status"] == "CANCELLED" and purchase_orders.get(po["id"])["po_number"] == po["po_number"]


def test_overseas_fields_are_optional_cleaned_and_printed_only_when_filled():
    import fitz
    from app.services import order_documents as od
    lines = od.po_lines([{"quantity": 1, "description": "liner", "hts_code": " 8431.43.80 ", "origin": "CN\x00", "uom": "ea", "net_kg": "12.5", "cert_required": True},
                         {"quantity": 1, "description": "plate", "net_kg": "-4", "hts_code": "​"}], strict=True)
    assert lines[0]["hts_code"] == "8431.43.80" and lines[0]["origin"] == "CN" and lines[0]["net_kg"] == 12.5 and lines[0]["cert_required"] is True
    assert "hts_code" not in lines[1] and "net_kg" not in lines[1] and "cert_required" not in lines[1]
    text = "".join(p.get_text() for p in fitz.open(stream=od.vendor_po_pdf({"po_number": "1", "vendor_name": "V", "incoterm": "FOB Ningbo", "lines": lines}), filetype="pdf"))
    assert "HTS 8431.43.80" in text and "ORIGIN CN" in text and "Incoterm: FOB Ningbo" in text
    plain = "".join(p.get_text() for p in fitz.open(stream=od.vendor_po_pdf({"po_number": "1", "vendor_name": "V", "lines": od.po_lines([{"quantity": 1, "description": "x"}])}), filetype="pdf"))
    assert "HTS" not in plain and "Incoterm" not in plain


def test_the_po_keeps_an_incoterm_and_the_job_checks_speak_up_without_blocking():
    po, j = _approved_po("OVERSEAS")
    purchase_orders.update(po["id"], {"incoterm": "fob!", "incoterm_place": "Ningbo \x00 port"}, BOSS)
    got = purchase_orders.get(po["id"])
    assert got["incoterm"] == "FOB" and got["incoterm_place"] == "Ningbo port"
    keys = {c["key"] for c in jobs_mod_checks(j["id"])}
    assert {"overseas_details_missing", "tariff_reminder"} <= keys


def jobs_mod_checks(job_id):
    from app.services.coordination import jobs as jobs_mod
    return jobs_mod.get(job_id)["checks"]


def test_all_pos_cancelled_is_said_and_a_cert_line_on_a_sent_po_is_remembered():
    from app.services.coordination import jobs as jobs_mod
    out = {c["key"] for c in jobs_mod.job_checks({"docs": [], "purchase_orders": [{"po_number": "5", "vendor": "V", "status": "CANCELLED"}]})}
    assert "all_pos_cancelled" in out
    out = {c["key"] for c in jobs_mod.job_checks({"docs": [], "purchase_orders": [{"po_number": "5", "vendor": "V", "status": "SENT", "cert_lines": 1}]})}
    assert "certificate_expected" in out


def test_the_ledger_prune_keeps_vendor_mail_for_good_and_removes_old_routine_rows():
    import uuid
    from datetime import datetime, timedelta, timezone
    from app.core.database import SessionLocal
    from app.models.db_models import OutboundMailDB
    from app.services.outbound_email_service import outbound_email_service as svc
    old = datetime.now(timezone.utc) - timedelta(days=400)
    ids = {}
    with SessionLocal() as db:
        for cat in ("PURCHASE_ORDER", "PRICE_REQUEST", "STAFF_ALERT", "GENERAL"):
            mid = f"M{uuid.uuid4().hex[:10]}"
            ids[cat] = mid
            db.add(OutboundMailDB(message_id=mid, category=cat, reference="R", status="SENT", created_at=old))
        db.add(OutboundMailDB(message_id=(fresh := f"M{uuid.uuid4().hex[:10]}"), category="STAFF_ALERT", reference="R", status="SENT"))
        db.commit()
    assert svc.prune_ledger() >= 2
    with SessionLocal() as db:
        left = {m for (m,) in db.query(OutboundMailDB.message_id).filter(OutboundMailDB.message_id.in_(list(ids.values()) + [fresh])).all()}
    assert left == {ids["PURCHASE_ORDER"], ids["PRICE_REQUEST"], fresh}


def test_a_cloud_synced_backup_folder_is_refused_unless_allowed(tmp_path, monkeypatch):
    from app.core.database import init_database
    init_database()
    monkeypatch.setattr(settings, "BACKUP_DIR", str(tmp_path / "bk"))
    monkeypatch.setattr(settings, "BACKUP_COPY_DIR", str(tmp_path / "OneDrive" / "allied"))
    monkeypatch.setattr(backups, "_tell", lambda out: None)
    out = backups.run()
    assert out["copy"]["ok"] is False and "cloud-synced" in out["copy"]["error"]
    monkeypatch.setattr(settings, "BACKUP_COPY_ALLOW_SYNCED", True)
    assert backups.run()["copy"]["ok"] is True


# ============================================================ monday "Send PO" respects cancelled and already-sent purchase orders

def test_monday_send_po_skips_cancelled_and_pre_ledger_sent_purchase_orders(monkeypatch):
    import uuid
    from app.services import monday_events
    from app.services.outbound_email_service import OutboundEmailService, outbound_email_service
    po_cancelled, po_sent = f"C{uuid.uuid4().hex[:6]}", f"S{uuid.uuid4().hex[:6]}"
    c, _ = _approved_po("RELCANCEL")
    s, _ = _approved_po("RELSENT")
    from app.core.database import SessionLocal
    from app.models.db_models import PurchaseOrderDB
    with SessionLocal() as db:
        db.get(PurchaseOrderDB, c["id"]).po_number, db.get(PurchaseOrderDB, c["id"]).status = po_cancelled, "CANCELLED"
        db.get(PurchaseOrderDB, s["id"]).po_number, db.get(PurchaseOrderDB, s["id"]).status = po_sent, "SENT"
        db.commit()
    order = {"sales_order_number": "SO-9", "customer_po_number": "C-9", "purchase_orders": {"NIBROCK": po_cancelled, "KWS": po_sent}}
    monkeypatch.setattr(monday_events, "find_order_for_item", lambda *a, **kw: order)

    class Matcher:
        vendors_cache = {"NIBROCK": {"vendor_name": "Nibrock", "email": "po@nibrock.test"}, "KWS": {"vendor_name": "KWS", "email": "po@kws.test"}}

    class Orch:
        entity_matcher = Matcher()

    class Board:
        def post_pulse_update(self, *a, **kw):
            return True

    monkeypatch.setattr(monday_events, "_orchestrator", lambda: Orch())
    monkeypatch.setattr(monday_events, "_monday", lambda: Board())
    monkeypatch.setattr(settings, "VENDOR_PO_MODE", "auto")             # even in auto mode nothing may go out for these two
    sends = []
    monkeypatch.setattr(OutboundEmailService, "send_purchase_order", lambda self, **kw: sends.append(kw["po_number"]) or {"status": "SENT"})
    out = monday_events.handle_release_po({"pulseId": str(uuid.uuid4().int % 10**9), "pulseName": "x"})
    assert sends == [] and ("skipped" in out or {o["status"] for o in out["purchase_orders"].values()} <= {"cancelled, not sent", "sent earlier"})


def test_a_sage_sales_order_declares_the_lines_it_really_has(monkeypatch):
    import csv
    import io
    from app.services.sage_export_service import SageExportService, sage_export_service
    o = {"sales_order_number": "9123456", "customer_id": "C1", "customer_name": "Owens", "po_date": "2026-11-14", "currency": "USD", "purchase_orders": {},
         "line_items": [{"item_id": "A1", "description": "ok", "quantity": 1, "unit_price": "5.00"}, {"item_id": "A2", "description": "bad", "quantity": "ten", "unit_price": "5.00"},
                        {"item_id": "A3", "description": "ok2", "quantity": 2, "unit_price": "1.00"}]}
    monkeypatch.setattr(SageExportService, "_get_orders", lambda self, facility=None, only_new=False, kind="so": [o])
    rows = list(csv.reader(io.StringIO(sage_export_service.export_sales_orders()[0])))
    assert len(rows) == 3 and {r[4] for r in rows[1:]} == {"2"}                       # two real lines: every row says 2, not 3
    assert any("A2" in w for w in sage_export_service.last_skipped)


def test_the_slip_redacts_a_vendor_written_the_short_way(monkeypatch):
    from app.services import packing_slips
    monkeypatch.setattr(packing_slips, "_vendor_name_set", lambda extra=None: {"KWS Ltd", "Zibo Supereal Ceramics"})
    out = packing_slips._without_vendor_names("Liner made by KWS and Zibo Supereal", None, {"KWS Ltd", "Zibo Supereal Ceramics"})
    assert "KWS" not in out and "Zibo" not in out and out.startswith("Liner made by")
