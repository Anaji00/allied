"""Unit and integration tests for Sales Intelligence, Wear ROI Calculator, and Target Plant Accounts."""

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services.lead_agent import ROICalculatorRequest, lead_agent

client = TestClient(app)


def test_wear_roi_calculator_service():
    """Verify ROI financial calculator computes correct TCO, savings, and payback period."""
    req = ROICalculatorRequest(
        equipment_type="Hammermill Screen",
        current_material="AR400 Carbon Steel",
        current_part_cost=1200.0,
        current_lifespan_months=3.0,
        allied_upgrade_material="Allied 300XL Chromium Carbide",
        allied_part_cost=2800.0,
        allied_lifespan_months=12.0,
        replacement_labor_cost=2500.0,
        downtime_cost_per_hour=1500.0,
        downtime_hours_per_swap=6.0,
    )

    result = lead_agent.calculate_roi(req)

    assert result.lifespan_multiplier == 4.0
    assert result.current_annual_swaps == 4.0
    assert result.allied_annual_swaps == 1.0
    assert result.net_annual_savings > 0
    assert result.savings_percentage > 50.0
    assert result.payback_period_days > 0
    assert "saves" in result.executive_summary


def test_roi_calculator_api_endpoint():
    """Verify /api/v1/leads/roi-calculator REST API endpoint."""
    payload = {
        "equipment_type": "Slurry Hose",
        "current_material": "Standard Rubber Hose",
        "current_part_cost": 850.0,
        "current_lifespan_months": 2.0,
        "allied_upgrade_material": "92% Alumina Ceramic Lined Slurry Hose",
        "allied_part_cost": 2200.0,
        "allied_lifespan_months": 12.0,
        "replacement_labor_cost": 1500.0,
        "downtime_cost_per_hour": 2000.0,
        "downtime_hours_per_swap": 4.0,
    }
    response = client.post("/api/v1/leads/roi-calculator", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["lifespan_multiplier"] == 6.0
    assert data["net_annual_savings"] > 20000.0


def test_target_accounts_api_endpoint():
    """Verify /api/v1/leads/targets returns enterprise plant accounts across target industries."""
    response = client.get("/api/v1/leads/targets")
    assert response.status_code == 200
    targets = response.json()
    assert len(targets) >= 4
    companies = [t["company"] for t in targets]
    assert "Owens Corning" in companies
    assert "Knauf Insulation" in companies


def test_lead_cold_outreach_generation():
    """Verify generation of personalized cold outreach emails for plant engineers."""
    payload = {
        "name": "Marcus Vance",
        "title": "Plant Maintenance Manager",
        "company": "Owens Corning",
        "industry": "Asphalt & Roofing",
        "email": "m.vance@owenscorning.com",
    }
    response = client.post("/api/v1/leads/outreach", json=payload)
    assert response.status_code == 200
    assert "Marcus" in response.json()["email_body"]
    assert "Allied 300XL" in response.json()["email_body"]


def test_lead_outreach_needs_no_contact_address():
    """Target accounts have no email on file; drafting must not require one."""
    payload = {
        "name": "Marcus Vance",
        "title": "Plant Maintenance Manager",
        "company": "Owens Corning",
        "industry": "Asphalt & Roofing",
    }
    response = client.post("/api/v1/leads/outreach", json=payload)
    assert response.status_code == 200, response.text
    assert "Marcus" in response.json()["email_body"]
