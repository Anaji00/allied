# Allied Operations

Allied Welding Systems' automation toolkit. It runs alongside **monday.com** (where projects and jobs are tracked) and **Sage 50 US** (the books). Every automation runs in this software; monday and Sage only hold data, which keeps Allied off monday's paid automation actions.

Allied designs parts and has them made overseas, so the system is built around that: project milestones and design-document reviews, freight-forwarder shipments, staff alerts, and the order paperwork that ends up in Sage.

> **New here?** [SYSTEM_WALKTHROUGH.md](SYSTEM_WALKTHROUGH.md) explains what it does in plain English. [INTEGRATION_GUIDE.md](INTEGRATION_GUIDE.md) covers the monday and Sage connection in detail. This README is the technical reference.

---

## What it does

| Area | What happens | Where |
|---|---|---|
| **Projects and milestones** | Reads the monday projects board every 10 minutes (items = projects, subitems = milestones, with owner, due date and status). Works out what is overdue, due soon or waiting for review. | `app/services/coordination/projects.py` |
| **Design documents** | Engineers upload drawings, models or calcs against a milestone. A reviewer approves or sends them back with a required note. Nobody can approve their own work, and only the milestone's reviewers, the project owners, leadership or managers can review. Each step is written back to monday and to the audit log. | `projects.py` |
| **Alerts** | One alert system for everything: an in-app inbox, email, ntfy phone push (warning and up) and Twilio text (critical only). Each alert is sent once, closes itself when the problem clears, waits out nights and weekends (non-critical), and goes to the owner first, leadership only on escalation. Weekday leadership summary at 7 am. | `coordination/notify.py`, `schedule.py` |
| **Shipments** | Containers and air shipments with full history. Flags late, slipped 3+ days, missed departure, forwarder silent (5 days at sea, 2 in port) and port free time ending (demurrage). Forwarder status emails update the matching shipment by container or air waybill number. | `coordination/shipments.py` |
| **monday → orders** | Status changes on the jobs board (webhooks, plus a board reader that polls every 5 minutes) create the Sage sales order and vendor POs, release POs, or dispatch packing slips. Messages are saved before replying, de-duplicated, retried with backoff, and escalated to a person after 6 failures. | `monday_events.py`, `order_orchestrator.py` |
| **Sage 50** | Import files (`SO_IMPORT.CSV`, `PO_IMPORT.CSV`) in Sage's mm/dd/yy format, only orders not yet exported, claimed atomically so two downloads never contain the same order. Direct writing into Sage is not built (it needs the Sage SDK on the Sage PC). | `sage_export_service.py` |
| **Vendor bills** | 3-way match of the bill against the PO and the sales order: unit price, line totals, freight, invoice total, vendor, and duplicate invoice numbers. Anything off is held and sent to a person. A dispute email goes to the vendor at most once, and never for a bill with no matching PO. | `reconciliation_service.py` |
| **AI tools** | Email triage and drafts, Vietnamese and Mandarin translation with a glossary, call summaries, order lookup, exception explanations and drawing comparison. Runs on local **Ollama** by default; Claude is opt-in. Anything binding waits for a person to approve it. | `app/services/ai/` |
| **Documents** | Reads customer POs, drawings and mill certificates. A file that can't be read gets 0% confidence and goes to a person; nothing is filled in from the file name. | `document_scanner_service.py` |
| **Mill certificates** | Heat-number lookup (exact match only), ASTM limits and quarantines, and certificates of conformance. | `mtr_service.py` |

---

## Screens

| Screen | Path | For |
|---|---|---|
| Overview | `/` | What needs attention today |
| Projects | `/projects` | Milestones, design documents to submit or review, what is behind |
| Shipments | `/shipments` | Freight on the way, flags, forwarder emails |
| Alerts | `/alerts` | My alerts, everyone's (managers), who gets told and how, daily summary |
| Orders & Sage | `/operations` | Order emails, bill checks, Sage import files, CSV imports, exceptions |
| Documents | `/documents` | Read POs, drawings and certificates |
| AI tools | `/ai` | Translate, read email, draft replies, check drawings, call summaries, review queue |
| monday.com link | `/integrations` | Board events received, what each did, replay of stuck ones |
| What runs by itself | `/automation` | Which steps are automatic, what still needs a person, the outbox |
| System health | `/health` | Service, database and connection status |
| Wear & quotes, Leads | `/sales/pipeline`, `/sales/leads` | Equipment wear tracking and quotes |
| Mill certificates | `/compliance/vault` | Heat numbers and quarantines |
| Audit trail | `/admin/audit` | Who did what, and when |
| Ask the assistant | `/chat` | Search specs, procedures and past orders (read-only) |

Every screen sits behind sign-in and works at phone width.

---

## Running it

