"""Round 31 (adversary): the 'less typing' routes (app/api/v1/endpoints/extract.py, services/extract.py, services/assist.py): extract/*, ai/extraction-stats, prefill, duplicate-template, create/from-text, records/bulk, me/defaults.
Temporary database and files only (conftest); synthetic emails/PDFs; AI and network are fakes; nothing is saved to monday/Sage or sent. xfail(strict=False, reason="ADV31-Tn ...") marks holes."""
import io
import json
import threading
import time
import uuid

import fitz
import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.core.database import SessionLocal
from app.services import assist, extract
from tests.conftest import staff_token

TAG = uuid.uuid4().hex[:5]
ORDER = ["VIEWER", "OPERATOR", "MANAGER", "ADMIN"]


@pytest.fixture()
def api(auth_enforced):
    from app.main import app

    client = TestClient(app, raise_server_exceptions=False)
    heads = {r: {"Authorization": "Bearer " + staff_token(f"a31-{r.lower()}-{TAG}@alliedweldingsystems.com", r)} for r in ORDER}

    def call(method, path, role="OPERATOR", **kw):
        h = {} if role is None else heads[role]
        return client.request(method, "/api/v1" + path, headers=h, **kw)

    return call


@pytest.fixture()
def dbdir(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "DB_DIR", tmp_path / "udb")
    return tmp_path / "udb"


def _scale(fn, small=2500, big=5000):
    """Time of fn(n) at two sizes. A ratio near 4 means quadratic; the projection is what the full-size text would cost."""
    t0 = time.perf_counter(); fn(small); a = time.perf_counter() - t0
    t0 = time.perf_counter(); fn(big); b = time.perf_counter() - t0
    return a, b, (b / max(a, 1e-4))


# =============================================================== who may call what

ROUTES = [("post", "/extract/rfq-from-email", {"text": "RFQ 4455 please quote 10 x liner plate"}, "OPERATOR"), ("post", "/extract/order-from-email", {"text": "PO 45009 total 100.00"}, "OPERATOR"),
          ("post", "/extract/call-to-job-note", {"text": "We will send the drawing on Friday."}, "OPERATOR"), ("post", "/extract/feedback", {"extraction_id": "nope", "fields": {}}, "OPERATOR"),
          ("get", "/ai/extraction-stats", None, "MANAGER"), ("get", "/prefill/order", None, "OPERATOR"), ("get", "/records/rfq/NOPE/duplicate-template", None, "OPERATOR"),
          ("post", "/create/from-text", {"text": "Please quote 10 x liner plate for plant 7"}, "OPERATOR"), ("post", "/records/bulk", {"kind": "rfq", "refs": ["X-1"], "changes": {"add_tags": ["a"]}}, "OPERATOR"),
          ("get", "/me/defaults", None, "VIEWER"), ("put", "/me/defaults", {"currency": "USD"}, "VIEWER")]


@pytest.mark.parametrize("method,path,body,minimum", ROUTES, ids=[r[1] + "-" + r[0] for r in ROUTES])
def test_each_route_needs_its_minimum_role_and_never_a_missing_token(api, dbdir, method, path, body, minimum):
    kw = {"json": body} if body is not None else {}
    assert api(method, path, role=None, **kw).status_code in (401, 403)
    for role in ORDER:
        r = api(method, path, role=role, **kw)
        if ORDER.index(role) < ORDER.index(minimum):
            assert r.status_code == 403, (role, r.status_code)
        else:
            assert r.status_code != 403 and r.status_code < 500, (role, r.status_code, r.text[:200])


