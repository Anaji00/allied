"""Service for Inbound Sales RFQs, Predictive Equipment Wear Life, Dynamic Surcharges, and Upsells."""

import json
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, List, Optional
from datetime import date, datetime, timedelta

from app.config import settings
from app.core.logging import logger
from app.models.sales_pipeline import (
    DraftQuoteFromWearRequest,
    PredictiveWearEquipment,
)
from app.services.entity_matcher import EntityMatcher
from app.core.container import get_entity_matcher

# Ceiling on a volume discount applied to a drafted quote. An unbounded value
# let a caller draft a quote at a negative price.
MAX_VOLUME_DISCOUNT_PCT = Decimal("50")


class SalesService:
    """Orchestrates industrial sales pipelines, equipment wear tracking, and dynamic pricing."""

    def __init__(
        self,
        equipment_json_path: Optional[Path] = None,
        matcher: Optional[EntityMatcher] = None,
    ):
        self.equip_path = equipment_json_path or (settings.DATA_DIR / "plant_equipment.json")
        self.matcher = matcher or get_entity_matcher()
        self.equipment_cache: Dict[str, PredictiveWearEquipment] = {}
        self.load_equipment()

    def load_equipment(self) -> None:
        """Loads the equipment register and computes current wear for each item."""
        if not self.equip_path.exists():
            return

        with open(self.equip_path, mode="r", encoding="utf-8") as f:
            data = json.load(f)

        observed = self._observed_service_lives()

        for entry in data:
            self.equipment_cache[entry["equipment_tag"]] = self._build_equipment(entry, observed)

        logger.info(
            f"Loaded {len(self.equipment_cache)} plant equipment records; "
            f"{sum(1 for e in self.equipment_cache.values() if e.lifespan_basis == 'OBSERVED')} "
            f"using service life observed from Allied's own reorder history."
        )

    def refresh_wear(self) -> int:
        """Recomputes wear against today. Returns the number of records updated.

        Wear is a function of the date, so a cache loaded on Monday is wrong
        by Friday. Anything reading these records calls this first.
        """
        if not self.equip_path.exists():
            return 0
        with open(self.equip_path, mode="r", encoding="utf-8") as f:
            data = json.load(f)
        observed = self._observed_service_lives()
        for entry in data:
            self.equipment_cache[entry["equipment_tag"]] = self._build_equipment(entry, observed)
        return len(self.equipment_cache)

    def _build_equipment(
        self, entry: Dict[str, Any], observed: Dict[tuple, Dict[str, Any]]
    ) -> PredictiveWearEquipment:
        """Derives wear from elapsed service time against the part's average life.

        The stored ``days_in_service`` and ``estimated_wear_pct`` in the
        register are ignored. They were written once and never moved, which
        made every wear figure a statement about the day the file was authored
        rather than about today.
        """
        install_date = self._parse_date(entry.get("install_date"))
        today = datetime.now().date()
        days_in_service = max(0, (today - install_date).days) if install_date else int(entry.get("days_in_service", 0))

        catalogue_life = int(entry.get("expected_lifespan_days", 0) or 0)
        item_id = entry.get("installed_item_id", "")
        customer_id = entry.get("customer_id", "")

        key = (customer_id, item_id)
        obs = observed.get(key) or observed.get(("", item_id))
        life = catalogue_life
        basis = "CATALOGUE"
        basis_detail = (
            f"Published service life of {catalogue_life} days. Allied has not yet replaced "
            f"this part often enough to measure its own figure."
        )
        observed_life = None
        observations = 0

        # A customer ordering the same part twice in a fortnight bought two of
        # them; they did not wear one out. Intervals far below the published
        # life are restocking, and averaging them in would report a wear part
        # as failing in days. The floor is a fraction of the published figure
        # rather than a fixed number of days, because a liner and a screen do
        # not have comparable lifetimes.
        floor_days = max(14, int(catalogue_life * 0.25)) if catalogue_life else 14
        if obs:
            credible = [g for g in obs.get("gaps", []) if g >= floor_days]
            obs = (
                {"mean_days": sum(credible) / len(credible), "count": len(credible), "gaps": credible}
                if credible
                else None
            )

        if obs and obs["count"] >= settings.WEAR_MIN_OBSERVATIONS:
            observed_life = int(round(obs["mean_days"]))
            observations = obs["count"]
            if observed_life > 0:
                life = observed_life
                basis = "OBSERVED"
                scope = "this customer" if key in observed else "all customers"
                basis_detail = (
                    f"Average of {observations} actual replacements for {scope}: "
                    f"{observed_life} days between reorders."
                )
                if catalogue_life:
                    drift = (observed_life - catalogue_life) / catalogue_life * 100.0
                    if abs(drift) >= 15.0:
                        direction = "longer" if drift > 0 else "shorter"
                        basis_detail += (
                            f" That is {abs(drift):.0f}% {direction} than the published "
                            f"{catalogue_life} days."
                        )

        life = life or catalogue_life or 1
        wear_pct = round((days_in_service / life) * 100.0, 1) if life else 0.0

        if wear_pct >= settings.WEAR_CRITICAL_PCT:
            alert = "CRITICAL_REPLACE"
        elif wear_pct >= settings.WEAR_WARNING_PCT:
            alert = "WARNING_75_PCT"
        else:
            alert = "HEALTHY"

        days_remaining = int(round(life - days_in_service))
        projected = None
        if install_date:
            projected = (install_date + timedelta(days=life)).isoformat()

        return PredictiveWearEquipment(
            equipment_tag=entry["equipment_tag"],
            customer_id=customer_id,
            customer_name=entry["customer_name"],
            plant_location=entry["plant_location"],
            component_name=entry["component_name"],
            installed_item_id=item_id,
            install_date=entry["install_date"],
            expected_lifespan_days=life,
            days_in_service=days_in_service,
            estimated_wear_pct=wear_pct,
            alert_level=alert,
            suggested_replacement_sku=entry["suggested_replacement_sku"],
            historical_unit_price=Decimal(str(entry["historical_unit_price"])),
            last_service_order=entry["last_service_order"],
            lifespan_basis=basis,
            lifespan_basis_detail=basis_detail,
            observed_lifespan_days=observed_life,
            replacement_observations=observations,
            days_remaining=days_remaining,
            projected_replacement_date=projected,
        )

    @staticmethod
    def _parse_date(raw: Any) -> Optional[date]:
        if not raw:
            return None
        for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y"):
            try:
                return datetime.strptime(str(raw)[:10], fmt).date()
            except ValueError:
                continue
        return None

    def _observed_service_lives(self) -> Dict[tuple, Dict[str, Any]]:
        """Measures how long each part actually lasts, from Allied's own reorders.

        A wear part that is reordered is a wear part that wore out, so the gap
        between successive orders of the same item by the same customer is an
        observation of its service life. Two orders give one interval, which
        is why ``WEAR_MIN_OBSERVATIONS`` gates the switch away from the
        published figure -- one interval is an anecdote, not an average.

        Returns ``{(customer_id, item_id): {"mean_days", "count"}}`` plus an
        all-customer rollup under ``("", item_id)``.
        """
        try:
            from app.core.database import SessionLocal
            from app.models.db_models import OrderDB
        except Exception:
            return {}

        purchases: Dict[tuple, List[date]] = {}
        try:
            with SessionLocal() as db:
                rows = db.query(
                    OrderDB.customer_id, OrderDB.line_items, OrderDB.po_date, OrderDB.created_at
                ).all()
        except Exception as exc:
            logger.debug(f"Service-life history unavailable: {exc}")
            return {}

        for customer_id, line_items, po_date, created_at in rows:
            when = self._parse_date(po_date) or (created_at.date() if created_at else None)
            if not when:
                continue
            for li in (line_items or []):
                item_id = (li or {}).get("item_id")
                if not item_id:
                    continue
                purchases.setdefault((customer_id or "", item_id), []).append(when)

        observed: Dict[tuple, Dict[str, Any]] = {}

        def summarise(dates: List[date]) -> Optional[Dict[str, Any]]:
            # Same-day repeats are one purchase split over lines, not a
            # replacement cycle, so they are collapsed before measuring.
            uniq = sorted(set(dates))
            if len(uniq) < 2:
                return None
            gaps = [(b - a).days for a, b in zip(uniq, uniq[1:]) if (b - a).days > 0]
            if not gaps:
                return None
            # The individual gaps travel with the summary so the caller can
            # discard the ones too short to be a service life at all.
            return {"mean_days": sum(gaps) / len(gaps), "count": len(gaps), "gaps": gaps}

        rollup: Dict[str, List[date]] = {}
        for (customer_id, item_id), dates in purchases.items():
            summary = summarise(dates)
            if summary:
                observed[(customer_id, item_id)] = summary
            rollup.setdefault(item_id, []).extend(dates)

        for item_id, dates in rollup.items():
            summary = summarise(dates)
            if summary:
                observed[("", item_id)] = summary

        return observed

    def get_predictive_equipment(self, customer_id: Optional[str] = None) -> List[PredictiveWearEquipment]:
        """Returns current wear status, optionally filtered by customer.

        Recomputes before answering: wear is a function of today's date, so a
        value cached at startup is stale by definition.
        """
        try:
            self.refresh_wear()
        except Exception as exc:  # noqa: BLE001 - stale figures beat no figures
            logger.warning(f"Could not refresh wear figures, serving cached: {exc}")
        items = list(self.equipment_cache.values())
        if customer_id:
            items = [e for e in items if e.customer_id == customer_id]
        return sorted(items, key=lambda e: e.estimated_wear_pct, reverse=True)

    def draft_quote_from_wear(self, req: DraftQuoteFromWearRequest) -> Dict[str, Any]:
        """Proactively generates an itemized quote draft for an expiring equipment component."""
        equip = self.equipment_cache.get(req.equipment_tag)
        if not equip:
            # Falling back to the first record quoted a different customer's
            # component under the requested tag.
            raise KeyError(f"Equipment tag '{req.equipment_tag}' not found")

        discount = Decimal(str(req.apply_volume_discount_pct))
        if discount < Decimal("0") or discount > MAX_VOLUME_DISCOUNT_PCT:
            raise ValueError(
                f"Volume discount {discount}% is outside the allowed range "
                f"0-{MAX_VOLUME_DISCOUNT_PCT}%."
            )

        discount_mult = (Decimal("100.0") - req.apply_volume_discount_pct) / Decimal("100.0")
        quote_price = (equip.historical_unit_price * discount_mult).quantize(Decimal("0.01"))
        quote_number = f"Q-{equip.customer_id}-{datetime.now().strftime('%m%d%H%M')}"

        return {
            "quote_number": quote_number,
            "equipment_tag": equip.equipment_tag,
            "customer_id": equip.customer_id,
            "customer_name": equip.customer_name,
            "plant_location": equip.plant_location,
            "component_name": equip.component_name,
            "suggested_sku": equip.suggested_replacement_sku,
            "quoted_unit_price": float(quote_price),
            "regular_price": float(equip.historical_unit_price),
            "discount_applied_pct": float(req.apply_volume_discount_pct),
            "target_lead_time_weeks": req.target_delivery_weeks,
            "urgency_rationale": (
                f"Component is at {equip.estimated_wear_pct:.1f}% wear life ({equip.days_in_service} days in service). "
                f"Proactive fabrication ensures zero plant shutdown."
            ),
            # Nothing is posted to Monday here; callers that post the alert
            # report that themselves.
            "status": "DRAFTED",
        }

