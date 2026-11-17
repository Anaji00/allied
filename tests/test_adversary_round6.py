"""Adversarial round 6: check_cell against every monday column type, the lowercase-colour rule, and the assistant HTTP endpoints. Fakes, blocked network and temporary database only."""
import json
import socket
import time

import pytest

from app.config import settings
from app.services import monday_push
from app.services.assistant import agent, tools
from tests.test_adversary_copilot import _board, _item, live, only  # noqa: F401
from tests.test_board_changes import OP, FakeMonday, _empty_staging  # noqa: F401

ADMIN = {"email": settings.ADMIN_EMAIL, "role": "ADMIN"}


def _one(ctype, labels=None):
    col = {"id": "c1", "title": "Cell", "type": ctype}
    if labels is not None:
        col["labels"] = labels
    return _board("Open RFQs", [{"id": "1", "name": "HECLA", "values": {}}], [col])


# ============================================================ every monday column type

@pytest.mark.parametrize("ctype,value", [("checkbox", True), ("checkbox", "yes"), ("rating", "5"), ("rating", 4), ("email", "ann@example.com"), ("phone", "+1 555 123 4567"),
                                         ("link", "https://example.com"), ("timeline", "2026-11-01 to 2026-11-10"), ("hour", "13:30"), ("week", "2026-W46"), ("tags", "urgent")])
def test_object_column_types_are_shaped_or_refused(only, ctype, value):
    a = _one(ctype)
    try:
        col, val = monday_push.check_cell(a, "Cell", value)
    except ValueError:
        return
    shaped = monday_push._shape(a, {col["id"]: val})
    assert isinstance(shaped.get(col["id"]), dict), shaped


@pytest.mark.parametrize("ctype,value", [("email", "not an email"), ("phone", "hello"), ("link", "javascript:alert(1)"), ("link", "data:text/html,<script>1</script>")])
def test_email_phone_and_link_content_is_checked(only, ctype, value):
    with pytest.raises(ValueError):
        monday_push.check_cell(_one(ctype), "Cell", value)


@pytest.mark.parametrize("ctype,value", [("date", {"date": "next friday"}), ("text", {"a": 1}), ("numbers", {"x": 1}), ("date", {})])
def test_a_dict_value_is_checked_against_the_column_type(only, ctype, value):
    with pytest.raises(ValueError):
        monday_push.check_cell(_one(ctype), "Cell", value)


@pytest.mark.parametrize("ctype,value", [("numbers", "1_000"), ("numbers", "١٢"), ("numbers", "１２"), ("date", "٢٠٢٦-١١-٢٠")])
def test_python_lenient_number_and_date_forms_are_refused(only, ctype, value):
    with pytest.raises(ValueError):
        monday_push.check_cell(_one(ctype), "Cell", value)


def test_plain_columns_accept_plain_values_and_bad_dates_numbers_are_refused(only):
    for ctype, value in (("text", "hello"), ("long_text", "a\nb"), ("numbers", "12.5"), ("numbers", 7), ("date", "2026-11-20")):
        assert monday_push.check_cell(_one(ctype), "Cell", value)
    for ctype, value in (("date", "2026-02-30"), ("date", "11/20/2026"), ("numbers", "1e999"), ("numbers", "12,5"), ("numbers", "nan"), ("text", None), ("text", ""), ("text", "  ")):
        with pytest.raises(ValueError):
            monday_push.check_cell(_one(ctype), "Cell", value)


def test_read_only_column_types_are_refused(only):
    for ctype in ("formula", "mirror", "subtasks", "board_relation", "button", "creation_log", "last_updated", "item_id", "auto_number", "file", "name"):
        with pytest.raises(ValueError):
            monday_push.check_cell(_one(ctype), "Cell", "x")


def test_a_new_row_may_leave_a_cell_blank_but_an_edit_may_not(only):
    a = _one("text")
    assert monday_push.check_cell(a, "Cell", "", new_row=True)
    with pytest.raises(ValueError):
        monday_push.check_cell(a, "Cell", "")


