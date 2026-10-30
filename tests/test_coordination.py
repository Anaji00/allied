"""Projects and milestones from monday, design-doc submissions, schedule alerts,
shipments and the notification system."""

import json
import uuid
from datetime import date, datetime, timedelta, timezone

import pytest

from app.config import settings
from app.core.database import SessionLocal
from app.models.db_models import MondayMilestoneDB, NotificationDB, ShipmentDB
from app.services.coordination import notify as alerts
from app.services.coordination import projects, schedule, shipments

TODAY = alerts.today()


def _d(days: int) -> str:
    return (TODAY + timedelta(days=days)).isoformat()


class FakeMonday:
    """Records board writes; serves a projects board built by the test."""

    live = True

    def __init__(self):
        self.updates, self.columns = [], []

    def post_pulse_update(self, item_id, text, idempotency_key=None):
        self.updates.append((item_id, text))
        return True

    def update_column_values(self, board_id, item_id, values, idempotency_key=None):
        self.columns.append((board_id, item_id, values))
        return True


def _people(*ids):
    return json.dumps({"personsAndTeams": [{"id": i, "kind": "person"} for i in ids]})


def _col(cid, text="", value=None):
    return {"id": cid, "text": text, "value": value}


@pytest.fixture
def board(monkeypatch):
    """A projects board with one project and three milestones in different states."""
    board_id = str(uuid.uuid4().int % 10**9)
    pid = str(uuid.uuid4().int % 10**9)
    ids = [str(uuid.uuid4().int % 10**9) for _ in range(3)]
    users = {"11": "olivia.owner@allied.test", "22": "eli.engineer@allied.test", "33": "rhea.reviewer@allied.test"}
    items = [{
        "id": pid, "name": f"Chute liner redesign {pid}", "group": {"id": "g1", "title": "Active"},
        "column_values": [_col("person", "Olivia", _people(11)), _col("date", _d(10), json.dumps({"date": _d(10)})),
                          _col("status", "Working on it")],
        "subitems": [
            {"id": ids[0], "name": "Design drawings", "board": {"id": "900"},
             "column_values": [_col("person", "Eli", _people(22)), _col("date", _d(-3), json.dumps({"date": _d(-3)})),
                               _col("status", "Working on it")]},
            {"id": ids[1], "name": "Customer kickoff", "board": {"id": "900"},
             "column_values": [_col("person", "Eli", _people(22)),
                               _col("date", f"{_d(-5)} - {_d(1)}", json.dumps({"from": _d(-5), "to": _d(1)})),
                               _col("status", "Working on it")]},
            {"id": ids[2], "name": "Quote approved", "board": {"id": "900"},
             "column_values": [_col("person", "Eli", _people(22)), _col("date", _d(-10), json.dumps({"date": _d(-10)})),
                               _col("status", "Done")]},
        ],
    }]
    fake = FakeMonday()
    monkeypatch.setattr(settings, "MONDAY_PROJECTS_BOARD_ID", board_id)
    monkeypatch.setattr(settings, "MONDAY_PROJECTS_LAYOUT", "subitems")
    monkeypatch.setattr(settings, "LEADERSHIP_EMAILS", "boss@allied.test")
    monkeypatch.setattr("app.services.monday_client.fetch_projects_board", lambda c, b, with_subitems=True: items)
    monkeypatch.setattr("app.services.monday_client.fetch_user_emails", lambda c: users)
    monkeypatch.setattr(projects, "_users_cache", {"at": 0.0, "map": {}})
    monkeypatch.setattr(projects, "_client", lambda: fake)
    result = projects.sync_projects(fake)
    assert result == {"projects": 1, "milestones": 3, "removed": 0}
    return {"pid": pid, "drawings": ids[0], "kickoff": ids[1], "quote": ids[2], "fake": fake}


def _project(pid):
    return next(p for p in projects.overview()["projects"] if p["item_id"] == pid)


def _alerts_for(key_part):
    with SessionLocal() as db:
        return [n for n in db.query(NotificationDB).all() if key_part in n.dedup_key]


# -- reading monday ------------------------------------------------------------


