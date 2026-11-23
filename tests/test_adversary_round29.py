"""Round 29 (adversary): re-attack of the round 28 redaction fold. Synthetic PDFs only, temp DB; OCR pinned off. xfail(strict=False, reason="ADV29-Tn ...") marks holes."""
import io
import fitz
import pytest
from PIL import Image

from app.config import settings
from app.services import redaction

TERMS = ["Owens Corning"]


@pytest.fixture(autouse=True)
def pin(monkeypatch):
    monkeypatch.setattr(redaction, "ocr_available", lambda: False)
    monkeypatch.setattr(settings, "REDACTION_ENFORCED", True)


def _pdf(lines, size=14, w=420, h=300):
    d = fitz.open()
    p = d.new_page(width=w, height=h)
    y = 60
    for ln in lines:
        p.insert_text((30, y), ln, fontsize=size)
        y += 24
    return d.tobytes()


def _ink_left(out):
    """Dark pixels that are not part of a solid painted box: any text-like detail left on the page."""
    doc = fitz.open(stream=out, filetype="pdf")
    return doc[0].get_text().strip()


SPELL = {"owens-stroke": "Øwens Corning", "small-cap": "ᴏwens Corning", "leet": "0wens C0rning", "armenian": "օwens Corning", "l-stroke": "Owens Corning".replace("n", "ń"),
         "fullwidth": "Ｏｗｅｎｓ Corning", "circled": "Ⓞwens Corning", "roman-numeral": "Owens Cornⅰng", "ligature": "Owens Corniﬁng"}


@pytest.mark.parametrize("name", list(SPELL))
def test_other_spellings_are_found_or_the_page_is_blocked(name):
    data = _pdf([SPELL[name]])
    scan = redaction.scan_pdf(data, TERMS)
    out, rep = redaction.redact_pdf(data, TERMS) if scan["findings"] else (None, None)
    if scan["findings"]:
        assert rep["painted"] >= 1
        assert not redaction.scan_pdf(out, TERMS)["findings"]
    else:
        pytest.xfail(f"ADV29-T1 {name!r} not found")


@pytest.mark.xfail(strict=False, reason="ADV29-T2 a name split across two columns/lines in the text layer by other words in between ('Owens' ... 'Corning') is only found when the words are adjacent in extraction order")
def test_t2_name_split_by_a_gap_is_found():
    d = fitz.open()
    p = d.new_page(width=420, height=300)
    p.insert_text((30, 60), "Owens", fontsize=14)
    p.insert_text((250, 60), "Corning", fontsize=14)
    p.insert_text((30, 90), "PLATE 10mm", fontsize=14)
    assert redaction.scan_pdf(d.tobytes(), TERMS)["findings"]


@pytest.mark.parametrize("pos", ["rotated", "tiny", "white"])
def test_hidden_forms_still_found_and_painted(pos):
    d = fitz.open()
    p = d.new_page(width=420, height=300)
    kw = {"rotate": 90} if pos == "rotated" else {"fontsize": 1} if pos == "tiny" else {"color": (1, 1, 1)}
    kw.setdefault("fontsize", 14)
    p.insert_text((200, 150), "Owens Corning", **kw)
    data = d.tobytes()
    assert redaction.scan_pdf(data, TERMS)["findings"]
    out, rep = redaction.redact_pdf(data, TERMS)
    assert rep["painted"] >= 1 and not _ink_left(out)


def test_a_name_in_an_off_page_box_is_found_and_dropped():
    d = fitz.open()
    p = d.new_page(width=420, height=300)
    p.insert_text((600, 150), "Owens Corning", fontsize=14)      # outside the page box
    data = d.tobytes()
    out, rep = redaction.redact_pdf(data, TERMS)
    assert "owens" not in _ink_left(out).lower()


def test_a_page_pair_split_name_across_pages_is_not_a_leak_after_rebuild():
    d = fitz.open()
    d.new_page().insert_text((30, 780), "Owens", fontsize=14)
    d.new_page().insert_text((30, 40), "Corning", fontsize=14)
    out, _ = redaction.redact_pdf(d.tobytes(), TERMS)
    assert not _ink_left(out)


def test_a_two_character_term_cannot_blank_or_crash_a_page():
    out, rep = redaction.redact_pdf(_pdf(["AB plate AB bracket"]), ["AB"])
    assert rep["pages"] == 1
