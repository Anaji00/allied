"""Guards for the two scanning/concurrency fixes.

These are structural assertions rather than wall-clock ones: timing tests are
flaky on shared hardware, but the properties that made the system slow are
easy to assert directly.
"""

import ast
import pathlib

import pytest

from app.services.document_scanner_service import DocumentScannerService
from app.services.entity_matcher import PREFIX_LEN, EntityMatcher

ENDPOINTS = pathlib.Path(__file__).resolve().parent.parent / "app" / "api" / "v1" / "endpoints"


# ---------------------------------------------------------------------------
# F-01: catalogue scanning must not depend on catalogue size
# ---------------------------------------------------------------------------


def _grow(matcher, n):
    """Pads the catalogue to n synthetic SKUs and rebuilds the scan index."""
    seed = list(matcher.items_cache.items())
    while len(matcher.items_cache) < n:
        i = len(matcher.items_cache)
        key, rec = seed[i % len(seed)]
        synthetic = f"SYN-{i:06d}-{key}"
        matcher.items_cache[synthetic] = dict(rec, item_id=synthetic)
    matcher._build_scan_index()
    return matcher


def test_scan_index_is_built_and_ordered_longest_first():
    """A longer SKU must be offered before a shorter one contained in it."""
    matcher = EntityMatcher()
    assert matcher.items_by_length, "scan index was not built on load"
    lengths = [len(k) for k, _ in matcher.items_by_length]
    assert lengths == sorted(lengths, reverse=True)


def test_scan_candidates_never_loses_a_real_match():
    """The prefix prefilter must be exact: no SKU present in the text is dropped."""
    matcher = _grow(EntityMatcher(), 1200)
    for item_id, _ in list(matcher.items_by_length)[:40]:
        line = f"LINE 1: 2X {item_id} SOME DESCRIPTION"
        found = [k for k, _ in matcher.scan_candidates(line)]
        assert item_id in found, f"{item_id} was filtered out of its own line"


def test_scan_candidates_narrows_the_search():
    """A line should consider a small fraction of a large catalogue."""
    matcher = _grow(EntityMatcher(), 5000)
    line = "LINE 1: 2X OC755507 HAMMERMILL SCREEN"
    candidates = matcher.scan_candidates(line)
    assert len(candidates) < len(matcher.items_by_length) / 10, (
        f"prefilter kept {len(candidates)} of {len(matcher.items_by_length)} SKUs; "
        "scanning is still proportional to catalogue size"
    )


def test_document_scan_cost_is_flat_across_catalogue_sizes():
    """Scanning must not slow down as the catalogue grows.

    Asserted by counting containment tests rather than by timing: the scanner
    used to re-sort the whole catalogue for every line, so cost grew faster
    than the catalogue did.
    """
    text = "PURCHASE ORDER\nBUYER: Owens Corning\n" + "\n".join(
        f"Line {i}: 2x OC755507 Hammermill Screen" for i in range(1, 21)
    )
    counts = {}
    for size in (100, 4000):
        matcher = _grow(EntityMatcher(), size)
        seen = []
        original = matcher.scan_candidates

        def counting(haystack, aliases=False, _o=original, _s=seen):
            out = _o(haystack, aliases=aliases)
            _s.append(len(out))
            return out

        matcher.scan_candidates = counting
        DocumentScannerService(entity_matcher=matcher).scan_document(text.encode(), "po.txt")
        counts[size] = sum(seen)

    assert counts[4000] <= counts[100] * 3, (
        f"candidate tests grew from {counts[100]} to {counts[4000]} when the catalogue "
        "grew 40x; the scan is still catalogue-proportional"
    )


def test_prefix_length_is_sane():
    assert PREFIX_LEN >= 2, "a 1-character prefix buckets almost nothing"


# ---------------------------------------------------------------------------
# F-02: no handler may do blocking work on the event loop
# ---------------------------------------------------------------------------


def _async_handlers_without_await():
    offenders = []
    for path in sorted(ENDPOINTS.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in tree.body:
            if isinstance(node, ast.AsyncFunctionDef):
                if not any(isinstance(x, ast.Await) for x in ast.walk(node)):
                    offenders.append(f"{path.name}::{node.name}")
    return offenders


def test_no_endpoint_is_async_without_awaiting():
    """An `async def` handler that never awaits blocks the whole server.

    FastAPI runs a plain `def` handler in a worker thread but runs `async def`
    directly on the event loop, so a handler declared async that only does
    blocking work stalls every concurrent request for its full duration.
    Declare such handlers `def` and let FastAPI thread them.
    """
    offenders = _async_handlers_without_await()
    assert not offenders, (
        "these handlers are declared async but never await, so they run blocking "
        "work on the event loop:\n  " + "\n  ".join(offenders)
    )
