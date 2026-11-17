"""The whole pipeline in one working story, wired together the way it runs for real:

RFQ email -> AI reads it -> RFQ logged + row for the Open RFQs board and an acknowledgement queued + ONE alert ->
approved on a phone -> row staged for monday (never sent) -> the customer's PO arrives as a SIDEWAYS scan -> order made,
slip queued, customer PO (not our SO number) on every paper -> engineer (Mandarin) gets the ball, comments, a manager reads
English -> files and drawing compare -> Chinese vendor reply read -> time passes and the ball is chased -> freight priced ->
downloads and search -> monday push previewed and refused while read-only.
Fakes: Gmail in/out, the AI, OCR, translation, monday. Nothing external is contacted, nothing real is changed."""

import importlib
import io
import uuid
from datetime import date, timedelta
from types import SimpleNamespace

import pytest
from openpyxl import load_workbook
from PIL import Image, ImageDraw

from app.config import settings
from app.core.database import SessionLocal
from app.models.db_models import AssistantActionDB, PackingSlipDB, RfqDB, StaffContactDB
from app.models.orders import InboundOrderRequest
from app.services import bridge, exports, monday_push, search
from app.services.assistant import quick
from app.services.coordination import ball, files, forms, replies, rfqs, updates, vendors
from app.services.order_orchestrator import OrderOrchestrator
from tests.conftest import staff_token
from tests.test_e2e_allied_use_cases import REVIEWER, TEST_INBOX, _alerts, _drafts, day  # noqa: F401

dss = importlib.import_module("app.services.document_scanner_service")
TAG = uuid.uuid4().hex[:5]
MGR, WEI = f"athish{TAG}@alliedweldingsystems.com", f"wei{TAG}@alliedweldingsystems.com"
BOSS = {"email": MGR, "role": "MANAGER"}
ENG = {"email": WEI, "role": "OPERATOR"}

PAGE_TEXT = "PURCHASE ORDER\nPO Number: PL{tag}\nCustomer: Owens Corning\nQty 2 Hammermill Screen Allied 300XL  $410.00\nTerms: Net 45"


class SidewaysOCR:
    """Reads the page only when it is the right way up, as real OCR does."""

    @staticmethod
    def image_to_string(img, *a, **k):
        ok = img.getpixel((50, 50)) < 80 and img.getpixel((img.width - 50, img.height - 50)) > 200 and img.width < img.height
        return PAGE_TEXT.format(tag=TAG) if ok else "ll1 Iu |ii ww nn xz q7 mm vv .. zq xx cc bb 11 || //"


def _sideways_scan_pdf():
    import pymupdf

    page = Image.new("L", (1200, 1600), 255)
    ImageDraw.Draw(page).rectangle([0, 0, 200, 200], fill=0)
    sideways = page.rotate(90, expand=True)                                    # the customer scanned it on its side
    buf = io.BytesIO()
    sideways.convert("RGB").save(buf, "PNG")
    doc = pymupdf.open()
    pg = doc.new_page(width=sideways.width * 0.5, height=sideways.height * 0.5)
    pg.insert_image(pg.rect, stream=buf.getvalue())
    return doc.tobytes()


def _dxf(hole_d):
    import ezdxf

    doc = ezdxf.new("R2018")
    doc.units = 4
    msp = doc.modelspace()
    msp.add_lwpolyline([(0, 0), (250, 0), (250, 120), (0, 120)], close=True)
    msp.add_circle((125, 60), hole_d / 2)
    msp.add_text("REV: B", height=4, dxfattribs={"insert": (5, 130)})
    out = io.StringIO()
    doc.write(out)
    return out.getvalue().encode()


