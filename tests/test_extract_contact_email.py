"""The pasted-email reader takes the contact from the sender or the From: line, never an Allied address, and always asks a person to check it. No database; nothing is saved or sent."""
from app.services import extract


def _rfq(text, sender=""):
    return extract.rfq_from_email(text=text, sender=sender, subject="Quote please")


def test_the_from_line_is_preferred_over_other_addresses_in_the_body():
    out = _rfq("From: Dana Buyer <dana@buyerco.test>\nTo: sales@alliedweldingsystems.com\nPlease quote 4 plates. Our AP is ap@buyerco.test")
    f = out["fields"]["contact_email"]
    assert f["value"] == "dana@buyerco.test" and f["source"]["text"] == "the From: line" and "contact_email" in out["needs_check"]


def test_allied_addresses_are_never_the_contact_even_as_the_sender():
    out = _rfq("Forwarded message\nFrom: Dana <dana@buyerco.test>\nPlease quote", sender="Doug <doug@alliedweldingsystems.com>")
    assert out["fields"]["contact_email"]["value"] == "dana@buyerco.test"
    only_us = _rfq("From: doug@alliedweldingsystems.com\nTo: me@mail.alliedweldingsystems.com\nNothing else", sender="doug@alliedweldingsystems.com")
    assert "contact_email" not in only_us["fields"]


def test_an_address_in_the_text_is_the_last_resort_and_less_sure():
    out = _rfq("Hello, please quote. Reach me at sam@somewhere.test")
    f = out["fields"]["contact_email"]
    assert f["value"] == "sam@somewhere.test" and f["confidence"] < 0.9 and "contact_email" in out["needs_check"]
