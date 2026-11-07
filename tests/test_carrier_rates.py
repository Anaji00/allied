"""Carrier-neutral prices: the editable rate card, how fresh each price is, and live providers beside it.
Writes go to a temporary data folder; no carrier is called."""

import uuid
from datetime import date, timedelta

import pytest

from app.config import settings
from app.services import carrier_providers, carrier_rates
from app.services.logistics_dispatcher import logistics_dispatcher as d
from tests.conftest import staff_token

ADDR = "Receiving, 1 Main St, Irving, TX 75063"
CA = "831 Hayter Rd NW, Edmonton, AB T6S 1A1"


@pytest.fixture(autouse=True)
def _own_rate_file(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "DATA_DIR", tmp_path)
    monkeypatch.setattr(settings, "FEDEX_ENABLED", False)


def _by_id(i):
    return next(r for r in carrier_rates.load() if r["id"] == i)


def _names(q):
    return {x.carrier_name for x in q}


def test_with_no_saved_card_the_starter_lines_show_as_unreviewed_estimates_and_no_carrier_is_required():
    q = d.get_carrier_quotes("HQ", ADDR)
    assert _names(q) == {"TForce Freight", "FedEx Freight Priority"}
    assert all("estimate, not reviewed" in x.service_level for x in q)
    assert "Day & Ross Cross-Border" in _names(d.get_carrier_quotes("HQ", CA))
    assert {"Expeditors International", "DHL Global Forwarding"} <= _names(d.get_carrier_quotes("ZIBO", ADDR))


def test_changing_a_price_is_saved_stamped_today_and_used_by_the_next_quote():
    carrier_rates.update("tforce-ltl", {"rate_usd": 455.5})
    line = _by_id("tforce-ltl")
    assert line["rate_usd"] == 455.5 and line["as_of"] == date.today().isoformat()
    tf = next(x for x in d.get_carrier_quotes("HQ", ADDR) if x.carrier_name == "TForce Freight")
    assert tf.rate_usd == 455.5 and f"checked {date.today().isoformat()}" in tf.service_level


def test_a_price_nobody_has_checked_for_a_month_says_to_confirm_it():
    old = (date.today() - timedelta(days=settings.CARRIER_RATE_STALE_DAYS + 10)).isoformat()
    carrier_rates.update("tforce-ltl", {"as_of": old})
    tf = next(x for x in d.get_carrier_quotes("HQ", ADDR) if x.carrier_name == "TForce Freight")
    assert "days old: confirm before booking" in tf.service_level
    assert carrier_rates.confirm("tforce-ltl")["as_of"] == date.today().isoformat()


def test_a_per_pound_price_scales_with_weight():
    carrier_rates.update("tforce-ltl", {"rate_usd": 100, "per_lb_usd": 0.5})

    def tf(w):
        return next(x for x in d.get_carrier_quotes("HQ", ADDR, w) if x.carrier_name == "TForce Freight").rate_usd

    assert tf(None) == 100 and tf(200) == 200


def test_a_new_carrier_can_be_added_switched_off_and_removed():
    new = carrier_rates.add({"carrier": "Estes Express", "service": "LTL", "regions": ["domestic"], "rate_usd": 390, "transit_days": 3})
    assert new["id"] == "estes-express-ltl" and new["enabled"]
    assert "Estes Express" in _names(d.get_carrier_quotes("HQ", ADDR))
    assert "Estes Express" not in _names(d.get_carrier_quotes("HQ", CA))
    carrier_rates.update(new["id"], {"enabled": False})
    assert "Estes Express" not in _names(d.get_carrier_quotes("HQ", ADDR))
    carrier_rates.remove(new["id"])
    assert new["id"] not in {r["id"] for r in carrier_rates.load()}
    assert carrier_rates.add({"carrier": "Estes Express", "service": "LTL"})["id"] == "estes-express-ltl"


@pytest.mark.parametrize("bad,msg", [
    ({"carrier": ""}, "name"), ({"carrier": "X", "rate_usd": -1}, "between 0"), ({"carrier": "X", "rate_usd": "abc"}, "number"),
    ({"carrier": "X", "regions": ["mars"]}, "Where it applies"), ({"carrier": "X", "transit_days": 500}, "Transit days"),
    ({"carrier": "X", "as_of": "last week"}, "checked date"), ({"carrier": "x" * 90}, "name"),
])
def test_bad_lines_are_refused_in_plain_words(bad, msg):
    with pytest.raises(carrier_rates.RateCardError, match=msg):
        carrier_rates.add(bad)


