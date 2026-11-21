"""Round 24b (adversary): re-attack of the round-24 lead/mail fixes. Temporary database, fake mail. xfail(strict=False, reason="ADV24b-Tn ...") marks open holes."""
import pytest

from tests.test_adversary_round24 import BOSS, OP, _lead, _mail, _u, outbox, _tidy, agent, leads  # noqa: F401


@pytest.mark.parametrize("body", ["Please remove me from your list.", "STOP", "Do not email me again.", "opt out", "Not interested, please stop contacting us.", "unsubscribe",
                                  "Please take us off your mailing list.", "Je souhaite me désabonner.", "请退订。", "Vui lòng hủy đăng ký."],
                         ids=["remove-from-list", "stop", "do-not-email", "opt-out", "not-interested", "unsub", "take-us-off", "french", "chinese", "vietnamese"])
def test_clear_stop_requests_are_not_missed(body):
    lid, em = _lead()
    out = leads.on_mail(_mail(em), em, "Re: Wear parts", body, True)
    if out != "unsubscribe":
        pytest.xfail(f"ADV24b-T1 a plain stop request is not recognised: {body!r} -> {out}")


def test_t4_a_long_real_reply_that_mentions_unsubscribe_is_a_reply_and_a_short_stop_is_a_stop():
    lid, em = _lead()
    long = "We are very interested in a quote for forty liners and a second set for the line two mill, delivered by March, with drawings attached for each part. " * 3
    assert leads.on_mail(_mail(em), em, "Re: x", long + " Also please unsubscribe my colleague Bob.", True) == "reply"
    lid2, em2 = _lead()
    assert leads.on_mail(_mail(em2), em2, "Re: x", "Unsubscribe", True) == "unsubscribe"


def test_t2_a_mailer_daemon_name_on_a_stranger_domain_is_not_a_bounce():
    lid, em = _lead()
    leads.on_mail(_mail("mailer-daemon@stranger.example.test"), "mailer-daemon@stranger.example.test", "Delivery Status Notification (Failure)", f"Message to {em} could not be delivered.", True)
    assert not leads.get_lead(lid)["bad_address"]


def test_t3_editing_the_to_address_of_a_lead_draft_cannot_bypass_the_list(outbox):
    lid, em = _lead()
    blocked_em = f"{_u('blk')}@{_u('co')}.example.test"
    leads.add_dnc(blocked_em, BOSS, "asked")
    got = leads.draft_outreach(lid, OP)
    try:
        agent.edit_action(got["action_id"], {"to": blocked_em}, OP)
        agent.decide(got["action_id"], True, OP)
    except Exception:  # noqa: BLE001
        pass
    assert not [m for m in outbox if blocked_em in str(m)]


@pytest.mark.parametrize("probe", ["ann+a+b@{d}", "ANN+Tag@{d}", "ann@Sub.{d}", "ann@sub.sub2.{d}", "ann@{d}.", "<ann@{d}>", "\"Ann Lee\" <ann@{d}>"],
                         ids=["double-plus", "upper-plus", "subdomain", "deep-subdomain", "trailing-dot", "angle", "quoted-name"])
def test_more_dnc_spellings_are_blocked(probe):
    dom = f"{_u('blk')}.example.test"
    leads.add_dnc(f"ann@{dom}", BOSS, "asked")
    assert leads.blocked(probe.format(d=dom))


def test_lookalikes_and_other_people_are_not_blocked():
    dom = f"{_u('ok')}.example.test"
    leads.add_dnc(f"ann@{dom}", BOSS, "asked")
    for probe in (f"joann@{dom}", f"ann@x{dom}", f"ann@{dom}.evil.test", f"bob@{dom}", f"ann@evil-{dom}"):
        assert not leads.blocked(probe), probe


@pytest.mark.parametrize("body", ["Please don't contact us again.", "Darme de baja por favor.", "Bitte abmelden.", "No thanks, remove us from this.", "Cease and desist: stop all email to this address.",
                                  "Unsubscribe me", "UNSUBSCRIBE!!!", "Stop." , "Please stop", "remove", "take me off the list"],
                         ids=["dont-contact", "spanish", "german", "remove-us", "cease", "unsub-me", "unsub-caps", "stop-dot", "please-stop", "remove-alone", "take-me-off"])
def test_more_stop_requests_are_recognised(body):
    lid, em = _lead()
    out = leads.on_mail(_mail(em), em, "Re: Wear parts", body, True)
    if out != "unsubscribe":
        pytest.xfail(f"ADV24c-T1 stop request not recognised: {body!r} -> {out}")


@pytest.mark.parametrize("body", ["Please don't stop sending quotes, we need them.", "We will not unsubscribe, keep the quotes coming.", "Thanks. How do I unsubscribe from the other newsletter? Anyway, send the quote.",
                                  "Stop by our booth next week", "Please remove the second line item from the quote."], ids=["dont-stop", "not-unsub", "other-newsletter", "stop-by", "remove-line"])
def test_ordinary_replies_are_not_read_as_stop(body):
    lid, em = _lead()
    out = leads.on_mail(_mail(em), em, "Re: Wear parts", body, True)
    if out == "unsubscribe":
        pytest.xfail(f"ADV24c-T2 an ordinary reply is read as a stop: {body!r}")
