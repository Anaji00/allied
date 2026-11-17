"""Task forms: a design submission can't go in until the engineer has filled the form in properly.
monday is a fake; templates are written to a temporary data folder."""

import json
import uuid
from pathlib import Path

import pytest

from app.config import settings
from app.services.coordination import forms, projects
from tests.conftest import staff_token
from tests.test_coordination import board  # noqa: F401  (the projects-board fixture)

ENG = {"email": "eli.engineer@allied.test", "role": "OPERATOR"}
FILES = [("liner_revB.pdf", b"%PDF-1.4 drawing")]
GOOD = {"drawing_revision": "D-4410 Rev C", "po_checked": True, "mtr_checked": True, "customer_approval": "Approved in writing",
        "welding_procedure": "WPS attached", "special_requirements": "None"}


@pytest.fixture(autouse=True)
def _own_templates(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "DATA_DIR", tmp_path)


def test_an_incomplete_form_blocks_the_submission_and_says_what_to_fix(board):  # noqa: F811
    with pytest.raises(forms.FormError) as e:
        projects.submit(board["drawings"], FILES, "", ENG)
    assert set(e.value.fields) == {"drawing_revision", "po_checked", "mtr_checked", "customer_approval", "welding_procedure", "special_requirements"}
    assert "Tick this once you have done it" in e.value.fields["po_checked"]
    assert not any(True for _ in board["fake"].updates)                    # nothing reached monday
    assert not (settings.DB_DIR / "submissions" / board["drawings"]).exists()      # and nothing was stored


def test_a_complete_form_submits_and_the_reviewer_sees_exactly_what_was_confirmed(board):  # noqa: F811
    sub = projects.submit(board["drawings"], FILES, "Rev C for review", ENG, answers=GOOD)
    assert sub["status"] == "SUBMITTED"
    assert "Drawing number and revision: D-4410 Rev C" in sub["note"] and "Yes, confirmed" in sub["note"] and "Note: Rev C for review" in sub["note"]
    saved = json.loads(next((settings.DB_DIR / "submissions").rglob("answers.json")).read_text(encoding="utf-8"))
    assert saved["form"] == "design-release" and saved["answers"]["drawing_revision"] == "D-4410 Rev C"


def test_lazy_answers_are_refused(board):  # noqa: F811
    for bad, key in (({"drawing_revision": "x"}, "drawing_revision"), ({"special_requirements": "ok"}, "special_requirements"),
                     ({"customer_approval": "Whatever"}, "customer_approval"), ({"po_checked": "no"}, "po_checked"),
                     ({"drawing_revision": "   "}, "drawing_revision")):
        with pytest.raises(forms.FormError) as e:
            projects.submit(board["drawings"], FILES, "", ENG, answers={**GOOD, **bad})
        assert key in e.value.fields, bad


def test_a_milestone_the_form_does_not_cover_is_not_blocked(board):  # noqa: F811
    assert forms.for_milestone("Customer kickoff") is None
    assert projects.submit(board["kickoff"], FILES, "kickoff notes", ENG)["status"] == "SUBMITTED"


def test_switching_a_form_off_lets_submissions_through_again(board):  # noqa: F811
    t = forms.get("design-release")
    forms.save({**t, "enabled": False})
    assert projects.submit(board["drawings"], FILES, "no form", ENG)["status"] == "SUBMITTED"


def test_every_field_type_is_checked():
    t = forms.save({"name": "Odd", "applies_to": ["odd"], "fields": [
        {"label": "Quantity", "type": "number", "required": True}, {"label": "Ship by", "type": "date", "required": True},
        {"label": "Colour", "type": "choice", "options": ["Red", "Blue"], "required": True},
        {"label": "Long", "type": "longtext", "min_chars": 10, "required": False}]})
    with pytest.raises(forms.FormError) as e:
        forms.validate(t, {"quantity": "many", "ship_by": "soon", "colour": "Green", "long": "short"})
    assert set(e.value.fields) == {"quantity", "ship_by", "colour", "long"}
    ok = forms.validate(t, {"quantity": "4", "ship_by": "2026-11-20", "colour": "Red"})
    assert ok["quantity"] == 4.0 and ok["ship_by"] == "2026-11-20" and ok["long"] == ""


