"""The PO and sales-order sheet follow Allied's own forms. Test data only."""
from pypdf import PdfReader
import io

from app.services import order_documents as od


def _text(pdf):
    return " ".join(p.extract_text() or "" for p in PdfReader(io.BytesIO(pdf)).pages)


def test_vendor_po_has_allieds_layout_and_never_names_the_customer():
    pdf = od.vendor_po_pdf({"po_number": "9248", "vendor_name": "KWS LTD.", "terms": "Net 45 Days", "ship_via": "MOTOR FRT", "good_thru": "9/13/26",
                            "lines": od.po_lines([{"quantity": 1, "description": "300XL screw", "unit_cost": 12592.43, "drawing_ref": "0096551-A"}])})
    t = _text(pdf)
    for want in ("PURCHASE ORDER", "Purchase Order No.", "9248", "Ship To", "Good Thru", "Net 45 Days", "12,592.43", "IMPORTANT NOTES", "Authorized Signature", "TO ARRANGE SHIPPING"):
        assert want in t, want
    assert "NO MARKINGS OR DOCUMENTS OF KWS LTD." in t and "ANY CUSTOMER" not in t     # the note names the vendor, as on PO 9248


def test_sales_order_sheet_is_internal_with_four_vendor_blocks():
    t = _text(od.sales_order_sheet_pdf({"customer_name": "IKO", "customer_reference": "26001556", "sales_order_number": "12889",
                                        "vendors": [{"po_number": "9248", "vendor": "KWS"}], "so_total": 2550}))
    for want in ("SALES ORDER INFORMATION", "VENDOR 1 ALLIED PO#", "VENDOR 4 ALLIED PO#", "9248", "12889", "26001556", "2,550.00", "internal"):
        assert want in t, want
