"""FastAPI router for autonomous customer email ingestion and inbox feed."""

from typing import Any, Dict, List
from app.core.security import require_operator, require_viewer
from fastapi import APIRouter, Depends, HTTPException, status

from app.services.inbound_email_daemon import (
    InboundEmailMessage,
    EmailIngestionResult,
    inbound_email_daemon,
)

router = APIRouter()


@router.post(
    "/simulate",
    response_model=EmailIngestionResult,
    status_code=status.HTTP_200_OK,
    summary="Simulate or Ingest Inbound Customer PO Email",
)
def ingest_customer_email(
    email: InboundEmailMessage,
    _user: dict = Depends(require_operator),
) -> EmailIngestionResult:
    """
    Ingests an inbound customer PO/RFQ email, autonomously parses customer & items,
    generates Sage 50 Sales Order + Split Vendor POs, updates Monday.com,
    and returns the customer Order Acknowledgment.
    """
    try:
        return inbound_email_daemon.process_inbound_email(email)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Failed to process inbound email: {str(e)}",
        )


@router.get(
    "/feed",
    response_model=List[Dict[str, Any]],
    summary="Get Inbound Email Ingestion Stream",
)
def get_inbound_email_feed(
    limit: int = 20,
    _user: dict = Depends(require_viewer),
) -> List[Dict[str, Any]]:
    """Returns recent customer emails, extracted PO numbers, and generated orders."""
    return inbound_email_daemon.get_inbox_feed(limit=limit)
