"""Strict Pydantic V2 models for orders, webhooks, and dual-sided transactions."""

from datetime import date, datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, ConfigDict, Field


class OrderArchetype(str, Enum):
    """Core fulfillment and processing archetypes."""
    STANDARD_STOCK = "STANDARD_STOCK"
    BLIND_DROPSHIP = "BLIND_DROPSHIP"
    BLUEPRINT_ASSEMBLY = "BLUEPRINT_ASSEMBLY"
    CROSS_BORDER = "CROSS_BORDER"


class OrderStatus(str, Enum):
    """Processing and sync status states."""
    RECEIVED = "RECEIVED"
    VALIDATED = "VALIDATED"
    PROCESSING = "PROCESSING"
    SYNCED_TO_SAGE = "SYNCED_TO_SAGE"
    VALIDATION_ERROR = "VALIDATION_ERROR"
    SAGE_ERROR = "SAGE_ERROR"


class VendorCategory(str, Enum):
    """Primary supplier routing categories."""
    NIBROCK = "NIBROCK"
    ZIBO = "ZIBO"
    KWS = "KWS"
    STOCK = "STOCK"
    OTHER = "OTHER"


class MondayChallengeRequest(BaseModel):
    """Payload sent by Monday.com when configuring a new webhook."""
    challenge: str


class MondayChallengeResponse(BaseModel):
    """Challenge echo response required by Monday.com webhook setup."""
    challenge: str


class MondayWebhookEvent(BaseModel):
    """Internal event structure within Monday webhook payload."""
    app: Optional[str] = None
    type: Optional[str] = None
    triggerTime: Optional[datetime] = None
    subscriptionId: Optional[int] = None
    userId: Optional[int] = None
    pulseId: Optional[int] = None
    pulseName: Optional[str] = None
    boardId: Optional[int] = None
    columnValues: Optional[Dict[str, Any]] = None

    model_config = ConfigDict(extra="ignore")


class MondayWebhookPayload(BaseModel):
    """Inbound Monday.com webhook payload wrapper."""
    event: Optional[MondayWebhookEvent] = None
    challenge: Optional[str] = None

    model_config = ConfigDict(extra="ignore")


class OrderLineItem(BaseModel):
    """Single line item on an inbound or master order."""
    item_id: Optional[str] = Field(None, description="Resolved Sage 50 Item ID / SKU")
    raw_item_text: str = Field(..., description="Original raw text or description from PO/Drawing")
    description: Optional[str] = Field(None, description="Sanitized official item description")
    quantity: Decimal = Field(..., gt=0, description="Quantity ordered")
    unit_price: Decimal = Field(..., ge=0, description="Customer selling price per unit")
    unit_cost: Optional[Decimal] = Field(default=Decimal("0.00"), ge=0, description="Vendor expense cost per unit")
    uom: str = Field(default="EA", description="Unit of measure (EA, SHT, SPL, etc.)")
    preferred_vendor: Optional[str] = Field(None, description="Vendor code (NIBROCK, ZIBO, KWS, STOCK)")
    drawing_ref: Optional[str] = Field(None, description="Drawing number or takeoff reference")
    revision: Optional[str] = Field(None, description="Drawing revision code")
    cut_length_inches: Optional[Decimal] = Field(None, description="Length in inches for 1D nesting items")
    confidence_score: Optional[float] = Field(default=100.0, description="Entity matching confidence %")

    model_config = ConfigDict(extra="ignore", from_attributes=True)


class ShippingDetails(BaseModel):
    """Destination and logistics compliance specifications."""
    recipient_name: str
    shipping_address: str
    city: Optional[str] = None
    state: Optional[str] = None
    postal_code: Optional[str] = None
    country: str = "USA"
    shipping_method: str = "MOTOR FRT"
    blind_shipping_required: bool = True
    carrier_account: Optional[str] = None
    tracking_number: Optional[str] = None
    international_notes: Optional[str] = None

    model_config = ConfigDict(extra="ignore", from_attributes=True)


class CustomerDetails(BaseModel):
    """Customer account and billing metadata."""
    customer_id: Optional[str] = Field(None, description="Sage 50 Customer ID (e.g., 10221, 10195)")
    customer_name: str
    billing_address: Optional[str] = None
    email: Optional[str] = None
    contact_name: Optional[str] = None
    payment_terms: str = "Net 30 Days"
    sales_rep: str = "Karissa M. Rakestraw"

    model_config = ConfigDict(extra="ignore", from_attributes=True)


class MasterOrder(BaseModel):
    """Unified Master Order schema representing an approved transaction."""
    order_id: str = Field(..., description="Unique internal bridge Order ID")
    customer_po_number: str = Field(..., description="Customer official PO Number")
    po_date: date = Field(default_factory=date.today)
    customer: CustomerDetails
    shipping: ShippingDetails
    line_items: List[OrderLineItem]
    archetype: OrderArchetype = OrderArchetype.STANDARD_STOCK
    internal_notes: Optional[str] = None
    status: OrderStatus = OrderStatus.RECEIVED
    facility: str = Field(default="HQ", description="Originating facility (HQ, EDMONTON, ZIBO)")
    currency: str = Field(default="USD", description="Transaction currency (USD, CAD, CNY)")

    @property
    def total_revenue(self) -> Decimal:
        """Calculates total customer billing amount."""
        return sum(item.quantity * item.unit_price for item in self.line_items)

    @property
    def total_expense(self) -> Decimal:
        """Calculates total vendor cost."""
        return sum(item.quantity * (item.unit_cost or Decimal("0.00")) for item in self.line_items)

    @property
    def gross_margin_pct(self) -> Decimal:
        """Calculates gross margin percentage."""
        rev = self.total_revenue
        if rev <= Decimal("0.00"):
            return Decimal("0.00")
        margin = rev - self.total_expense
        return (margin / rev) * Decimal("100.00")

    model_config = ConfigDict(extra="ignore", from_attributes=True)


class InboundOrderRequest(BaseModel):
    """Direct JSON submission payload for processing or replaying orders."""
    customer_name: str
    customer_po_number: str
    po_date: Optional[date] = None
    shipping_address: str
    recipient_name: Optional[str] = None
    payment_terms: Optional[str] = None
    shipping_method: Optional[str] = "MOTOR FRT"
    line_items: List[OrderLineItem]
    notes: Optional[str] = None
    blind_shipping: bool = True

    model_config = ConfigDict(extra="ignore")


class DualSidedResult(BaseModel):
    """Outcome of an atomic dual-sided transaction orchestration."""
    order_id: str
    customer_po_number: str
    sales_order_number: str
    purchase_orders: Dict[str, str] = Field(
        default_factory=dict,
        description="Map of Vendor Code to Generated Sage PO Number (e.g. {'NIBROCK': '9254'})",
    )
    customer_id: str
    total_revenue: Decimal
    total_expense: Decimal
    gross_margin_pct: Decimal
    packing_slip_path: Optional[str] = None
    facility: str = "HQ"
    currency: str = "USD"
    status: OrderStatus
    messages: List[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    model_config = ConfigDict(extra="ignore", from_attributes=True)
