"""A follow-up draft is shown under a 'who owes us' row only when its reference is a whole token in the draft and the recipient agrees. Temporary database; nothing is sent."""
import uuid

from app.core.database import SessionLocal
from app.models.db_models import AssistantActionDB
from app.services import my_day
from app.services.assistant import agent
from app.services.coordination import ball

USER = {"email": "ops.chase@alliedweldingsystems.com", "role": "MANAGER"}


def _draft(args, summary):
    did = uuid.uuid4().hex
    with SessionLocal() as db:
        db.add(AssistantActionDB(id=did, user_email=USER["email"], user_role="MANAGER", tool="send_email", args=args, summary=summary, status="PENDING"))
        db.commit()
    return {"id": did, "kind": "rfq_vendor_chase", "summary": summary, "created_at": "2026-11-20T00:00:00+00:00"}


def _holder(ref, name):
    return {"item_type": "po", "item_ref": ref, "ball_with": "vendor", "ball_name": name, "ball_label": "Vendor", "resolved": False, "days_waiting": 36, "label": f"{name} PO {ref}", "note": "a delivery update"}


def _rows(monkeypatch, drafts, holders):
    monkeypatch.setattr(agent, "pending", lambda user: drafts)
    monkeypatch.setattr(ball, "list_open", lambda *a, **k: holders)
    return my_day.chase(USER)["items"]


def test_a_draft_about_something_else_is_not_put_under_the_row_even_when_the_number_is_inside_its_text(monkeypatch):
    other = _draft({"to": "helen@intrepidpotash.test", "subject": "Price on RFQ-2026-0012", "body": "Please quote. Ref 192823"}, "Ask Helen for a price on RFQ-2026-0012 (Intrepid Potash)")
    rows = _rows(monkeypatch, [other], [_holder("9282", "KWS")])
    row = next(r for r in rows if r["id"] == "ball:po:9282")
    assert row["draft_id"] is None and row["send"] is None                                   # 9282 is inside 192823 only: not a whole token
    assert any(r["id"] == "draft:" + other["id"] for r in rows)                              # the draft still shows, in its own row


def test_the_recipient_must_agree_when_both_are_known_and_a_whole_token_match_with_the_right_vendor_is_kept(monkeypatch):
    wrong = _draft({"to": "helen@intrepidpotash.test", "subject": "About PO 9282"}, "Check in about PO 9282")
    right = _draft({"to": "sales@kws.test", "vendor_name": "KWS", "subject": "About PO 9282"}, "Check in with KWS on PO 9282")
    rows = _rows(monkeypatch, [wrong, right], [_holder("9282", "KWS")])
    row = next(r for r in rows if r["id"] == "ball:po:9282")
    assert row["draft_id"] == right["id"]
    assert any(r["id"] == "draft:" + wrong["id"] for r in rows)


def test_one_vendor_name_inside_another_is_not_the_same_vendor_and_a_company_suffix_is_ignored(monkeypatch):
    steel = _draft({"to": "sales@kwssteel.test", "vendor_name": "KWS Steel", "subject": "About PO 9282"}, "Check in with KWS Steel on PO 9282")
    rows = _rows(monkeypatch, [steel], [_holder("9282", "KWS")])
    assert next(r for r in rows if r["id"] == "ball:po:9282")["draft_id"] is None            # KWS is not KWS Steel
    plain = _draft({"to": "sales@kws.test", "vendor_name": "KWS", "subject": "About PO 9283"}, "Check in with KWS on PO 9283")
    rows = _rows(monkeypatch, [plain], [_holder("9283", "KWS Steel")])
    assert next(r for r in rows if r["id"] == "ball:po:9283")["draft_id"] is None            # and KWS Steel is not KWS
    ltd = _draft({"vendor_name": "Zibo Ltd", "to": "a@zibo.test", "subject": "PO 77"}, "Check in with Zibo Ltd on PO 77")
    rows = _rows(monkeypatch, [ltd], [_holder("77", "Zibo")])
    assert next(r for r in rows if r["id"] == "ball:po:77")["draft_id"] == ltd["id"]         # "Ltd" is only a suffix


def test_a_reference_inside_a_longer_or_dashed_reference_is_not_a_match(monkeypatch):
    longer = _draft({"to": "sales@kws.test", "vendor_name": "KWS", "subject": "About PO 92821 and RFQ-2026-9282-B"}, "Check in with KWS on PO 92821")
    rows = _rows(monkeypatch, [longer], [_holder("9282", "KWS")])
    assert next(r for r in rows if r["id"] == "ball:po:9282")["draft_id"] is None
    exact = _draft({"to": "sales@kws.test", "vendor_name": "KWS", "subject": "About PO-9282."}, "Check in with KWS on PO 9282, please")
    rows = _rows(monkeypatch, [exact], [_holder("9282", "KWS")])
    assert next(r for r in rows if r["id"] == "ball:po:9282")["draft_id"] == exact["id"]