def test_a_damaged_file_falls_back_to_the_starter_lines_and_saves_are_whole():
    (settings.DATA_DIR / "carrier_rates.json").write_text("{not json", encoding="utf-8")
    assert len(carrier_rates.load()) == 5
    carrier_rates.update("tforce-ltl", {"rate_usd": 1})
    assert _by_id("tforce-ltl")["rate_usd"] == 1 and not list(settings.DATA_DIR.glob(".carrier_rates.*"))


def test_a_live_provider_is_listed_first_one_that_fails_is_skipped_and_the_card_still_shows():
    class Fake:
        id, name = "ups", "UPS"

        def status(self):
            return {"id": "ups", "name": "UPS", "ready": True, "detail": "ok", "covers": ""}

        def quotes(self, a, w=None):
            return [{"name": "UPS Ground", "price": 80, "currency": "CAD", "transit": "3 days"}]

    class Down(Fake):
        id, name = "x", "Xcarrier"

        def quotes(self, a, w=None):
            raise RuntimeError("down")

    old = list(carrier_providers.PROVIDERS)
    carrier_providers.PROVIDERS[:] = [Fake(), Down()]
    try:
        q = d.get_carrier_quotes("HQ", ADDR)
    finally:
        carrier_providers.PROVIDERS[:] = old
    assert q[0].carrier_name == "UPS Ground" and q[0].service_level.startswith("Live UPS")
    assert q[0].rate_usd == round(80 * settings.CAD_TO_USD_RATE, 2)
    assert "TForce Freight" in _names(q) and not any("Xcarrier" in n for n in _names(q))


def test_the_api_lets_managers_edit_and_viewers_only_read(auth_enforced, client):
    def mk(role):
        return {"Authorization": f"Bearer {staff_token(f'{role.lower()}{uuid.uuid4().hex[:4]}@alliedweldingsystems.com', role)}"}

    v, m = mk("VIEWER"), mk("MANAGER")
    got = client.get("/api/v1/carriers/rates", headers=v).json()
    assert len(got["lines"]) == 5 and got["needs_review"] == 5 and got["live"][0]["id"] == "fedex" and "any" in got["regions"]
    assert client.patch("/api/v1/carriers/rates/tforce-ltl", json={"rate_usd": 1}, headers=v).status_code == 403
    assert client.post("/api/v1/carriers/rates", json={"carrier": "A"}, headers=v).status_code == 403
    assert client.delete("/api/v1/carriers/rates/tforce-ltl", headers=v).status_code == 403
    ok = client.patch("/api/v1/carriers/rates/tforce-ltl", json={"rate_usd": 433, "id": "hack"}, headers=m)
    assert ok.status_code == 200 and ok.json()["id"] == "tforce-ltl" and ok.json()["rate_usd"] == 433
    assert client.patch("/api/v1/carriers/rates/tforce-ltl", json={"rate_usd": -2}, headers=m).status_code == 400
    assert client.patch("/api/v1/carriers/rates/nope", json={"rate_usd": 2}, headers=m).status_code == 404
    assert client.post("/api/v1/carriers/rates/tforce-ltl/confirm", headers=m).json()["as_of"] == date.today().isoformat()
    made = client.post("/api/v1/carriers/rates", json={"carrier": "Estes", "service": "LTL", "rate_usd": 5}, headers=m)
    assert made.status_code == 201 and client.delete(f"/api/v1/carriers/rates/{made.json()['id']}", headers=m).status_code == 204
    assert client.delete("/api/v1/carriers/rates/nope", headers=m).status_code == 404
    assert client.get("/api/v1/carriers/rates").status_code in (401, 403)


def test_one_generic_quote_endpoint_lists_every_carrier_live_ones_flagged(client, monkeypatch):
    class Fake:
        id, name = "ups", "UPS"

        def status(self):
            return {"id": "ups", "name": "UPS", "ready": True, "detail": "", "covers": ""}

        def quotes(self, a, w=None):
            return [{"name": "UPS Ground", "price": 50, "currency": "USD", "transit": "2 days"}]

    monkeypatch.setattr(carrier_providers, "PROVIDERS", [Fake()])
    rows = client.get("/api/v1/carriers/quote", params={"address": ADDR, "weight_lbs": 120}).json()
    assert rows[0]["carrier"] == "UPS Ground" and rows[0]["live"] and rows[0]["usd"] == 50
    assert any(r["carrier"] == "TForce Freight" and not r["live"] for r in rows)
    assert client.get("/api/v1/carriers/quote", params={"address": "x"}).status_code == 422
