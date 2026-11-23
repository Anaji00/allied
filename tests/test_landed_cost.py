"""Landed cost on temporary data: Decimal math, tariff as-of dates and the stale warning, "duty unknown" never zero, the tier prices, the markup workbook, the freight quote request. Nothing is sent."""
import io
from datetime import date, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from openpyxl import load_workbook

from app.config import settings
from app.core.database import SessionLocal
from app.models.db_models import TariffRateDB
from app.services import landed_cost as lc
from tests.conftest import staff_token

MGR = {"email": "mgr.lc@allied.test", "role": "MANAGER"}


@pytest.fixture(autouse=True)
def clean_table():
    with SessionLocal() as db:
        db.query(TariffRateDB).delete()
        db.commit()
    yield


def _line(**kw):
    return {"description": "AR400 liner", "quantity": "10", "unit_cost": "100.00", "category": "wear plate", **kw}


def test_duty_freight_and_tiers_are_exact_decimals():
    lc.set_rate("wear plate", "25", MGR, hs_code="7208", country="CN", effective_from="2026-01-01")
    out = lc.compute([_line(country="CN")], freight="150.50", tiers=["20", "35.5"])
    assert out["goods"] == "1000.00" and out["tariff_total"] == "250.00" and out["freight"] == "150.50" and out["landed_total"] == "1400.50"
    assert out["landed_note"] is None and out["tariff_unknown"] is False
    assert [t["price"] for t in out["tiers"]] == ["1680.60", "1897.68"] and [t["margin"] for t in out["tiers"]] == ["280.10", "497.18"] and all(t["firm"] for t in out["tiers"])


def test_an_unknown_duty_or_freight_is_never_zero_and_the_tiers_say_they_are_not_firm():
    out = lc.compute([_line()], freight=None, tiers=["30"])
    assert out["tariff_unknown"] and out["freight_unknown"] and out["tariff_total"] == "0.00"
    assert out["lines"][0]["duty"] is None and "unknown" in out["lines"][0]["duty_note"]
    assert "EXCLUDES duty and freight" in out["landed_note"] and out["tiers"][0]["firm"] is False
    zero = lc.compute([_line()], freight="0", tiers=["30"])
    assert zero["freight_unknown"] is False and "EXCLUDES duty" in zero["landed_note"] and "freight" not in zero["landed_note"].split("EXCLUDES")[1]


def test_the_rate_in_force_on_the_as_of_date_wins_and_a_country_rate_beats_a_general_one():
    lc.set_rate("wear plate", "10", MGR, effective_from="2026-01-01")
    lc.set_rate("wear plate", "25", MGR, effective_from="2026-06-01")
    lc.set_rate("wear plate", "40", MGR, country="CN", effective_from="2026-06-01")
    assert lc.rate_for("wear plate", as_of="2026-03-01")["rate_percent"] == "10.00"
    assert lc.rate_for("wear plate", as_of="2026-07-01")["rate_percent"] == "25.00"
    assert lc.rate_for("wear plate", country="CN", as_of="2026-07-01")["rate_percent"] == "40.00"
    assert lc.rate_for("wear plate", country="DE", as_of="2026-07-01")["rate_percent"] == "25.00"
    assert lc.rate_for("wear plate", as_of="2025-12-31") is None and lc.rate_for("unknown thing") is None


def test_the_table_is_flagged_stale_when_empty_or_old():
    assert lc.list_rates()["stale"] and "no tariff rates" in lc.list_rates()["warning"]
    lc.set_rate("x", "5", MGR)
    assert lc.list_rates()["stale"] is False and lc.list_rates()["warning"] is None
    with SessionLocal() as db:
        from datetime import datetime, timezone

        for r in db.query(TariffRateDB):
            r.updated_at = datetime.now(timezone.utc) - timedelta(days=settings.TARIFF_STALE_DAYS + 5)
        db.commit()
    got = lc.list_rates()
    assert got["stale"] and "days ago" in got["warning"]


@pytest.mark.parametrize("bad", [{"quantity": "0"}, {"quantity": "abc"}, {"unit_cost": "-1"}, {"unit_cost": ""}, {"quantity": "nan"}, {"unit_cost": "inf"}])
def test_bad_numbers_are_refused_in_plain_words(bad):
    with pytest.raises(lc.CostError) as e:
        lc.compute([_line(**bad)])
    assert e.value.status == 422 and e.value.message
    with pytest.raises(lc.CostError):
        lc.compute([])
    with pytest.raises(lc.CostError):
        lc.set_rate("x", "900", MGR)
    with pytest.raises(lc.CostError):
        lc.set_rate("x", "5", MGR, effective_from="2026-13-01")


