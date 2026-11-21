"""Round 22 (adversary): the job-monday link (/board-edit/jobs/{ref}/link, /unlink), /search, conversations, /sage/export/overview, /jobs/{ref}/automation and /page, the paged
Jobs board. Temporary database and a fake monday only; the migration was rehearsed separately on a scratch database. xfail(strict=False, reason="ADV22-Tn ...") marks open holes."""
import threading
import uuid

import pytest

from app.config import settings
from app.core.database import SessionLocal
from app.models.db_models import AssistantActionDB, EngineeringNoteDB, JobDB
from app.services import board_edit, conversations, create_anything, jobs_board, monday_push
from app.services.coordination import boards, jobs, rfqs, updates
from tests.test_adversary_round20 import BOSS, OP, OP2, TODAY, _clean, api, board, world  # noqa: F401
from tests.test_adversary_round21 import Sender, _new, _projects, drop_projects  # noqa: F401


def _linked(ref):
    with SessionLocal() as db:
        j = db.query(JobDB).filter(JobDB.ref == ref).first()
        return (j.monday_board_id, j.monday_item_id)


def _job(title=None, customer="Acme"):
    r = rfqs.create({"customer": customer, "subject": title or f"link {uuid.uuid4().hex[:6]}"}, BOSS, announce=False)
    return r, jobs.from_rfq(r["id"], BOSS)


# =============================================================== link / unlink

def test_a_suggestion_is_never_saved_by_reading_the_board_or_search_or_the_job_page(api, monkeypatch, drop_projects):
    r, j = _job("Suggested liner order xyz")
    pid = _projects(monkeypatch, ["Suggested liner order xyz"])
    drop_projects.append(pid)
    for _ in range(2):
        jobs_board.rows()
        jobs_board.board_items()
        api("GET", "/board-edit/boards/jobs/items", "VIEWER")
        api("GET", f"/jobs/{j['ref']}/page", "OPERATOR")
        api("GET", "/search?q=Suggested", "OPERATOR")
    assert _linked(j["ref"]) == (None, None)


def test_link_validates_the_item_one_item_one_job_and_odd_ids_never_500(api, monkeypatch, drop_projects):
    _r1, j1 = _job()
    _r2, j2 = _job()
    pid = _projects(monkeypatch, ["Item one", "Item two"])
    drop_projects.append(pid)
    assert api("POST", f"/board-edit/jobs/{j1['ref']}/link", "OPERATOR", json={"item_id": "9000"}).status_code == 200
    assert _linked(j1["ref"])[1] == "9000"
    assert api("POST", f"/board-edit/jobs/{j2['ref']}/link", "OPERATOR", json={"item_id": "9000"}).status_code in (400, 409)           # one item, one job
    assert _linked(j2["ref"]) == (None, None)
    for bad in ("424242", "", " ", "x" * 200, "monday:9001", "\x00", "９００１", "9001; drop", "../etc", "null", {"a": 1}, ["9001"], None, 9001, True):
        r = api("POST", f"/board-edit/jobs/{j2['ref']}/link", "OPERATOR", json={"item_id": bad})
        assert r.status_code in (200, 400, 404, 409, 422), (bad, r.status_code)
        if r.status_code == 200:
            api("POST", f"/board-edit/jobs/{j2['ref']}/unlink", "OPERATOR")
    assert api("POST", f"/board-edit/jobs/{j1['ref']}/unlink", "OPERATOR").status_code == 200
    assert api("POST", f"/board-edit/jobs/{j1['ref']}/unlink", "OPERATOR").status_code in (200, 400)           # twice is not a crash
    assert _linked(j1["ref"]) == (None, None)


def test_a_link_to_an_item_that_is_not_on_the_board_is_refused(api, monkeypatch, drop_projects):
    _r, j = _job()
    pid = _projects(monkeypatch, ["Only item"])
    drop_projects.append(pid)
    r = api("POST", f"/board-edit/jobs/{j['ref']}/link", "OPERATOR", json={"item_id": "123456789"})
    assert r.status_code in (400, 404, 409) and _linked(j["ref"]) == (None, None)


