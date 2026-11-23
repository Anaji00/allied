"""Builds a clean DEMO database with a few believable Allied records, in a place you choose. It never touches the real database.

    python scripts/demo_seed.py --db C:/demo/allied_demo.db
    python scripts/demo_seed.py --db C:/demo/allied_demo.db --replace      # rebuild a demo database this script made earlier

What it makes next to the database file (<name> is the file name without .db):
    <name>.db        the demo database (customer, RFQs, jobs, purchase orders, a shipment, a sales order, a packing slip, staff accounts)
    <name>_files/    uploads for the demo      <name>_output/    generated packing slips      <name>_data/   a COPY of the master catalogues
    <name>.demo.env  the settings that point the app at these and keep every outside connection off (mail, monday, Sage, AI)

Safety rules, checked before anything is created or imported:
  * the path must not be data/allied_enterprise.db, and must not be inside this project's data/ or output/ folders;
  * an existing file is never overwritten unless --replace is given AND the file was made by this script (it carries a marker table);
  * the settings written for the demo turn mail sending, monday, Sage and AI off, so approving a draft in the demo cannot reach a real person;
  * the demo accounts use a throwaway password that is printed once, and every vendor address in the demo is a .test address.

Nothing here writes to monday or Sage."""
import argparse
import os
import secrets
import shutil
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MARKER_TABLE = "_demo_seed_marker"
FOLDER_MARKER = ".demo_seed"          # a file inside every folder this script makes: --replace deletes only folders that carry it


def _is_inside(path: Path, folder: Path) -> bool:
    try:
        path.resolve().relative_to(folder.resolve())
        return True
    except ValueError:
        return False


def check_target(db_arg: str, replace: bool = False) -> Path:
    """The demo database path, or a SystemExit with a plain reason. Runs before anything is created."""
    if not db_arg or not str(db_arg).strip():
        raise SystemExit("Give the demo database path with --db, for example --db C:/demo/allied_demo.db")
    path = Path(db_arg).expanduser()
    if path.suffix.lower() != ".db":
        raise SystemExit(f"The demo database file must end in .db (got '{path.name}').")
    real = ROOT / "data" / "allied_enterprise.db"
    if path.resolve() == real.resolve() or (real.exists() and path.exists() and os.path.samefile(path, real)):
        raise SystemExit("Refused: that is the REAL database (data/allied_enterprise.db). Pick a different path for the demo.")
    for folder in (ROOT / "data", ROOT / "output", ROOT / "app" / "data"):
        if _is_inside(path, folder):
            raise SystemExit(f"Refused: {path} is inside {folder}, which holds real data. Put the demo database somewhere else, like a folder named demo.")
    if path.exists():
        if not replace:
            raise SystemExit(f"Refused: {path} already exists. Use --replace to rebuild a demo database this script made, or choose another path.")
        try:
            con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
            try:
                made_here = con.execute("select name from sqlite_master where type='table' and name=?", (MARKER_TABLE,)).fetchone()
            finally:
                con.close()
        except sqlite3.DatabaseError:
            made_here = None
        if not made_here:
            raise SystemExit(f"Refused: {path} was not made by this script (it has no demo marker), so it will not be replaced.")
    return path


def _paths(db: Path) -> dict:
    stem = db.with_suffix("").name
    base = db.parent
    return {"db": db, "files": base / f"{stem}_files", "output": base / f"{stem}_output", "data": base / f"{stem}_data", "backups": base / f"{stem}_backups", "env": base / f"{stem}.demo.env"}


