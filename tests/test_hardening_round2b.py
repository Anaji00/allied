"""A vendor's quotation that mentions Allied is still the vendor's; two Allied quotes with different numbers are two documents. Synthetic text."""
from app.services.coordination import packets

VENDOR_QUOTE = "QUOTATION\nSome Vendor Manufacturing LLC\n3041 Conveyor Drive\nOrder Number: 0234268\nOrder Date: 8/13/2026\nShip To: Allied Welding Systems Dallas TX\n"
ALLIED_Q1 = "ALLIED\nQUOTATION\nQuote Number: 05082026-2\nQuote Date: May 8, 2026\n"
ALLIED_Q2 = "ALLIED\nWELDING SYSTEMS\nP.O. BOX 795459\nQuote Number: 07242026-1\n"


def test_a_vendor_quotation_that_names_allied_lower_down_is_still_the_vendors():
    assert packets.classify(VENDOR_QUOTE) == "vendor_quote"
    assert packets.extract("vendor_quote", VENDOR_QUOTE)["quote_order_number"] == "0234268"
    assert packets.classify(ALLIED_Q1) == "allied_quote" and packets.classify(ALLIED_Q2) == "allied_quote"


def test_two_allied_quotes_with_different_numbers_are_two_documents():
    docs = packets.split([ALLIED_Q1, ALLIED_Q2])
    assert [(d["kind"], d["fields"].get("quote_number")) for d in docs] == [("allied_quote", "05082026-2"), ("allied_quote", "07242026-1")]
    same = packets.split([ALLIED_Q1, "ALLIED\nQUOTATION\nQuote Date: May 8, 2026\nPage 2\n"])
    assert len(same) == 1 and same[0]["page_to"] == 2                                              # a page with no number of its own continues the quote
