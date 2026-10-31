"""MONDAY_READ_ONLY: the app may read monday, and no change of any kind leaves it."""

import pytest

from app.config import settings
from app.services.monday_client import MondayClient, MondayReadOnlyError, is_mutation


class FakeHTTP:
    """Stands in for the network; records every request that would have been sent."""

    def __init__(self):
        self.sent = []

    def post(self, url, json=None, headers=None):
        self.sent.append(json["query"])

        class R:
            status_code = 200
            headers = {}
            text = ""

            @staticmethod
            def json():
                return {"data": {"boards": [{"id": "1"}]}}

        return R()


@pytest.fixture
def live(monkeypatch):
    monkeypatch.setattr(settings, "MONDAY_SIMULATION_MODE", False)
    client = MondayClient(api_key="test-token", api_url="https://monday.invalid/v2")
    fake = FakeHTTP()
    monkeypatch.setattr(client, "_http", lambda: fake)
    return client, fake


def test_read_only_is_the_default():
    assert type(settings).model_fields["MONDAY_READ_ONLY"].default is True


def test_reads_go_out_and_every_write_is_blocked(live, monkeypatch):
    monkeypatch.setattr(settings, "MONDAY_READ_ONLY", True)
    client, fake = live
    assert client.live and not client.writable
    assert client.execute_query("query { boards(ids: [1]) { id } }")["data"]["boards"]
    assert client.post_pulse_update(123, "hello") is True          # carries on as in simulation
    assert client.update_column_values(1, 123, {"status": {"label": "Done"}}) is True
    result = client.execute_query("mutation { delete_item(item_id: 123) { id } }")
    assert result["data"]["read_only"] is True
    assert fake.sent == ["query { boards(ids: [1]) { id } }"]      # only the read left
    blocked = [c for c in client.simulated_calls if c.get("blocked") == "read-only"]
    assert len(blocked) == 3


def test_the_transport_refuses_a_write_even_if_called_directly(live, monkeypatch):
    monkeypatch.setattr(settings, "MONDAY_READ_ONLY", True)
    client, fake = live
    with pytest.raises(MondayReadOnlyError):
        client._post_once({"query": "mutation { archive_item(item_id: 1) { id } }"})
    assert fake.sent == []


def test_writes_go_out_only_when_switched_off_for_a_test_board(live, monkeypatch):
    monkeypatch.setattr(settings, "MONDAY_READ_ONLY", False)
    client, fake = live
    assert client.writable
    client.post_pulse_update(123, "on the test board")
    assert len(fake.sent) == 1 and "create_update" in fake.sent[0]


@pytest.mark.parametrize("query, writes", [
    ("query { boards { id } }", False),
    ("{ me { id } }", False),
    ("query ($x: [ID!]) { items(ids: $x) { name } }", False),
    ('query { items_page_by_column_values(columns: [{column_id: "t", column_values: ["mutation"]}]) { cursor } }', False),
    ("# a comment saying mutation\nquery { me { id } }", False),
    ("mutation { create_update(item_id: 1, body: \"x\") { id } }", True),
    ("   mutation ($a: ID!) { delete_item(item_id: $a) { id } }", True),
    ("# note\nmutation { archive_item(item_id: 1) { id } }", True),
    ('query { a(s: "#") { id } } mutation { b { id } }', True),          # a '#' in a string hides nothing
    ('query { a(s: "say \\"hi\\"") { id } } mutation { b { id } }', True),
    ('query { a(s: """\nmutation\n""") { id } }', False),
    ("subscription { x }", True),                                         # not plainly a read
    ("", True),
])
def test_what_counts_as_a_write(query, writes):
    assert is_mutation(query) is writes


def test_the_assistant_says_nothing_was_posted(live, monkeypatch):
    from app.services.assistant import tools

    monkeypatch.setattr(settings, "MONDAY_READ_ONLY", True)
    client, fake = live
    monkeypatch.setattr("app.core.container.get_monday_client", lambda: client)
    out = tools.post_monday_update({"item_id": 5, "text": "hi"}, {"email": "a@allied.test", "role": "OPERATOR"})
    assert "read-only" in out["done"] and fake.sent == []
