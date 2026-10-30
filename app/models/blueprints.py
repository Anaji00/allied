"""Engineering drawing CAD takeoff and BOM models."""

from decimal import Decimal
from typing import List, Optional
from pydantic import BaseModel, ConfigDict, Field


class BOMItem(BaseModel):
    """Bill of Materials line extracted from engineering drawing."""
    item_tag: str
    description: str
    quantity: Decimal
    material: Optional[str] = None
    dimensions: Optional[str] = None
    weight_lbs: Optional[Decimal] = None
    is_commercial_hardware: bool = False

    model_config = ConfigDict(extra="ignore")


class DrawingTakeoff(BaseModel):
    """CAD / Drawing Takeoff representation."""
    drawing_number: str
    revision: str = "0"
    title: str
    customer_tag: Optional[str] = None
    sap_code: Optional[str] = None
    material: Optional[str] = None
    total_weight_lbs: Optional[Decimal] = None
    bom: List[BOMItem] = Field(default_factory=list)
    general_notes: List[str] = Field(default_factory=list)

    model_config = ConfigDict(extra="ignore")