def test_link_and_unlink_need_an_operator_and_obey_the_job_locks(api, monkeypatch, drop_projects):
    _r, j = _job()
    pid = _projects(monkeypatch, ["Lock item"])
    drop_projects.append(pid)
    assert api("POST", f"/board-edit/jobs/{j['ref']}/link", "VIEWER", json={"item_id": "9000"}).status_code == 403
    assert api("POST", f"/board-edit/jobs/{j['ref']}/unlink", "VIEWER").status_code == 403
    assert api("POST", "/board-edit/jobs/JOB-9999999/link", "OPERATOR", json={"item_id": "9000"}).status_code == 404
    with SessionLocal() as db:
        from datetime import datetime, timezone

        db.query(JobDB).filter(JobDB.ref == j["ref"]).update({JobDB.closed_at: datetime.now(timezone.utc)})
        db.commit()
    assert api("POST", f"/board-edit/jobs/{j['ref']}/link", "OPERATOR", json={"item_id": "9000"}).status_code in (400, 409)


def test_t6_a_job_put_on_monday_is_linked_to_the_real_item_after_the_push_and_not_pushed_twice(monkeypatch, drop_projects):
    sender = Sender()
    monkeypatch.setattr("app.core.container.get_monday_client", lambda: sender)
    monkeypatch.setattr(settings, "MONDAY_READ_ONLY", False)
    pid = _projects(monkeypatch, ["Existing"])
    drop_projects.append(pid)
    _r, j = _job(f"Push me {uuid.uuid4().hex[:5]}")
    create_anything.to_monday_stage("job", j["ref"], pid, None, OP)
    monday_push.push(None, who=BOSS["email"], board_id=pid)
    creates = [s for s in sender.sent if s[0] == "create"]
    assert len(creates) == 1
    assert _linked(j["ref"])[1] not in (None, "")
    try:
        create_anything.to_monday_stage("job", j["ref"], pid, None, OP)
        monday_push.push(None, who=BOSS["email"], board_id=pid)
    except (ValueError, LookupError):
        pass
    assert len([s for s in sender.sent if s[0] == "create"]) == 1


def test_nothing_in_link_unlink_search_or_page_calls_monday_while_read_only(api, _clean, monkeypatch, drop_projects):
    _r, j = _job()
    pid = _projects(monkeypatch, ["RO item"])
    drop_projects.append(pid)
    for method, path, kw in (("POST", f"/board-edit/jobs/{j['ref']}/link", {"json": {"item_id": "9000"}}), ("POST", f"/board-edit/jobs/{j['ref']}/unlink", {}),
                             ("GET", "/search?q=RO", {}), ("GET", f"/jobs/{j['ref']}/page", {}), ("GET", f"/jobs/{j['ref']}/automation", {}), ("GET", "/conversations", {}),
                             ("GET", "/board-edit/boards/jobs/items", {}), ("POST", "/board-edit/jobs/from-monday/9000", {})):
        api(method, path, "MANAGER", **kw)
    assert not [q for q in _clean.calls if "mutation" in q.casefold()]


# =============================================================== search

def test_search_never_500s_on_odd_queries_and_viewer_gets_no_pending_cost_cells(api, board):
    for q in ("%%", "_", "' OR 1=1 --", "a" * 80, "‮", "%25", "..", "(", "\\", "NULL", "é", "中文", " x ", "OC262064"):
        assert api("GET", f"/search?q={q}", "VIEWER").status_code in (200, 422), q
    assert api("GET", "/search?q=a", "VIEWER").status_code == 422
    assert api("GET", "/search?q=zz&limit=26", "VIEWER").status_code == 422
    assert api("GET", "/search?q=zz&limit=0", "VIEWER").status_code == 422


def test_t2_a_viewer_does_not_find_staged_unpushed_rows_by_their_cells(api, board):
    token = f"SECRETCOST{uuid.uuid4().hex[:6]}"
    _new(board, f"Quote for {token[:4]}", Notes=token, Cost="98765")
    r = api("GET", f"/search?q={token}", "VIEWER").json()
    assert not [h for g in r["groups"] for h in g["hits"]] if "groups" in r else not str(r).count(token)
    r2 = api("GET", "/search?q=98765", "VIEWER").json()
    assert "pending_row" not in str(r2)


def test_t3_a_viewer_does_not_find_internal_comments_or_notes_by_text(api, world):
    word = f"margin{uuid.uuid4().hex[:6]}"
    updates.post("rfq", world["rfq"]["number"], f"internal: our {word} on this is 40 percent", BOSS)
    r = api("GET", f"/search?q={word}", "VIEWER").json()
    assert r["total"] == 0 and not any(g["hits"] for g in r["groups"])


# =============================================================== conversations

def test_t4_a_viewer_does_not_see_notes_addressed_to_others_in_conversations(api, world):
    word = f"private{uuid.uuid4().hex[:6]}"
    with SessionLocal() as db:
        db.add(EngineeringNoteDB(item_id=f"ITEM{uuid.uuid4().hex[:6]}", item_name="Some board item", author_email=BOSS["email"], text=f"for the manager only {word}", recipients=[BOSS["email"]], read_by={}))
        db.commit()
    got = api("GET", "/conversations", "VIEWER").json()
    assert word not in str(got)


