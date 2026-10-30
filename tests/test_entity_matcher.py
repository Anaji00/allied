"""Unit tests for in-memory RapidFuzz catalog matcher and alias normalizer."""

import pytest
from app.core.exceptions import EntityNotFoundError
from app.services.entity_matcher import EntityMatcher


@pytest.fixture
def matcher():
    """Initializes EntityMatcher with standard catalogs."""
    return EntityMatcher()


def test_exact_sku_matching(matcher):
    """Tests direct SKU matching against master catalog."""
    item, score = matcher.match_item("OC755507")
    assert item["item_id"] == "OC755507"
    assert score == 100.0
    assert item["preferred_vendor"] == "NIBROCK"


def test_alias_dictionary_resolution(matcher):
    """Tests shop slang resolution via alias dictionary."""
    aliases_to_test = [
        ("Hammermill Screens", "OC755507"),
        ("Suction Hoses", "OC262064"),
        ("Flight Screw", "KN5218554"),
        ("Engineering Scans", "SCANNING/ENGINEERIN"),
        ("9S10302", "SCANNING/ENGINEERIN"),
        ("SAP 755507", "OC755507"),
    ]
    for slang, expected_sku in aliases_to_test:
        item, score = matcher.match_item(slang)
        assert item["item_id"] == expected_sku
        assert score >= 90.0


def test_fuzzy_sku_matching(matcher):
    """Tests fuzzy token-sort matching for noisy inputs."""
    noisy_inputs = [
        ("2.5 in dia hammermill screen 41 length", "OC755507"),
        ("5 id 120 lg high density ceramic rubber hose", "OC262064"),
        ("16 dia 16 pitch 14 ft sectional screw assembly end shaft", "KN5218554"),
    ]
    for noisy, expected_sku in noisy_inputs:
        item, score = matcher.match_item(noisy, min_confidence=60.0)
        assert item["item_id"] == expected_sku
        assert score >= 60.0


def test_customer_resolution(matcher):
    """Tests customer account resolution and terms lookup."""
    test_cases = [
        ("OC-Edmonton", "10221"),
        ("Owens Corning Starr", "10195"),
        ("Knauf", "10148"),
        ("IKO-SW", "681111"),
    ]
    for cust_query, expected_id in test_cases:
        cust, score = matcher.match_customer(cust_query)
        assert cust["customer_id"] == expected_id
        assert score >= 80.0


def test_vendor_resolution(matcher):
    """Tests vendor resolution including contact person aliases."""
    test_cases = [
        ("Duke Parker", "NIBROCK"),
        ("Helen", "ZIBO"),
        ("KWS Manufacturing", "KWS"),
        ("Shop", "STOCK"),
    ]
    for vend_query, expected_id in test_cases:
        vend, score = matcher.match_vendor(vend_query)
        assert vend["vendor_id"] == expected_id
        assert score >= 80.0


def test_unmatched_item_exception(matcher):
    """Tests that unrecognizable input raises EntityNotFoundError."""
    with pytest.raises(EntityNotFoundError):
        matcher.match_item("Completely Random Gibberish XYZ999000", min_confidence=80.0)
