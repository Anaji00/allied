"""Plain wording from the live review: a bare account name is not a person (Logistics), "No forwarder yet", the ball summary's nobody-named holder counted on its own, and a mail draft saying what will
really happen to it (draft only, or redirected while testing). Temporary database."""
import uuid

import pytest

from app.config import settings
from app.core.database import SessionLocal
from app.models.db_models import ShipmentDB
from app.services.assistant import agent
from app.services.coordination import ball, rfqs, shipments

BOSS = {"email": "boss.word@alliedweldingsystems.com", "role": "MANAGER"}


@pytest.mark.parametrize("email,want", [("najialessio1@gmail.com", "Not assigned"), ("bob7@x.test", "Not assigned"), ("jane.doe@x.test", "Jane Doe"), ("karen_smith@x.test", "Karen Smith"), (None, None), ("", None)])
def test_a_bare_account_name_with_digits_is_not_shown_as_a_person(email, want):
    assert shipments.owner_name(email) == want


def test_a_shipment_row_says_not_assigned_and_no_forwarder_yet():
    with SessionLocal() as db:
        s = ShipmentDB(forwarder=None, mode="SEA", reference=f"W{uuid.uuid4().hex[:5]}", status="BOOKED", owner_email="najialessio1@gmail.com")
        db.add(s)
        db.commit()
        sid = s.id
    row = next(r for r in shipments.list_shipments() if r["id"] == sid)
    assert row["owner_name"] == "Not assigned" and row["forwarder_label"] == "No forwarder yet"
    with SessionLocal() as db:
        db.get(ShipmentDB, sid).forwarder = "Zibo Fwd"
        db.commit()
    assert next(r for r in shipments.list_shipments() if r["id"] == sid)["forwarder_label"] == "Zibo Fwd"


def test_the_ball_summary_counts_items_with_nobody_named_separately_and_never_lists_a_question_mark():
    r = rfqs.create({"customer": "NoOwnerCo", "subject": f"no owner {uuid.uuid4().hex[:4]}"}, BOSS, announce=False)
    ball.set_ball("rfq", r["number"], BOSS, ball_with="vendor", ball_person="Named Vendor")
    out = ball.summary()
    assert "Named Vendor" in [h["name"] for h in out["by_holder"]]
    assert "?" not in [h["name"] for h in out["by_holder"]] and out["no_owner_set"]["label"] == "No owner set" and out["no_owner_set"]["count"] >= 0


@pytest.mark.parametrize("configure,want", [({"SMTP_HOST": "", "EMAIL_REDIRECT_ALL_TO": ""}, "Draft only: nothing is sent"),
                                            ({"SMTP_HOST": "smtp.x.test", "EMAIL_SEND_ENABLED": True, "SMTP_USERNAME": "u", "SMTP_PASSWORD": "p", "EMAIL_REDIRECT_ALL_TO": "pilot@x.test"}, "Redirected to pilot@x.test while testing"),
                                            ({"SMTP_HOST": "smtp.x.test", "EMAIL_SEND_ENABLED": True, "SMTP_USERNAME": "u", "SMTP_PASSWORD": "p", "EMAIL_REDIRECT_ALL_TO": ""}, None)])
def test_a_draft_says_what_will_really_happen_to_it(monkeypatch, configure, want):
    for k, v in configure.items():
        monkeypatch.setattr(settings, k, v)
    assert agent.delivery_note() == want