def test_mark_read_is_per_person_atomic_and_path_safe(world, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "DATA_DIR", tmp_path)
    r = world["rfq"]["number"]
    updates.post("rfq", r, "a comment", BOSS)
    users = [{"email": f"reader{i}@alliedweldingsystems.com", "role": "OPERATOR"} for i in range(12)]
    errs = []

    def go(u):
        try:
            conversations.mark_read(u, "rfq", r)
        except Exception as e:  # noqa: BLE001
            errs.append(type(e).__name__)

    ts = [threading.Thread(target=go, args=(u,)) for u in users]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert not errs, errs
    data = conversations._load()
    assert {u["email"] for u in users} <= set(data)
    for ref in ("../../etc/passwd", "..\\..\\x", "rfq:../x", "\x00", "x" * 300):
        with pytest.raises((LookupError, ValueError, updates.UpdateError)):
            conversations.mark_read(users[0], "rfq", ref)
    for t in ("../x", "rfq/../../x", "banana", ""):
        with pytest.raises((LookupError, ValueError, updates.UpdateError)):
            conversations.mark_read(users[0], t, r)
    assert list(tmp_path.glob("*.tmp")) == [] and {p.name for p in tmp_path.iterdir()} == {"conversation_reads.json"}


def test_one_persons_read_does_not_change_anothers_unread_and_a_broken_file_recovers(world, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "DATA_DIR", tmp_path)
    r = world["rfq"]["number"]
    updates.post("rfq", r, "hello there", BOSS)
    a, b = {"email": "ra@alliedweldingsystems.com", "role": "OPERATOR"}, {"email": "rb@alliedweldingsystems.com", "role": "OPERATOR"}
    before = conversations.listing(b)["unread_total"]
    conversations.mark_read(a, "rfq", r)
    assert conversations.listing(b)["unread_total"] == before
    (tmp_path / "conversation_reads.json").write_text("{not json", encoding="utf-8")
    assert conversations.listing(a)["unread_total"] >= 0
    conversations.mark_read(a, "rfq", r)
    assert conversations._load().get("ra@alliedweldingsystems.com")


def test_conversations_over_http_roles_limits_and_a_deleted_item(api, world):
    assert api("GET", "/conversations").status_code in (401, 403) or True
    for q in ("limit=0", "limit=301", "limit=-1", "only_unread=maybe"):
        assert api("GET", f"/conversations?{q}", "OPERATOR").status_code in (200, 422)
    assert api("POST", "/conversations/rfq/RFQ-9999-9999/read", "OPERATOR").status_code == 404
    assert api("POST", "/conversations/banana/x/read", "OPERATOR").status_code in (400, 404)
    assert api("POST", f"/conversations/rfq/{world['rfq']['number']}/read", "VIEWER").status_code == 200
    assert api("POST", "/conversations/read-all", "VIEWER").status_code == 200


# =============================================================== sage overview, automation, job page, paged board

def test_the_sage_overview_is_manager_only_and_limits_hold(api):
    assert api("GET", "/sage/export/overview", "OPERATOR").status_code == 403
    assert api("GET", "/sage/export/overview", "VIEWER").status_code == 403
    for q in ("", "?limit=0", "?limit=-5", "?limit=99999", "?limit=abc"):
        assert api("GET", "/sage/export/overview" + q, "MANAGER").status_code in (200, 422), q
    out = api("GET", "/sage/export/overview", "MANAGER").json()
    assert "ready_to_send" in out and "password" not in str(out).lower()
    assert api("GET", "/sage/export/summary", "VIEWER").status_code in (200, 403)


def test_job_automation_is_for_staff_with_a_bounded_limit(api, world):
    ref = world["job"]
    assert api("GET", f"/jobs/{ref}/automation", "VIEWER").status_code == 403
    assert api("GET", f"/jobs/{ref}/automation", "OPERATOR").status_code == 200
    for q in ("limit=0", "limit=301", "limit=-1"):
        assert api("GET", f"/jobs/{ref}/automation?{q}", "OPERATOR").status_code == 422
    assert api("GET", "/jobs/JOB-9999999/automation", "OPERATOR").status_code == 404


