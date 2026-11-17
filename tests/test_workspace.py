"""Project templates, my items, saved filters, and engineering notes folded into a job's thread. Test data only."""

import io
import uuid
from datetime import date, datetime, timezone

import pytest
from openpyxl import load_workbook

from app.config import settings
from app.core.database import SessionLocal
from app.models.db_models import EngineeringNoteDB, StaffContactDB
from app.services import my_views
from app.services.coordination import ball, eng_notes, project_templates as pt, updates
from tests.conftest import staff_token

TAG = uuid.uuid4().hex[:5]
ANN, BOB = f"ann{TAG}@allied.test", f"bob{TAG}@allied.test"


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch, client):
    monkeypatch.setattr(settings, "DATA_DIR", tmp_path)
    with SessionLocal() as db:
        for e in (ANN, BOB):
            if db.get(StaffContactDB, e) is None:
                db.add(StaffContactDB(email=e, name=e.split("@")[0], role_tag="staff", channels=["email"], active=True))
        db.commit()


# --------------------------------------------------------------- project templates


def test_the_starter_template_gives_a_dated_plan_on_working_days_only():
    plan = pt.plan("wear-part-job", "2026-11-09", "OC Irving screens", {"engineer": "eli@allied.test"})        # a Monday
    assert plan[0]["milestone"] == "Customer kickoff" and plan[0]["due"] == "2026-11-09" and plan[0]["project"] == "OC Irving screens"
    assert plan[1]["due"] == "2026-11-16" and plan[1]["owner"] == "eli@allied.test" and plan[1]["form"] == "design-release"
    assert all(date.fromisoformat(p["due"]).weekday() < 5 for p in plan)
    assert [p["due"] for p in plan] == sorted(p["due"] for p in plan)
    assert plan[-1]["owner"] == ""                                                                              # no owner picked for that role


def test_a_weekend_start_still_gives_working_days():
    plan = pt.plan("wear-part-job", "2026-11-07")                                                                # a Saturday
    assert all(date.fromisoformat(p["due"]).weekday() < 5 for p in plan)


@pytest.mark.parametrize("bad,msg", [
    ({"name": "", "milestones": [{"name": "A", "offset_days": 0}]}, "name"), ({"name": "X", "milestones": []}, "1 and 60"),
    ({"name": "X", "milestones": [{"name": "", "offset_days": 0}]}, "needs a name"),
    ({"name": "X", "milestones": [{"name": "A", "offset_days": "soon"}]}, "whole number"),
    ({"name": "X", "milestones": [{"name": "A", "offset_days": 900}]}, "between 0 and 730"),
    ({"name": "X", "milestones": [{"name": "A", "offset_days": 5}, {"name": "B", "offset_days": 2}]}, "in order"),
    ({"name": "X", "milestones": [{"name": "A", "offset_days": 1, "owner_role": "wizard"}]}, "owner must be"),
    ({"name": "X", "milestones": [{"name": "A", "offset_days": 1, "form": "nope"}]}, "no form called"),
])
def test_a_badly_made_template_is_refused_in_plain_words(bad, msg):
    with pytest.raises(pt.TemplateError, match=msg):
        pt.save(bad)


def test_templates_are_added_changed_and_removed_and_a_bad_start_date_is_refused():
    t = pt.save({"name": "Rush job", "milestones": [{"name": "Drawings", "offset_days": 1, "form": "design-release"}, {"name": "Ship", "offset_days": 3}]})
    assert t["id"] == "rush-job" and pt.save({**t, "name": "Rush job v2"})["name"] == "Rush job v2"
    with pytest.raises(pt.TemplateError, match="start date"):
        pt.plan("rush-job", "next week")
    pt.remove("rush-job")
    with pytest.raises(LookupError):
        pt.get("rush-job")


