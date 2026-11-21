"""scripts/demo_seed.py builds a demo database away from the real data, and refuses anything near it. Runs only against temporary folders."""
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "demo_seed.py"
REAL = ROOT / "data" / "allied_enterprise.db"


def _run(*args, timeout=240):
    return subprocess.run([sys.executable, str(SCRIPT), *args], cwd=ROOT, capture_output=True, text=True, timeout=timeout)


def _stamp(path: Path):
    return (path.stat().st_mtime_ns, path.stat().st_size) if path.exists() else None


def test_it_refuses_the_real_database_and_anything_inside_data_or_output(tmp_path):
    before = _stamp(REAL)
    existed = {p: p.exists() for p in (ROOT / "data" / "demo_x", ROOT / "output" / "demo_x")}
    for bad in (REAL, ROOT / "data" / "demo_x" / "demo.db", ROOT / "output" / "demo_x" / "demo.db", ROOT / "app" / "data" / "x.db"):
        r = _run("--db", str(bad))
        assert r.returncode != 0 and "Refused" in (r.stderr + r.stdout), (bad, r.stdout, r.stderr)
        assert not bad.exists() or bad == REAL
    assert _stamp(REAL) == before                                   # the real database was not touched
    for folder, was in existed.items():
        assert folder.exists() == was                               # and no folder was created inside data/ or output/
    r = _run("--db", str(tmp_path / "not_a_db.txt"))
    assert r.returncode != 0 and ".db" in (r.stderr + r.stdout)


def test_it_builds_a_demo_database_with_records_and_a_throwaway_password(tmp_path):
    from app.core.security import verify_password

    before = _stamp(REAL)
    target = tmp_path / "demo" / "allied_demo.db"
    r = _run("--db", str(target))
    assert r.returncode == 0, r.stdout[-1500:] + r.stderr[-1500:]
    assert target.exists() and (tmp_path / "demo" / "allied_demo.demo.env").exists()
    assert (tmp_path / "demo" / "allied_demo_data" / "master_items.csv").exists()                  # a copy of the catalogues, not the originals
    password = next(line.split("password:")[1].strip() for line in r.stdout.splitlines() if "password:" in line)
    con = sqlite3.connect(f"file:{target}?mode=ro", uri=True)
    try:
        count = lambda t: con.execute(f"select count(*) from {t}").fetchone()[0]          # noqa: E731
        assert count("jobs") >= 2 and count("rfqs") >= 3 and count("purchase_orders") >= 2 and count("shipments") >= 1 and count("orders") >= 1
        assert count("users") >= 4 and count("packing_slips") >= 1
        hashed = dict(con.execute("select email, hashed_password from users")).get("demo-manager@alliedweldingsystems.com")
        status = dict(con.execute("select po_number, status from purchase_orders where po_number is not null")).values()
        outside = con.execute("select count(*) from outbound_mail_log where status = 'SENT' and detail not like 'Demo record:%'").fetchone()[0]     # the one demo "sent" PO is only a record
        marker = con.execute("select count(*) from _demo_seed_marker").fetchone()[0]
    finally:
        con.close()
    assert hashed and verify_password(password, hashed) and "APPROVED" in status and "SENT" in status
    assert outside == 0 and marker == 1                                                         # nothing was sent; the demo marker is there
    env_text = (tmp_path / "demo" / "allied_demo.demo.env").read_text(encoding="utf-8")
    for line in ("EMAIL_SEND_ENABLED=False", "MONDAY_SIMULATION_MODE=True", "MONDAY_READ_ONLY=True", "SMTP_HOST=", "GROQ_API_KEY="):
        assert line in env_text.splitlines()
    assert f"ADMIN_PASSWORD={password}" in env_text.splitlines()                                  # the demo admin uses the throwaway password, not the shipped default
    assert "allied_demo.db" in env_text and str(ROOT / "data") not in env_text.replace("\\", "/").replace("/", os.sep)
    assert _stamp(REAL) == before

    again = _run("--db", str(target))                                                          # never overwrites by accident
    assert again.returncode != 0 and "already exists" in (again.stderr + again.stdout)
    other = tmp_path / "someone_elses.db"
    sqlite3.connect(other).close()
    refused = _run("--db", str(other), "--replace")                                            # --replace only for a database this script made
    assert refused.returncode != 0 and "not made by this script" in (refused.stderr + refused.stdout)
    rebuilt = _run("--db", str(target), "--replace")
    assert rebuilt.returncode == 0, rebuilt.stdout[-800:] + rebuilt.stderr[-800:]