def test_sync_reads_people_dates_timelines_and_done(board):
    p = _project(board["pid"])
    ms = {m["item_id"]: m for m in p["milestones"]}
    assert p["owner_emails"] == ["olivia.owner@allied.test"]
    assert ms[board["drawings"]]["owner_emails"] == ["eli.engineer@allied.test"]
    assert ms[board["kickoff"]]["due_date"] == _d(1)          # a timeline's end date counts
    assert ms[board["quote"]]["state"] == "done"
    assert ms[board["drawings"]]["state"] == "overdue"
    assert ms[board["kickoff"]]["state"] == "due_soon"
    assert ms[board["drawings"]]["needs_submission"] and ms[board["drawings"]]["missing_docs"]
    assert not ms[board["kickoff"]]["needs_submission"]


def test_sync_is_idle_without_a_board_or_in_simulation(monkeypatch):
    monkeypatch.setattr(settings, "MONDAY_PROJECTS_BOARD_ID", "")
    assert "not set" in projects.sync_projects(FakeMonday())["skipped"]
    monkeypatch.setattr(settings, "MONDAY_PROJECTS_BOARD_ID", "1")
    sim = FakeMonday()
    sim.live = False
    assert "simulation" in projects.sync_projects(sim)["skipped"]


# -- design-document submissions ----------------------------------------------


def test_submission_review_cycle_with_four_eyes(board):
    eng = {"email": "eli.engineer@allied.test", "role": "OPERATOR"}
    with pytest.raises(ValueError, match="not accepted"):
        projects.submit(board["drawings"], [("virus.exe", b"x")], "", eng)

    sub = projects.submit(board["drawings"], [("liner_revB.pdf", b"%PDF-1.4 drawing")], "Rev B for review", eng)
    assert sub["version"] == 1 and sub["status"] == "SUBMITTED"
    # monday gets an update and the milestone's status changes
    assert any("Design documents submitted" in t for _, t in board["fake"].updates)
    assert board["fake"].columns[-1][2] == {"status": {"label": settings.MILESTONE_SUBMITTED_LABEL}}
    # the project owner is asked to review
    ask = _alerts_for(f"submission-ready:{sub['id']}")
    assert ask and ask[0].recipients == ["olivia.owner@allied.test"]
    assert _project(board["pid"])["milestones"][0]["state"] in ("waiting_review",) or any(
        m["state"] == "waiting_review" for m in _project(board["pid"])["milestones"])

    with pytest.raises(ValueError, match="already waiting"):
        projects.submit(board["drawings"], [("again.pdf", b"x")], "", eng)
    with pytest.raises(PermissionError):
        projects.review(sub["id"], True, "", eng)            # nobody approves their own work
    owner = {"email": "olivia.owner@allied.test", "role": "MANAGER"}
    with pytest.raises(ValueError, match="what needs to change"):
        projects.review(sub["id"], False, "", owner)

    back = projects.review(sub["id"], False, "Hole pattern is off by 5 mm", owner)
    assert back["status"] == "CHANGES_REQUESTED"
    told = _alerts_for(f"submission-reviewed:{sub['id']}")
    assert told[0].recipients == ["eli.engineer@allied.test"] and told[0].severity == "warning"

    v2 = projects.submit(board["drawings"], [("liner_revC.pdf", b"%PDF-1.4 fixed")], "", eng)
    assert v2["version"] == 2
    projects.review(v2["id"], True, "", owner)
    ms = {m["item_id"]: m for m in _project(board["pid"])["milestones"]}
    assert ms[board["drawings"]]["state"] == "done"
    name, path = projects.submission_file(v2["id"], 0)
    assert name == "liner_revC.pdf" and path.read_bytes() == b"%PDF-1.4 fixed"


# -- schedule alerts -------------------------------------------------------------


def test_behind_schedule_alerts_escalate_and_are_not_repeated(board, monkeypatch):
    monkeypatch.setattr(settings, "ESCALATE_AFTER_DAYS", 2)
    schedule.run_checks()
    # 3 days late >= 2: one escalated alert to engineer, owner and leadership, not two alerts
    assert not _alerts_for(f"ms-overdue:{board['drawings']}")
    escalated = _alerts_for(f"ms-escalate:{board['drawings']}")
    assert escalated and escalated[0].severity == "warning"
    assert set(escalated[0].recipients) == {"eli.engineer@allied.test", "olivia.owner@allied.test", "boss@allied.test"}
    assert "Design documents: not submitted" in escalated[0].body

    monkeypatch.setattr(settings, "ESCALATE_AFTER_DAYS", 5)              # 3 days late < 5: owner only
    with SessionLocal() as db:
        db.query(NotificationDB).filter(NotificationDB.dedup_key.like(f"%{board['drawings']}%")).delete(synchronize_session=False)
        db.commit()
    schedule.run_checks()
    overdue = _alerts_for(f"ms-overdue:{board['drawings']}")
    assert overdue and "boss@allied.test" not in overdue[0].recipients
    assert _alerts_for(f"ms-due-soon:{board['kickoff']}")
    assert not _alerts_for(f"ms-overdue:{board['quote']}")                  # done items are quiet

    before = len(_alerts_for(board["drawings"]))
    schedule.run_checks()
    assert len(_alerts_for(board["drawings"])) == before                    # sent once, not every check