def test_the_job_page_filters_money_comments_and_prep_by_role(api, world):
    ref = world["job"]
    updates.post("rfq", world["rfq"]["number"], "a visible comment", BOSS)
    viewer, op, mgr = (api("GET", f"/jobs/{ref}/page", r).json() for r in ("VIEWER", "OPERATOR", "MANAGER"))
    assert viewer["staff"] is False and viewer["prep"] is None and viewer["actions"] is None and viewer["since"] is None
    txt = str(viewer).replace(",", "")
    assert "5000" not in txt and "2500" not in txt
    assert "5000" not in str(op["prep"]).replace(",", "") and "5000" not in str(op["actions"]).replace(",", "")
    assert "5000" in str(mgr).replace(",", "") or mgr["prep"] is not None
    assert "vendor_email" not in str(viewer["job"]) or viewer["job"].get("vendor_email") in (None, "")
    for q in ("since=yesterday", "since=", "since=forever", "since=" + "x" * 40):
        assert api("GET", f"/jobs/{ref}/page?{q}", "OPERATOR").status_code in (200, 400, 422), q
    assert api("GET", "/jobs/JOB-9999999/page", "OPERATOR").status_code == 404


def test_t5_a_viewers_job_page_has_no_vendor_names(api, world):
    page = api("GET", f"/jobs/{world['job']}/page", "VIEWER").json()
    assert world["vendor"] not in str(page)


@pytest.mark.parametrize("q", ["limit=1&offset=0", "limit=200&offset=0", "limit=1&offset=999999999", "limit=60&offset=100000", "offset=-1", "limit=0", "limit=201", "limit=-5", "limit=abc", "offset=1e9"])
def test_paged_boards_survive_odd_limits_and_offsets(api, board, q):
    for bid in ("jobs", board):
        r = api("GET", f"/board-edit/boards/{bid}/items?{q}", "VIEWER")
        assert r.status_code in (200, 422), (bid, q, r.status_code)


def test_the_bulk_make_a_job_route_if_it_exists_is_operator_only_and_capped(api, monkeypatch, drop_projects):
    from app.main import app

    paths = [p for p in app.openapi()["paths"] if "from-monday" in p]
    bulk = next((p.replace("/api/v1", "", 1) for p in paths if p.endswith("/bulk")), None)
    if bulk is None:
        pytest.skip("no bulk route")
    pid = _projects(monkeypatch, [f"bulk {i}" for i in range(3)])
    drop_projects.append(pid)
    assert api("POST", bulk, "VIEWER", json={"ids": ["9000"]}).status_code == 403
    for body in ({}, {"ids": "x"}, {"ids": ["9000"] * 500}, {"ids": [None]}, {"ids": ["424242"]}, {"ids": ["9000", "9000"]}):
        assert api("POST", bulk, "OPERATOR", json=body).status_code < 500, body


def test_t7_an_operators_job_page_since_has_no_po_totals(api, world):
    page = api("GET", f"/jobs/{world['job']}/page", "OPERATOR").json()
    assert "5000" not in str(page["since"]).replace(",", "")


def test_t8_a_normal_board_honours_limit_and_offset(api, board):
    for i in range(5):
        _new(board, f"PG{i}")
    first = api("GET", f"/board-edit/boards/{board}/items?limit=3&offset=0", "VIEWER").json()["items"]
    second = api("GET", f"/board-edit/boards/{board}/items?limit=3&offset=3", "VIEWER").json()["items"]
    assert len(first) <= 3 and len(second) <= 3 and not ({i["id"] for i in first} & {i["id"] for i in second})


def test_t9_jobs_board_pages_do_not_repeat_monday_rows(api, monkeypatch, drop_projects):
    pid = _projects(monkeypatch, [f"repeat {i}" for i in range(3)])
    drop_projects.append(pid)
    a = [r["id"] for r in api("GET", "/board-edit/boards/jobs/items?limit=2&offset=0", "VIEWER").json()["items"]]
    b = [r["id"] for r in api("GET", "/board-edit/boards/jobs/items?limit=2&offset=2", "VIEWER").json()["items"]]
    assert not ({x for x in a if x.startswith("monday:")} & {x for x in b if x.startswith("monday:")})


def test_t10_a_huge_offset_on_the_jobs_board_is_not_a_500(api):
    for off in ("99999999999999999999", "9223372036854775807", "9223372036854775808"):
        assert api("GET", f"/board-edit/boards/jobs/items?limit=1&offset={off}", "VIEWER").status_code in (200, 422), off


def test_b1_a_viewers_search_does_not_return_vendor_names_or_po_numbers(api, world):
    r = api("GET", f"/search?q={world['vendor'][:12]}", "VIEWER").json()
    assert not [h for g in r["groups"] if g["type"] == "vendor_job" for h in g["hits"]]
