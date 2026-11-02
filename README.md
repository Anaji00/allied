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
| **Vendor lead times** | One job per vendor PO, read from a monday vendor board (or added here). Ship-by date = project due date − transit (sea 45, air 10, truck 5 days) − 7-day buffer. Expected date = the vendor's promise, or the PO date plus this vendor's real lead time (8 of 10 past jobs) once it has 5 shipped jobs, else its quote + 20%. Flags: vendor late, will make the project late, at risk (little slack or the vendor usually runs late), PO not confirmed after 2 business days, reconfirm 7 days before ship. The first promise is kept, so on-time % and date moves can't be hidden by re-promising. Dates changed here are written back to monday. | `coordination/vendors.py` |
| **RFQs** | Every request for quote is logged with an owner and a deadline in business hours: acknowledge within 4 working hours, quote in 1, 2 or 5 business days (repeat part, standard, new design or vendor pricing). Emails the AI sorts as quote requests (or with RFQ/quote in the subject when AI is off) become RFQs, once per email. Waiting on a vendor price still counts and gets a chaser at 2 days and escalation at 4; waiting on the customer pauses the clock. Tracks median and 90th-percentile quote time, on-time %, win rate, and win rate by quote speed. Lost and no-bid need a reason. | `coordination/rfqs.py`, `workdays.py` |
| **Assistant (chat widget)** | Chat on every screen. It answers through narrow tools (projects, vendor jobs and scores, RFQs, shipments, orders, alerts, knowledge, Sage lookup) run as the signed-in user. Changes (log or update an RFQ, record a vendor date, send an email, post on monday) come back as a card the user confirms; the role is checked again then, and proposals expire after 15 minutes. Tool results are marked as data so text in an email can't instruct it. Third-party services connect over MCP (`ASSISTANT_MCP_SERVERS`): read-only tools run, others need confirmation. At most 5 lookups per question; every tool call is in the audit log. Shortcut buttons work with the AI off. | `app/services/assistant/` |
| **monday → orders** | Status changes on the jobs board (webhooks, plus a board reader that polls every 5 minutes) create the Sage sales order and vendor POs, release POs, or dispatch packing slips. Messages are saved before replying, de-duplicated, retried with backoff, and escalated to a person after 6 failures. | `monday_events.py`, `order_orchestrator.py` |
| **Sage 50** | Import files (`SO_IMPORT.CSV`, `PO_IMPORT.CSV`) in Sage's mm/dd/yy format. Each file holds only orders not yet sent to Sage (sales orders and vendor POs are tracked separately) and marks them as it is made, claimed atomically so two downloads never contain the same order. The marked order numbers come back in the `X-Sage-Exported-Orders` header; if a file never saved, `POST /sage/export/unmark` (or the screen's "Put them back") returns them to the next file. Direct writing into Sage is not built (it needs the Sage SDK on the Sage PC). | `sage_export_service.py` |
| **Vendor bills** | 3-way match of the bill against the PO and the sales order: unit price, line totals, freight, invoice total, vendor, and duplicate invoice numbers. Anything off is held and sent to a person. A dispute email goes to the vendor at most once, and never for a bill with no matching PO. | `reconciliation_service.py` |
| **AI tools** | Email triage and drafts, Vietnamese and Mandarin translation with a glossary, call summaries, order lookup, exception explanations and drawing comparison. Runs on **Groq's free tier** by default (fast, rate limited); local Ollama or a fallback to it is optional, and Claude is opt-in. Anything binding waits for a person to approve it. | `app/services/ai/` |
| **Documents** | Reads customer POs, drawings and mill certificates. A file that can't be read gets 0% confidence and goes to a person; nothing is filled in from the file name. | `document_scanner_service.py` |
| **Mill certificates** | Heat-number lookup (exact match only), ASTM limits and quarantines, and certificates of conformance. | `mtr_service.py` |

---

## Screens

| Screen | Path | For |
|---|---|---|
| Overview | `/` | What needs attention today |
| Projects | `/projects` | Milestones, design documents to submit or review, what is behind |
| Shipments | `/shipments` | Freight on the way, flags, forwarder emails |
| Vendors | `/vendors` | Vendor jobs against project dates, what to chase, vendor scorecard |
| RFQs | `/rfqs` | Quote requests, deadlines, owners, turnaround and win rate |
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