def test_nothing_is_saved_by_the_extract_prefill_and_create_from_text_routes(api, dbdir):
    from app.models.db_models import OrderDB, PurchaseOrderDB, RfqDB, ShipmentDB

    def counts():
        with SessionLocal() as db:
            return [db.query(m).count() for m in (RfqDB, OrderDB, PurchaseOrderDB, ShipmentDB)]

    before = counts()
    api("post", "/extract/rfq-from-email", json={"text": "RFQ 4455 please quote 10 x liner plate", "sender": "a@b.test"})
    api("post", "/extract/order-from-email", json={"text": "PO 45009 total 100.00"})
    api("post", "/create/from-text", json={"text": "Container MSKU1234567 ETA 2026-12-01 booking ABC12345"})
    api("post", "/create/from-text", json={"text": "Please quote 10 x liner plate"})
    api("get", "/prefill/order", params={"customer": "x"})
    assert counts() == before


# =============================================================== regex time on hostile text (each endpoint takes up to 60,000 characters)

def test_x2_a_long_run_of_address_characters_is_linear_time():
    a, b, ratio = _scale(lambda n: extract.rfq_from_email(text="a" * n, subject="x"), small=4000, big=8000)
    assert ratio < 3.0 or b * 56 < 5.0, f"{a:.3f}s -> {b:.3f}s (x{ratio:.1f})"


def test_x3_create_from_text_detect_is_linear_time():
    a, b, ratio = _scale(lambda n: assist.create_from_text("p.o." + " " * n + "!", ""), small=300, big=600)
    assert ratio < 3.0 or b * 8000 < 5.0, f"{a:.3f}s -> {b:.3f}s (x{ratio:.1f})"


def test_ordinary_long_text_is_fast():
    text = ("Hello, please find our order below. " * 1500)[:60000]
    t0 = time.perf_counter()
    extract.rfq_from_email(text=text)
    extract.order_from_email(text=text)
    assist.create_from_text(text, "")
    extract.call_to_job_note(text)
    assert time.perf_counter() - t0 < 5


# =============================================================== are the fields honest?

EMAIL = "Hello,\nPlease supply 10 x AR400 liner plate @ $55.00\n4 x HARDOX bolt kit @ $12.50\nTotal $550.00 net 30\nCustomer PO 45009\nDeliver by 03/04/2026\nThanks"


def test_the_extraction_names_how_each_field_was_found_and_lists_money_dates_and_numbers_to_check():
    out = extract.order_from_email(text=EMAIL, sender="buyer@somewhere-example.test", subject="Order")
    f = out["fields"]
    assert f["customer_po_number"]["value"] == "45009" and f["total"]["value"] == "550.00" and f["payment_terms"]["value"] == "Net 30 Days"
    assert {"customer_po_number", "total", "payment_terms"} <= set(out["needs_check"]) and "due_date" not in f and any("two ways" in w for w in out["warnings"])
    assert all({"value", "confidence", "method", "source"} <= set(v) for v in f.values()) and out["ai_used"] is False


def test_h1_item_quantities_and_unit_prices_are_listed_for_checking():
    out = extract.order_from_email(text=EMAIL, sender="b@somewhere-example.test")
    assert out["fields"]["items"]["value"][0]["unit_price"] == "55.00" and "items" in out["needs_check"]


def test_h2_a_customer_named_in_passing_is_not_taken_as_the_sender():
    names = [str(r.get("customer_name") or "") for r in extract._customers().values() if len(str(r.get("customer_name") or "")) >= 6]
    if not names:
        pytest.skip("no master customers in this test run")
    out = extract.rfq_from_email(text=f"Hi, we are a different company and have nothing to do with {names[0]}. Please quote 5 x angle bar.", sender="someone@unrelated-example.test", subject="Quote")
    c = out["fields"].get("customer")
    assert not c or "customer" in out["needs_check"]


@pytest.mark.parametrize("line", ["Signed by Jan 5, 2026", "Quote prepared by 2026-01-05 for review"])
def test_h3_a_date_after_the_word_by_is_not_always_a_due_date(line):
    out = extract.rfq_from_email(text=f"Please quote 3 x plate.\n{line}", subject="x")
    assert "due_date" not in out["fields"]