def demo_env(p: dict, password: str = "") -> dict:
    """Every setting that points the app at the demo files and keeps the outside world switched off."""
    data = p["data"]
    extra = {"ADMIN_PASSWORD": password} if password else {}        # the demo's own admin login uses the throwaway password, not the shipped default
    return {
        **extra,
        "DATABASE_URL": f"sqlite:///{p['db'].as_posix()}",
        "DB_DIR": p["files"].as_posix(), "OUTPUT_DIR": p["output"].as_posix(), "DATA_DIR": data.as_posix(),
        "MASTER_ITEMS_PATH": (data / "master_items.csv").as_posix(), "MASTER_CUSTOMERS_PATH": (data / "master_customers.csv").as_posix(),
        "MASTER_VENDORS_PATH": (data / "master_vendors.csv").as_posix(), "ALIAS_DICT_PATH": (data / "alias_dictionary.json").as_posix(),
        # nothing leaves the machine
        "MONDAY_SIMULATION_MODE": "True", "MONDAY_READ_ONLY": "True", "MONDAY_PUSH_MODE": "manual",
        "EMAIL_SEND_ENABLED": "False", "SMTP_HOST": "", "SMTP_USERNAME": "", "SMTP_PASSWORD": "", "EMAIL_REDIRECT_ALL_TO": "",
        "IMAP_POLL_ENABLED": "False", "IMAP_HOST": "", "IMAP_USERNAME": "", "IMAP_PASSWORD": "", "AI_EMAIL_TRIAGE_ENABLED": "False",
        "GROQ_API_KEY": "", "AI_FALLBACK_PROVIDER": "", "VENDOR_PO_MODE": "suggest", "SEED_DEMO_DATA": "False",
        "TWILIO_ACCOUNT_SID": "", "TWILIO_AUTH_TOKEN": "", "TWILIO_FROM": "", "NTFY_URL": "", "NTFY_TOPIC": "", "NTFY_TOKEN": "",
        "SMS_PROVIDER": "", "SLACK_WEBHOOK_URL": "", "TEAMS_WEBHOOK_URL": "", "LEADERSHIP_EMAILS": "",
        "AUTOPILOT_REVIEWER_EMAILS": "demo-manager@alliedweldingsystems.com,demo-admin@alliedweldingsystems.com",       # drafts wait for the demo manager and admin, not a real person
        "BACKUP_COPY_DIR": "", "BACKUP_DIR": (p["db"].parent / f"{p['db'].with_suffix('').name}_backups").as_posix(), "PO_APPROVAL_LIMIT": "10000",
        "FEDEX_ENABLED": "False", "FEDEX_CLIENT_ID": "", "FEDEX_CLIENT_SECRET": "", "FEDEX_ACCOUNT_NUMBER": "",
        "SAGE_LIVE_CONNECTION": "False", "SAGE_SIMULATION_MODE": "True", "SAGE_PASSWORD": "", "FX_LIVE_RATES": "False",
        "MONDAY_API_KEY": "", "ASSISTANT_MCP_SERVERS": "",
        "INBOX_SIMULATION_ENABLED": "True",                          # the practice-order route works only here: it makes a real order, but in this scratch database
    }


def go_quiet(settings, env: dict) -> None:
    """Switches every outside connection off in THIS process, then checks it. The settings loader ignores empty environment values (an empty
    value means "use the default", and the real .env would win), so the blanks are applied to the loaded settings directly. Stops with a plain
    error, before any record is made, if anything that could reach a person or a service is still on."""
    for key, value in env.items():
        if value == "":
            setattr(settings, key, "")
    on = [name for name, v in (("mail server", settings.SMTP_HOST), ("phone push", settings.NTFY_URL), ("Slack", settings.SLACK_WEBHOOK_URL),
                               ("Teams", settings.TEAMS_WEBHOOK_URL), ("Twilio", settings.TWILIO_ACCOUNT_SID), ("AI key", settings.GROQ_API_KEY),
                               ("mailbox", settings.IMAP_HOST), ("FedEx key", settings.FEDEX_CLIENT_ID), ("monday key", settings.MONDAY_API_KEY),
                               ("Sage password", settings.SAGE_PASSWORD), ("third-party assistant tools", settings.ASSISTANT_MCP_SERVERS)) if str(v or "").strip()]
    if on or settings.EMAIL_SEND_ENABLED or settings.IMAP_POLL_ENABLED or str(settings.SMS_PROVIDER or "").lower() == "twilio"             or settings.FEDEX_ENABLED or settings.SAGE_LIVE_CONNECTION or not settings.SAGE_SIMULATION_MODE or settings.FX_LIVE_RATES             or not settings.MONDAY_READ_ONLY or not settings.MONDAY_SIMULATION_MODE:
        raise SystemExit("Refused: an outside connection is still on (" + ", ".join(on or ["a switch"]) + "), so the demo was not started.")


