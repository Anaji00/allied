"""Next steps after reading a document lead to screens that exist."""

from urllib.parse import parse_qs, urlparse

import pytest

from app.services.document_scanner_service import document_scanner_service

# The app's pages; a next step pointing anywhere else is a dead link.
PAGES = {"/orders", "/compliance/vault", "/chat", "/shipments", "/projects"}


@pytest.mark.parametrize("sample", document_scanner_service.get_sample_documents(), ids=lambda s: s.sample_id)
def test_sample_next_steps_point_at_real_pages(sample):
    res = document_scanner_service.scan_document(
        sample.sample_text.encode(), sample.sample_filename, sample.document_type, reconcile=False
    )
    for action in res.suggested_actions:
        assert urlparse(action.route_url).path in PAGES, action.route_url


def test_customer_po_opens_a_filled_in_new_order():
    po = next(s for s in document_scanner_service.get_sample_documents() if s.document_type.value == "CUSTOMER_PO")
    res = document_scanner_service.scan_document(po.sample_text.encode(), po.sample_filename, po.document_type, reconcile=False)
    url = urlparse(res.suggested_actions[0].route_url)
    query = parse_qs(url.query)
    assert url.path == "/orders" and query["new"] == ["true"]
    assert query["customer"][0] and query["part"][0]
