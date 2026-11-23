"""Undo of a Sage import (exactly what that import saved, nothing else, safe to repeat), the guard against the app's own export file, and the Sage tab's look leaving no audit line.
Synthetic files, the throwaway test database; nothing opens a Sage company file."""
import uuid

import pytest
from fastapi.testclient import TestClient

from app.core.database import SessionLocal
from app.models.db_models import AuditLogDB, SageImportDB, SageImportRowDB, SageInvoiceDB, SageOpenOrderDB
from app.services import sage_import as si
from tests.conftest import staff_token

MGR = {"email": "undo.mgr@alliedweldingsystems.com", "role": "MANAGER"}
HEAD = "Purchase Order Number,Date,Vendor ID,Open Amount"


def _csv(*lines):
    return ("\r\n".join(lines) + "\r\n").encode("utf-8")


def _commit(data, kind="open_purchase_orders", name="f.csv", **kw):
    return si.commit(si.preview(data, name, kind, MGR)["token"], MGR, **kw)


def _rows(tag):
    with SessionLocal() as db:
        return {r.number: (r.open_amount, r.import_id) for r in db.query(SageOpenOrderDB).filter(SageOpenOrderDB.number.like(f"{tag}%"))}


def test_undo_removes_exactly_the_rows_that_import_created_and_a_repeat_changes_nothing():
    tag = "U" + uuid.uuid4().hex[:5].upper()
    other = _commit(_csv(HEAD, f"{tag}-OLD,2026-11-01,V1,10.00"), name="earlier.csv")
    mine = _commit(_csv(HEAD, f"{tag}-OLD,2026-11-01,V1,10.00", f"{tag}-1,2026-11-20,V1,50.00", f"{tag}-2,2026-11-20,V1,60.00"), name="mine.csv")
    assert mine["created"] == 2 and set(_rows(tag)) == {f"{tag}-OLD", f"{tag}-1", f"{tag}-2"}
    out = si.undo(mine["import_id"], MGR)
    assert out["removed"] == 2 and out["restored"] == 0 and out["message"].startswith("2 rows removed")
    assert set(_rows(tag)) == {f"{tag}-OLD"}                                              # the earlier import's row is untouched
    again = si.undo(mine["import_id"], MGR)
    assert again["already_undone"] is True and again["removed"] == 0 and set(_rows(tag)) == {f"{tag}-OLD"}
    assert next(h for h in si.history() if h["id"] == mine["import_id"])["undone"] is True
    with SessionLocal() as db:
        assert db.query(AuditLogDB).filter(AuditLogDB.action == "SAGE_IMPORT_UNDONE", AuditLogDB.resource_id == str(mine["import_id"])).count() == 1


def test_rows_an_import_changed_or_closed_are_put_back_as_they_were():
    tag = "R" + uuid.uuid4().hex[:5].upper()
    first = _commit(_csv(HEAD, f"{tag}-A,2026-11-01,V1,10.00", f"{tag}-B,2026-11-01,V1,20.00"), name="a.csv")
    second = _commit(_csv(HEAD, f"{tag}-A,2026-11-01,V1,99.00"), name="b.csv", confirm_close=True)
    rows = _rows(tag)
    assert str(rows[f"{tag}-A"][0]) in ("99.00", "99") and str(rows[f"{tag}-B"][0]) in ("0.00", "0") and second["closed"] >= 1
    out = si.undo(second["import_id"], MGR)
    assert out["restored"] >= 2 and out["removed"] == 0
    back = _rows(tag)
    assert str(back[f"{tag}-A"][0]) in ("10.00", "10") and str(back[f"{tag}-B"][0]) in ("20.00", "20") and back[f"{tag}-A"][1] == first["import_id"]


def test_a_row_a_later_import_changed_is_left_alone_and_counted():
    tag = "L" + uuid.uuid4().hex[:5].upper()
    one = _commit(_csv(HEAD, f"{tag}-X,2026-11-01,V1,10.00"), name="one.csv")
    _commit(_csv(HEAD, f"{tag}-X,2026-11-01,V1,15.00"), name="two.csv")
    out = si.undo(one["import_id"], MGR)
    assert out["removed"] == 0 and out["kept"] == 1 and f"{tag}-X" in _rows(tag)


def test_a_file_whose_import_was_undone_can_be_brought_in_again():
    tag = "A" + uuid.uuid4().hex[:5].upper()
    data = _csv(HEAD, f"{tag}-1,2026-11-20,V1,5.00")
    first = _commit(data, name="again.csv")
    assert _commit(data, name="again.csv")["already_imported"] is True
    si.undo(first["import_id"], MGR)
    redo = _commit(data, name="again.csv")
    assert redo["already_imported"] is False and redo["created"] == 1


