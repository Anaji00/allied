"""Shared service instances for the application.

Every router and service used to construct its own `OrderOrchestrator`, and
each of those built its own entity matcher, MTR service, sales service and
Sage reader. Importing the application therefore read the master catalogues
six or more times, and — more importantly — state that looked shared was not:
that is what produced the duplicate sales-order numbering defect, where six
writers each believed they owned the sequence.

Accessors are lazy and memoized, so importing a module no longer does disk
I/O, and everything that asks for a service gets the same one.
"""

from functools import lru_cache
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - import cycle avoidance
    from app.services.entity_matcher import EntityMatcher
    from app.services.monday_client import MondayClient
    from app.services.mtr_service import MTRService
    from app.services.order_orchestrator import OrderOrchestrator
    from app.services.packing_slips import PackingSlipGenerator
    from app.services.sage_sdk_writer import SageSDKWriter


@lru_cache(maxsize=1)
def get_entity_matcher() -> "EntityMatcher":
    """The catalogue matcher. Loading it reads every master CSV, so there is one."""
    from app.services.entity_matcher import EntityMatcher

    return EntityMatcher()


@lru_cache(maxsize=1)
def get_mtr_service() -> "MTRService":
    """Reuses the module-level instance that the compliance router imports.

    Building a second one here would mean the NCR registry the API reads is
    not the one order processing writes to.
    """
    from app.services.mtr_service import mtr_service

    return mtr_service


@lru_cache(maxsize=1)
def get_monday_client() -> "MondayClient":
    from app.services.monday_client import MondayClient

    return MondayClient()


@lru_cache(maxsize=1)
def get_packing_slip_generator() -> "PackingSlipGenerator":
    from app.services.packing_slips import PackingSlipGenerator

    return PackingSlipGenerator()


@lru_cache(maxsize=1)
def get_sage_writer() -> "SageSDKWriter":
    """Owns document numbering, so there must be exactly one."""
    from app.services.sage_sdk_writer import SageSDKWriter

    return SageSDKWriter()


@lru_cache(maxsize=1)
def get_orchestrator() -> "OrderOrchestrator":
    """The order pipeline, wired from the shared components above."""
    from app.services.order_orchestrator import OrderOrchestrator

    return OrderOrchestrator(
        entity_matcher=get_entity_matcher(),
        sdk_writer=get_sage_writer(),
        packing_slips=get_packing_slip_generator(),
        monday_client=get_monday_client(),
        mtr_service=get_mtr_service(),
    )


def reset() -> None:
    """Drops every cached instance. For tests that reconfigure settings."""
    for accessor in (
        get_entity_matcher,
        get_mtr_service,
        get_monday_client,
        get_packing_slip_generator,
        get_sage_writer,
        get_orchestrator,
    ):
        accessor.cache_clear()