def test_h4_an_ambiguous_date_is_warned_about():
    out = extract.order_from_email(text="PO 45009\nDeliver by 03/04/2026", subject="x")
    assert any("03/04" in w or "ambiguous" in w.lower() or "day" in w.lower() for w in out["warnings"]) or out["fields"]["due_date"]["confidence"] < 0.9


@pytest.mark.parametrize("line", ["Total weight 1250.00 kg", "Total quantity of plates 12.00"])
def test_h5_a_weight_or_quantity_after_total_is_not_the_order_total(line):
    out = extract.order_from_email(text=f"PO 45009\n{line}", subject="x")
    assert "total" not in out["fields"]


def test_h6_a_later_real_po_number_is_found_after_a_p_o_box_mention():
    out = extract.order_from_email(text="Send invoices to our P.O. Box in Toledo.\nCustomer PO 45009\n10 x plate", subject="x")
    assert out["fields"].get("customer_po_number", {}).get("value") == "45009"


def test_blanks_stay_blank_and_a_missing_item_or_customer_warns():
    out = extract.order_from_email(text="Hello, thanks for the call.", subject="Hi")
    assert "customer_po_number" not in out["fields"] and any("PO number" in w for w in out["warnings"])
    with pytest.raises(extract.ExtractError):
        extract.rfq_from_email(text="", subject="")


# =============================================================== PDFs: unreadable, hostile, scanned

def _pdf(pages=1, text="Customer PO 45009\nTotal $100.00\nPurchase Order"):
    d = fitz.open()
    for i in range(pages):
        d.new_page().insert_text((40, 80), text, fontsize=12)
    return d.tobytes()


def _post_pdf(api, data, name="x.pdf", role="OPERATOR"):
    return api("post", "/extract/po-from-pdf", role=role, files={"file": (name, io.BytesIO(data), "application/pdf")})


@pytest.mark.parametrize("data,why", [(b"", "empty"), (b"not a pdf", "text"), (b"%PDF-1.4\n" + b"x" * 300, "truncated"), (b"\x00" * 5000, "zeros")], ids=["empty", "text", "truncated", "zeros"])
def test_a_broken_file_is_a_plain_4xx_never_a_500(api, dbdir, data, why):
    r = _post_pdf(api, data)
    assert 400 <= r.status_code < 500, (r.status_code, r.text[:200])


def test_a_password_pdf_and_a_blank_page_say_so_plainly(api, dbdir):
    d = fitz.open()
    d.new_page().insert_text((40, 40), "secret PO 45009")
    locked = d.tobytes(encryption=fitz.PDF_ENCRYPT_AES_256, owner_pw="o", user_pw="u")
    r = _post_pdf(api, locked)
    assert 400 <= r.status_code < 500
    blank = fitz.open()
    blank.new_page()
    out = _post_pdf(api, blank.tobytes())
    if out.status_code == 200:
        assert out.json()["warnings"], "a blank page must warn"
    else:
        assert 400 <= out.status_code < 500


def test_a_normal_po_pdf_is_read_with_page_and_source(api, dbdir):
    r = _post_pdf(api, _pdf())
    assert r.status_code == 200, r.text[:200]
    body = r.json()
    f = body["fields"]
    assert ("customer_po_number" in f and f["customer_po_number"]["source"]["page"] == 1) or any("purchase order" in w.lower() for w in body["warnings"])


def test_a_very_long_pdf_is_bounded_in_time_and_does_not_crash(api, dbdir):
    t0 = time.perf_counter()
    r = _post_pdf(api, _pdf(pages=80))
    assert r.status_code < 500 and time.perf_counter() - t0 < 60


