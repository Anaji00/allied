"""Domain-specific exceptions for the Allied Universal Bridge microservice."""

from typing import Any, Dict, Optional


class BridgeBaseException(Exception):
    """Base exception class for all bridge domain errors."""

    def __init__(self, message: str, details: Optional[Dict[str, Any]] = None):
        super().__init__(message)
        self.message = message
        self.details = details or {}


class SageODBCError(BridgeBaseException):
    """Raised when an Actian Zen / PSQL ODBC query fails or times out."""
    pass


class SageSDKWriteError(BridgeBaseException):
    """Raised when COM SDK transaction execution fails to commit SO/PO."""
    pass


class EntityNotFoundError(BridgeBaseException):
    """Raised when SKU, customer, or vendor lookup falls below confidence threshold."""
    pass


class MathEngineValidationError(BridgeBaseException):
    """Raised when nesting, kerf, or geometry calculations produce invalid configurations."""
    pass


class ComplianceViolationError(BridgeBaseException):
    """Raised when an order violates blind-shipping, margin threshold, or export rules."""
    pass


class MondayAPIError(BridgeBaseException):
    """Raised when communication with Monday.com v2 GraphQL API encounters an error."""
    pass


class DualSidedTransactionError(BridgeBaseException):
    """Raised when dual-sided generation fails to balance revenue SO and expense POs."""
    pass


class OrderValidationError(BridgeBaseException):
    """Raised when an inbound PO fails structural validation."""
    pass