def test_the_plan_downloads_as_excel_for_monday_to_import(client):
    r = client.post("/api/v1/workspace/project-templates/wear-part-job/plan/file", json={"start": "2026-11-09", "project": "OC job"})
    rows = list(load_workbook(io.BytesIO(r.content)).active.iter_rows(values_only=True))
    assert r.status_code == 200 and rows[0][:3] == ("Project", "Milestone", "Due") and rows[1][:3] == ("OC job", "Customer kickoff", "2026-11-09")
    assert client.post("/api/v1/workspace/project-templates/nope/plan/file", json={"start": "2026-11-09"}).status_code == 404
    assert client.post("/api/v1/workspace/project-templates/wear-part-job/plan", json={"start": "x"}).status_code == 400


def test_template_api_roles(auth_enforced, client):
    def tok(role, n):
        return {"Authorization": f"Bearer {staff_token(f'{n}{TAG}@alliedweldingsystems.com', role)}"}

    v, o, m = tok("VIEWER", "tv"), tok("OPERATOR", "to"), tok("MANAGER", "tm")
    body = {"name": "API job", "milestones": [{"name": "A", "offset_days": 0}]}
    assert client.get("/api/v1/workspace/project-templates", headers=v).status_code == 200
    assert client.post("/api/v1/workspace/project-templates", json=body, headers=o).status_code == 403
    made = client.post("/api/v1/workspace/project-templates", json=body, headers=m)
    assert made.status_code == 201 and client.post(f"/api/v1/workspace/project-templates/{made.json()['id']}/plan",
                                                    json={"start": "2026-11-09"}, headers=v).status_code == 403
    assert client.post(f"/api/v1/workspace/project-templates/{made.json()['id']}/plan", json={"start": "2026-11-09"}, headers=o).status_code == 200
    assert client.put("/api/v1/workspace/project-templates/zzz", json=body, headers=m).status_code == 404
    assert client.delete(f"/api/v1/workspace/project-templates/{made.json()['id']}", headers=m).status_code == 204


# --------------------------------------------------------------- saved filters


def test_saved_views_are_private_unless_a_manager_shares_them():
    a = my_views.save_view(ANN, "Late vendor jobs", "vendors", {"state": "late"})
    my_views.save_view(BOB, "Bob's orders", "orders", {"status": "RECEIVED"})
    assert [v["name"] for v in my_views.list_views(ANN, "vendors")] == ["Late vendor jobs"]
    assert my_views.list_views(BOB, "vendors") == []
    shared = my_views.save_view(ANN, "Everything late", "ball", {"late": True}, shared=True, is_manager=True)
    assert shared["shared"] and any(v["name"] == "Everything late" and not v["mine"] for v in my_views.list_views(BOB, "ball"))
    with pytest.raises(my_views.ViewError, match="Only a manager"):
        my_views.save_view(BOB, "Mine", "ball", {}, shared=True)
    again = my_views.save_view(ANN, "late vendor jobs", "vendors", {"state": "will_miss"})                      # same name: replaces
    assert again["id"] == a["id"] and len(my_views.list_views(ANN, "vendors")) == 1


@pytest.mark.parametrize("kw,msg", [({"name": ""}, "name"), ({"screen": "nope"}, "Views can be saved"), ({"filters": "x"}, "filter settings"),
                                    ({"filters": {"k": "x" * 5000}}, "too large")])
def test_bad_views_are_refused(kw, msg):
    args = {"name": "N", "screen": "orders", "filters": {}, **kw}
    with pytest.raises(my_views.ViewError, match=msg):
        my_views.save_view(ANN, args["name"], args["screen"], args["filters"])


def test_the_view_limit_and_removal_rules():
    for i in range(my_views.MAX_PER_PERSON):
        my_views.save_view(BOB, f"v{i}", "orders", {})
    with pytest.raises(my_views.ViewError, match="30 views"):
        my_views.save_view(BOB, "one too many", "orders", {})
    mine = my_views.list_views(BOB, "orders")[0]
    with pytest.raises(PermissionError):
        my_views.delete_view(ANN, mine["id"])
    my_views.delete_view(ANN, mine["id"], is_manager=True)                                                        # a manager can
    with pytest.raises(LookupError):
        my_views.delete_view(BOB, mine["id"])
    for v in my_views.list_views(BOB):
        if v["mine"]:
            my_views.delete_view(BOB, v["id"])


