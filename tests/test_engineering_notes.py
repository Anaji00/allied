"""Engineering notes from management: to the job's engineers, with Mandarin/Vietnamese attached
when asked, by text when urgent, and read receipts. Fake translator and phone; test database."""

import threading
import uuid

import pytest

from app.config import settings
from app.core.database import SessionLocal
from app.models.db_models import MondayMilestoneDB, NotificationDB, StaffContactDB
from app.services.ai import features
from app.services.coordination import eng_notes
from app.services.coordination import notify as alerts

BOSS = {"email": "boss@alliedweldingsystems.com", "role": "MANAGER"}
PAUL = "paul.wang@alliedweldingsystems.com"
QUAN = "quan.tran@alliedweldingsystems.com"


@pytest.fixture
def job(monkeypatch):
    mid = f"m{uuid.uuid4().hex[:8]}"
    with SessionLocal() as db:
        db.add(MondayMilestoneDB(item_id=mid, project_item_id="g1-x", board_id="1657858464", name="Splitter Box",
                                 owner_emails=[PAUL, QUAN], status="Working on it"))
        for email, phone in ((PAUL, "+12055550111"), (QUAN, "+12055550112")):
            if db.get(StaffContactDB, email) is None:
                db.add(StaffContactDB(email=email, name=email.split("@")[0], role_tag="engineer", phone=phone,
                                      channels=["email", "push", "sms"], daily_summary=False, active=True))
        db.commit()
    alerts.forget_contacts()
    sent = {"sms": [], "push": []}
    monkeypatch.setattr(alerts, "_sms", lambda phone, n, url: sent["sms"].append((phone, n["title"])) or "sent")
    monkeypatch.setattr(alerts, "_push", lambda n, url: sent["push"].append(n["title"]) or "sent")
    monkeypatch.setattr(alerts, "_quiet", lambda at=None: False)

    def fake_translate(text, target, source="en", actor="system"):
        word = {"zh": "注意：", "vi": "Lưu ý: "}[target]
        return features.Translation(translation=f"{word}{text}", unsure_terms=["[[chamfer]]"] if target == "vi" else []), 1

    monkeypatch.setattr(features, "translate", fake_translate)
    return {"id": mid, "sent": sent}


def _alert(note_id):
    with SessionLocal() as db:
        return db.query(NotificationDB).filter(NotificationDB.dedup_key == f"eng-note:{note_id}").one()


def test_a_manager_sends_a_note_with_both_translations_to_the_jobs_engineers(job, client):
    r = client.post("/api/v1/engineering-notes", json={
        "item_id": job["id"], "text": "Use 3/8 in chamfer on all liner edges, drawing rev C.", "languages": ["zh", "vi"]})
    assert r.status_code == 201
    note = r.json()
    assert note["recipients"] == [PAUL, QUAN] and note["item_name"] == "Splitter Box"
    assert note["translations"]["zh"]["text"].startswith("注意") and note["translations"]["vi"]["unsure"] == ["[[chamfer]]"]
    alert = _alert(note["id"])
    assert "中文" in alert.body and "Tiếng Việt" in alert.body and "rev C" in alert.body
    assert alert.severity == "warning" and job["sent"]["sms"] == []          # not urgent: no text message


def test_an_urgent_note_also_goes_by_text_at_any_hour(job, monkeypatch):
    monkeypatch.setattr(alerts, "_quiet", lambda at=None: True)              # 10 pm
    note = eng_notes.post(job["id"], "STOP welding the hopper: wrong plate grade delivered.", BOSS, urgent=True)
    alerts.notify  # delivery runs on the alert pool
    import time

    for _ in range(50):
        if len(job["sent"]["sms"]) >= 2:
            break
        time.sleep(0.1)
    assert {p for p, _ in job["sent"]["sms"]} == {"+12055550111", "+12055550112"}
    assert all("URGENT" in t for _, t in job["sent"]["sms"]) and _alert(note["id"]).severity == "critical"