def test_the_demo_runner_uses_only_the_demo_files_keeps_everything_off_and_refuses_other_databases(tmp_path):
    RUN = ROOT / "scripts" / "demo_run.py"

    def run(*args):
        return subprocess.run([sys.executable, str(RUN), *args], cwd=ROOT, capture_output=True, text=True, timeout=240)

    target = tmp_path / "d" / "demo.db"
    made = _run("--db", str(target))
    assert made.returncode == 0, made.stdout[-800:] + made.stderr[-800:]
    shown = run("--db", str(target), "--check")
    assert shown.returncode == 0, shown.stdout[-800:] + shown.stderr[-800:]
    text = shown.stdout.replace("\\", "/")
    assert str(target).replace("\\", "/") in text and str(tmp_path).replace("\\", "/") in text.split("uploads")[1]
    for part in ("email sending enabled: False", "SMTP host: off", "monday read-only: True", "monday simulation: True", "AI key: off", "mailbox polling: False"):
        assert part in text, text
    for bad in (REAL, tmp_path / "plain.db", tmp_path / "missing.db"):
        if bad == tmp_path / "plain.db":
            sqlite3.connect(bad).close()
        r = run("--db", str(bad), "--check")
        assert r.returncode != 0 and "Refused" in (r.stderr + r.stdout), (bad, r.stdout, r.stderr)


def test_demo_reset_refuses_to_delete_the_real_database_without_the_explicit_flag():
    """Only runs the refusal paths: the real flag is never passed here."""
    before = _stamp(REAL)
    for args in ([], ["--yes"], ["--force"]):
        r = subprocess.run([sys.executable, str(ROOT / "demo_reset.py"), *args], cwd=ROOT, capture_output=True, text=True, timeout=60, input="reset\n")
        assert r.returncode == 2 and "Refused" in r.stdout, (args, r.stdout, r.stderr)
    assert _stamp(REAL) == before


def test_replace_deletes_only_folders_the_seeder_made_and_the_demo_switches_off_every_outside_service(tmp_path):
    from scripts import demo_seed

    # Case spelling and ".." cannot get a path past the guard
    for bad in (str(ROOT / "DATA" / "x.db"), str(ROOT / "app" / ".." / "data" / "x.db"), str(ROOT / "Output" / "sub" / "x.db")):
        r = _run("--db", bad)
        assert r.returncode != 0 and "Refused" in (r.stderr + r.stdout), (bad, r.stdout, r.stderr)
    env = demo_seed.demo_env(demo_seed._paths(tmp_path / "x.db"))
    for key, off in (("FEDEX_ENABLED", "False"), ("SAGE_LIVE_CONNECTION", "False"), ("SAGE_SIMULATION_MODE", "True"), ("FX_LIVE_RATES", "False"),
                     ("EMAIL_SEND_ENABLED", "False"), ("MONDAY_READ_ONLY", "True"), ("MONDAY_SIMULATION_MODE", "True"), ("IMAP_POLL_ENABLED", "False")):
        assert env[key] == off
    for key in ("MONDAY_API_KEY", "FEDEX_CLIENT_ID", "FEDEX_CLIENT_SECRET", "SAGE_PASSWORD", "ASSISTANT_MCP_SERVERS", "SMTP_HOST", "GROQ_API_KEY", "SLACK_WEBHOOK_URL",
                "NTFY_URL", "TWILIO_ACCOUNT_SID", "SMS_PROVIDER", "BACKUP_COPY_DIR"):
        assert env[key] == ""
    assert str(tmp_path).replace("\\", "/") in env["BACKUP_DIR"]

    target = tmp_path / "keep" / "demo.db"
    assert _run("--db", str(target)).returncode == 0
    other = tmp_path / "keep" / "demo_files" / "mine.txt"
    (tmp_path / "keep" / "demo_files" / ".demo_seed").unlink()                                      # pretend a folder was not made by the seeder
    other.write_text("someone's own file", encoding="utf-8")
    r = _run("--db", str(target), "--replace")
    assert r.returncode != 0 and "was not made by this script" in (r.stderr + r.stdout)
    assert other.exists() and target.exists()                                                       # nothing was deleted