def test_the_whole_pipeline_from_rfq_email_to_a_staged_monday_row_and_a_delivered_order(day, client, monkeypatch):  # noqa: F811
    from app.core.container import get_monday_client

    # ---------------------------------------------------------------- the world: monday connected but READ-ONLY, translation faked
    monkeypatch.setattr(settings, "MONDAY_RFQ_BOARD_ID", "1657738509")
    monkeypatch.setattr(settings, "MONDAY_READ_ONLY", True)
    monkeypatch.setattr(settings, "MONDAY_PUSH_MODE", "manual")
    mon = get_monday_client()
    monkeypatch.setattr(type(mon), "live", property(lambda self: True))
    monkeypatch.setattr(bridge, "_ask", lambda t, s, g, a: SimpleNamespace(translation=f"[{g}] {t}", unsure_terms=[]))
    for email, role, lang, name in ((MGR, "MANAGER", "en", f"Pipeline Manager {TAG}"), (WEI, "OPERATOR", "zh", "王伟")):
        staff_token(email, role)
        bridge.set_language(email, lang)
        with SessionLocal() as db:
            if db.get(StaffContactDB, email) is None:
                db.add(StaffContactDB(email=email, name=name, role_tag="staff", channels=["email"], active=True))
            db.commit()
    with SessionLocal() as db:
        db.query(monday_push.MondayChangeDB).delete()
        db.commit()

    # ---------------------------------------------------------------- 1. an RFQ email arrives
    mid = day["gmail"].add("Buyer <buyer@owenscorning.test>", f"RFQ - hammermill screens {TAG}",
                           "Please quote 2 Hammermill Screen Allied 300XL per the attached drawing. Need by end of January.")
    day["read_mail"]()
    with SessionLocal() as db:
        rfq = db.query(RfqDB).filter(RfqDB.email_id == mid).one()
    assert rfq.complexity == "COMPLEX" and rfq.status == "NEW"
    ack = next(d for d in _drafts("rfq_acknowledge") if (d.args or {}).get("_rfq_id") == rfq.id)
    row = next(d for d in _drafts("rfq_board_row") if (d.args or {}).get("_rfq_id") == rfq.id)
    ready = [n for n in _alerts("autopilot-drafts:") if REVIEWER in n.recipients and rfq.number in n.body]
    assert len(ready) == 1 and ready[0].link == "/inbox?show=approvals"                      # ONE "drafts are ready" alert covers both
    assert day["sent"] == [] or all("Received" not in s for s, _, _ in day["sent"])          # nothing has left the building

    # ---------------------------------------------------------------- 2. approved: the ack goes (to the test inbox only); the row is STAGED, not sent
    t = quick.token(ack.id, REVIEWER)
    assert client.post(f"/api/v1/assistant/quick/{ack.id}", data={**t, "decision": "send"}).status_code == 200
    assert day["sent"][-1][1] == [TEST_INBOX] and rfqs.get(rfq.id)["status"] == "ACKNOWLEDGED"
    from app.services.assistant import agent

    out = agent.decide(row.id, True, {"email": REVIEWER, "role": "MANAGER"})
    assert "Staged (not sent yet)" in str(out)
    staged = [c for c in monday_push.changes("STAGED") if c["kind"] == "create_item" and c["payload"].get("rfq_id") == rfq.id]
    assert len(staged) == 1
    pushed = monday_push.push(who=MGR)
    assert pushed["blocked"] == "read-only" and pushed["pushed"] == 0                        # the "git push" refuses while read-only
    assert "create_item" in str(monday_push.preview([staged[0]["id"]])[0]["kind"]) and settings.MONDAY_READ_ONLY is True

    # ---------------------------------------------------------------- 3. the customer's PO arrives as a SIDEWAYS scan
    monkeypatch.setattr(dss, "pytesseract", SidewaysOCR)
    monkeypatch.setattr(dss, "init_tesseract", lambda: "fake")
    scan = dss.document_scanner_service.scan_document(_sideways_scan_pdf(), f"scan {TAG}.pdf")
    assert scan.document_type.value == "CUSTOMER_PO"
    po = scan.fields["customer_po_number"].normalized_value
    assert po == f"PL{TAG}" and scan.fields["payment_terms"].raw_value == "Net 45 Days" and scan.line_items
    request = InboundOrderRequest(customer_name=scan.fields["customer_name"].normalized_value, customer_po_number=po,
                                  shipping_address=scan.fields["shipping_address"].normalized_value,
                                  line_items=[{"raw_item_text": l.description, "quantity": l.quantity, "unit_price": l.unit_price or 410} for l in scan.line_items])
    order = OrderOrchestrator().process_inbound_order(request)
    so = str(order.sales_order_number)
    with SessionLocal() as db:
        slip = db.query(PackingSlipDB).filter(PackingSlipDB.sales_order_number == so).one()
    assert slip.status == "READY" and slip.customer_po_number == po                           # queued and made on creation
    revised = OrderOrchestrator().process_inbound_order(InboundOrderRequest(**{**request.model_dump(), "line_items": [
        {**request.line_items[0].model_dump(), "quantity": 9}]}))
    assert revised.sales_order_number == order.sales_order_number and any("NOT changed" in m for m in revised.messages)   # a revised PO is flagged

    # ---------------------------------------------------------------- 4. the engineer (Mandarin) gets the ball; a manager reads English
    ball.set_ball("order", so, BOSS, "engineer", WEI, owner=MGR, note="Check revision C against the PO", due=str(date.today() + timedelta(days=5)))
    c = updates.post("order", so, "我们需要确认图纸版本", ENG)
    english = updates.thread("order", so, MGR, "en")[-1]
    assert english["author_language"] == "zh" and english["translation"]["text"].startswith("[en]")

    # ---------------------------------------------------------------- 5. files and drawing compare
    f = files.add("order", so, "vendor rev.dxf", _dxf(45), ENG, update_id=c["id"])
    assert updates.thread("order", so, MGR, "en")[-1]["files"][0]["filename"] == "vendor rev.dxf"
    from app.services import drawing_compare

    diff = drawing_compare.compare(_dxf(40), "ours.dxf", files.open_file(f["id"])[0].read_bytes(), "theirs.dxf")
    assert diff["geometry"]["circle_diameters_mm"] == {"only_on_first": [40.0], "only_on_second": [45.0]}

    # ---------------------------------------------------------------- 6. the design form must be complete; a Chinese vendor replies
    with pytest.raises(forms.FormError):
        forms.validate(forms.get("design-release"), {"drawing_revision": "x"})
    vendors.create({"vendor": f"KINGCERA{TAG}", "description": "Cyclone", "po_number": "9277", "ordered_on": str(date.today() - timedelta(days=20))}, BOSS)
    monkeypatch.setattr(settings, "AI_PROVIDER", "off")
    when = date.today() + timedelta(days=14)
    said = replies.read("sales@kingcera.test", "RE: PO 9277", f"您好，预计{when.year}年{when.month}月{when.day}日发货，附照片", date.today())
    assert said["ship_date"] == when and said["photos"] is True
    assert replies.read("sales@kingcera.test", "RE: PO 9277", "Expected ship 12/11", date.today())["unclear"] is True

    # ---------------------------------------------------------------- 7. time passes: the ball is chased up the ladder
    monkeypatch.setattr(ball, "_days", lambda since: 7.0)
    chased = ball.run()
    assert sum(chased.values()) >= 1 and ball.get("order", so)["status"] == "leadership told"

    # ---------------------------------------------------------------- 8. freight priced, customs number free of our SO number
    from app.services.logistics_dispatcher import logistics_dispatcher as d

    quotes = d.get_carrier_quotes("HQ", "Receiving, 1 Main St, Irving, TX 75063")
    assert quotes and all("estimate" in q.service_level or "rate card" in q.service_level for q in quotes)
    cci = d.generate_customs_invoice(so_number=so, customer_name="Owens Corning", shipping_address="Edmonton, AB", origin_facility="E",
                                     items=[{"item_id": "X", "quantity": 1, "unit_price": 5}], customer_po=po)
    assert cci.cci_number.startswith(f"CCI-2026-PL{TAG}") and so not in cci.cci_number

    # ---------------------------------------------------------------- 9. downloads and search find everything
    rows = list(load_workbook(io.BytesIO(exports.to_xlsx("Orders", *exports.dataset("orders", manager=True)[1:]))).active.iter_rows(values_only=True))
    assert any(r[0] == po for r in rows[1:])
    kinds = {g["type"] for g in search.search(f"PL{TAG}")["groups"]}
    assert {"order", "rfq"} & kinds and "file" in {g["type"] for g in search.search("vendor rev")["groups"]}
    assert any(g["type"] == "comment" for g in search.search("图纸版本")["groups"])        # CJK text in a comment is searchable too

    # ---------------------------------------------------------------- 10. nothing was ever sent to monday; the only mail went to the test inbox
    assert monday_push.status()["staged"] >= 1 and monday_push.status()["read_only"] is True
    assert all(to == [TEST_INBOX] for _, to, _ in day["sent"])
