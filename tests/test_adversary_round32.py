"""Round 32 (adversary): re-attack of the round 31 fixes. Temp DB, synthetic text, nothing saved or sent. xfail(strict=False, reason="ADV32-Tn ...") marks holes."""
import time
import uuid

import pytest

from app.services import assist, extract

TAG = uuid.uuid4().hex[:5]
MGR = {"email": f"m32-{TAG}@alliedweldingsystems.com", "role": "MANAGER"}


@pytest.mark.parametrize("make", [lambda: "PO" + " " * 30000 + "!", lambda: "p.o. " + "#:" * 15000 + "x", lambda: "rfq " + "-\t" * 15000, lambda: "total " + " " * 20000 + "$",
                                  lambda: "a" * 60000, lambda: "a@" * 30000, lambda: "x" * 30000 + "@" + "y" * 30000, lambda: "lead time " + "1 " * 30000, lambda: "weight " + "1," * 30000 + "kg",
                                  lambda: "ETA " + "1/" * 30000, lambda: "." * 60000, lambda: ("PO 1 " * 12000)], ids=range(12))
def test_every_extractor_is_fast_on_hostile_60k_text(make):
    t = make()[:60000]
    t0 = time.perf_counter()
    for fn in (lambda: extract.rfq_from_email(text=t), lambda: extract.order_from_email(text=t), lambda: extract.call_to_job_note(t), lambda: extract.vendor_quote(t), lambda: assist.create_from_text(t + " pad pad", "")):
        try:
            fn()
        except (extract.ExtractError, assist.AssistError):
            pass
    assert time.perf_counter() - t0 < 5


def test_t1_text_cut_at_the_cap_says_so():
    out = extract.order_from_email(text=("filler words. " * 2000) + "\nCustomer PO 45009\nTotal $100.00", subject="x")
    assert "customer_po_number" in out["fields"] or any("long" in w.lower() or "cut" in w.lower() or "first" in w.lower() for w in out["warnings"])


def test_bulk_owner_must_be_a_real_staff_address_and_lookalikes_are_refused():
    from app.services.coordination import rfqs

    r = rfqs.create({"customer": "Bulk Co", "subject": f"b32 {TAG}", "items": [{"description": "x", "quantity": 1}]}, MGR, announce=False)
    for bad in ("boss@alliedweldingsystems.com.evil.test", "boss@alliedweldingsystems.com@evil.test", "boss@evil.test", "BOSS@ALLIEDWELDINGSYSTEMS.COM ", "a b@alliedweldingsystems.com", "<x>@alliedweldingsystems.com", "nobody"):
        try:
            out = assist.bulk("rfq", [r["number"]], {"owner": bad}, MGR, dry_run=False)
        except assist.AssistError:
            continue
        assert out["changed"] == 0 or bad.strip().lower() == "boss@alliedweldingsystems.com", (bad, out["rows"])


@pytest.mark.parametrize("tags", ["urgent", ["a"] * 11, ["x" * 41], [1, 2], [None], {"a": 1}, [["nested"]], ["ok", "ok", "ok"]], ids=range(8))
def test_bulk_tags_are_validated(tags):
    from app.services.coordination import rfqs

    r = rfqs.create({"customer": "Bulk Co", "subject": f"t32 {TAG}", "items": [{"description": "x", "quantity": 1}]}, MGR, announce=False)
    try:
        out = assist.bulk("rfq", [r["number"]], {"add_tags": tags}, MGR, dry_run=False)
    except assist.AssistError:
        return
    assert tags == ["ok", "ok", "ok"] and out["changed"] in (0, 1)


def test_feedback_is_only_by_the_person_shown_it_and_only_once(auth_enforced):
    from fastapi.testclient import TestClient

    from app.main import app
    from tests.conftest import staff_token

    c = TestClient(app, raise_server_exceptions=False)
    ha = {"Authorization": "Bearer " + staff_token(f"a32-{TAG}@alliedweldingsystems.com", "OPERATOR")}
    hb = {"Authorization": "Bearer " + staff_token(f"b32-{TAG}@alliedweldingsystems.com", "OPERATOR")}
    out = c.post("/api/v1/extract/order-from-email", json={"text": "Customer PO 45009\nTotal $5.00", "subject": "s"}, headers=ha).json()
    body = {"extraction_id": out["extraction_id"], "fields": {"total": "1"}}
    assert c.post("/api/v1/extract/feedback", json=body, headers=hb).status_code == 403
    assert c.post("/api/v1/extract/feedback", json=body, headers=ha).status_code == 200
    assert c.post("/api/v1/extract/feedback", json=body, headers=ha).status_code == 409
    assert c.post("/api/v1/extract/feedback", json=body, headers=hb).status_code == 403