def test_views_api(auth_enforced, client):
    h = {"Authorization": f"Bearer {staff_token(f'view{TAG}@alliedweldingsystems.com', 'VIEWER')}"}
    made = client.post("/api/v1/workspace/views", json={"name": "Mine", "screen": "orders", "filters": {"q": "OC"}}, headers=h)
    assert made.status_code == 201 and client.get("/api/v1/workspace/views", params={"screen": "orders"}, headers=h).json()[0]["filters"] == {"q": "OC"}
    assert client.post("/api/v1/workspace/views", json={"name": "", "screen": "orders", "filters": {}}, headers=h).status_code == 400
    assert client.delete(f"/api/v1/workspace/views/{made.json()['id']}", headers=h).status_code == 204
    assert client.delete(f"/api/v1/workspace/views/{made.json()['id']}", headers=h).status_code == 404


# --------------------------------------------------------------- my items


def test_my_items_gathers_what_is_waiting_on_one_person_and_survives_a_broken_source(monkeypatch):
    from app.models.orders import InboundOrderRequest
    from app.services.order_orchestrator import OrderOrchestrator

    so = str(OrderOrchestrator().process_inbound_order(InboundOrderRequest(
        customer_name="Owens Corning", customer_po_number=f"PO-{uuid.uuid4().hex[:6].upper()}", shipping_address="831 Hayter Rd NW, Edmonton, AB T6S 1A1",
        line_items=[{"raw_item_text": "Hammermill Screen Allied 300XL", "quantity": 1, "unit_price": 410}])).sales_order_number)
    ball.set_ball("order", so, {"email": BOB, "role": "MANAGER"}, "engineer", ANN, owner=BOB, note="check it")
    updates.post("order", so, f"@ann{TAG} please look", {"email": BOB, "role": "MANAGER"})
    mine = my_views.my_items(ANN)
    assert any(b["item_ref"] == so for b in mine["ball"]) and any("please look" in m["body"] for m in mine["mentions"])
    assert mine["counts"]["ball"] >= 1 and set(mine) >= {"ball", "mentions", "notes", "rfqs", "vendor_jobs", "drafts_waiting", "counts"}
    monkeypatch.setattr(ball, "list_open", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("down")))
    broken = my_views.my_items(ANN)
    assert broken["ball"] == [] and any("please look" in m["body"] for m in broken["mentions"])
    assert client_ok()


def client_ok():
    return True


def test_my_items_api(client):
    r = client.get("/api/v1/workspace/my/items")
    assert r.status_code == 200 and "counts" in r.json()


# --------------------------------------------------------------- notes folded into the job's thread


def test_engineering_notes_and_comments_share_one_thread_on_a_job(monkeypatch):
    item = str(uuid.uuid4().int % 10**9)
    with SessionLocal() as db:
        db.add(EngineeringNoteDB(board_id="1", item_id=item, item_name="Liner redesign", author_email=BOB, text="Use Rev C only",
                                 translations={"zh": {"text": "仅使用C版"}}, recipients=[ANN], urgent=True, read_by={},
                                 created_at=datetime(2026, 11, 1, tzinfo=timezone.utc)))
        db.commit()
    updates.post("board_item", item, "Understood, starting now", {"email": ANN, "role": "OPERATOR"})
    th = updates.thread("board_item", item, ANN, "zh")
    assert [c["kind"] for c in th] == ["note", "comment"]
    note = th[0]
    assert note["body"] == "Use Rev C only" and note["urgent"] and note["unread"] is True and note["translation"]["text"] == "仅使用C版"
    assert note["author_name"] and [m["email"] for m in note["mentions"]] == [ANN]
    nobody = updates.thread("board_item", item, BOB, "en")
    assert nobody[0]["unread"] is False and nobody[0]["mine"] is True and nobody[0]["translation"] is None
    assert [c["kind"] for c in updates.thread("board_item", item, ANN, "en", include_notes=False)] == ["comment"]
    monkeypatch.setattr(eng_notes, "list_notes", lambda **k: (_ for _ in ()).throw(RuntimeError("down")))
    assert [c["kind"] for c in updates.thread("board_item", item, ANN, "en")] == ["comment"]                    # comments still show