def test_check_cell_survives_hostile_values_for_every_type(only):
    vals = [None, True, 0, -1, 10 ** 400, 1e308, "\x00", "‮" * 100, "x" * 5000, {"a": {"b": {"c": 1}}}, {"label": None}, {"label": {"x": 1}}, [], [1], object()]
    for ctype in ("text", "long_text", "numbers", "date", "status", "dropdown", "color", "checkbox", "rating", "email", "phone", "link", "timeline", "hour", "week", "tags", "people"):
        a = _one(ctype, {"Done": "", "Stuck": ""} if ctype in ("status", "dropdown", "color") else None)
        for v in vals:
            try:
                monday_push.check_cell(a, "Cell", v)
            except (ValueError, TypeError):
                pass
            except Exception as exc:  # noqa: BLE001
                pytest.fail(f"{ctype} with {type(v).__name__}: {type(exc).__name__}: {str(exc)[:80]}")


# ============================================================ the lowercase-colour rule

@pytest.mark.parametrize("labels,good", [({"0": "red", "1": "green", "2": "blue"}, "red"), ({"0": "steel", "1": "gold", "2": "silver"}, "steel")])
def test_lowercase_labels_in_an_index_board_are_labels(only, labels, good):
    a = _one("status", labels)
    assert monday_push.check_cell(a, "Cell", good)
    with pytest.raises(ValueError):
        monday_push.check_cell(a, "Cell", "1")


def test_uppercase_hex_colours_are_colours(only):
    a = _one("status", {"1": "#00C875", "2": "#FDAB3D"})
    assert monday_push.check_cell(a, "Cell", "1")


def test_the_usual_shapes_still_read_correctly(only):
    for labels, good, bad in (({"Stuck": "", "Done": ""}, "stuck", "1"), ({"0": "Working on it", "1": "Done"}, "done", "0"), ({"Done": "done-green", "Stuck": "stuck-red"}, "Done", "done-green"),
                              ({"1": "red", "2": "green"}, "2", "green"), ({"1": "#00c875", "2": "#fdab3d"}, "1", "#fdab3d"), ({"0": "Red", "1": "Green"}, "Green", "1")):
        a = _one("status", labels)
        assert monday_push.check_cell(a, "Cell", good)
        with pytest.raises(ValueError):
            monday_push.check_cell(a, "Cell", bad)


# ============================================================ the HTTP endpoints

@pytest.fixture()
def no_network(monkeypatch):
    real = socket.socket.connect

    def guarded(self, address, *a, **k):
        host = address[0] if isinstance(address, tuple) else str(address)
        if host in ("127.0.0.1", "::1", "localhost"):          # the test client's own loop-back socket pair
            return real(self, address, *a, **k)
        raise OSError("tests never open the network")
    monkeypatch.setattr(socket.socket, "connect", guarded)
    monkeypatch.setattr(socket, "create_connection", lambda *a, **k: (_ for _ in ()).throw(OSError("tests never open the network")))


@pytest.mark.parametrize("body", [{"messages": []}, {"messages": [{"role": "system", "content": "x"}]}, {"messages": [{"role": "user", "content": ["a"]}]},
                                  {"messages": [{"role": "user", "content": None}]}, {"messages": [{"role": "user", "content": "x" * 8001}]},
                                  {"messages": [{"role": "user", "content": "x"}] * 41}, {"messages": "hello"}, {"messages": [None]}, {}, [], "x", 5])
def test_chat_refuses_malformed_bodies_with_a_4xx(client, body):
    assert client.post("/api/v1/assistant/chat", json=body).status_code in (400, 422)


@pytest.mark.parametrize("bad", ['["x", null, 5, ["a"], {"role": 5}, {"role": "user", "content": {"a": 1}}]', '{"a": 1}', '"text"', "5", "null", "not json", "[" * 200])
def test_voice_command_survives_odd_message_json(client, no_network, bad):
    r = client.post("/api/v1/assistant/voice-command", data={"transcript": "what is the status", "messages": bad})
    assert r.status_code != 500 and r.status_code < 600 and r.status_code in (200, 400, 422, 503), (bad, r.status_code, r.text[:200])