def test_p1_a_scanned_po_is_flagged_as_read_from_a_picture(api, dbdir):
    from PIL import Image, ImageDraw, ImageFont

    img = Image.new("RGB", (900, 400), "white")
    d = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("arial.ttf", 44)
    except OSError:
        pytest.skip("no font")
    d.text((30, 40), "PURCHASE ORDER", fill="black", font=font)
    d.text((30, 120), "Customer PO 45009", fill="black", font=font)
    d.text((30, 200), "Total $100.00", fill="black", font=font)
    doc = fitz.open()
    pg = doc.new_page(width=450, height=200)
    buf = io.BytesIO(); img.save(buf, format="PNG")
    pg.insert_image(pg.rect, stream=buf.getvalue())
    r = _post_pdf(api, doc.tobytes())
    if r.status_code != 200 or "customer_po_number" not in r.json()["fields"]:
        pytest.skip("OCR did not read the synthetic scan here")
    body = r.json()
    f = body["fields"]["customer_po_number"]
    assert f["confidence"] < 0.9 or f["method"] != "rule" or any("scan" in w.lower() or "picture" in w.lower() for w in body["warnings"])


def test_the_quote_text_form_has_a_size_limit_or_is_fast(api, dbdir):
    big = "plain words with no numbers at all " * 40000          # about 1.4 MB
    t0 = time.perf_counter()
    r = api("post", "/extract/vendor-quote", data={"text": big})
    assert r.status_code in (200, 400, 413, 422) and time.perf_counter() - t0 < 30


# =============================================================== feedback and the stats

def _extraction():
    return extract.order_from_email(text=EMAIL, sender="b@somewhere-example.test")


def test_feedback_counts_kept_changed_blank_and_added_per_field(api, dbdir):
    out = _extraction()
    po = out["fields"]["customer_po_number"]["value"]
    r = api("post", "/extract/feedback", json={"extraction_id": out["extraction_id"], "fields": {"customer_po_number": po, "total": "999.00", "payment_terms": "", "notes": "typed"}})
    assert r.status_code == 200
    got = r.json()["counted"]
    assert got["customer_po_number"]["outcome"] == "kept" and got["total"]["outcome"] == "changed" and got["payment_terms"]["outcome"] == "blank" and got["notes"]["outcome"] == "added"
    assert api("post", "/extract/feedback", json={"extraction_id": "no-such-id", "fields": {}}).status_code == 404


def test_f1_one_extraction_is_counted_once(api, dbdir):
    out = _extraction()
    body = {"extraction_id": out["extraction_id"], "fields": {"total": "1.00"}}
    before = api("get", "/ai/extraction-stats", role="MANAGER").json()
    for _ in range(5):
        api("post", "/extract/feedback", json=body)
    after = api("get", "/ai/extraction-stats", role="MANAGER").json()
    assert after["forms_reviewed"] - before["forms_reviewed"] <= 1


def test_f2_feedback_field_names_are_limited_to_the_fields_that_were_shown(api, dbdir):
    out = _extraction()
    junk = {f"<b>{i}</b>" * 3: "x" for i in range(300)}
    r = api("post", "/extract/feedback", json={"extraction_id": out["extraction_id"], "fields": junk})
    stats = api("get", "/ai/extraction-stats", role="MANAGER").json()
    assert r.status_code in (400, 422) or not any("<b>" in row["field"] for row in stats["fields"])


def test_extraction_stats_need_a_manager_and_the_days_are_bounded(api, dbdir):
    assert api("get", "/ai/extraction-stats", role="OPERATOR").status_code == 403
    for d, code in ((0, 422), (3651, 422), (-5, 422), (180, 200)):
        assert api("get", "/ai/extraction-stats", role="MANAGER", params={"days": d}).status_code == code


# =============================================================== call note