def test_alerts_close_themselves_when_the_milestone_is_done(board):
    schedule.run_checks()
    with SessionLocal() as db:
        db.get(MondayMilestoneDB, board["drawings"]).done = True
        db.commit()
    schedule.run_checks()
    assert _alerts_for(f"ms-escalate:{board['drawings']}")
    for n in _alerts_for(f"ms-escalate:{board['drawings']}"):
        assert n.acknowledged_by == "system: resolved"


def test_project_past_due_is_critical_for_leadership(board, monkeypatch):
    from app.models.db_models import MondayProjectDB

    with SessionLocal() as db:
        db.get(MondayProjectDB, board["pid"]).due_date = _d(-1)
        db.commit()
    schedule.run_checks()
    crit = _alerts_for(f"proj-overdue:{board['pid']}")
    assert crit and crit[0].severity == "critical" and "boss@allied.test" in crit[0].recipients


def test_leadership_summary_lists_what_is_behind(board):
    text = schedule.build_summary()["text"]
    assert "Behind schedule" in text and "Design drawings" in text and "3 days late, no design docs submitted" in text


# -- shipments -----------------------------------------------------------------


def _ship(**over):
    data = dict(forwarder="BlueSea Logistics", mode="SEA", container_no=f"MSKU{uuid.uuid4().int % 10**7:07d}",
                origin="Qingdao", destination="Mobile, AL", etd=_d(-20), eta=_d(5), status="DEPARTED",
                owner_email="ops@allied.test")
    data.update(over)
    return shipments.create(data, {"email": "ops@allied.test"})


def test_shipment_flags():
    late = _ship(eta=_d(-2))
    assert "late" in shipments.flags(late, TODAY)
    slipped = _ship()
    slipped = shipments.update(slipped["id"], {"eta": _d(9)}, "tester")
    assert any(f.startswith("slipped 4") for f in shipments.flags(slipped, TODAY))
    rolled = _ship(status="BOOKED", etd=_d(-1))
    assert "missed departure" in shipments.flags(rolled, TODAY)
    port = _ship(status="DISCHARGED", free_time_ends=_d(1))
    assert "port free time ending" in shipments.flags(port, TODAY)
    with SessionLocal() as db:
        db.get(ShipmentDB, port["id"]).last_update_at = datetime.now(timezone.utc) - timedelta(days=3)
        db.commit()
    stale = next(s for s in shipments.list_shipments() if s["id"] == port["id"])
    assert any(f.startswith("no update for 3") for f in stale["flags"])     # 2-day limit at port


def test_free_time_ending_is_critical_and_clears_on_delivery():
    s = _ship(status="DISCHARGED", free_time_ends=_d(1))
    schedule.run_checks()
    crit = _alerts_for(f"ship-free-time:{s['id']}")
    assert crit and crit[0].severity == "critical"
    shipments.update(s["id"], {"status": "DELIVERED"}, "tester")
    assert all(n.acknowledged_at for n in _alerts_for(f"ship-free-time:{s['id']}"))


def test_forwarder_email_updates_the_matching_shipment(monkeypatch):
    from app.services.ai.providers import AIProvider, use_provider

    s = _ship()

    class Reader(AIProvider):
        name = "fake"

        def structured(self, system, prompt, schema, images=None):
            return schema(status="ARRIVED", references=[], etd="", eta=_d(4), last_free_day=_d(9),
                          summary="Vessel arrived at Mobile.", confidence=0.95)

    use_provider(Reader())
    try:
        out = shipments.ingest_forwarder_email("ops@bluesea.test", "Arrival notice",
                                               f"Container {s['container_no']} has arrived at Mobile.")
        missing = shipments.ingest_forwarder_email("ops@bluesea.test", "Arrival notice", "Container ZZZU0000000 arrived.")
    finally:
        use_provider(None)
    assert out["applied"] and out["shipment"]["status"] == "ARRIVED"
    assert out["shipment"]["free_time_ends"] == _d(9)
    assert not missing["applied"] and missing["reason"] == "no matching shipment"


