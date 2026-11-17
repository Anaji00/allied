"""Adversarial round 7: the blind-shipment check, PO line revisions, and the second backup copy. Fakes and temporary database only."""
import sqlite3
import threading
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.config import settings
from app.services import backups
from app.services.coordination import blind, jobs, purchase_orders, rfqs
from tests.test_adversary_po import BOSS, _approved_po, _job  # noqa: F401

CUSTOMER_FULL = ["Owens Corning Insulating Systems Canada LP", "Owens Corning Starr Composites", "Knauf Insulation Inc. - Lanett", "IKO Southwest Inc."]
VENDOR_EMAIL = "helen@zbsupereal.com"


def _to_vendor(body, **extra):
    return blind.check("send_vendor_price_request", {"to": VENDOR_EMAIL, "subject": "Price request", "body": body, **extra})


def _to_customer(body, to="buyer@customer-example.com"):
    return blind.check("send_email", {"to": to, "subject": "Your quote", "body": body})


# ============================================================ blind shipment

def test_a_full_customer_name_going_to_a_vendor_is_caught_whatever_the_case_or_punctuation():
    for name in CUSTOMER_FULL:
        for variant in (name, name.upper(), name.replace(" ", "  "), name.replace(".", "").replace("-", " ")):
            assert _to_vendor(f"Please quote parts for {variant} by Friday."), variant


@pytest.mark.parametrize("short", ["Owens Corning", "Knauf Insulation", "IKO Southwest", "Knauf"])
def test_the_usual_short_form_of_a_customer_name_is_caught(short):
    assert _to_vendor(f"These liners are for {short}.")


@pytest.mark.parametrize("short", ["KWS Manufacturing", "Zibo Supereal", "Nibrock", "KWS"])
def test_the_usual_short_form_of_a_vendor_name_is_caught(short):
    assert _to_customer(f"Your parts are being made by {short}.")


@pytest.mark.parametrize("hidden", ["Оwens Corning Starr Composites", "Owens Corning Starr Compo​sites", "Owens Corning Starr Composites", "O w e n s Corning Starr Composites"])
def test_a_disguised_customer_name_is_still_caught(hidden):
    assert _to_vendor(f"For {hidden} please.")


@pytest.mark.parametrize("to", [f"{VENDOR_EMAIL}, other@example.com", f"Helen <{VENDOR_EMAIL}>", f"other@example.com; {VENDOR_EMAIL}"])
def test_a_vendor_in_a_multi_recipient_or_named_address_is_still_a_vendor(to):
    out = blind.check("send_email", {"to": to, "subject": "s", "body": "Parts for Owens Corning Starr Composites."})
    assert out and "customer" in out[0].lower()


def test_a_short_customer_name_is_caught():
    r = rfqs.create({"customer": "3M", "subject": f"liners {uuid.uuid4().hex[:5]}", "items": [{"description": "liner", "quantity": 1}]}, BOSS, announce=False)
    assert blind.check("send_vendor_price_request", {"to": VENDOR_EMAIL, "subject": "s", "body": "These are for 3M.", "_rfq_id": r["id"]})


def test_one_bad_hint_does_not_switch_the_whole_check_off():
    out = _to_vendor("Parts for Owens Corning Starr Composites.", _rfq_id=99999999)
    assert out


def test_allied_s_own_name_and_ordinary_text_are_not_a_leak():
    assert not _to_vendor("Allied Welding Systems purchase order 50123 for 2 liners per drawing A.")
    assert not _to_customer("Your quote from Allied Welding Systems is attached.")


def test_the_check_never_raises_on_hostile_args():
    for args in ({}, {"to": None, "body": None}, {"to": 5, "body": ["x"], "subject": {"a": 1}}, {"body": "x" * 2_000_000}, {"_attachments": [None, 5, "x", {"name": None}]},
                 {"_rfq_id": "abc"}, {"_job_id": -1}, {"body": "\x00‮" * 1000}):
        for tool in ("send_vendor_po", "send_vendor_price_request", "send_email", "other"):
            assert isinstance(blind.check(tool, args), list)


def test_a_drawing_file_name_that_names_the_customer_is_caught():
    out = _to_vendor("Please see the attached.", _attachments=[{"name": "Owens Corning Starr Composites liner rev C.pdf"}])
    assert out


# ============================================================ PO line revisions

def test_the_rfq_items_revision_reaches_the_po_line():
    r = rfqs.create({"customer": "Owens Corning", "subject": f"rev part {uuid.uuid4().hex[:5]}", "items": [{"description": "liner", "quantity": 2, "revision": "C"}]}, BOSS, announce=False)
    j = jobs.from_rfq(r["id"], BOSS)
    jobs.approve_design(j["id"], BOSS)
    po = purchase_orders.create(j["id"], "HELEN", BOSS)
    assert po["lines"][0].get("revision") == "C"


@pytest.mark.parametrize("rev", ["A", "Rev B", "C" * 500, "<b>&</para>", "\r\nBcc: x@y.com", "\x00\x1b[31m","修订 C"])
def test_a_typed_revision_is_cleaned_and_the_pdf_still_builds(rev):
    _, j = _job()
    po = purchase_orders.create(j["id"], f"REVCO {uuid.uuid4().hex[:5]}", BOSS, lines=[{"quantity": 1, "description": "liner", "revision": rev}])
    stored = purchase_orders.get(po["id"])["lines"][0].get("revision", "")
    assert len(stored) <= 20 and not any(ord(c) < 32 or ord(c) == 127 for c in stored) and "‮" not in stored
    assert purchase_orders.pdf(po["id"]).startswith(b"%PDF")