def test_voice_command_survives_deeply_nested_json(client, no_network):
    r = client.post("/api/v1/assistant/voice-command", data={"transcript": "what is the status", "messages": "[" * 20000})
    assert r.status_code in (200, 400, 422, 503)


def test_voice_confirm_ids_through_http_are_bounded_and_tell_nothing_about_others(client):
    ids = ",".join(f"i{i:03d}" for i in range(50))
    t = time.time()
    r = client.post("/api/v1/assistant/voice-command", data={"transcript": "yes", "confirm_ids": ids})
    assert r.status_code == 200 and time.time() - t < 5
    too_long = client.post("/api/v1/assistant/voice-command", data={"transcript": "yes", "confirm_ids": ",".join(f"i{i:03d}" for i in range(51))})
    assert too_long.status_code in (400, 422)
    aid = agent._propose(tools.TOOLS["approve_design"], {"job": "JOB-X"}, {"email": "someone-else@x.com", "role": "MANAGER"})["id"]
    mine = client.post("/api/v1/assistant/voice-command", data={"transcript": "yes", "confirm_ids": aid}).json()["tool_actions_executed"][0]
    none = client.post("/api/v1/assistant/voice-command", data={"transcript": "yes", "confirm_ids": "nope"}).json()["tool_actions_executed"][0]
    assert mine["error"] == none["error"]                              # a foreign proposal looks exactly like a missing one


def test_batch_decide_is_capped_and_isolated(client):
    assert client.post("/api/v1/assistant/actions/batch", json={"ids": [f"x{i}" for i in range(51)], "confirm": True}).status_code == 422
    assert client.post("/api/v1/assistant/actions/batch", json={"ids": [], "confirm": True}).status_code == 422
    out = client.post("/api/v1/assistant/actions/batch", json={"ids": ["a", "a", "b"], "confirm": False}).json()
    assert [r["status"] for r in out["results"]] == ["SKIPPED", "SKIPPED"]


def test_edit_with_odd_changes_never_500s(client):
    aid = agent._propose(tools.TOOLS["send_email"], {"to": "a@b.com", "subject": "s", "body": "b"}, ADMIN)["id"]
    for changes in ({}, {"body": None}, {"body": ["x"]}, {"body": {"a": 1}}, {"to": 5}, {"_secret": "x"}, {"unknown": "x"}, {"body": "x" * 200000}, {"body": "\x00‮"}):
        r = client.patch(f"/api/v1/assistant/actions/{aid}", json={"changes": changes})
        assert r.status_code < 500, (str(changes)[:40], r.status_code)


def test_a_waiting_draft_cannot_be_edited_to_a_huge_body(client):
    aid = agent._propose(tools.TOOLS["send_email"], {"to": "a@b.com", "subject": "s", "body": "b"}, ADMIN)["id"]
    r = client.patch(f"/api/v1/assistant/actions/{aid}", json={"changes": {"body": "x" * 3_000_000}})
    assert r.status_code in (400, 413, 422)


def test_lookup_with_wrongly_typed_arguments_is_refused_or_harmless(client, no_network):
    bad = []
    names = [n for n, t in tools.for_user(ADMIN).items() if not t.write]
    for name in names:
        t = tools.TOOLS[name]
        for weird in (None, 5, [1], {"a": 1}, "x" * 100000, "\x00", True):
            args = {k: weird for k in (t.params or {"search": 1})}
            r = client.post(f"/api/v1/assistant/lookup/{name}", json={"args": args})
            body = r.text
            if r.status_code >= 500 or "Traceback" in body or "lookup failed" in body:
                bad.append((name, type(weird).__name__, r.status_code))
    assert not bad, bad[:6]


def test_lookup_of_a_write_tool_or_unknown_tool_is_refused(client):
    for name in ("send_email", "approve_po", "stage_cell_edit", "nope", "../x", "chat_action", "send_vendor_po"):
        assert client.post(f"/api/v1/assistant/lookup/{name}", json={"args": {}}).status_code in (404, 422)
