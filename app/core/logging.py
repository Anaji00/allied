"""Structured JSON logging with trace context for Allied Universal Bridge."""

import json
import logging
import sys
from datetime import datetime, timezone
from typing import Any, Dict


class JSONFormatter(logging.Formatter):
    """Formats log records as structured JSON lines."""

    def format(self, record: logging.LogRecord) -> str:
        log_payload: Dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "module": record.module,
            "line": record.lineno,
        }

        # Include custom context attributes if passed in extra
        if hasattr(record, "trace_id"):
            log_payload["trace_id"] = record.trace_id
        if hasattr(record, "order_id"):
            log_payload["order_id"] = record.order_id
        if hasattr(record, "customer_id"):
            log_payload["customer_id"] = record.customer_id
        if hasattr(record, "vendor_id"):
            log_payload["vendor_id"] = record.vendor_id

        if record.exc_info:
            log_payload["exception"] = self.formatException(record.exc_info)

        return json.dumps(log_payload)


def setup_logging(level: str = "INFO") -> logging.Logger:
    """Configures root logger with JSONFormatter on stdout."""
    root_logger = logging.getLogger("allied_bridge")
    root_logger.setLevel(getattr(logging, level.upper(), logging.INFO))

    # Remove existing handlers to avoid duplicates
    if not root_logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(JSONFormatter())
        root_logger.addHandler(handler)

    return root_logger


logger = setup_logging()
