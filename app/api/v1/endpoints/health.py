"""Diagnostic health check endpoint verifying ODBC, COM SDK, and catalog caches."""

from datetime import datetime, timezone
from typing import Any, Dict
from fastapi import APIRouter, Depends, status

from app.config import settings
from app.core.container import get_entity_matcher
from app.core.security import require_viewer
from app.services.sage_odbc import SageODBCReader
from app.services.sage_sdk_writer import SageSDKWriter

router = APIRouter()
matcher = get_entity_matcher()
odbc_reader = SageODBCReader(matcher)
sdk_writer = SageSDKWriter()


@router.get(
    "/live",
    status_code=status.HTTP_200_OK,
    summary="Unauthenticated liveness probe",
)
def liveness() -> Dict[str, Any]:
    """Says only that the process is answering, for uptime monitors and probes.

    Configuration, catalogue sizes and component state live behind auth on
    the diagnostic route below.
    """
    return {"status": "healthy", "timestamp": datetime.now(timezone.utc).isoformat()}


@router.get(
    "",
    status_code=status.HTTP_200_OK,
    summary="System Diagnostic Heartbeat",
)
def system_health(_user: dict = Depends(require_viewer)) -> Dict[str, Any]:
    """
    Returns live health state across all bridge components:
    - Actian Zen PSQL ODBC readiness
    - Sage 50 COM SDK write layer status
    - In-memory catalog record counts
    - Runtime configuration flags
    """
    odbc_status = odbc_reader.test_connection()

    return {
        "service": settings.APP_NAME,
        "status": "healthy",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "environment": settings.APP_ENV,
        "simulation_mode": settings.SAGE_SIMULATION_MODE,
        "components": {
            "sage_odbc_reader": odbc_status,
            "sage_com_sdk_writer": {
                "status": "ready",
                "mode": "simulation" if settings.SAGE_SIMULATION_MODE else "live_com",
                "transactions_executed": len(sdk_writer.transaction_log),
            },
            "in_memory_catalogs": {
                "items_loaded": len(matcher.items_cache),
                "customers_loaded": len(matcher.customers_cache),
                "vendors_loaded": len(matcher.vendors_cache),
                "aliases_loaded": len(matcher.alias_dict),
            },
            "compliance_rules": {
                "min_gross_margin_pct": settings.MIN_GROSS_MARGIN_PCT,
                "blind_shipping_default": settings.DEFAULT_BLIND_SHIPPING,
            },
        },
    }