# -- notification system -------------------------------------------------------


def test_channels_follow_severity_and_quiet_hours(monkeypatch):
    from app.models.db_models import StaffContactDB

    email = f"lead{uuid.uuid4().hex[:6]}@allied.test"
    with SessionLocal() as db:
        db.add(StaffContactDB(email=email, name="Lead", role_tag="leadership", phone="+12055550100",
                              channels=["email", "sms", "push"], daily_summary=True, active=True))
        db.commit()
    sent = {"sms": 0, "push": 0}
    monkeypatch.setattr(alerts, "_sms", lambda *a: sent.__setitem__("sms", sent["sms"] + 1) or "sent")
    monkeypatch.setattr(alerts, "_push", lambda *a: sent.__setitem__("push", sent["push"] + 1) or "sent")
    monkeypatch.setattr(alerts, "_quiet", lambda at=None: False)

    alerts.notify("t", f"t-info:{email}", "info", "b", severity="info", to=[email], wait=True)
    assert sent == {"sms": 0, "push": 0}
    alerts.notify("t", f"t-warn:{email}", "warn", "b", severity="warning", to=[email], wait=True)
    assert sent == {"sms": 0, "push": 1}
    alerts.notify("t", f"t-crit:{email}", "crit", "b", severity="critical", to=[email], wait=True)
    assert sent == {"sms": 1, "push": 2}

    monkeypatch.setattr(alerts, "_quiet", lambda at=None: True)
    nid = alerts.notify("t", f"t-night:{email}", "night", "b", severity="warning", to=[email], wait=True)
    assert alerts.inbox(email, limit=1)[0]["deliveries"]["_push"] == alerts.HELD
    assert nid and alerts.notify("t", f"t-night:{email}", "night", "b", to=[email]) is None   # repeat suppressed

    # Morning: the held push goes out once, and is not sent again.
    monkeypatch.setattr(alerts, "_quiet", lambda at=None: False)
    pushes = sent["push"]
    assert alerts.release_held() >= 1 and sent["push"] == pushes + 1
    assert alerts.inbox(email, limit=1)[0]["deliveries"]["_push"] == "sent"
    alerts.release_held()
    assert sent["push"] == pushes + 1


def test_open_alert_is_not_repeated_after_the_cooldown(monkeypatch):
    key = f"t-open:{uuid.uuid4().hex}"
    assert alerts.notify("t", key, "once", "b", to=["a@allied.test"])
    monkeypatch.setattr(settings, "NOTIFY_COOLDOWN_HOURS", 0)
    assert alerts.notify("t", key, "once", "b", to=["a@allied.test"]) is None       # still open
    alerts.resolve([key])
    assert alerts.notify("t", key, "again", "b", to=["a@allied.test"])               # came back after clearing


def test_only_recipients_or_managers_acknowledge(monkeypatch):
    nid = alerts.notify("t", f"t-ack:{uuid.uuid4().hex}", "x", "b", to=["mine@allied.test"])
    assert alerts.acknowledge(nid, "someone.else@allied.test") == "forbidden"
    assert alerts.acknowledge(nid, "MINE@allied.test") == "ok"
    assert alerts.acknowledge(nid, "mine@allied.test") == "done"
    other = alerts.notify("t", f"t-ack:{uuid.uuid4().hex}", "x", "b", to=["mine@allied.test"])
    assert alerts.acknowledge(other, "boss@allied.test", any_alert=True) == "ok"


def test_inbox_finds_old_alerts_among_many(monkeypatch):
    me = f"me{uuid.uuid4().hex[:6]}@allied.test"
    first = alerts.notify("t", f"t-mine:{me}", "mine", "b", to=[me])
    for i in range(30):
        alerts.notify("t", f"t-noise:{me}:{i}", "noise", "b", to=["other@allied.test"])
    assert [n["id"] for n in alerts.inbox(me, limit=5)] == [first]


# -- review fixes ----------------------------------------------------------------


def test_empty_board_answer_does_not_wipe_the_mirror(board, monkeypatch):
    monkeypatch.setattr("app.services.monday_client.fetch_projects_board", lambda c, b, with_subitems=True: [])
    with pytest.raises(RuntimeError, match="Nothing was removed"):
        projects.sync_projects(board["fake"])
    assert _project(board["pid"])["milestones"]