def test_the_english_note_still_goes_when_a_translation_fails(job, monkeypatch):
    def down(*a, **k):
        raise RuntimeError("Groq rate limited")

    monkeypatch.setattr(features, "translate", down)
    note = eng_notes.post(job["id"], "Check weld size on the bracket.", BOSS, languages=["zh"])
    assert note["translations"]["zh"]["text"] is None and "unavailable" in note["translations"]["zh"]["error"]
    assert "Check weld size" in _alert(note["id"]).body


def test_read_receipts_and_the_alert_closes_when_everyone_has_read_it(job):
    note = eng_notes.post(job["id"], "Rev D replaces rev C.", BOSS)
    after_paul = eng_notes.mark_read(note["id"], {"email": PAUL})
    assert after_paul["unread"] == [QUAN] and _alert(note["id"]).acknowledged_at is None
    eng_notes.mark_read(note["id"], {"email": PAUL})                       # again: no change
    eng_notes.mark_read(note["id"], {"email": QUAN})
    assert _alert(note["id"]).acknowledged_at is not None
    with pytest.raises(PermissionError):
        eng_notes.mark_read(note["id"], {"email": "shane@alliedweldingsystems.com"})


def test_both_engineers_reading_at_the_same_moment_are_both_recorded(job):
    note = eng_notes.post(job["id"], "Galvanize after welding.", BOSS)
    barrier = threading.Barrier(2)

    def read(who):
        barrier.wait()
        eng_notes.mark_read(note["id"], {"email": who})

    threads = [threading.Thread(target=read, args=(w,)) for w in (PAUL, QUAN)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert set(eng_notes.list_notes(item_id=job["id"])[0]["read_by"]) == {PAUL, QUAN}


def test_only_managers_send_notes_and_a_job_with_nobody_needs_a_name(job, client, auth_enforced):
    from tests.conftest import staff_token

    viewer = staff_token(f"v{uuid.uuid4().hex[:4]}@alliedweldingsystems.com", "VIEWER")
    assert client.post("/api/v1/engineering-notes", json={"item_id": job["id"], "text": "x"},
                       headers={"Authorization": f"Bearer {viewer}"}).status_code == 403
    with SessionLocal() as db:
        db.get(MondayMilestoneDB, job["id"]).owner_emails = []
        db.commit()
    with pytest.raises(ValueError, match="choose who"):
        eng_notes.post(job["id"], "Who builds this?", BOSS)
    picked = eng_notes.post(job["id"], "Athish, please take this one.", BOSS, to=["athish@alliedweldingsystems.com"])
    assert picked["recipients"] == ["athish@alliedweldingsystems.com"]
    with pytest.raises(ValueError):
        eng_notes.post(job["id"], "x", BOSS, languages=["fr"])


def test_engineers_see_their_notes_and_the_board_lists_them(job):
    eng_notes.post(job["id"], "Note one.", BOSS)
    eng_notes.post(job["id"], "Note two.", BOSS, to=[QUAN])
    assert [n["text"] for n in eng_notes.list_notes(item_id=job["id"])] == ["Note two.", "Note one."]
    assert [n["text"] for n in eng_notes.list_notes(item_id=job["id"], for_email=PAUL)] == ["Note one."]
    assert len(eng_notes.list_notes(board_id="1657858464")) >= 2


# ---------------------------------------------------------------------------
# Voice notes
# ---------------------------------------------------------------------------

WEBM = b"\x1aE\xdf\xa3" + b"\x00" * 2048        # a recording as a browser's recorder hands it over


@pytest.fixture
def heard(monkeypatch):
    from app.services.ai import speech

    calls = []

    def fake(audio, suffix=".wav", language=None):
        calls.append(suffix)
        return {"text": "Use the rev D drawing for the splitter box, not rev C.", "language": "en", "duration_seconds": 6.4}

    monkeypatch.setattr(speech, "transcribe", fake)
    return calls


def test_a_spoken_note_is_written_out_translated_and_kept_to_play(job, heard, client):
    r = client.post("/api/v1/engineering-notes/voice", data={"item_id": job["id"], "languages": "zh,vi"},
                    files={"file": ("note.webm", WEBM, "audio/webm")})
    assert r.status_code == 201, r.text
    note = r.json()
    assert note["text"].startswith("Use the rev D drawing") and note["voice"]["seconds"] == 6.4
    assert note["translations"]["zh"]["text"].startswith("注意") and heard == [".webm"]
    assert "voice note is attached" in _alert(note["id"]).body
    played = client.get(note["voice"]["url"])
    assert played.status_code == 200 and played.content == WEBM and played.headers["content-type"].startswith("audio/webm")
    assert eng_notes.list_notes(item_id=job["id"])[0]["voice"]["transcript"].startswith("Use the rev D")


def test_an_iphone_recording_and_typed_words_together(job, heard, client):
    r = client.post("/api/v1/engineering-notes/voice", files={"file": ("Voice Memo.m4a", b"\x00\x00\x00 ftypM4A " + b"\x00" * 900, "audio/mp4")},
                    data={"item_id": job["id"], "text": "See my voice note about the drawing.", "urgent": "true"})
    note = r.json()
    assert note["text"] == "See my voice note about the drawing." and note["voice"]["mime"] == "audio/mp4"
    assert note["urgent"] and note["voice"]["transcript"]                        # both kept


def test_a_recording_that_cannot_be_written_out_still_goes_to_be_played(job, client, monkeypatch):
    from app.services.ai import speech

    monkeypatch.setattr(speech, "transcribe", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("Groq down")))
    note = client.post("/api/v1/engineering-notes/voice", data={"item_id": job["id"]},
                       files={"file": ("note.ogg", b"OggS" + b"\x00" * 500, "audio/ogg")}).json()
    assert note["voice"]["transcript"] is None and "listen" in note["text"].lower()
    assert "couldn't be written out" in _alert(note["id"]).body