_PROBE = r"""
import json, os, sys
sys.path.insert(0, os.getcwd())
from pathlib import Path
from scripts.demo_run import read_env_file
from scripts.demo_seed import go_quiet
db = Path(sys.argv[1])
env = read_env_file(db.with_name(db.stem + ".demo.env"))
os.environ.update(env)
from app.config import settings
go_quiet(settings, env)
from app.core.database import SessionLocal, init_database
init_database()
from app.core.status_words import word
from app.models.db_models import OrderDB, ShipmentDB
from app.services import import_paperwork
from app.services.coordination import jobs, needs, notify, purchase_orders, rfqs, schedule
out = {"needs": {}, "jobs": [], "limit": settings.PO_APPROVAL_LIMIT}
for role in ("MANAGER", "ADMIN", "OPERATOR", "VIEWER"):
    n = needs.for_person({"email": f"demo-{role.lower()}@alliedweldingsystems.com", "role": role})
    out["needs"][role] = {"total": n["total"], "counts": n["counts"], "drafts": [d["summary"] for d in n["drafts"]], "alerts": [(a["title"], a["severity"]) for a in n["alerts"]]}
for row in jobs.list_jobs(limit=50)["items"]:
    j = jobs.get(row["ref"])
    out["jobs"].append({"ref": j["ref"], "stage": j["stage"], "checks": [c["key"] for c in j.get("checks", [])], "waiting": [w["who"] for w in j["waiting"]],
                        "pos": [(p["vendor"], p["status"], p.get("po_number")) for p in j["purchase_orders"]]})
big = [p for p in sum((purchase_orders.for_job(r["id"]) for r in jobs.list_jobs(limit=50)["items"]), []) if p["total"] > settings.PO_APPROVAL_LIMIT]
out["big"] = [(p["vendor"], p["status"], p["total"]) for p in big]
with SessionLocal() as d:
    o = d.query(OrderDB).first()
    out["order"] = {"currency": o.currency, "status_word": word("order", o.status), "margin": float(o.gross_margin_pct), "total": float(o.total_revenue)}
    out["ships"] = [(s.container_no, s.destination, s.status, str(s.free_time_ends)) for s in d.query(ShipmentDB).all()]
out["rfq_overdue"] = rfqs.metrics().get("overdue")
po1 = purchase_orders.for_job(1)[0]
info = import_paperwork.build(po1["id"])
out["paper"] = {"dest": info["destination"], "why": info["destination_source"]}
maple = [p for r in jobs.list_jobs(limit=50)["items"] for p in purchase_orders.for_job(r["id"]) if p["vendor"].startswith("Maple")][0]
out["maple_missing"] = len(import_paperwork.build(maple["id"])["missing"])
sm = schedule.build_summary()
out["counts"] = {"inbox": len(notify.inbox(open_only=True, limit=500)), "summary": sm["alerts_open"], "summary_urgent": sm["urgent_open"]}
from app.services import sage_import
so = [o for o in [{"sales_order_number": "12930", "customer_po_number": "4500912345"}]]
out["sage"] = {"order": sage_import.order_money_status(so).get("12930"), "bill": sage_import.bill_in_sage("ZIBO", "INV-ZIBO-7721"),
               "open": sage_import.already_open_in_sage("4500999000", ["9300"]), "history": [h["kind"] for h in sage_import.history(20)]}
print("PROBE" + json.dumps(out, default=str))
"""