def seed(db_arg: str, replace: bool = False) -> dict:
    path = check_target(db_arg, replace)
    p = _paths(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if replace and path.exists():
        for folder in (p["files"], p["output"], p["data"], p["backups"]):
            if folder.exists():
                if not (folder / FOLDER_MARKER).is_file():
                    raise SystemExit(f"Refused: {folder} exists but was not made by this script (no {FOLDER_MARKER} inside), so it will not be deleted. Move it away or pick another name.")
        for f in (path, path.with_name(path.name + "-wal"), path.with_name(path.name + "-shm")):
            f.unlink(missing_ok=True)
        for folder in (p["files"], p["output"], p["data"], p["backups"]):
            if folder.exists():
                shutil.rmtree(folder)
    for folder in (p["files"], p["output"], p["backups"]):
        folder.mkdir(parents=True, exist_ok=True)
    shutil.copytree(ROOT / "app" / "data", p["data"])               # a copy: the demo's CSV imports can never rewrite the real catalogues
    for folder in (p["files"], p["output"], p["data"], p["backups"]):
        (folder / FOLDER_MARKER).write_text("made by scripts/demo_seed.py; safe to delete with the demo\n", encoding="utf-8")
    password = secrets.token_urlsafe(9)
    env = demo_env(p, password)
    os.environ.update(env)                                          # before the app is imported: it reads these when it loads
    sys.path.insert(0, str(ROOT))

    from app.config import settings
    assert settings.DATABASE_URL == env["DATABASE_URL"], "the app did not take the demo database path"
    go_quiet(settings, env)                                         # before any record is made: nothing can reach a real person while it is built
    assert Path(str(settings.DATABASE_URL).replace("sqlite:///", "")).resolve() == path.resolve()
    from app.core.database import SessionLocal, init_database
    init_database()
    from app.core.security import hash_password
    from app.services.database_service import DatabaseService

    accounts = {"admin": "ADMIN", "manager": "MANAGER", "operator": "OPERATOR", "viewer": "VIEWER"}
    with SessionLocal() as db:
        for name, role in accounts.items():
            email = f"demo-{name}@alliedweldingsystems.com"
            if DatabaseService.get_user_by_email(db, email) is None:
                DatabaseService.create_user(db, email=email, hashed_password=hash_password(password), full_name=f"Demo {name.title()}", role=role)
    people = {role: {"email": f"demo-{name}@alliedweldingsystems.com", "role": role} for name, role in accounts.items()}
    _records(people)
    con = sqlite3.connect(path)
    try:
        con.execute(f"create table if not exists {MARKER_TABLE} (made_at text)")
        con.execute(f"insert into {MARKER_TABLE} values (datetime('now'))")
        con.commit()
    finally:
        con.close()
    lines = [f"{k}={v}" for k, v in env.items()]
    p["env"].write_text("# Settings for the demo only. Load them before starting the app (see scripts/demo_seed.py).\n" + "\n".join(lines) + "\n", encoding="utf-8")
    return {"paths": p, "password": password, "accounts": [f"demo-{n}@alliedweldingsystems.com ({r})" for n, r in accounts.items()]}


def _records(people: dict) -> None:
    """A few believable Allied jobs at different points of the journey, with approvals waiting and alerts to act on. All through the app's own
    services, as the screens would do it. Nothing is emailed: the one "sent" purchase order is only recorded as sent, in the send ledger."""
    import uuid
    from datetime import date, timedelta

    from app.core.database import SessionLocal
    from app.models.db_models import StaffContactDB
    from app.services.coordination import jobs, notify, purchase_orders, rfqs, shipments
    from app.services.outbound_email_service import outbound_email_service

    ops, boss, admin = people["OPERATOR"], people["MANAGER"], people["ADMIN"]
    customer = "Owens Corning Starr Composites"
    zibo = "Zibo Supereal Industrial Ceramic Co. Ltd"
    today = date.today()
    boss_mail, admin_mail, ops_mail = boss["email"], admin["email"], ops["email"]

    with SessionLocal() as db:                                      # who is told, and who counts as leadership (escalations, "urgent" alerts)
        for email, name, tag in ((boss_mail, "Demo Manager", "leadership"), (admin_mail, "Demo Admin", "leadership"), (ops_mail, "Demo Operator", "operations")):
            row = db.get(StaffContactDB, email) or StaffContactDB(email=email, name=name)
            row.name, row.role_tag, row.channels, row.active, row.daily_summary = name, tag, ["email"], True, False
            db.add(row)
        db.commit()
    notify.forget_contacts()

    # 1. A finished journey: RFQ -> job -> design approved -> PO approved, customs filled and SENT -> shipment on its way -> customer order -> sales order -> packing slip
    r1 = rfqs.create({"customer": customer, "subject": "Replacement flex hose for the forming line", "complexity": "STANDARD",
                      "items": [{"description": "5in ID x 120in alumina ceramic-lined rubber flex hose, per drawing OC-SR-08272024-FH_01", "quantity": 2}], "vendor": zibo}, ops, announce=False)
    j1 = jobs.from_rfq(r1["id"], ops)
    jobs.approve_design(j1["ref"], boss)
    po1 = purchase_orders.create(j1["ref"], zibo, ops, lines=[{"description": "5in ID x 120in alumina ceramic-lined rubber flex hose, per drawing", "quantity": 2, "unit_cost": 1040, "revision": "B"}],
                                 terms="Net 45", ship_via="Sea freight", currency="CNY")             # a Chinese vendor, priced in yuan
    purchase_orders.update(po1["id"], {"vendor_email": "purchasing@zibo-demo.example.test"}, boss)
    po1 = purchase_orders.approve(po1["id"], boss)
    purchase_orders.set_customs(po1["id"], {"lines": [{"line": 1, "hts_code": "4016.93.50", "origin": "China", "uom": "EA", "net_kg": 38.5}],
                                            "incoterm": "FOB", "incoterm_place": "Qingdao"}, ops)
    token = purchase_orders.begin_send(po1["id"])                   # recorded as sent, the way a real send ends; no email exists
    outbound_email_service._ledger({"message_id": f"MSG-{uuid.uuid4().hex[:10].upper()}", "category": "PURCHASE_ORDER", "reference": str(po1["po_number"]),
                                    "to": ["purchasing@zibo-demo.example.test"], "intended_to": ["purchasing@zibo-demo.example.test"], "status": "SENT",
                                    "redirected": False, "subject": f"Purchase order {po1['po_number']}", "detail": "Demo record: nothing was emailed"})
    purchase_orders.finish_send(po1["id"], True, token)
    shipments.create({"reference": f"PO {po1['po_number']}", "mode": "SEA", "forwarder": "Pacific Bridge Logistics", "container_no": "PBLU4410229",
                      "origin": "Qingdao", "destination": "Houston, TX", "etd": (today - timedelta(days=35)).isoformat(), "eta": (today - timedelta(days=3)).isoformat(),
                      "free_time_ends": (today + timedelta(days=2)).isoformat(), "status": "DISCHARGED"}, ops)      # at the Houston port, free time ends in 2 days
    jobs.create_sales_order(j1["ref"], {"customer_reference": "4500912345", "lines": [{"raw_item_text": "OC262064", "quantity": 2, "unit_price": 1385.00}],
                                        "shipping_address": "OWENS CORNING- STARR\n4837 Highway 81 South\nStarr, SC 29684"}, boss)

    _walk_extras(j1, r1, po1, boss, ops)

    # 2. A request that has only just become a job (stage "Request received"): the presenter runs design approval -> purchase order live from here
    r2 = rfqs.create({"customer": customer, "subject": "Quote for ceramic liner tiles, 400 pieces", "complexity": "SIMPLE",
                      "items": [{"description": "Ceramic liner tile 6in x 6in x 1in, per drawing OC-LT-0612", "quantity": 400}], "vendor": zibo}, ops, announce=False)
    jobs.from_rfq(r2["id"], ops)

    # 3. Design approved, two vendors: a big PO a manager cannot approve (over PO_APPROVAL_LIMIT, 10,000 in the demo settings: the administrator can),
    #    and a small one that is approved with its email waiting in the review queue as a draft
    r3 = rfqs.create({"customer": customer, "subject": "Hammermill screens, set of 12, and mounting brackets", "complexity": "COMPLEX",
                      "items": [{"description": "Hammermill screen 2-1/2in holes, per drawing OC-ED-218-R2", "quantity": 12}]}, ops, announce=False)
    j3 = jobs.from_rfq(r3["id"], ops)
    jobs.approve_design(j3["ref"], boss)
    purchase_orders.create(j3["ref"], "Nibrock, Inc.", ops, lines=[{"description": "Hammermill screen per drawing OC-ED-218-R2", "quantity": 12, "unit_cost": 1500, "revision": "A"}])
    maple = purchase_orders.create(j3["ref"], "Maple Fab Ltd", ops, lines=[{"description": "Mounting bracket per drawing OC-MB-07", "quantity": 4, "unit_cost": 40, "revision": "A"}])
    purchase_orders.update(maple["id"], {"vendor_email": "orders@maple-fab-demo.example.test"}, boss)
    purchase_orders.approve(maple["id"], boss)
    purchase_orders.email_draft(maple["id"], ops)                   # waits for a reviewer (the demo manager and admin); nothing is sent

    # 4. A quote that really is overdue (received 9 days ago), so the urgent alert and the RFQ screen agree. Made after the jobs so the job numbers stay 1, 2, 3.
    from datetime import datetime, timezone

    r4 = rfqs.create({"customer": customer, "subject": "Quote for wear plates, 20 pieces", "complexity": "SIMPLE",
                      "received_at": datetime.now(timezone.utc) - timedelta(days=9),
                      "items": [{"description": "AR400 wear plate 24in x 36in x 1/2in", "quantity": 20}]}, ops, announce=False)
    due = str(rfqs.get(r4["id"]).get("effective_due_at") or "")[:10]
    late_days = max(1, (today - date.fromisoformat(due)).days) if due else 1

    # Drafts for requests that are already finished or old would be stale on a clean walk: only the fresh request keeps its "add to the board" draft.
    from app.models.db_models import AssistantActionDB

    stale = {r1["id"], r3["id"], r4["id"]}
    with SessionLocal() as db:
        for row in db.query(AssistantActionDB).filter(AssistantActionDB.user_role == "AUTOPILOT", AssistantActionDB.status == "PENDING").all():
            if (row.args or {}).get("_rfq_id") in stale:
                row.status = "CANCELLED"
        db.commit()

    # 5. Alerts to act on: snooze one, hand one to a colleague. One of them is urgent. Each one has something real behind it.
    mine = [boss_mail, ops_mail]
    notify.notify("vendor_noack", f"demo-vendor-noack-{uuid.uuid4().hex[:6]}", "Nibrock has not replied to the price request for the hammermill screens",
                  "Asked 6 days ago. Without a price the purchase order cannot be raised: ask Nibrock again, or ask another vendor.", severity="warning", to=mine, link="/vendors", wait=True)
    notify.notify("shipment_free_time", f"demo-free-time-{uuid.uuid4().hex[:6]}", "Free time at the Houston port ends in 2 days (container PBLU4410229)",
                  "After that the port charges storage every day. Book the truck or ask the forwarder for more time.", severity="warning", to=mine, link="/shipments", wait=True)
    notify.notify("rfq_overdue", f"demo-rfq-overdue-{uuid.uuid4().hex[:6]}", "Quote overdue: wear plates for Owens Corning Starr",
                  f"The quote was due {due or 'last week'} ({late_days} day{'s' if late_days != 1 else ''} ago) and no price has gone to the customer yet.", severity="critical", to=mine, include_leadership=True, link="/rfqs", wait=True)
    notify.notify("vendor_checkin", f"demo-checkin-{uuid.uuid4().hex[:6]}", "Check in with Zibo about the ceramic liner tile price",
                  "The price request for the liner tiles has had no answer yet. A quick message keeps the quote on schedule.", severity="info", to=mine, link="/vendors", wait=True)

    # 6. What Sage already knows, from synthetic Sage export files (scripts/sage_samples): one invoice part paid, one paid, an open sales order and purchase order,
    #    and a vendor bill that the 3-way match will flag as already held in Sage. Read from files only: nothing here touches a Sage company file.
    from app.services import sage_import

    folder = Path(__file__).resolve().parent / "sage_samples"
    for kind, name in (("invoices", "invoices.csv"), ("payments", "payments.csv"), ("open_sales_orders", "open_sales_orders.csv"),
                       ("open_purchase_orders", "open_purchase_orders.csv"), ("bills", "bills.csv")):
        shown = sage_import.preview((folder / name).read_bytes(), name, kind, admin)
        sage_import.commit(shown["token"], admin)


def _walk_extras(j1: dict, r1: dict, po1: dict, boss: dict, ops: dict) -> None:
    """What the PO screens need to be walked: a vendor quote with weights and dimensions on the PO, a drawing that names the customer (to be cleaned), a payment step that is due, and a supplier bill
    that was checked against the PO with its own lines. All made-up, all in the scratch database and its own uploads folder."""
    import uuid
    from pathlib import Path as _P

    import fitz

    from app.config import settings
    from app.core.database import SessionLocal
    from app.models.db_models import ItemFileDB
    from app.services import payments
    from app.services.coordination import files
    from app.services.database_service import DatabaseService

    folder = _P(settings.DB_DIR) / "demo_extras"
    folder.mkdir(parents=True, exist_ok=True)
    quote = folder / "zibo_quote_Q-2026-114.txt"
    quote.write_text("Quotation Q-2026-114\nZibo Supereal\nGross weight: 1,250 kg\nDimensions: 120 x 80 x 100 cm\n12 crates\nTotal CNY 148,000\nPayment terms: 30/50/20\n", encoding="utf-8")
    with SessionLocal() as db:                                        # the vendor's quote, attached to the PO (the only kind of file that may go into a payment note or a freight request)
        db.add(ItemFileDB(item_type="po", item_ref=str(po1["id"]), filename=quote.name, stored_path=str(quote), size=quote.stat().st_size, sha256=uuid.uuid4().hex, content_type="text/plain", uploaded_by=ops["email"]))
        db.commit()
    pdf = fitz.open()
    page = pdf.new_page()
    page.insert_text((40, 80), "Owens Corning Starr Composites  -  DRAWING OC-SR-08272024-FH_01  REV B", fontsize=11)
    page.insert_text((40, 110), "Flex hose 5in ID x 120in. Customer PO 4500912345. Starr, SC.", fontsize=10)
    files.add("rfq", r1["number"], "OC-SR-08272024-FH_01.pdf", pdf.tobytes(), ops)                 # names the customer: the redaction check paints it out

    payments.create_schedule(po1["id"], "30/50/20", boss, currency="CNY")
    payments.fire(po1["id"], "PO_ISSUED", boss)                                                   # the first step is now due

    with SessionLocal() as db:                                        # a supplier bill, checked against the PO: one line billed higher than the PO, with freight
        DatabaseService.persist_reconciliation(db, {
            "reconciliation_id": uuid.uuid4().hex[:16], "invoice_number": "ZB-INV-2026-0457", "vendor_name": po1["vendor"], "po_number": po1["po_number"], "reconciliation_status": "DISCREPANCY_FLAGGED",
            "authorized_po_total": 2080, "actual_billed_total": 2405, "cost_variance_amount": 325, "cost_variance_pct": 15.6,
            "discrepancies": ["Price Surcharge on 1: Billed $1,170.00 vs PO $1,040.00 (+$130.00/unit)", "Unauthorized Freight Add-On: Billed $195.00 vs Agreed $0.00 (+$195.00)"],
            "suggested_action": "Ask Zibo to correct the invoice (demo).",
            "bill_detail": {"lines": [{"item_id": "1", "description": "5in ID x 120in alumina ceramic-lined rubber flex hose, per drawing", "quantity": "2", "unit_cost": "1170.00", "amount": "2340.00"}],
                            "freight": "195.00", "currency": "CNY", "invoice_date": "2026-11-20"}})


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Build a clean DEMO database (never the real one).")
    ap.add_argument("--db", required=True, help="where to make the demo database, for example C:/demo/allied_demo.db")
    ap.add_argument("--replace", action="store_true", help="rebuild a demo database this script made earlier")
    args = ap.parse_args(argv)
    out = seed(args.db, args.replace)
    p = out["paths"]
    print("Demo database built.\n")
    print(f"  database : {p['db']}\n  uploads  : {p['files']}\n  output   : {p['output']}\n  catalogs : {p['data']} (a copy)\n  settings : {p['env']}\n")
    print("Sign in with any of these (one throwaway password, shown only now):")
    for a in out["accounts"]:
        print("  " + a)
    print(f"  password: {out['password']}\n")
    print("Start the app on the demo data (from the project folder; nothing real is used, nothing leaves the machine):")
    print(f"  python scripts/demo_run.py --db \"{p['db']}\" --port 8010")
    print("  (add --check first to see what it will use). The frontend must point at http://localhost:8010 (NEXT_PUBLIC_API_URL).")
    print("To go back to the real data, close that window and start the app the normal way.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
