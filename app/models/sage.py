"""Data Transfer Objects (DTOs) for Sage 50 US Accounting (ODBC & COM SDK)."""

from datetime import date
from decimal import Decimal
from typing import List, Optional
from pydantic import BaseModel, ConfigDict, Field


class SageCustomer(BaseModel):
    """Sage 50 Customer Master DTO."""
    customer_id: str
    name: str
    bill_address_line1: str
    bill_address_line2: Optional[str] = None
    bill_city: Optional[str] = None
    bill_state: Optional[str] = None
    bill_zip: Optional[str] = None
    ship_address_line1: str
    ship_address_line2: Optional[str] = None
    terms_description: str = "Net 30 Days"
    sales_rep_id: str = "KR"
    tax_code: str = "NONTAX"
    credit_hold: bool = False
    balance: Decimal = Decimal("0.00")

    model_config = ConfigDict(extra="ignore", from_attributes=True)


class SageVendor(BaseModel):
    """Sage 50 Vendor Master DTO."""
    vendor_id: str
    name: str
    address_line1: str
    address_line2: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    zip: Optional[str] = None
    country: str = "USA"
    terms_description: str = "Net 30 Days"
    tax_id: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None

    model_config = ConfigDict(extra="ignore", from_attributes=True)


class SageInventoryItem(BaseModel):
    """Sage 50 Item Master DTO."""
    item_id: str
    description: str
    item_class: str = "Stock item"  # Stock item, Non-stock item, Service, Assembly
    gl_sales_account: str = "4000-00"
    gl_inventory_account: str = "1200-00"
    gl_cogs_account: str = "5000-00"
    standard_cost: Decimal = Decimal("0.00")
    last_unit_cost: Decimal = Decimal("0.00")
    price_level1: Decimal = Decimal("0.00")
    quantity_on_hand: Decimal = Decimal("0.00")
    preferred_vendor_id: Optional[str] = None
    uom: str = "EA"

    model_config = ConfigDict(extra="ignore", from_attributes=True)


class SageGLAccount(BaseModel):
    """Sage 50 Chart of Accounts General Ledger DTO."""
    account_id: str
    description: str
    account_type: str = "Income"  # Income, Expense, Inventory, Accounts Receivable

    model_config = ConfigDict(extra="ignore", from_attributes=True)


class SageSalesOrderLine(BaseModel):
    """Single line item on a Sage 50 Sales Order."""
    item_id: str
    description: str
    quantity: Decimal
    unit_price: Decimal
    amount: Decimal
    gl_sales_account: str = "4000-00"
    uom: str = "EA"
    taxable: bool = False

    model_config = ConfigDict(extra="ignore", from_attributes=True)


class SageSalesOrder(BaseModel):
    """Sage 50 Sales Order document structure."""
    sales_order_number: str
    customer_id: str
    customer_po_number: str
    order_date: date
    ship_by_date: Optional[date] = None
    ship_via: str = "MOTOR FRT"
    payment_terms: str = "Net 30 Days"
    sales_rep: str = "Karissa M. Rakestraw"
    ship_to_name: str
    ship_to_address: str
    bill_to_address: str
    lines: List[SageSalesOrderLine]
    subtotal: Decimal
    sales_tax: Decimal = Decimal("0.00")
    freight_amount: Decimal = Decimal("0.00")
    total_amount: Decimal
    internal_notes: Optional[str] = None

    model_config = ConfigDict(extra="ignore", from_attributes=True)


class SagePurchaseOrderLine(BaseModel):
    """Single line item on a Sage 50 Vendor Purchase Order."""
    item_id: str
    description: str
    quantity: Decimal
    unit_cost: Decimal
    amount: Decimal
    gl_expense_account: str = "5000-00"
    uom: str = "EA"
    drawing_reference: Optional[str] = None

    model_config = ConfigDict(extra="ignore", from_attributes=True)


class SagePurchaseOrder(BaseModel):
    """Sage 50 Purchase Order document structure."""
    purchase_order_number: str
    vendor_id: str
    order_date: date
    good_thru_date: Optional[date] = None
    ship_via: str = "MOTOR FRT"
    payment_terms: str = "Net 30 Days"
    vendor_name: str
    vendor_address: str
    ship_to_name: str = "ALLIED WILL ARRANGE SHIPPING"
    ship_to_address: str = "ALLIED WELDING SYSTEMS, 17810 DAVENPORT ROAD, SUITE 109, DALLAS, TX 75252"
    lines: List[SagePurchaseOrderLine]
    total_amount: Decimal
    special_notes: List[str] = Field(default_factory=list)
    blind_shipment: bool = True

    model_config = ConfigDict(extra="ignore", from_attributes=True)