def test_figures_read_from_a_scan_are_flagged_for_a_person_and_the_workbook_shows_everything():
    lc.set_rate("wear plate", "25", MGR)
    sheet = lc.create_sheet({"lines": [_line(from_ocr=True)], "freight": None, "currency": "usd", "tiers": ["20", "30"], "vendor_quote_ref": "Q-1"}, MGR)
    assert sheet["lines"][0]["check"] == lc.NEEDS_CONFIRM and sheet["currency"] == "USD" and sheet["freight_unknown"] and "EXCLUDES freight" in sheet["landed_note"]
    with pytest.raises(lc.CostError) as e:
        lc.pick_tier(sheet["id"], "Tier 2", MGR)                                              # figures read from a scan: a person confirms them first
    assert e.value.status == 409
    assert lc.confirm_sheet(sheet["id"], MGR)["lines"][0]["confirmed_by"] == MGR["email"]
    assert lc.pick_tier(sheet["id"], "Tier 2", MGR)["picked_tier"] == "Tier 2"
    with pytest.raises(lc.CostError):
        lc.pick_tier(sheet["id"], "Tier 9", MGR)
    wb = load_workbook(io.BytesIO(lc.sheet_xlsx(sheet["id"])))
    assert wb.sheetnames == ["Landed cost", "Markup tiers"]
    cells = [c.value for row in wb["Landed cost"].iter_rows() for c in row if c.value is not None]
    assert lc.NEEDS_CONFIRM in cells and "unknown" in cells
    tiers = list(wb["Markup tiers"].iter_rows(min_row=2, values_only=True))
    assert tiers[1][5] == "<- picked" and str(tiers[0][4]).startswith("NO")


CARGO = "Packing: 3 crates. Gross weight: 1,250.5 kg. Dimensions: 120 x 60 x 45 cm. Net weight 1,100 kg. Dimensions: 100x50x40 cm"


def test_weights_and_dimensions_are_read_from_a_quote_and_always_flagged():
    c = lc.parse_cargo(CARGO)
    assert c["weights"][0] == {"value": "1250.5", "unit": "kg"} and c["dimensions"][0] == {"l": "120", "w": "60", "h": "45", "unit": "cm"} and c["packages"] == ["3"]
    assert c["check"] == lc.NEEDS_CONFIRM and c["complete"] and lc.parse_cargo("nothing here")["complete"] is False


def test_the_freight_request_is_staged_names_no_customer_and_needs_figures_and_a_real_address():
    draft = lc.freight_rfq_draft(lc.parse_cargo(CARGO), "ops@forwarder.test", "Ningbo", "Houston", MGR)
    assert draft["drafted"] and draft["check"] == lc.NEEDS_CONFIRM
    with SessionLocal() as db:
        from app.models.db_models import AssistantActionDB

        row = db.get(AssistantActionDB, draft["action_id"])
        assert row.status == "PENDING" and "1250.5 kg" in row.args["body"] and "120 x 60 x 45 cm" in row.args["body"] and "Ningbo" in row.args["body"]
    with pytest.raises(lc.CostError):
        lc.freight_rfq_draft(lc.parse_cargo(CARGO), "not-an-email", "", "", MGR)
    with pytest.raises(lc.CostError):
        lc.freight_rfq_draft(lc.parse_cargo("no figures"), "ops@forwarder.test", "", "", MGR)


def test_the_api_is_manager_only(auth_enforced):
    from app.main import app

    c = TestClient(app, raise_server_exceptions=False)
    h = lambda role: {"Authorization": "Bearer " + staff_token(f"lc-{role.lower()}@allied.test", role)}  # noqa: E731
    body = {"lines": [{"quantity": "2", "unit_cost": "5", "category": "none"}], "freight": "1"}
    assert c.post("/api/v1/landed-cost/calculate", json=body, headers=h("OPERATOR")).status_code == 403
    assert c.get("/api/v1/landed-cost/tariffs", headers=h("VIEWER")).status_code == 403
    ok = c.post("/api/v1/landed-cost/sheets", json=body, headers=h("MANAGER"))
    assert ok.status_code == 200 and ok.json()["landed_total"] == "11.00"
    x = c.get(f"/api/v1/landed-cost/sheets/{ok.json()['id']}/xlsx", headers=h("MANAGER"))
    assert x.status_code == 200 and x.content[:2] == b"PK"
    assert c.post("/api/v1/landed-cost/calculate", json={"lines": []}, headers=h("MANAGER")).status_code == 422