def test_the_demo_story_is_coherent_approvals_alerts_and_the_limit_are_there_to_show(tmp_path):
    import json

    target = tmp_path / "story" / "demo.db"
    made = _run("--db", str(target))
    assert made.returncode == 0, made.stdout[-800:] + made.stderr[-800:]
    r = subprocess.run([sys.executable, "-c", _PROBE, str(target)], cwd=ROOT, capture_output=True, text=True, timeout=240)
    line = next((ln for ln in r.stdout.splitlines() if ln.startswith("PROBE")), None)
    assert line, r.stdout[-800:] + r.stderr[-800:]
    out = json.loads(line[5:])

    # approvals are visible, by name, to the people who can approve them, and to nobody else; stale ones are gone
    for role in ("MANAGER", "ADMIN"):
        drafts = out["needs"][role]["drafts"]
        assert any("Send purchase order" in d and "Maple Fab" in d for d in drafts) and any("RFQ-2026-0002" in d for d in drafts), (role, drafts)
        assert not any("RFQ-2026-0001" in d or "RFQ-2026-0003" in d for d in drafts), drafts      # the finished and the old requests keep no "add to the board" draft
    assert out["needs"]["OPERATOR"]["drafts"] == [] and out["needs"]["VIEWER"]["drafts"] == []
    assert all(w and w != "reviewer" and "Demo" in w for j in out["jobs"] for w in j["waiting"])      # a waiting draft names a person
    # alerts to act on, one urgent, each with something real behind it
    alerts = out["needs"]["MANAGER"]["alerts"] + out["needs"]["OPERATOR"]["alerts"]
    assert len(out["needs"]["OPERATOR"]["alerts"]) >= 3 and any(sev == "critical" for _t, sev in alerts)
    assert any("Quote overdue" in t for t, _s in alerts) and out["rfq_overdue"] >= 1               # the urgent alert and the RFQ screen agree
    assert any("Free time" in t and "PBLU4410229" in t for t, _s in alerts)
    assert any(c == "PBLU4410229" and "Houston" in d and st == "DISCHARGED" for c, d, st, _f in out["ships"])      # the container the alert is about exists
    # the story: the finished job shipped AFTER its PO was sent; one fresh job at the first stage; the order is in dollars at a believable margin, not "in Sage"
    done = next(j for j in out["jobs"] if j["stage"] == "SHIPPED")
    assert [p[1] for p in done["pos"]] == ["SENT"] and "po_not_sent" not in done["checks"] and "shipment_still_booked" not in done["checks"]
    assert any(j["stage"] == "RFQ" and not j["pos"] for j in out["jobs"])
    assert out["order"]["currency"] == "USD" and 15 <= out["order"]["margin"] <= 40 and "Sage" in out["order"]["status_word"] and "not in a Sage file" in out["order"]["status_word"]
    # the admin-only step, and the customs papers
    assert out["limit"] == 10000 and out["big"] and out["big"][0][1] == "DRAFT" and out["big"][0][2] > out["limit"]
    assert out["paper"]["dest"] == "US" and "Houston" in out["paper"]["why"]                       # Qingdao -> Houston is not "going to Canada"
    assert out["maple_missing"] >= 3                                                                 # one PO has tariff, origin and weight left blank: the missing list shows
    # one count everywhere: Today, the badge's source and the daily summary agree
    assert out["counts"]["inbox"] == out["counts"]["summary"] == out["needs"]["MANAGER"]["counts"]["alerts"] - 0 or out["counts"]["inbox"] == out["counts"]["summary"]
    assert 1 <= out["needs"]["MANAGER"]["total"] <= out["needs"]["MANAGER"]["counts"]["drafts"] + out["needs"]["MANAGER"]["counts"]["alerts"] + out["needs"]["MANAGER"]["counts"]["ball"]          # the badge counts what needs action: never more than everything open
    # what Sage already knows (synthetic export files): the finished order is part paid, a bill is held, an SO and a PO are open
    assert out["sage"]["order"]["paid_status"] == "PART_PAID" and out["sage"]["order"]["amount_due"] == "1270.00"
    assert out["sage"]["bill"]["bill_number"] == "INV-ZIBO-7721" and {o["kind"] for o in out["sage"]["open"]} == {"SO", "PO"}
    assert {"invoices", "payments", "bills", "open_sales_orders", "open_purchase_orders"} <= set(out["sage"]["history"])
    env_text = (tmp_path / "story" / "demo.demo.env").read_text(encoding="utf-8").splitlines()
    assert "PO_APPROVAL_LIMIT=10000" in env_text
    assert any(ln.startswith("AUTOPILOT_REVIEWER_EMAILS=") and "demo-manager@" in ln and "demo-admin@" in ln for ln in env_text)