Every screen sits behind sign-in and works at phone width. The assistant opens from the button at the bottom right of every screen.

---

## Running it

**You need:** Python 3.11+, Node 18+ (22 tested), and a free [Groq API key](https://console.groq.com/keys) for the AI tools (set `GROQ_API_KEY`). [Ollama](https://ollama.com) is optional, as a local engine or a fallback (`ollama pull qwen2.5:7b`).

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

### Docker

```bash
docker compose up --build -d
```

- **What runs:** two images.
  - The backend: Python 3.11, Tesseract for OCR, a non-root user, a single Uvicorn worker. The app runs its own background workers, so a second web worker would run every automation twice.
  - The frontend: the Next.js standalone server on Node 22.
- **Ports:** only the frontend is published (`3000`, or `FRONTEND_PORT`). It proxies `/api/v1` to the backend, so monday webhooks use `http://<host>:3000/api/v1/webhooks/monday`. The backend listens on `127.0.0.1:8000` for local tools. If either port is taken on the machine, set `BACKEND_PORT` / `FRONTEND_PORT` when starting (e.g. `BACKEND_PORT=8010 FRONTEND_PORT=3010 docker compose up -d`).
- **Data:** `data/` (database, uploads, master catalog) and `output/` (packing slips) are bind-mounted and survive rebuilds.
  - The catalog lives in `data/catalog/`. The shipped defaults are copied in on first start, and a file there is never replaced, so CSV imports are kept.
  - On a Linux host, make the folders writable by the container user first: `mkdir -p data output && sudo chown -R 10001:10001 data output`.
- **Settings:** come from `.env`. `.env` and `data/` are never copied into an image. Folder paths in `.env` (such as `DATA_DIR`) are for running outside Docker; the compose file always sets the container's own.
  - `MONDAY_SIMULATION_MODE` and `MONDAY_READ_ONLY` default to `True` unless `.env` says otherwise.
- **Building the frontend:** `next build` fetches the IBM Plex fonts from Google Fonts, so it needs network access. The backend address is fixed into the `/api/v1` rewrite at build time (the `NEXT_PUBLIC_API_URL` build argument, `http://backend:8000` in compose).
- **Health:** both containers have health checks. The backend's is `GET /api/v1/health/live`, and the frontend starts only once the backend is healthy.

### On phones

The same app installs on a phone's home screen and opens full screen like a normal app (a PWA). There is no app store and nothing separate to build: an update to the server reaches every phone the next time it opens.

- **What staff get:** a tab bar at the bottom with Boards, Inbox (with the waiting count), Orders and Projects. "More" opens the full menu. The assistant button sits above the tab bar.
- **Reaching the server:** phones need HTTPS to install the app (Android won't offer it at all over plain `http://`), and browsers only give the microphone (voice notes, dictation) to HTTPS pages. Three ways:
  - **On the office network, built in:** the compose file's `https` service (Caddy, `deploy/Caddyfile`) serves the app over HTTPS with its own certificate authority. Start it with the addresses people use, for example:
    ```bash
    FRONTEND_PORT=3010 HTTPS_PORT=8443 ALLIED_HTTPS_SITES="https://localhost, https://192.168.12.235" ALLIED_DEFAULT_SNI=192.168.12.235 docker compose up -d https
    ```
    `HTTPS_PORT` is 8443 on this PC because another project already uses 443. Use the server's own Wi-Fi/LAN address (`ipconfig`), and give the server a fixed address in the router so it doesn't change. Staff then open `https://192.168.12.235:8443`.
    Each phone trusts the certificate once:
    - **iPhone:** open `https://<address>:8443/allied-root.crt` in Safari (tap through the one warning) → Allow the download → Settings → **Profile Downloaded** → Install. Then Settings → General → About → **Certificate Trust Settings** → turn on "Caddy Local Authority".
    - **Android:** open the same link in Chrome to download it, then Settings → Security → Encryption & credentials → **Install a certificate** → CA certificate → pick `allied-root.crt`.
    - **Windows/Mac browsers on the network:** install it as a trusted root the same way, or use `https://localhost:8443` on the server itself.
    The certificate authority is kept in the `caddy_data` volume, so phones keep trusting it across rebuilds. Removing that volume makes a new one that every phone has to install again. If Windows Firewall asks, allow Docker on private networks.
  - **Tailscale** (simplest, free for small teams): install Tailscale on the server and on each phone, then run `tailscale serve --bg 3000` on the server. Staff open `https://<server-name>.<tailnet>.ts.net`. Only people signed in to your tailnet can reach it.
  - **A domain and a reverse proxy** (Caddy, nginx) in front of port 3000, with a public certificate (no install on phones). Anyone on the internet can reach the sign-in page, so `APP_ENV=production` and strong passwords matter.
- **Installing:** iPhone: open the address in Safari → Share → **Add to Home Screen**. Android: open it in Chrome → menu → **Install app**.
- **No signal:** the phone shows a "No connection" page with a Try again button, and reloads by itself once it's back online. Nothing is stored on the phone: orders, dates and statuses always come fresh from the server.
- **Files:** `frontend/src/app/manifest.ts` (name, icons, shortcuts), `frontend/public/sw.js` (the offline page only), `frontend/src/components/shell/phone-tab-bar.tsx`.

### Settings that matter

| Setting | Why |
|---|---|
| `APP_ENV=production` | Locks the monday webhook (refused without a token or signing secret). The app **refuses to start** if `SECRET_KEY`, `JWT_SECRET_KEY` or `ADMIN_PASSWORD` is empty or still a shipped default. |
| `MONDAY_API_TOKEN`, `MONDAY_SIMULATION_MODE` | monday stays in simulation (no reads or writes) until you turn it off. |
| `MONDAY_READ_ONLY` | On by default. With simulation off, the app reads monday boards but sends **no change of any kind**: every GraphQL mutation is blocked in the client (twice: before the call and at the transport) and logged as blocked. Writes back to monday (submission statuses, vendor dates, comments, order statuses) are then skipped. Turn it off only for a test board. |
| `MONDAY_WEBHOOK_TOKEN` / `MONDAY_WEBHOOK_SECRET` | Webhook authentication: a URL token, or the monday app's signing secret. |
| `MONDAY_PROJECTS_BOARD_ID` + `PROJECT_*` / `MILESTONE_*` columns | Which board holds projects, and which columns are owner, due date and status. Any column setting takes the column id or its title as shown on the board (e.g. `"Estimate finish"`). |
| `LEADERSHIP_EMAILS`, `NTFY_*`, `SMS_PROVIDER` / `TWILIO_*` | Who gets escalations, and the push and text channels. |
| `FORWARDER_DOMAINS` | Email domains whose messages are read as shipment updates. |
| `AI_PROVIDER=groq`, `GROQ_API_KEY`, `GROQ_*_MODEL` | AI engine: Groq's free tier (text `openai/gpt-oss-120b`, scans `qwen/qwen3.8-27b`, calls `whisper-large-v3-turbo`). `ollama` runs locally; `claude` costs money per call. |
| `AI_FALLBACK_PROVIDER` | Engine to use when Groq is rate limited or down (e.g. `ollama`). Blank = the screen says AI is busy. |
| `MONDAY_VENDOR_BOARD_ID` + `VENDOR_*_COLUMN_ID` | The monday board with one item per vendor PO, and which columns hold vendor, project link, PO, owner, and the ordered / asked-for / confirmed / promised / shipped dates. `VENDOR_TRANSIT_DAYS_*` and `VENDOR_BUFFER_DAYS` set the ship-by date. |
| `VENDOR_DOMESTIC_VENDORS` | Vendors that ship within the US, by their board name. With no mode column on the board, they get `VENDOR_TRANSIT_DAYS_TRUCK` (5) and every other vendor `VENDOR_TRANSIT_DAYS_SEA` (35 in Allied's `.env`, from the 2025 orders: US vendors arrive 1-5 days after shipping, overseas contacts 22-38). |
| `VENDOR_CUSTOMER_*`, `VENDOR_OPEN_GROUPS` | For vendor jobs not linked to a project: the customer's date (a date column, or a start date + weeks, as Allied's Vendor PO Issued + Cust Lead), and whether it is delivery (transit taken off) or the ship date. Only items in the open groups are tracked; orders already finished when first read are history and not imported. With no promise column, a job is late once past PO date + quoted lead time. |
| `MILESTONE_UNPLANNED_ALERTS` | A weekly list to each owner of open milestones with no due date or owner (leadership gets the unowned ones), also in the daily summary. |
| `MILESTONE_HOLD_LABELS` | Statuses that mean a job is open but waiting on someone else (Allied: Paused, Sent for Review). Shown with state `on_hold`; never chased for a due date and never alerted as late. |
| `AUTOPILOT_*` | What the system does on its own, hourly. Plays: `rfq_board` (reads the monday Open RFQs board, `MONDAY_RFQ_BOARD_ID`, into the RFQ tracker), `orders_watch` (weekly list of unpaid orders and vendor photos waiting), `vendor_chaser`, `rfq_vendor_chaser` and `quote_followup` (emails to vendors and customers). Each is `off`, `suggest` (outside messages are drafted into the assistant's confirm queue for up to `AUTOPILOT_DRAFT_HOURS`) or `auto`. No play writes to monday. `GET /api/v1/autopilot` shows each play; `POST /api/v1/autopilot/run` is a dry run unless `dry_run` is false. |
| `VENDOR_EMAILS` | Contacts for vendor names on the boards (`HELEN=helen@...`). Names are also matched against the vendor master by id, name or the part before the @. |
| `VENDOR_ALIASES` | Contact names the boards use in place of a vendor, as `NAME=VENDOR` (Allied: `MAX=KWS,VICKI=EUTECTIC`, from the Open RFQs dropdown "KWS (Max)", "Eutectic (Vicki)"). |
| `MONDAY_REFERENCE_BOARD_IDS`, `MONDAY_DOC_IDS` | Boards shown in the board view as they are, with no tracking (Allied: 2025 Orders, Athish's Board, Daily Work Schedule, Project Follow Up), and monday Docs whose table is shown the same way (the Customer Visit Tracker). Read on start, on change and hourly; never written. |
| monday features, rebuilt read-only | `GET /api/v1/boards/{id}/activity` (monday's activity log: who changed what, last ~90 days, refreshed on every read), `/status-times` (how long items spend in each status, and which open items have sat longest), `/dashboard` (monday's battery in its colours, each person's open load, a calendar of due-type dates), `/items/{item}/updates` (an item's comments and files, read live). |
| Board view (`GET /api/v1/boards`, `/api/v1/boards/{id}?hide_done=true`) | Each watched board exactly as monday shows it: columns in monday's order with status colours, groups in order, every item and every column (formula results included), refreshed on every read, with the app's view of each row (job state, vendor order state, RFQ number). Nothing is stored or written. Vendor checks use the board's own VENDOR ETS and Target Date, so "late" in the app is exactly a negative Vendor Days Left on monday. |
| Vendor repricing | Dropped: orders are never held for an old vendor price, and there are no repricing actions or urgent price RFQs. |
| `AUTOPILOT_REACTIONS`, `AUTOPILOT_EVENTS_ASYNC` | Event-driven actions, run the moment something happens: `rfq_acknowledge` (a new RFQ by email: tell the customer it arrived and when the quote is due), `rfq_ask_vendors` (an RFQ goes to waiting on a vendor: ask each bidder for a price), `rfq_won` (tell the team), `order_shipped` (an order goes to SHIPPED on the orders board: start tracking the shipment), `order_photos` (waiting on photos: tell staff), `vendor_late_chase` (a vendor order turns late: draft the chaser), `drawing_ready` (a drawing job is finished: tell the owners of the vendor orders linked to it). Same modes as the plays; they run on a pool of four background workers and share keys with the plays. `GET /api/v1/autopilot/events` shows recent events and what each reaction did. The assistant can answer "what has the system done on its own?" (`automation_status`) and run a play on request (`run_autopilot`, admins, confirmed first). |
| Recipes (`/api/v1/autopilot/recipes`) | People's own automations, built like monday's: "when status becomes X" or "when a date column is N days away or past", then alert people (owner, leadership, reviewers or addresses) or draft an email (to the vendor, the customer or an address; templates fill `{item}`, `{status}`, `{date}`, `{days}` and any column title). A third trigger, `status_for`, fires when an item has sat in one status (`column`) for `value` days, timed from monday's activity log (~90 days back). Each belongs to one board (`board_role`, `board_id`) and fires once per item and trigger. `GET /recipes/starters?preview=true` lists 13 Allied starters built from what the boards show (vendor photo, proforma and invoice requests, drawing approvals overdue, long fabrication, idle scans, reviews waiting, quiet vendor quotes, hand-offs to Jane and Athish), each with the fact behind it and the items it would act on today; `POST /recipes/starters/{slug}` adds one. Emails wait for a person unless the recipe is on `auto` and mail is on. Managers edit them; date recipes are checked hourly (`POST /recipes/run-dates` is a dry run by default). Stored in `automation_recipes`. |
| `AUTOPILOT_SCORECARD_MIN` | `GET /api/v1/autopilot/scorecard`: for each play, reaction and recipe, how many drafts people sent, cancelled or let lapse in the last 90 days, with advice (nearly always sent: consider auto; mostly cancelled: change or switch off). |
| Vendor risk digest | Orders not linked to a project that are at risk of missing the customer's date come as one list per person per day; orders linked to a project keep their own alert, critical when the project will be late. |
| Drawing links | An order linked to an unfinished drawing job shows `blocked_by` and "waits on drawing ..." in its reasons (and says when the drawing is past its date). `GET /api/v1/vendors/link-suggestions` offers likely drawings for unlinked orders: same customer (from the order's "CUSTOMER - part" name and the Engineering board's Customer column) and shared words. A person saves the link with `PATCH /api/v1/vendors/{id}` `project_item_id`. |
| `RFQ_FORM_TOKEN`, `RFQ_FORM_PER_HOUR` | Customer request form: `GET`/`POST /api/v1/rfqs/form?token=...` (or the `X-Form-Token` header). No login; a shared token, a hidden honeypot field and an hourly limit per address. Each request becomes an RFQ (`source=form`), which drafts the receipt to the customer. Off while the token is empty. |
| Board timeline (`GET /api/v1/boards/{id}/timeline`) | The board as monday's Kanban (lanes in monday's label order and colours), Gantt (Scan Date to Estimate finish; Lead Start to VENDOR ETS with the Target Date; RFQs from received to quote due) and Calendar (every deadline between `start` and `end`, default this month and the next two). |
| `RFQ_ACK_BUSINESS_HOURS`, `RFQ_QUOTE_DAYS_*`, `RFQ_TEAM_EMAILS` | Quote deadlines, and who hears about new RFQs. `BUSINESS_DAY_START/END` and `BUSINESS_HOLIDAYS` define a working day. |
| `ASSISTANT_MCP_SERVERS` | Third-party tools for the assistant, as JSON (see `.env.example`). Header values like `env:NAME` are read from the environment. |
| `SAGE_LIVE_CONNECTION` | Leave off: import files are the supported way into Sage. (Replaces `SAGE_SIMULATION_MODE`, which is still honoured.) |

### Roles

`VIEWER` reads. `OPERATOR` submits documents, adds shipments, runs checks and processes orders. `MANAGER` sees everyone's alerts, sends the summary and downloads Sage files. `ADMIN` manages alert contacts. The role is read from the account, not the login token, so a disabled, removed or re-roled account takes effect within 30 seconds rather than when the 8-hour token expires.

---

## Background workers

All three start with the API and stop with it. Each loop survives any single failure and records it.

| Worker | Does | How often |
|---|---|---|
| monday inbox | Retries failed board events; reads the jobs board for changes webhooks missed | `MONDAY_POLL_SECONDS` (300) |
| Coordination | Syncs the projects board, runs schedule and shipment checks, sends held pushes and texts after quiet hours, weekday summary | change check every `MONDAY_CHANGE_CHECK_SECONDS` (90, working hours; 900 outside), full re-read every `PROJECTS_POLL_SECONDS` (3600), `SCHEDULE_CHECK_SECONDS` (900), summary at `DIGEST_HOUR` |
| Mailbox | Reads the order mailbox (off unless configured) | as configured |

---

## Tests

```bash
python -m pytest -q
```

Results: 424 passed, 1 skipped. The skipped test calls the live local AI and runs only when Ollama is up. No test calls Groq: its API is faked, and any key in `.env` is ignored during tests.

```bash
python tests/run_end_to_end_use_cases.py
```

Results: all 60 end-to-end business scenarios pass.

```bash
cd frontend && npm run lint && npm run build
```

Results: clean lint and a production build.

Tests and scenarios each use their own temporary database, data files and upload folders, never anything under `data/`.

### What is not in git

`.env` (secrets), `data/` (the live database, uploads, design-document submissions, backups), `output/` (packing slips), `.claude/` and Python caches are ignored. Copy `.env.example` to start a new install; back up `data/` with `scripts/backup.py`.

---

## Architecture

One FastAPI process (`--workers 1`) holds the API, three background workers and every automation. monday and Sage hold the data; nothing automates inside them.

```
             monday (read only)        Gmail (IMAP in, SMTP out)      ntfy / Slack / Teams
                   |                            |                              ^
   change check 90 s, full read hourly   mailbox poller 120 s                  |
                   v                            v                              |
  +---------------------------- FastAPI process ---------------------------------------+
  |  Coordination worker (30 s loop)    monday inbox worker     Mailbox worker          |
  |   sync boards -> board copy           webhook sweep 20 s      order / RFQ emails    |
  |   checks -> alerts (notify) ---------------------------------------------------------+--> people
  |   autopilot plays + date recipes      board read 300 s                              |
  |                                                                                     |
  |  Events: status change -> reactions + recipes (1 ordered worker, queue of 500)      |
  |  Drafts -> confirm queue (assistant_actions) -> a person sends or cancels           |
  +---------------------------------------- SQLite (WAL) -------------------------------+
```

- **Data.** One SQLite file in WAL mode (`data/allied_enterprise.db`), migrated by Alembic on start. Main tables: orders, vendor_jobs, rfqs, shipments, monday_projects and milestones, notifications, assistant_actions (drafts and confirmed actions), automation_recipes, audit_log.
- **Kept in memory, rebuilt after a restart:** the board copy and monday activity log (refilled by the next board read, within the hour), recent events, the RFQ form's rate limit, the email outbox counters, reconciliation's dispute memory, and caches of users and MCP tools.
- **Automations, four kinds, one queue.** Timed plays (`autopilot.py`), instant reactions to status changes (`events.py`), people's own recipes (`recipes.py`), and the behind-schedule checks (`schedule.py`). All of them either tell staff (an alert) or put a draft in the confirm queue. Each draft has a fixed key, so a repeat never sends twice, and a reviewer sees the email and why it was drafted.
- **Reading all mail.** The mailbox reader goes through every folder in `IMAP_FOLDERS` (on Gmail, `[Gmail]/All Mail` is everything) and records each message once in the mail log (`mail_log` table, `GET /api/v1/mail`), by its Message-ID, without marking anything read. What it does with each: orders and requests for quote go to their trackers, vendor replies become one-click updates, forwarder emails update shipments, delivery questions, complaints and supplier invoices raise an alert, and internal mail, sent mail and the rest are logged with a summary. Mail older than `IMAP_ACT_ON_HOURS` (the backlog on first start) is logged only, so old threads can't raise orders or RFQs. To keep the feed quiet: every email gets rule-based tags first (rfq, po, invoice, shipping, urgent, ...; `MAIL_TAG_RULES` adds more); newsletters, auto-replies, bounces, invites and no-reply notifications are recognised from their headers and logged as noise with no AI read and no alert; only `MAIL_ALERT_NOW` (complaints, delivery questions, anything urgent) alerts at once, and a whole conversation shares one alert a day; everything else that needs a person waits in the feed and the daily summary. `GET /api/v1/mail` hides quiet mail unless `include_quiet=true` and filters by `tag`. One login reads one mailbox; to cover the whole team, route a copy of everyone's mail to it (Google Admin, Gmail routing).
- **Engineering notes from management.** `POST /api/v1/engineering-notes` (managers): a note on one job (a monday item) goes to that job's engineers on the board, or to people picked; Mandarin (`zh`) and Vietnamese (`vi`) translations are optional, made with the welding glossary and attached under the English, which counts. Normal notes alert in the app, by email and by push; urgent ones are critical, so they also go by text and push at any hour. Engineers mark a note read (`POST /{id}/read`); once everyone has, its alert closes. `GET /api/v1/engineering-notes?item_id=|board_id=|mine=true` lists them. Texts need an SMS provider (Twilio) set up; push needs ntfy. **Voice:** `POST /api/v1/engineering-notes/voice` (a recording from a phone or desktop browser: webm, ogg, m4a/mp4, mp3, wav, up to 25 MB) is written out by speech-to-text (Groq Whisper), translated if asked, and kept: `GET /api/v1/engineering-notes/{id}/audio` plays it for anyone signed in. If it can't be written out, it still goes, marked "please play it". Any text box can dictate through `POST /api/v1/ai/transcribe`.
- **Point-and-click automations.** `GET /api/v1/autopilot/recipes/options?board_role=` gives a rule form every choice from the board (status labels in monday's colours, date columns, quotable fields, people, email targets); `POST /api/v1/autopilot/recipes/preview` shows what an unsaved rule would act on today.
- **Closing the loop with vendors.** A vendor's answer to a follow-up ("ships 12 Nov", "shipped yesterday", photos) is read from the mailbox (`coordination/replies.py`): matched to the order by Allied's PO number, read by the AI with a plain date reader as fallback, every date checked against the email, and offered as "KWS says PO 9277 ships 12 Nov. Update the order?" for one click. Replies with no clear date become an alert with the vendor's words. A date a person confirms is kept through board reads until someone changes VENDOR ETS on monday.
- **Approving from a phone.** Each new draft is pushed to ntfy with Send and Review buttons (`assistant/quick.py`). The links are signed per draft and reviewer and lapse with the draft; opening one decides nothing, only a POST does. The buttons need `APP_BASE_URL` to be an address phones can reach. `POST /api/v1/assistant/actions/batch` sends or cancels up to 50 drafts at once for keyboard review, and each waiting draft carries a `kind` for grouping.
- **Integrations.** monday over GraphQL, blocked from writing by `MONDAY_READ_ONLY`; Sage by import files; Gmail by SMTP and IMAP (draft-only until a login is set, redirected while testing); ntfy, Slack and Teams for alerts; Groq for AI (Ollama or Claude as fallbacks); MCP servers as assistant tools.

## Code layout

```
app/
  api/v1/endpoints/    REST routes, one file per area (coordination.py: projects, vendors, RFQs,
                       shipments, alerts, boards, autopilot, recipes, the customer RFQ form)
  core/                settings checks, auth and roles, audit log, database, upload safety
  services/
    coordination/      boards (board copy), activity (history, time in status, timeline),
                       projects, vendors, rfqs, shipments, schedule (checks + worker),
                       autopilot (plays), events (reactions), recipes (people's rules),
                       board_automations, changes (change check), notify (alerts)
    assistant/         chat agent, its tools, MCP connections, the confirm queue
    ai/                provider layer (Groq / Ollama / Claude), features, drawing check, speech
    monday_*.py        monday API client and the board-event inbox
    sage_*.py          Sage import files; SDK writer (not live)
    ...                orders, documents, bills, mill certs, CSV import, exceptions, email in/out
  data/                catalogues (items, customers, vendors), aliases, glossary, MTRs
  config.py            every setting, with defaults
migrations/            Alembic schema history (one head)
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
- **Runs on one server only:** sales order numbers, the submission lock, every background worker and the in-memory state above live in the process. A second API worker or container would run every automation twice. Don't run several against one database.
- **After a restart:** the board copy and monday history are empty until the next board read (up to an hour). Screens that use them say "not read yet" rather than fail.
- **Indexes:** several often-filtered columns have no index yet (assistant_actions status/role/created, orders status/created, exceptions, audit log). Fine at today's size; worth a migration as history grows.
- **Texts:** carrier email-to-text gateways are shut down. Real texts need Twilio with A2P 10DLC registration; ntfy push is the free option.
- **Engineer instructions:** the job brief, read-back and sign-off flow is designed but not built.
- **Groq free tier:** about 30 requests a minute, 1,000 a day and 8,000 tokens a minute on the text model (check your Groq console for your exact limits). A long drawing's text can exceed one minute's tokens on its own. Busy periods get a plain "try again in N seconds" or the fallback engine. The scan-reading model is a Groq preview and may change.
- **Vendor board layout:** assumed one item per vendor PO with a connect-boards column to the projects board. Not yet tried against Allied's real vendor board.
- **Assistant on the free tier:** each question re-sends the tool list and conversation, so a long chat uses up Groq's 8,000 tokens a minute quickly; it keeps the last 10 messages. MCP servers are listed again every 10 minutes.
- **Large uploads without a size header:** a call recording sent without a Content-Length is still written to disk in full before the 25 MB limit applies.