**You need:** Python 3.11+, Node 18+ (22 tested), and [Ollama](https://ollama.com) for the AI tools (`ollama pull qwen2.5:7b`; `qwen2.5vl:7b` for drawing checks from scans).

```bash
pip install -r requirements.txt -r requirements-dev.txt
```

```bash
cp .env.example .env
```

Edit `.env`. Every setting is listed and explained in `.env.example`.

```bash
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

```bash
cd frontend && npm install && npm run dev
```

- App: http://localhost:3000 (the Next.js dev server proxies `/api` to port 8000)
- API reference: http://127.0.0.1:8000/docs
- Liveness probe (no sign-in): `GET /api/v1/health/live`

The database is SQLite at `data/allied_enterprise.db`. Alembic owns the schema and migrations run on startup. Back it up with `python scripts/backup.py`.

Docker: `docker compose up` runs the API as a non-root user with `data/` and `output/` mounted.

### Settings that matter

| Setting | Why |
|---|---|
| `APP_ENV=production` | Locks the monday webhook (refused without a token or signing secret). The app **refuses to start** if `SECRET_KEY`, `JWT_SECRET_KEY` or `ADMIN_PASSWORD` is empty or still a shipped default. |
| `MONDAY_API_TOKEN`, `MONDAY_SIMULATION_MODE` | monday stays in simulation (no reads or writes) until you turn it off. |
| `MONDAY_WEBHOOK_TOKEN` / `MONDAY_WEBHOOK_SECRET` | Webhook authentication: a URL token, or the monday app's signing secret. |
| `MONDAY_PROJECTS_BOARD_ID` + `PROJECT_*` / `MILESTONE_*` column ids | Which board holds projects, and which columns are owner, due date and status. |
| `LEADERSHIP_EMAILS`, `NTFY_*`, `SMS_PROVIDER` / `TWILIO_*` | Who gets escalations, and the push and text channels. |
| `FORWARDER_DOMAINS` | Email domains whose messages are read as shipment updates. |
| `AI_PROVIDER=ollama`, `AI_TEXT_MODEL`, `AI_VISION_MODEL` | Local AI. `claude` is supported but costs money per call. |
| `SAGE_SIMULATION_MODE` | Leave on: import files are the supported way into Sage. |

### Roles

`VIEWER` reads. `OPERATOR` submits documents, adds shipments, runs checks and processes orders. `MANAGER` sees everyone's alerts, sends the summary and downloads Sage files. `ADMIN` manages alert contacts. Disabled accounts are refused at once, not when their token expires.

---

## Background workers

All three start with the API and stop with it. Each loop survives any single failure and records it.

| Worker | Does | How often |
|---|---|---|
| monday inbox | Retries failed board events; reads the jobs board for changes webhooks missed | `MONDAY_POLL_SECONDS` (300) |
| Coordination | Syncs the projects board, runs schedule and shipment checks, sends held pushes and texts after quiet hours, weekday summary | `PROJECTS_POLL_SECONDS` (600), `SCHEDULE_CHECK_SECONDS` (900), summary at `DIGEST_HOUR` |
| Mailbox | Reads the order mailbox (off unless configured) | as configured |

---

## Tests

```bash
python -m pytest -q
```

Results: 286 passed, 1 skipped. The skipped test calls the live local AI and runs only when Ollama is up.

```bash
python tests/run_end_to_end_use_cases.py
```

Results: all 60 end-to-end business scenarios pass.

```bash
cd frontend && npm run lint && npm run build
```

Results: clean lint and a production build.

Tests and scenarios each use their own temporary database and data files, never `data/allied_enterprise.db`.

---

## Code layout

```
app/
  api/v1/endpoints/    REST routes (one file per area; coordination.py = projects, shipments, alerts)
  core/                settings checks, auth and roles, audit log, database, upload safety
  services/
    coordination/      projects, schedule checks + worker, alerts, shipments
    ai/                provider layer (Ollama / Claude), features, drawing check, speech, review store
    monday_*.py        monday API client and the board-event inbox
    sage_*.py          Sage import files; SDK writer (not live)
    ...                orders, documents, bills, mill certs, CSV import, exceptions
  data/                catalogues (items, customers, vendors), aliases, glossary, MTRs
  config.py            every setting, with defaults
migrations/            Alembic schema history
frontend/src/
  app/                 one folder per screen
  components/          layout, sidebar, header, shared UI kit (components/ui)
  lib/api.js           every API call, with sign-in and token refresh
tests/                 pytest suites + the 60-scenario script
scripts/               backup, Sage connection check
```

---

## Known limits

- **monday projects board:** not yet tested against Allied's real board; the board ID and column IDs need setting. If monday returns no items for a board the system already holds, the sync stops and removes nothing.
- **Sage writes:** import files only; nobody has yet done a test import into a Sage sample company.
- **Runs on one server only:** sales order numbers and the submission lock live in the process. Don't run several API workers against one database.
- **Texts:** carrier email-to-text gateways are shut down. Real texts need Twilio with A2P 10DLC registration; ntfy push is the free option.
- **Engineer instructions:** the job brief, read-back and sign-off flow is designed but not built.
- **Version control:** the project folder is not under git yet.