def test_call_note_with_ai_off_makes_a_rule_note_and_a_failing_ai_never_breaks_it(api, dbdir, monkeypatch):
    from app.services.ai import features

    monkeypatch.setattr(features, "summarize_call", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no network")))
    text = "Maria called about PO 45009. We will send the drawing on 2026-12-01. They need a revised quote."
    r = api("post", "/extract/call-to-job-note", json={"text": text, "use_ai": True})
    assert r.status_code == 200 and r.json()["ai_used"] is False and r.json()["warnings"]
    assert "45009" in r.json()["fields"]["po_numbers"]["value"]
    assert api("post", "/extract/call-to-job-note", json={"text": "short"}).status_code == 422


# =============================================================== prefill and duplicate template

def test_prefill_is_operator_plus_and_shows_where_each_value_came_from(api, dbdir):
    assert api("get", "/prefill/order", role="VIEWER").status_code == 403
    r = api("get", "/prefill/nonsense")
    assert r.status_code == 422
    names = [str(r.get("customer_name") or "") for r in extract._customers().values() if r.get("customer_name")]
    if names:
        body = api("get", "/prefill/order", params={"customer": names[0]}).json()
        assert all("source" in v for v in body["values"].values())


def test_prefill_does_not_match_a_partial_or_wildcard_customer_name(api, dbdir):
    for c in ("%", "_", "a", "' OR 1=1 --", "*"):
        body = api("get", "/prefill/order", params={"customer": c}).json()
        assert body["values"] == {}, (c, body)


def test_duplicate_template_drops_numbers_dates_and_status_and_404s_an_unknown_record(api, dbdir):
    from app.services.coordination import rfqs

    r = rfqs.create({"customer": "Dup Test Co", "subject": f"dup {TAG}", "items": [{"description": "liner", "quantity": 2}]}, {"email": "m@alliedweldingsystems.com", "role": "MANAGER"}, announce=False)
    got = api("get", f"/records/rfq/{r['number']}/duplicate-template").json()
    assert got["body"].get("customer") == "Dup Test Co" and not {"number", "id", "status", "created_at"} & set(got["body"])
    assert api("get", "/records/rfq/NOPE-1/duplicate-template").status_code == 404
    assert api("get", "/records/bogus/x/duplicate-template").status_code == 422


# =============================================================== bulk

def test_bulk_is_a_dry_run_by_default_and_changes_nothing(api, dbdir):
    from app.services import organize

    ref = f"BULK-{TAG}-1"
    r = api("post", "/records/bulk", json={"kind": "rfq", "refs": [ref], "changes": {"add_tags": ["urgent"]}})
    assert r.status_code == 200 and r.json()["dry_run"] is True and organize.tags_for("rfq", ref) == []


def test_bulk_limits_roles_and_unknown_changes(api, dbdir):
    refs = [f"B-{i}" for i in range(101)]
    assert api("post", "/records/bulk", json={"kind": "rfq", "refs": refs, "changes": {"add_tags": ["a"]}}).status_code == 422
    assert api("post", "/records/bulk", json={"kind": "rfq", "refs": ["A"] * 500, "changes": {"add_tags": ["a"]}}).status_code == 200          # duplicates collapse to one
    for bad in ({"status": "CANCELLED"}, {"total": "1"}, {"owner": "x@y.test", "status": "x"}, {}):
        assert api("post", "/records/bulk", json={"kind": "rfq", "refs": ["A"], "changes": bad}).status_code == 422
    assert api("post", "/records/bulk", json={"kind": "nonsense", "refs": ["A"], "changes": {"add_tags": ["a"]}}).status_code == 422
    assert api("post", "/records/bulk", role="VIEWER", json={"kind": "rfq", "refs": ["A"], "changes": {"add_tags": ["a"]}}).status_code == 403
    for payload in ({"owner": "boss@alliedweldingsystems.com"}, {"ball_with": "customer"}, {"owner": "a@b.test", "add_tags": ["x"]}):
        r = api("post", "/records/bulk", role="OPERATOR", json={"kind": "rfq", "refs": ["A"], "changes": payload, "dry_run": False})
        assert r.status_code == 403, payload
    from datetime import datetime, timedelta, timezone

    from app.core.database import SessionLocal
    from app.models.db_models import RfqDB, StaffContactDB
    from app.services.coordination import notify

    with SessionLocal() as db:                                                      # the record and the owner must really exist (round 31 B2/B4)
        now_ = datetime.now(timezone.utc)
        db.add(RfqDB(number=f"BM-{TAG}", customer="C", subject="s", ack_due_at=now_ + timedelta(days=1), due_at=now_ + timedelta(days=2)))
        db.merge(StaffContactDB(email="boss@alliedweldingsystems.com", name="Boss", role_tag="leadership", phone=None, channels=["email"], daily_summary=False, active=True))
        db.commit()
    notify._contacts_cache.update(at=0.0, value=None)
    ok = api("post", "/records/bulk", role="MANAGER", json={"kind": "rfq", "refs": [f"BM-{TAG}"], "changes": {"owner": "boss@alliedweldingsystems.com"}, "dry_run": False})
    assert ok.status_code == 200 and ok.json()["changed"] == 1


def test_b1_a_tag_given_as_a_string_is_one_tag_or_refused(api, dbdir):
    ref = f"BS-{TAG}"
    r = api("post", "/records/bulk", json={"kind": "rfq", "refs": [ref], "changes": {"add_tags": "urgent"}})
    rows = r.json()["rows"][0]["changes"] if r.status_code == 200 else []
    assert r.status_code == 422 or rows == ["add tag urgent"]


def test_b2_a_record_that_does_not_exist_is_not_reported_changed(api, dbdir):
    ref = f"NO-SUCH-RFQ-{TAG}"
    r = api("post", "/records/bulk", role="MANAGER", json={"kind": "rfq", "refs": [ref], "changes": {"add_tags": ["x"]}, "dry_run": False})
    assert r.json()["changed"] == 0


def test_b3_the_number_of_tags_per_call_is_capped(api, dbdir):
    tags = [f"t{i}" for i in range(1000)]
    r = api("post", "/records/bulk", json={"kind": "rfq", "refs": [f"BT-{TAG}"], "changes": {"add_tags": tags}})
    assert r.status_code == 422


def test_b4_an_owner_must_be_a_real_address(api, dbdir):
    r = api("post", "/records/bulk", role="MANAGER", json={"kind": "rfq", "refs": [f"BO-{TAG}"], "changes": {"owner": "<script>alert(1)</script>"}, "dry_run": False})
    assert r.status_code == 422 or r.json()["changed"] == 0


# =============================================================== remembered choices

def test_defaults_are_per_person_limited_to_known_keys_and_blank_forgets(api, dbdir):
    a = api("put", "/me/defaults", role="OPERATOR", json={"currency": "USD", "last_customer": "Acme"})
    assert a.status_code == 200 and a.json() == {"currency": "USD", "last_customer": "Acme"}
    assert api("get", "/me/defaults", role="MANAGER").json() == {}                         # another person never sees them
    assert api("put", "/me/defaults", json={"password": "x"}).status_code == 422
    assert api("put", "/me/defaults", json={"currency": "x" * 121}).status_code == 422
    assert api("put", "/me/defaults", json={"currency": ""}).json() == {"last_customer": "Acme"}
    assert api("put", "/me/defaults", json=["a"]).status_code == 422


def test_d1_simultaneous_saves_by_different_people_all_survive(dbdir):
    users = [{"email": f"u{i}-{TAG}@alliedweldingsystems.com", "role": "OPERATOR"} for i in range(12)]
    ts = [threading.Thread(target=lambda u=u: assist.put_defaults(u, {"currency": "EUR"})) for u in users]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert all(assist.get_defaults(u) == {"currency": "EUR"} for u in users)


def test_d2_a_damaged_defaults_file_does_not_wipe_other_peoples_choices(dbdir):
    a, b = {"email": f"a-{TAG}@alliedweldingsystems.com", "role": "OPERATOR"}, {"email": f"b-{TAG}@alliedweldingsystems.com", "role": "OPERATOR"}
    assist.put_defaults(a, {"currency": "USD"})
    (dbdir / "user_defaults.json").write_text('{"a": {"currency": "USD"', encoding="utf-8")
    assist.put_defaults(b, {"currency": "EUR"})
    assert assist.get_defaults(a) == {"currency": "USD"} or (dbdir / "user_defaults.json.bak").exists()