def test_only_reviewers_review_and_same_names_do_not_overwrite(board):
    eng = {"email": "eli.engineer@allied.test", "role": "OPERATOR"}
    sub = projects.submit(board["drawings"], [("a b.pdf", b"one"), ("a_b.pdf", b"two"), ("CON.pdf", b"three")], "", eng)
    stored = [projects.submission_file(sub["id"], i)[1] for i in range(3)]
    assert len({p.name for p in stored}) == 3 and [p.read_bytes() for p in stored] == [b"one", b"two", b"three"]
    assert stored[2].name.startswith("_CON")
    stranger = {"email": "random.operator@allied.test", "role": "OPERATOR"}
    with pytest.raises(PermissionError, match="reviewers"):
        projects.review(sub["id"], True, "", stranger)
    assert projects.review(sub["id"], True, "", {"email": "olivia.owner@allied.test", "role": "OPERATOR"})["status"] == "APPROVED"


def test_review_still_works_after_the_milestone_left_the_board(board):
    from app.models.db_models import MondayMilestoneDB as MS

    eng = {"email": "eli.engineer@allied.test", "role": "OPERATOR"}
    sub = projects.submit(board["drawings"], [("x.pdf", b"x")], "", eng)
    with SessionLocal() as db:
        db.query(MS).filter(MS.item_id == board["drawings"]).delete()
        db.commit()
    out = projects.review(sub["id"], False, "Wrong flange", {"email": "boss@allied.test", "role": "MANAGER"})
    assert out["status"] == "CHANGES_REQUESTED"
    assert _alerts_for(f"submission-reviewed:{sub['id']}")[0].recipients == ["eli.engineer@allied.test"]


def test_bad_dates_are_refused_and_never_break_the_list():
    s = _ship(eta="2026-02-30")
    assert s["eta_current"] is None
    shipments.update(s["id"], {"eta": "2026-13-01"}, "tester")
    assert any(x["id"] == s["id"] for x in shipments.list_shipments())


def test_short_reference_does_not_steal_another_shipments_email(monkeypatch):
    from app.services.ai.providers import AIProvider, use_provider

    a = _ship(reference="4521")
    b = _ship(container_no="MSCU1234521")

    class Reader(AIProvider):
        name = "fake"

        def structured(self, system, prompt, schema, images=None):
            return schema(status="ARRIVED", references=[], etd="", eta="", last_free_day="",
                          summary="Arrived.", confidence=0.95)

    use_provider(Reader())
    try:
        out = shipments.ingest_forwarder_email("ops@bluesea.test", "Arrival", "Container MSCU1234521 arrived.")
        back = shipments.ingest_forwarder_email("ops@bluesea.test", "Old news", "MSCU1234521 departed Qingdao.")
    finally:
        use_provider(None)
    assert out["shipment"]["id"] == b["id"]
    assert next(x for x in shipments.list_shipments() if x["id"] == a["id"])["status"] == "DEPARTED"
    # an older "departed" email arriving late does not move it backwards
    assert back["applied"] and back["shipment"]["status"] == "ARRIVED"


def test_late_alert_closes_once_the_shipment_arrives():
    s = _ship(eta=_d(-2))
    schedule.run_checks()
    assert _alerts_for(f"ship-late:{s['id']}")
    shipments.update(s["id"], {"status": "ARRIVED"}, "tester")
    schedule.run_checks()
    assert all(n.acknowledged_at for n in _alerts_for(f"ship-late:{s['id']}"))


def test_api_smoke(client, board):
    assert client.get("/api/v1/projects").status_code == 200
    resp = client.post(
        f"/api/v1/projects/milestones/{board['kickoff']}/submissions",
        files=[("files", ("notes.pdf", b"%PDF-1.4", "application/pdf"))], data={"note": "first pass"},
    )
    assert resp.status_code == 200, resp.text
    review = client.post(f"/api/v1/projects/submissions/{resp.json()['id']}/review", json={"approve": True})
    assert review.status_code == 403   # the dev user submitted it, so cannot approve it
    assert client.get("/api/v1/alerts").status_code == 200
    assert client.get("/api/v1/shipments").status_code == 200
    assert "text" in client.get("/api/v1/alerts/summary").json()
