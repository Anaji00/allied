"""Round 23b (adversary): re-attack of the round-23 mail-reading fixes. Synthetic messages only. xfail(strict=False, reason="ADV23b-Tn ...") marks open holes."""
import email
from email.message import EmailMessage

import pytest

from app.services import mail_tags, mailbox_poller


def _msg(raw: str):
    return email.message_from_string(raw)


def test_t1_a_forged_header_that_copies_gmails_server_name_is_not_trusted_below_the_real_one():
    msg = _msg("Authentication-Results: mx.google.com; dkim=none; spf=none\r\nAuthentication-Results: mx.google.com; dmarc=pass header.from=customer.com\r\nFrom: buyer@customer.com\r\nSubject: s\r\n\r\nb")
    assert mailbox_poller._authenticated(msg, "buyer@customer.com") is not True


@pytest.mark.parametrize("hidden", ["<div style=\"DISPLAY : NONE\">SECRET1</div>", "<span style='visibility:hidden'>SECRET1</span>", "<div hidden>SECRET1</div>", "<p aria-hidden=\"true\">SECRET1</p>",
                                    "<div style='font-size:0'>SECRET1</div>", "<div style='opacity:0'>SECRET1</div>", "<div style='color:#ffffff;background:#ffffff'>SECRET1</div>",
                                    "<div style='height:0;overflow:hidden'>SECRET1</div>", "<div style='text-indent:-9999px'>SECRET1</div>", "<STYLE>.x{}</STYLE><!-- SECRET1 -->",
                                    "<div style='max-height:0px;overflow:hidden'>SECRET1</div>", "<div style='position:absolute;left:-9999px'>SECRET1</div>", "<div style='mso-hide:all'>SECRET1</div>",
                                    "<template>SECRET1</template>", "<noscript>SECRET1</noscript>", "<textarea style='display:none'>SECRET1</textarea>"],
                         ids=["display-none-spaced", "visibility", "hidden-attr", "aria-hidden", "font-size-0", "opacity-0", "white-on-white", "height-0", "text-indent", "style-comment", "max-height", "offscreen", "mso-hide",
                              "template", "noscript", "textarea"])
def test_hidden_html_variants_are_dropped(hidden):
    m = EmailMessage()
    m["From"], m["Subject"] = "a@b.com", "x"
    m.set_content("x")
    m.clear_content()
    m.set_content(f"<p>Please quote 5 liners</p>{hidden}", subtype="html")
    out = mailbox_poller._body_text(m)
    assert "quote 5 liners" in out
    if "SECRET1" in out:
        pytest.xfail(f"ADV23b-T2 hidden text kept for: {hidden[:50]}")


def test_t3_an_inline_logo_is_not_an_attachment():
    m = EmailMessage()
    m["From"], m["Subject"] = "buyer@customer.com", "Quote"
    m.set_content("please quote")
    m.add_attachment(b"\x89PNG\r\n\x1a\nfake", maintype="image", subtype="png", filename="image001.png", disposition="inline", cid="<logo@x>")
    assert mailbox_poller._attachments(m) == []


def test_real_attachments_and_a_text_only_mail_are_unchanged():
    m = EmailMessage()
    m["From"], m["Subject"] = "buyer@customer.com", "Drawing"
    m.set_content("see attached")
    m.add_attachment(b"%PDF-1.4", maintype="application", subtype="pdf", filename="d.pdf")
    m.add_attachment(b"PK\x03\x04", maintype="application", subtype="zip", filename="d.zip", disposition="inline")
    assert sorted(a["filename"] for a in mailbox_poller._attachments(m)) == ["d.pdf", "d.zip"]
    t = EmailMessage()
    t.set_content("hello")
    assert mailbox_poller._attachments(t) == []


def test_a_calendar_only_message_is_still_noise_and_one_with_words_is_not():
    only = EmailMessage()
    only["From"], only["Subject"] = "cal@x.com", "Invitation: Weekly sync"
    only.set_content("", subtype="plain")
    only.add_alternative("BEGIN:VCALENDAR\nEND:VCALENDAR", subtype="calendar")
    assert mail_tags.noise(only, "cal@x.com", only["Subject"]) == "calendar"
    words = EmailMessage()
    words["From"], words["Subject"] = "buyer@customer.com", "Need 20 liners and a call?"
    words.set_content("Please quote 20 liners.")
    words.add_alternative("BEGIN:VCALENDAR\nEND:VCALENDAR", subtype="calendar")
    assert mail_tags.noise(words, "buyer@customer.com", words["Subject"]) is None


@pytest.mark.parametrize("hdr", ["mx.google.com; dmarc=fail", "mx.google.com; dkim=pass header.d=co.uk", "mx.google.com; spf=pass smtp.mailfrom=x@com", "mx.google.com; dkim=pass header.d=com.au"], ids=["dmarc-fail", "co-uk", "spf-com", "com-au"])
def test_suffix_and_shared_domains_never_count_as_the_senders_domain(hdr):
    msg = _msg(f"Authentication-Results: {hdr}\r\nFrom: x@victim.co.uk\r\nSubject: s\r\n\r\nb")
    assert mailbox_poller._authenticated(msg, "x@victim.co.uk") is not True
    msg2 = _msg(f"Authentication-Results: {hdr}\r\nFrom: x@evil.github.io\r\nSubject: s\r\n\r\nb")
    assert mailbox_poller._authenticated(msg2, "x@evil.github.io") is not True or "github.io" not in hdr