@pytest.mark.parametrize("bad,msg", [
    ({"name": "", "fields": [{"label": "A", "type": "text"}]}, "name"), ({"name": "X", "fields": []}, "1 and 40"),
    ({"name": "X", "fields": [{"label": "A", "type": "banana"}]}, "type"),
    ({"name": "X", "fields": [{"label": "A", "type": "choice", "options": ["one"]}]}, "two choices"),
    ({"name": "X", "fields": [{"label": "", "type": "text"}]}, "label"),
])
def test_a_badly_made_template_is_refused_in_plain_words(bad, msg):
    with pytest.raises(forms.TemplateError, match=msg):
        forms.save(bad)


def test_duplicate_labels_get_distinct_keys_and_a_damaged_file_falls_back_to_the_starter():
    t = forms.save({"name": "Dup", "fields": [{"label": "Same", "type": "text"}, {"label": "Same", "type": "text"}]})
    assert [f["key"] for f in t["fields"]] == ["same", "same_2"]
    (Path(settings.DATA_DIR) / "task_templates.json").write_text("not json", encoding="utf-8")
    assert [x["id"] for x in forms.load()] == ["design-release"]


def test_the_api_lets_managers_edit_operators_check_and_viewers_read(auth_enforced, client):
    def tok(role):
        return {"Authorization": f"Bearer {staff_token(f'{role.lower()}{uuid.uuid4().hex[:4]}@alliedweldingsystems.com', role)}"}

    v, o, m = tok("VIEWER"), tok("OPERATOR"), tok("MANAGER")
    assert client.get("/api/v1/forms/templates", headers=v).json()[0]["id"] == "design-release"
    assert client.get("/api/v1/forms/for-milestone", params={"name": "Design drawings"}, headers=v).json()["id"] == "design-release"
    assert client.get("/api/v1/forms/templates/nope", headers=v).status_code == 404
    live = client.post("/api/v1/forms/templates/design-release/check", json={"drawing_revision": "x"}, headers=o).json()
    assert live["ok"] is False and "drawing_revision" in live["fields"]
    assert client.post("/api/v1/forms/templates/design-release/check", json=GOOD, headers=o).json() == {"ok": True, "fields": {}}
    assert client.post("/api/v1/forms/templates/design-release/check", json=GOOD, headers=v).status_code == 403
    body = {"name": "Vendor release", "applies_to": ["vendor"], "fields": [{"label": "PO number", "type": "text", "required": True}]}
    assert client.post("/api/v1/forms/templates", json=body, headers=o).status_code == 403
    made = client.post("/api/v1/forms/templates", json=body, headers=m)
    assert made.status_code == 201 and made.json()["id"] == "vendor-release"
    assert client.put("/api/v1/forms/templates/vendor-release", json={**body, "name": "Vendor PO release"}, headers=m).json()["name"] == "Vendor PO release"
    assert client.put("/api/v1/forms/templates/vendor-release", json={"name": "", "fields": []}, headers=m).status_code == 400
    assert client.put("/api/v1/forms/templates/zzz", json=body, headers=m).status_code == 404
    assert client.delete("/api/v1/forms/templates/vendor-release", headers=m).status_code == 204
    assert client.delete("/api/v1/forms/templates/vendor-release", headers=m).status_code == 404


def test_the_submit_endpoint_returns_a_field_by_field_422(board, client):  # noqa: F811
    r = client.post(f"/api/v1/projects/milestones/{board['drawings']}/submissions",
                    files={"files": ("rev.pdf", b"%PDF-1.4 x", "application/pdf")}, data={"note": "hi"})
    assert r.status_code == 422 and "drawing_revision" in r.json()["detail"]["fields"] and r.json()["detail"]["message"]
    bad = client.post(f"/api/v1/projects/milestones/{board['drawings']}/submissions",
                      files={"files": ("rev.pdf", b"%PDF-1.4 x", "application/pdf")}, data={"answers": "{broken"})
    assert bad.status_code == 400
    ok = client.post(f"/api/v1/projects/milestones/{board['drawings']}/submissions",
                     files={"files": ("rev.pdf", b"%PDF-1.4 x", "application/pdf")}, data={"answers": json.dumps(GOOD)})
    assert ok.status_code == 200 and "D-4410 Rev C" in ok.json()["note"]
