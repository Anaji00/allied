"""The prospect search reports what it found, and never invents people."""

import pytest

from app.services import lead_agent as la

BODY = {"job_titles": ["Maintenance Manager"], "industries": ["Cement"], "location": "Texas"}


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


class _FakeSearch:
    hits = []
    error = None

    def text(self, query, max_results=15):
        if self.error:
            raise self.error
        return self.hits[:max_results]


@pytest.fixture
def search(monkeypatch):
    fake = _FakeSearch()
    fake.hits, fake.error = [], None
    monkeypatch.setattr(la, "HAS_DDGS", True)
    monkeypatch.setattr(la, "DDGS", lambda: fake, raising=False)
    return fake


def test_results_come_only_from_the_search(client, admin_token, search):
    search.hits = [{"title": "Dana Ruiz - Plant Maintenance Manager - Lehigh Hanson | LinkedIn", "body": "Cement plant, Texas", "href": "https://www.linkedin.com/in/example"}]
    res = client.post("/api/v1/leads/scrape", json=BODY, headers=_auth(admin_token))
    assert res.status_code == 200, res.text
    assert res.json() == [{
        "name": "Dana Ruiz",
        "title": "Plant Maintenance Manager",
        "company": "Lehigh Hanson",
        "snippet": "Cement plant, Texas",
        "profile_url": "https://www.linkedin.com/in/example",
    }]


def test_nothing_found_is_an_empty_list(client, admin_token, search):
    res = client.post("/api/v1/leads/scrape", json=BODY, headers=_auth(admin_token))
    assert res.status_code == 200 and res.json() == []


@pytest.mark.parametrize("installed", [False, True])
def test_search_that_cannot_run_says_so_instead_of_inventing_contacts(client, admin_token, search, monkeypatch, installed):
    if installed:
        search.error = RuntimeError("rate limited")
    else:
        monkeypatch.setattr(la, "HAS_DDGS", False)
    res = client.post("/api/v1/leads/scrape", json=BODY, headers=_auth(admin_token))
    assert res.status_code == 503
    assert "@" not in res.text  # no made-up email addresses


def test_titles_and_industries_are_required(client, admin_token, search):
    res = client.post("/api/v1/leads/scrape", json={**BODY, "job_titles": [" "]}, headers=_auth(admin_token))
    assert res.status_code == 422


def test_assistant_action_reports_an_outage_too(client, admin_token, search, monkeypatch):
    monkeypatch.setattr(la, "HAS_DDGS", False)
    res = client.post(
        "/api/v1/chat/execute-action",
        json={"action_type": "EXECUTE_LEAD_SCRAPE", "payload": BODY},
        headers=_auth(admin_token),
    )
    assert res.status_code == 503
    assert "@" not in res.text