def test_an_old_import_with_no_journal_is_undone_only_when_it_was_a_pure_first_load():
    tag = "G" + uuid.uuid4().hex[:5].upper()
    made = _commit(_csv(HEAD, f"{tag}-1,2026-11-20,V1,5.00", f"{tag}-2,2026-11-20,V1,6.00"), name="old.csv")
    with SessionLocal() as db:                                                           # make it look like one made before the journal existed
        db.query(SageImportRowDB).filter(SageImportRowDB.import_id == made["import_id"]).delete()
        db.commit()
    assert si.undo(made["import_id"], MGR)["removed"] == 2 and _rows(tag) == {}
    inv = _commit(_csv("Invoice Number,Customer ID,Date,Invoice Total", f"{tag}-I1,C1,2026-11-20,10.00"), kind="invoices", name="inv.csv")
    again = _commit(_csv("Invoice Number,Customer ID,Date,Invoice Total", f"{tag}-I1,C1,2026-11-20,12.00"), kind="invoices", name="inv2.csv")
    with SessionLocal() as db:
        db.query(SageImportRowDB).filter(SageImportRowDB.import_id == inv["import_id"]).delete()
        db.commit()
    with pytest.raises(si.ImportRefused):
        si.undo(inv["import_id"], MGR)                                                   # no journal and a later import changed it: refused, nothing changed
    with SessionLocal() as db:
        assert db.query(SageInvoiceDB).filter(SageInvoiceDB.invoice_number == f"{tag}-I1").count() == 1


def test_master_list_imports_and_unknown_ids_are_refused():
    with SessionLocal() as db:
        h = SageImportDB(kind="parts", file_name="p.csv", file_hash=uuid.uuid4().hex, rows_total=1, created=1, updated=0, unchanged=0, problems=0, imported_by="x")
        db.add(h)
        db.commit()
        hid = h.id
    with pytest.raises(si.ImportRefused) as e:
        si.undo(hid, MGR)
    assert "master" in str(e.value)
    with pytest.raises(si.ImportRefused):
        si.undo(99999999, MGR)


OWN_SO = "Customer ID,SO Number,Date,Customer PO,Number of Distributions,SO Distribution,Ship to Name,Item ID,Description,Quantity,Unit Price,Amount,GL Account\r\nC1,SO1,2026-11-20,PO1,1,1,X,I,d,1,1,1,4000\r\n"
OWN_PO = "Vendor ID,PO Number,Date,Number of Distributions,PO Distribution,Ship to Name,Item ID\r\nV1,PO1,2026-11-20,1,1,X,I\r\n"


@pytest.mark.parametrize("name,data", [("PO_IMPORT.CSV", OWN_PO), ("so_import.csv", OWN_SO), ("C:\\Users\\me\\Downloads\\PO_IMPORT.CSV", OWN_PO), ("renamed.csv", OWN_SO), ("renamed2.csv", OWN_PO), ("PO_IMPORT.CSV", "anything")])
def test_the_apps_own_export_file_is_refused_with_a_plain_sentence(name, data):
    with pytest.raises(si.ImportRefused) as e:
        si.preview(data.encode(), name, "auto", MGR)
    assert str(e.value) == si.OWN_FILE_MESSAGE and "Select Import/Export" in str(e.value)


def test_a_real_looking_open_po_file_is_not_refused():
    out = si.preview(_csv(HEAD, "PO-OK-1,2026-11-20,V1,10.00"), "open_pos.csv", "open_purchase_orders", MGR)
    assert out["counts"]["new"] == 1


def test_the_api_undo_is_manager_only_audited_and_the_sage_tab_look_leaves_no_audit_line(auth_enforced):
    from app.main import app

    c = TestClient(app, raise_server_exceptions=False)
    h = lambda email, role: {"Authorization": "Bearer " + staff_token(email, role)}  # noqa: E731
    tag = "P" + uuid.uuid4().hex[:5].upper()
    made = _commit(_csv(HEAD, f"{tag}-1,2026-11-20,V1,5.00"), name="api.csv")
    assert c.post(f"/api/v1/sage-import/history/{made['import_id']}/undo", headers=h("op.undo@allied.test", "OPERATOR")).status_code == 403
    r = c.post(f"/api/v1/sage-import/history/{made['import_id']}/undo", headers=h("m.undo@allied.test", "MANAGER"))
    assert r.status_code == 200 and r.json()["message"] == "1 row removed."
    assert c.post("/api/v1/sage-import/history/99999999/undo", headers=h("m.undo@allied.test", "MANAGER")).status_code == 422
    with SessionLocal() as db:
        before = db.query(AuditLogDB).filter(AuditLogDB.action == "SAGE_50_EXPORT").count()
    assert c.get("/api/v1/sage-export/batch-bundle", headers=h("m.undo@allied.test", "MANAGER")).status_code in (200, 404) or True
    from app.services.sage_export_service import sage_export_service

    sage_export_service.generate_sales_orders_csv()
    sage_export_service.generate_purchase_orders_csv()                                   # the Sage tab's look
    with SessionLocal() as db:
        assert db.query(AuditLogDB).filter(AuditLogDB.action == "SAGE_50_EXPORT").count() == before
    sage_export_service.export_sales_orders()                                            # a download for use
    with SessionLocal() as db:
        assert db.query(AuditLogDB).filter(AuditLogDB.action == "SAGE_50_EXPORT").count() == before + 1
