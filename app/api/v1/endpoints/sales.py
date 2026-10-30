"""FastAPI router for Sales Pipelines, Predictive Equipment Wear Life, Surcharges, and Upsell Engine."""

from typing import Any, Dict, List, Optional
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status

from app.models.sales_pipeline import (
    DraftQuoteFromWearRequest,
    PredictiveWearEquipment,
)
from app.core.security import require_operator, require_viewer
from app.services.monday_client import MondayClient
from app.services.sales_service import SalesService

router = APIRouter()
sales_service = SalesService()
monday_client = MondayClient()


@router.get(
    "/predictive-wear",
    response_model=List[PredictiveWearEquipment],
    summary="List customer plant equipment wear tracking and 75% wear alert flags",
)
def list_predictive_wear_equipment(
    customer_id: Optional[str] = Query(None, description="Optional customer ID filter"),
    _user: dict = Depends(require_viewer),
) -> List[PredictiveWearEquipment]:
    """Monitors plant equipment wear cycles for Owens Corning, Knauf, and IKO."""
    return sales_service.get_predictive_equipment(customer_id)


@router.post(
    "/predictive-wear/draft-quote",
    summary="Proactively draft replacement part quote for expiring equipment component",
)
def draft_quote_for_wear_component(
    request: DraftQuoteFromWearRequest,
    _user: dict = Depends(require_viewer),
) -> Dict[str, Any]:
    """Generates an itemized quote with volume discounts and lead times before component failure."""
    try:
        return sales_service.draft_quote_from_wear(request)
    except KeyError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=exc.args[0] if exc.args else "Equipment not found",
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Failed to draft wear quote: {str(exc)}",
        )


@router.post(
    "/predictive-wear/scan-and-alert",
    summary="Scan all equipment for WARNING/CRITICAL wear and push alerts to Monday.com",
)
def scan_wear_and_alert(
    background_tasks: BackgroundTasks,
    customer_id: Optional[str] = Query(None, description="Optional customer filter"),
    discount_pct: float = Query(5.0, description="Volume discount to apply on draft quotes"),
    delivery_weeks: int = Query(3, description="Target delivery lead time in weeks"),
    _user: dict = Depends(require_operator),
) -> Dict[str, Any]:
    """
    Proactive sales automation:
    1. Scans all plant equipment for components at 75%+ wear.
    2. Auto-drafts replacement quotes with volume discounts.
    3. Posts alerts to the Monday.com sales board.
    Returns a summary of all alerts dispatched.
    """
    equipment_list = sales_service.get_predictive_equipment(customer_id)

    alerts_dispatched: List[Dict[str, Any]] = []
    skipped_healthy: int = 0

    for equip in equipment_list:
        if equip.alert_level in ("HEALTHY", "UNKNOWN"):
            skipped_healthy += 1
            continue

        # Draft a proactive replacement quote
        try:
            draft_req = DraftQuoteFromWearRequest(
                equipment_tag=equip.equipment_tag,
                apply_volume_discount_pct=discount_pct,
                target_delivery_weeks=delivery_weeks,
            )
            quote = sales_service.draft_quote_from_wear(draft_req)
        except ValueError as exc:
            # An out-of-range discount applies to every component; fail the
            # scan rather than post a page of error quotes to Monday.
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=str(exc),
            )
        except Exception:
            quote = {"quote_number": "Q-ERROR", "quoted_unit_price": 0}

        # Build alert message for Monday.com
        severity = "🔴 CRITICAL" if equip.alert_level == "CRITICAL_REPLACE" else "⚠️ WARNING"
        alert_msg = (
            f"{severity}: {equip.component_name} at {equip.customer_name} ({equip.plant_location})\n"
            f"Wear Level: {equip.estimated_wear_pct:.1f}% ({equip.days_in_service} days in service)\n"
            f"Replacement SKU: {equip.suggested_replacement_sku}\n"
            f"Draft Quote: {quote.get('quote_number', 'N/A')} — ${quote.get('quoted_unit_price', 0):,.2f}\n"
            f"Lead Time: {delivery_weeks} weeks\n"
            f"Action: Send quote to customer before component failure."
        )

        alert_record = {
            "equipment_tag": equip.equipment_tag,
            "customer_name": equip.customer_name,
            "alert_level": equip.alert_level,
            "wear_pct": equip.estimated_wear_pct,
            "quote_number": quote.get("quote_number"),
            "quoted_price": quote.get("quoted_unit_price"),
            "monday_alert_message": alert_msg,
        }
        alerts_dispatched.append(alert_record)

        # Post to Monday.com in the background (non-blocking)
        # Using pulse_id=0 as placeholder — in production, this would create a new pulse
        background_tasks.add_task(monday_client.post_pulse_update, 0, alert_msg)

    return {
        "scan_summary": f"Scanned {len(equipment_list)} equipment components",
        "alerts_dispatched": len(alerts_dispatched),
        "healthy_skipped": skipped_healthy,
        "alerts": alerts_dispatched,
    }