def test_a_bidi_override_is_stripped_from_a_revision():
    _, j = _job()
    po = purchase_orders.create(j["id"], f"BIDI {uuid.uuid4().hex[:5]}", BOSS, lines=[{"quantity": 1, "description": "liner", "revision": "‮DCBA"}])
    assert "‮" not in purchase_orders.get(po["id"])["lines"][0].get("revision", "")


@pytest.mark.parametrize("rev", ["   ", "​", " ", "﻿"])
def test_an_invisible_revision_is_a_missing_revision(rev):
    _, j = _job()
    po = purchase_orders.create(j["id"], f"INVIS {uuid.uuid4().hex[:5]}", BOSS, lines=[{"quantity": 1, "description": "liner", "revision": rev}])
    assert purchase_orders.get(po["id"])["missing_revision"] == [1]


def test_a_line_without_a_revision_is_flagged_and_a_named_one_is_not():
    _, j = _job()
    po = purchase_orders.create(j["id"], f"MIX {uuid.uuid4().hex[:5]}", BOSS,
                                lines=[{"quantity": 1, "description": "a"}, {"quantity": 1, "description": "b", "revision": "B"}, {"quantity": 1, "description": "c", "drawing_rev": "D"}])
    assert purchase_orders.get(po["id"])["missing_revision"] == [1]


def test_a_revision_cannot_change_after_the_purchase_order_is_approved():
    po, _ = _approved_po()
    with pytest.raises(jobs.JobError):
        purchase_orders.update(po["id"], {"lines": [{"quantity": 1, "description": "liner", "revision": "Z"}]}, BOSS)


# ============================================================ the second backup copy

@pytest.fixture()
def bk(tmp_path, monkeypatch):
    from app.core.database import init_database
    init_database()
    monkeypatch.setattr(settings, "BACKUP_DIR", str(tmp_path / "bk"))
    monkeypatch.setattr(settings, "BACKUP_COPY_DIR", str(tmp_path / "copy"))
    backups.last.clear()
    return tmp_path


def test_a_backup_is_copied_checked_and_reported(bk):
    out = backups.run()
    assert out["ok"] and out["copy"]["ok"] and (bk / "copy" / out["file"]).is_file()
    assert backups.copy_line()["state"] == "made"
    assert not list((bk / "copy").glob("*.part"))


@pytest.mark.parametrize("where", ["same", "inside"])
def test_a_copy_folder_that_is_the_backup_folder_is_not_a_second_copy(bk, monkeypatch, where):
    monkeypatch.setattr(settings, "BACKUP_COPY_DIR", str(bk / "bk") if where == "same" else str(bk / "bk" / "sub"))
    out = backups.run()
    assert not out["copy"]["ok"]


def test_an_unusable_copy_folder_is_reported_and_the_first_backup_survives(bk, monkeypatch):
    (bk / "afile").write_text("x")
    monkeypatch.setattr(settings, "BACKUP_COPY_DIR", str(bk / "afile" / "sub"))        # a folder under a file can never exist
    out = backups.run()
    assert out["ok"] is False and out["copy"]["configured"] and not out["copy"]["ok"] and (bk / "bk" / out["file"]).is_file()


def test_leftover_part_files_are_cleaned_up(bk):
    (bk / "copy").mkdir()
    old = bk / "copy" / "allied-20200101-000000.db.part"
    old.write_bytes(b"half a database")
    backups.run()
    assert not old.exists()


def test_a_clock_jump_forward_never_prunes_below_the_keep_minimum(bk):
    for i in range(settings.BACKUP_KEEP_MIN + 3):
        backups.run(now=datetime.now(timezone.utc) - timedelta(days=30 - i))
    backups.run(now=datetime.now(timezone.utc) + timedelta(days=3650))
    assert len(list((bk / "copy").glob("allied-*.db"))) >= settings.BACKUP_KEEP_MIN
    assert len(backups.listing()) >= settings.BACKUP_KEEP_MIN


def test_a_stale_second_copy_is_not_reported_as_made(bk):
    out = backups.run(now=datetime.now(timezone.utc) - timedelta(days=5))
    assert out["copy"]["ok"]
    assert backups.copy_line()["state"] == "not_made"


def test_a_backup_that_fails_its_check_is_not_copied(bk, monkeypatch):
    monkeypatch.setattr(backups, "check", lambda *a, **k: {"integrity": "bad", "ok": False, "lost": {}, "rows": {}})
    out = backups.run()
    assert out["ok"] is False and not list((bk / "copy").glob("allied-*.db")) if (bk / "copy").exists() else True


def test_copying_a_missing_backup_never_raises(bk):
    out = backups.copy_off_server(bk / "bk" / "allied-20990101-000000.db", bk / "live.db")
    assert out["ok"] is False


def test_two_backups_in_the_same_second_leave_a_good_file(bk):
    now = datetime.now(timezone.utc)
    res = []
    ts = [threading.Thread(target=lambda: res.append(backups.run(now=now))) for _ in range(3)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    name = f"allied-{now.strftime('%Y%m%d-%H%M%S')}.db"
    for place in (bk / "bk" / name, bk / "copy" / name):
        if place.exists():
            with sqlite3.connect(f"file:{place}?mode=ro&immutable=1", uri=True) as c:
                assert c.execute("pragma integrity_check").fetchone()[0] == "ok"
    assert any(r.get("ok") for r in res)