def test_only_voice_files_of_a_sensible_size_are_taken(job, heard, client, monkeypatch):
    for name in ("evil.exe", "notes.pdf", "../../etc/passwd", "noext"):
        assert client.post("/api/v1/engineering-notes/voice", data={"item_id": job["id"]},
                           files={"file": (name, b"x" * 100, "application/octet-stream")}).status_code == 400, name
    assert client.post("/api/v1/engineering-notes/voice", data={"item_id": job["id"]},
                       files={"file": ("empty.webm", b"", "audio/webm")}).status_code in (400, 422)
    monkeypatch.setattr(eng_notes, "MAX_AUDIO_BYTES", 1000)
    assert client.post("/api/v1/engineering-notes/voice", data={"item_id": job["id"]},
                       files={"file": ("long.webm", b"x" * 5000, "audio/webm")}).status_code == 413


def test_everyone_signed_in_can_listen_and_nobody_else(job, heard, client, auth_enforced):
    from tests.conftest import staff_token

    note = eng_notes.post_voice(job["id"], WEBM, "n.webm", BOSS)
    viewer = staff_token(f"v{uuid.uuid4().hex[:4]}@alliedweldingsystems.com", "VIEWER")
    assert client.get(note["voice"]["url"], headers={"Authorization": f"Bearer {viewer}"}).status_code == 200
    assert client.get(note["voice"]["url"]).status_code == 401
    assert client.get("/api/v1/engineering-notes/999999/audio", headers={"Authorization": f"Bearer {viewer}"}).status_code == 404


def test_the_alert_opens_the_board_the_job_is_on(job):
    note = eng_notes.post(job["id"], "Check the flange.", BOSS)
    assert _alert(note["id"]).link == f"/?board=1657858464&item={job['id']}"
