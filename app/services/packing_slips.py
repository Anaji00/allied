"""Sanitized white-label PDF Packing Slip generator enforcing blind-shipping compliance."""

from datetime import date
from pathlib import Path
from typing import Optional
import xml.sax.saxutils as xml_escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import (
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from app.config import settings
from app.core.logging import logger
from app.models.orders import MasterOrder


def safe_xml(text: Optional[str]) -> str:
    """Escapes XML entities (&, <, >, ", ') to prevent ReportLab markup parsing crashes."""
    if not text:
        return ""
    return xml_escape.escape(str(text), entities={'"': "&quot;", "'": "&apos;"})


class PackingSlipGenerator:
    """Generates standardized, sanitized white-label packing lists using ReportLab."""

    def __init__(self, output_dir: Optional[Path] = None):
        self.output_dir = output_dir or (settings.OUTPUT_DIR / "packing_slips")
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def generate_pdf(
        self,
        order: MasterOrder,
        sales_order_number: str,
        ship_date: Optional[date] = None,
    ) -> Path:
        """
        Creates a PDF packing slip for the customer order.
        Strictly enforces blind-shipping: strips all internal costs, vendor names, and margins.
        """
        filename = f"packing_slip_SO_{sales_order_number}.pdf"
        file_path = self.output_dir / filename

        doc = SimpleDocTemplate(
            str(file_path),
            pagesize=letter,
            rightMargin=0.5 * inch,
            leftMargin=0.5 * inch,
            topMargin=0.5 * inch,
            bottomMargin=0.5 * inch,
        )

        styles = getSampleStyleSheet()
        normal_style = styles["Normal"]
        normal_style.fontSize = 8
        normal_style.leading = 10

        bold_style = ParagraphStyle(
            "BoldNormal",
            parent=normal_style,
            fontName="Helvetica-Bold",
        )

        title_style = ParagraphStyle(
            "TitleStyle",
            parent=styles["Heading1"],
            fontName="Helvetica-Bold",
            fontSize=18,
            leading=22,
            textColor=colors.HexColor("#4A5568"),
            alignment=2,  # Right align
        )

        elements = []

        # 1. Header Section: Company Branding (Left) & Document Title (Right)
        header_data = [
            [
                Paragraph(
                    "<b>ALLIED WELDING SYSTEMS</b><br/>"
                    "P.O. BOX 795459 &bull; DALLAS, TEXAS 75379-5459<br/>"
                    "(972) 248-8838 &bull; FAX (972) 248-8890<br/>"
                    "E-mail: info@alliedweldingsystems.com",
                    normal_style,
                ),
                Paragraph(
                    "<b>PACKING LIST</b><br/>"
                    # Allied's own sales order number is internal; the customer's PO is the reference they know.
                    f"<font size='9'>Your PO Number: <b>{safe_xml(order.customer_po_number)}</b><br/>"
                    f"Order Date: {order.po_date.strftime('%b %d, %Y')}<br/>"
                    f"Ship By: {(ship_date or order.po_date).strftime('%b %d, %Y')}<br/>"
                    "Page: 1</font>",
                    title_style,
                ),
            ]
        ]
        header_table = Table(header_data, colWidths=[4.2 * inch, 3.3 * inch])
        header_table.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
        ]))
        elements.append(header_table)
        elements.append(Spacer(1, 0.1 * inch))

        # 2. Addresses Block: "To" and "Ship To"
        cust_name_safe = safe_xml(order.customer.customer_name)
        billing_addr_safe = safe_xml(order.customer.billing_address or order.shipping.shipping_address)
        recipient_safe = safe_xml(order.shipping.recipient_name or order.customer.customer_name)
        shipping_addr_safe = safe_xml(order.shipping.shipping_address)

        to_address_text = f"<b>{cust_name_safe}</b><br/>{billing_addr_safe}"
        ship_to_text = f"<b>{recipient_safe}</b><br/>{shipping_addr_safe}"

        address_data = [
            [Paragraph("<b>To:</b>", bold_style), Paragraph("<b>Ship To:</b>", bold_style)],
            [Paragraph(to_address_text, normal_style), Paragraph(ship_to_text, normal_style)],
        ]
        address_table = Table(address_data, colWidths=[3.75 * inch, 3.75 * inch])
        address_table.setStyle(TableStyle([
            ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#A0AEC0")),
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#EDF2F7")),
            ("INNERGRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#E2E8F0")),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ]))
        elements.append(address_table)
        elements.append(Spacer(1, 0.1 * inch))

        # 3. Order Reference Summary Bar
        cust_id = safe_xml(order.customer.customer_id or "N/A")
        po_num = safe_xml(order.customer_po_number)
        sales_rep = safe_xml(order.customer.sales_rep)
        contact = safe_xml(order.customer.contact_name or "Receiving Dept")
        ship_method = safe_xml(order.shipping.shipping_method)
        terms = safe_xml(order.customer.payment_terms)

        summary_headers = [
            ["Customer ID", "PO Number", "Sales Rep Name"],
            [cust_id, po_num, sales_rep],
            ["Customer Contact", "Shipping Method", "Payment Terms"],
            [contact, ship_method, terms],
        ]
        summary_table = Table(summary_headers, colWidths=[2.5 * inch, 2.5 * inch, 2.5 * inch])
        summary_table.setStyle(TableStyle([
            ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#A0AEC0")),
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#EDF2F7")),
            ("BACKGROUND", (0, 2), (-1, 2), colors.HexColor("#EDF2F7")),
            ("INNERGRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#E2E8F0")),
            ("ALIGN", (0, 0), (-1, -1), "CENTER"),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTNAME", (0, 2), (-1, 2), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 8),
            ("TOPPADDING", (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ]))
        elements.append(summary_table)
        elements.append(Spacer(1, 0.1 * inch))

        # 4. Itemized Packing Manifest (No prices or costs!)
        items_data = [
            ["Quantity", "Item", "Description", "Shipped Prior", "This Shipment"]
        ]

        for item in order.line_items:
            qty_str = f"{float(item.quantity):.2f}"
            item_code = safe_xml(item.item_id or "CUSTOM-FAB")
            desc_text = safe_xml(item.description or item.raw_item_text)
            # A fabricated part is only identifiable by the drawing and
            # revision it was cut to. Carrying the reference onto the slip is
            # what lets the customer tie what arrived to what they approved,
            # and what lets either side settle a dispute about a later revision.
            drawing = getattr(item, "drawing_ref", None)
            if drawing:
                desc_text += f"<br/><font size='7'>Drawing: {safe_xml(drawing)}</font>"
            items_data.append([
                qty_str,
                item_code,
                Paragraph(desc_text, normal_style),
                "0.00",
                qty_str,
            ])

        # Fill remaining lines for professional layout appearance
        while len(items_data) < 6:
            items_data.append(["", "", "", "", ""])

        items_table = Table(
            items_data,
            colWidths=[0.9 * inch, 1.4 * inch, 3.4 * inch, 0.9 * inch, 0.9 * inch],
        )
        items_table.setStyle(TableStyle([
            ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#A0AEC0")),
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#EDF2F7")),
            ("INNERGRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#E2E8F0")),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 8),
            ("ALIGN", (0, 0), (0, -1), "CENTER"),
            ("ALIGN", (3, 0), (-1, -1), "CENTER"),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("TOPPADDING", (0, 0), (-1, -1), 5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ]))
        elements.append(items_table)
        elements.append(Spacer(1, 0.15 * inch))

        # 5. Notes & Blind-Shipping Notice Block
        notes_content = [
            [
                Paragraph(
                    "<b>RECEIVING INSTRUCTIONS & NOTES:</b><br/>"
                    "&bull; All shipments must be inspected upon arrival. Any damages must be noted on the BOL.<br/>"
                    "&bull; Direct technical inquiries to info@alliedweldingsystems.com / (972) 248-8838.<br/>"
                    f"&bull; Customer PO Reference: <b>{safe_xml(order.customer_po_number)}</b>",
                    normal_style,
                )
            ]
        ]
        notes_table = Table(notes_content, colWidths=[7.5 * inch])
        notes_table.setStyle(TableStyle([
            ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#CBD5E0")),
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#F7FAFC")),
            ("TOPPADDING", (0, 0), (-1, -1), 6),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ]))
        elements.append(notes_table)

        # Build document
        doc.build(elements)
        logger.info(f"Generated sanitized white-label packing slip PDF: {file_path}")
        return file_path
