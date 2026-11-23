"""Chinese reading in the OCR path. The decision logic is tested with a fake Tesseract (always runs); a real bilingual quotation image made here with PIL is read only when Tesseract and the Chinese data are
installed (as in the Docker image), otherwise that one test is skipped. Synthetic text only."""
import pytest
from PIL import Image, ImageDraw, ImageFont

import sys

import app.services.document_scanner_service  # noqa: F401  (registers the module)

scanner = sys.modules["app.services.document_scanner_service"]      # the module itself (the package name holds the service object)


class FakeTess:
    """Stands in for pytesseract: answers by language."""

    class Output:
        DICT = "dict"

    def __init__(self, eng, both, langs=("eng", "chi_sim", "osd")):
        self.eng, self.both, self.langs, self.calls = eng, both, langs, []

    def get_languages(self, config=""):
        return list(self.langs)

    def image_to_string(self, img, lang="eng"):
        self.calls.append(lang)
        return self.both if "chi_sim" in lang else self.eng

    def image_to_data(self, img, lang="eng", output_type=None):
        return {"text": ["Total", "$1,250.00", "價格", "x"], "conf": ["95", "41", "30", "-1"]}


def _img():
    return Image.new("RGB", (200, 80), "white")


def test_an_english_page_is_read_once_in_english_and_never_tries_chinese(monkeypatch):
    fake = FakeTess("PURCHASE ORDER number 12345 Ship To Terms Net 45 Days Quantity Description Unit Cost Amount TOTAL", "unused")
    monkeypatch.setattr(scanner, "pytesseract", fake)
    out = scanner.ocr_upright(_img())
    assert "PURCHASE ORDER" in out and fake.calls == ["eng"]


def test_a_page_that_comes_out_as_gibberish_is_read_again_with_chinese_and_the_better_reading_wins(monkeypatch):
    fake = FakeTess("lj ;; qx zz ww kk jj", "报价单 Quotation 总价 USD 1250.00 Payment Terms 交货期 9-11 weeks")
    monkeypatch.setattr(scanner, "pytesseract", fake)
    out = scanner.ocr_upright(_img())
    assert "报价单" in out and "Quotation" in out and fake.calls[:2] == ["eng", "eng+chi_sim"]


def test_without_the_chinese_data_it_falls_back_to_english_alone(monkeypatch):
    fake = FakeTess("lj ;; qx zz ww kk jj", "never read", langs=("eng", "osd"))
    monkeypatch.setattr(scanner, "pytesseract", fake)
    scanner.ocr_upright(_img())
    assert "eng+chi_sim" not in fake.calls and scanner.ocr_lang() == "eng" and scanner.chinese_available() is False


def test_words_the_reader_is_unsure_of_are_listed_for_a_person_to_check(monkeypatch):
    monkeypatch.setattr(scanner, "pytesseract", FakeTess("", ""))
    assert scanner.low_confidence_words(_img()) == ["$1,250.00", "價格"]
    assert scanner.low_confidence_words(_img(), threshold=20) == []


def _have_real_chinese():
    try:
        import pytesseract

        scanner.init_tesseract()
        return "chi_sim" in pytesseract.get_languages(config="")
    except Exception:  # noqa: BLE001
        return False


@pytest.mark.skipif(not _have_real_chinese(), reason="Tesseract with Simplified Chinese is not installed here (it is in the Docker image)")
def test_a_real_bilingual_quotation_image_reads_its_chinese_words_and_its_price_line():
    font = None
    for name in ("msyh.ttc", "simsun.ttc", "NotoSansCJK-Regular.ttc", "wqy-microhei.ttc", "DroidSansFallbackFull.ttf"):
        try:
            font = ImageFont.truetype(name, 40)
            break
        except OSError:
            continue
    if font is None:
        pytest.skip("no Chinese font on this machine to draw the test image")
    img = Image.new("RGB", (1400, 360), "white")
    d = ImageDraw.Draw(img)
    d.text((40, 30), "报价单 QUOTATION", fill="black", font=font)
    d.text((40, 120), "Total Price: USD 1250.00", fill="black", font=font)
    d.text((40, 210), "交货期 9-11 weeks", fill="black", font=font)
    out = scanner.ocr_upright(img)
    assert "1250" in out and ("报价" in out or "交货" in out)
