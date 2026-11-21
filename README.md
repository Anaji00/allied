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
| **Jobs and purchase orders** | One job per customer request, from RFQ through design approval to the vendor PO and shipment (`/api/v1/jobs`, `/api/v1/purchase-orders`). A PO is drafted, edited until sent, approved by a manager (refused until the design is approved), numbered on approval, then sent as a draft email. `PO_APPROVAL_LIMIT` (default 0 = off): above it, or for a PO with no prices, only an ADMIN approves. `approved_by_creator` and a job check `self_approved` flag a PO approved by its drafter. Customs details per line can be corrected after sending (`PATCH /{id}/customs`; `customs_changed_after_send` is set). Revisions are compared per line (`revision_marks`). | `coordination/jobs.py`, `purchase_orders.py`, `order_documents.py` |
| **Import paperwork** | From a saved PO: a commercial invoice and a packing list for goods from China or Canada into the US or Canada. `GET /api/v1/purchase-orders/{id}/import-paperwork`, `/commercial-invoice.pdf`, `/packing-list.pdf`, each with `destination` (US default, or CA) and `currency` (no default). Blank details are listed as missing and never guessed; PDFs say INCOMPLETE until nothing is missing. The packing list carries no vendor, customer, price or SO number. Operators only. Prepares paperwork; files nothing, no duty maths. | `app/services/import_paperwork.py` |
| **Bring in from Sage** | Reads Sage 50 export files (File > Export templates): customers, vendors and parts, sales invoices and receipts, open sales orders and POs, vendor bills. `GET /api/v1/sage-import/kinds`, `POST /preview` (operators; saves nothing, returns a token and what would be new, changed, unchanged, closed or a problem), `POST /commit` (managers; the same file twice changes nothing), `GET /history`. Columns are matched by header name; unrecognised headers, amounts with a non-USD currency, ambiguous dates and unreadable cells become plain problem rows, never guesses. Saved as read-only mirrors (`sage_invoices`, `sage_payments`, `sage_open_orders`, `sage_bills`, `sage_imports`; migration `n7d9f1a3b5c7`) that show Paid / Part paid / Unpaid and amount due on orders, flag a bill number Sage already holds, and warn when an order is already open in Sage. Never writes to Sage. | `app/services/sage_import*.py`, `endpoints/sage_import.py` |
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

The side menu has three groups. Phones get a bottom bar (Boards, Today, Orders, Projects) and "More" opens the rest.

| Screen | Path | For |
|---|---|---|
| Boards | `/` | Each monday board as monday shows it, with "Needs fixing" (how clean the board is), notes to the engineers, an owner picker (staged for monday, never sent from here) and waiting changes shown as a green diff |
| Jobs | `/jobs`, `/jobs/<ref>` | One job from the customer's request to the shipment: research, engineering, design approval, purchase orders to vendors, the sales order, shipments, drafts |
| Today | `/inbox`, `/mine`, `/ball` | One menu item, three tabs: **Inbox** (alerts folded into areas with "Done with all", problems, drafts to approve), **My day** (everything waiting on me), **Who has the ball** |
| Orders | `/orders` | Orders and packing slips in view; order emails, supplier bills and suppliers under More |
| Projects | `/projects` | Milestones, design documents to submit or review, what is behind; plan a new job from a template |
| Shipments | `/shipments` | Freight on the way, flags, forwarder emails |
| Vendors | `/vendors` | Vendor jobs against project dates, what to chase, vendor scorecard |
| Carrier prices | `/carriers` | Your rate card and live carrier prices (operators) |
| RFQs | `/rfqs` | Quote requests, deadlines, owners, turnaround and win rate; "Open the job" starts a job from one |
| Wear & quotes, Leads | `/sales/pipeline`, `/sales/leads` | Equipment wear and quotes; target accounts and Find prospects in view, calculator and proposal under More |
| Mill certificates | `/compliance/vault` | Heat numbers and quarantines |
| People & alerts | `/alerts` | Alert history, daily summary, who gets told, user accounts |
| System | `/health` | Health with a "Set these first" card for managers; **What runs by itself** (one list, Off / Suggest / Auto per item); monday changes (staged, preview, push); under More: the monday.com link, required forms, job templates |
| Sage & imports | `/operations` | Sage import files and list imports (managers) |
| Audit trail | `/admin/audit` | Who did what, and when (managers) |
| Search everything | Ctrl+K and the header box | One search across orders, RFQs, jobs, vendor jobs, shipments, comments, files, people and monday rows |
| Document tools | `/chat` (from the assistant's "Other tools") | Read a document, translate, email, drawing checks, compare drawings, phone calls, part names, glossary, tasks |
| Comments and Files | on every order, RFQ, vendor job, shipment, milestone and board row | Threads with @mentions (translated for each reader), attached files, owner and "ball is with" |

**Names in the lists.** Every owner, customer, vendor, forwarder and carrier box suggests what the app already knows. The seven engineers (`frontend/src/lib/engineers.ts`) are always in every people list; one without an email on file shows greyed out ("no email yet") until it is added under People & alerts. Emails ending `.invalid` are placeholders and are never offered.

**Waiting changes.** Anything the app has staged for monday (`/api/v1/monday-push`) shows on the board table: a changed cell has a green background with the old value struck through, and new rows sit in a green block under the table. The match is by board, item and column id when the backend sends them, and by board name, item name and column title otherwise; it clears once the change is sent or skipped.

Every screen sits behind sign-in and works at phone width. The assistant opens from the button at the bottom right of every screen.

---

### Words the system uses

| Word | Means | Seen by |
|---|---|---|
| **RFQ** | A customer's request for a quote. Customers send these. | Staff |
| **Customer reference** | The number on the order a customer sends when they accept a quote (the board's "Customer PO#" column; in the code `customer_po_number`). Shown to the customer as "Your reference". | Customers and staff |
| **Sales order (SO)** | Allied's internal document for the sale to the customer. Its number is never shown to a customer. | Staff only |
| **PO (purchase order)** | What Allied issues to a **vendor** (for example HELEN on the board). | Vendors and staff |

The AI is told these words too (`_COMPANY` in `ai/features.py`, `TERMS` in `assistant/agent.py`), so drafts and answers say "customer reference", never "PO", for a customer's number. The names inside the code and the API (`customer_po_number`) are unchanged.

## Features added in November 2026

Built around one aim: less chasing and fewer misunderstandings between management, the two in-house engineers (one works in
Mandarin, one in Vietnamese), the vendors (mostly Chinese, some American) and the customers. Everything here is safe by default:
nothing is sent to monday, to a customer or to a vendor without a person approving it, and `MONDAY_READ_ONLY` stays on.

- **Slip queue:** every new sales order has its packing slip queued the moment the order is created and made straight away, as a job of its own. If the PDF fails, the order still stands; the job is retried after 1, 5 and 15 minutes, then marked FAILED and the reviewers get one alert. `GET /api/v1/packing-slips/queue` lists jobs (QUEUED, READY, FAILED, PRINTED); `POST /packing-slips/{so}/retry` and `/printed` are for operators. Needs the `packing_slips` table (migration `d1f7b3a9e5c2`); without it, slips are made directly as before.
- **Our sales order number is internal:** the slip, the shipment email, the customs invoice number and the download name show the customer's PO, never Allied's SO number.
- **FedEx:** `FEDEX_*` settings (off by default). `GET /api/v1/fedex/status`, `POST /fedex/check` (logs in, buys nothing) and `GET /fedex/quote?address=...&weight_lbs=...` give live Ground/Express prices; dispatch shows them next to the other carriers. Prices only: no labels, no booking. FedEx's rates API does not cover FedEx Freight (LTL), which stays an estimate. The client has not yet been run against FedEx: enter the sandbox credentials, press check, and compare a quote with FedEx's own before relying on it.

- **More free AI per day:** Groq counts the free daily limit per model, so `GROQ_FALLBACK_MODELS` (default `openai/gpt-oss-20b,qwen/qwen3.8-27b`) lists models to try in order when the main one is used up: about 3,000 requests a day in all instead of 1,000. A model that reports a limit is skipped until Groq's wait is over. Checked against Groq's rate-limit page on 2026-11-06; the older Llama models are no longer on the free plan, so they are not listed.

- **Downloads:** `GET /api/v1/exports` lists them; `GET /api/v1/exports/{orders|packing-slips|carrier-prices|vendor-jobs|rfqs|shipments}?format=xlsx|csv|pdf` and `GET /api/v1/exports/boards/{board_id}?format=...` (the app's read-only copy of a monday board). Read-only copies built in the app, never sent anywhere. Cells that start with `=`, `+`, `-` or `@` are written as text so Excel can't run them. Costs and margins are in the orders file for managers only. Excel needs `openpyxl` (in requirements.txt).
- **Live exchange rates:** CAD and CNY rates come from a free public service (Frankfurter, ECB rates; no key, no Allied data sent), kept 12 hours. If it is off (`FX_LIVE_RATES=False`) or unreachable, the fixed `CAD_TO_USD_RATE` / `CNY_TO_USD_RATE` are used. Used by carrier quotes and customs invoices.

- **Task forms:** a design submission can't go in until its form is filled in properly. Each form is a list of fields (written answers with a minimum length so "ok" doesn't pass, choices, dates, numbers, and "I checked X" boxes that must be ticked). A starter form, "Design documents release", applies to milestones with design, drawing, release, submittal or approval in the name. Managers add, change, switch off or remove forms (`/api/v1/forms/templates`, saved in `data/task_templates.json`); the screen can ask what's still missing with `POST /forms/templates/{id}/check`. The answers are saved with the submission and written into its note for the reviewer. A refused submission returns 422 with one plain sentence per field.

- **Updates and @mentions:** one comment thread per order, RFQ, vendor job, shipment, milestone or board item, with one level of replies (`/api/v1/updates`). Writing `@athish` (first name, email name or full name) or picking someone from `/updates/people` alerts them with a link back; two people with the same first name are not guessed (the screen asks which). Replying alerts the person replied to. Authors edit their own comments; authors and managers remove them (hidden, not erased). `/updates/mentions` is a person's "you were mentioned" list and `/updates/counts` gives badge numbers for lists. The threads are the app's own: nothing is written to monday. Needs the `item_updates` table (migration `e2a8c4d6f1b3`, not yet run on the real database).

- **Owner and "ball is with":** any order, RFQ, vendor job, shipment, milestone or board item can have an owner and a ball holder (one of our people, or an outsider: vendor, customer, carrier). Handing the ball over (`PUT /api/v1/ball`) restarts the clock, alerts the person receiving it and writes a line in the item's comment thread. Chasing runs by itself in working days since the ball moved (`BALL_NUDGE_DAYS` 2, `BALL_ESCALATE_DAYS` 4, `BALL_LEADERSHIP_DAYS` 7): remind the holder (for an outsider, tell the owner to follow up), then tell the owner, then leadership; a passed due date goes straight to the owner. Each step is told once per hand-over. `GET /ball/open` lists everything with a ball, longest wait first; open RFQs and vendor jobs nobody set by hand are worked out from what the app knows (and are already chased by the autopilot plays). `GET /ball/summary` is the management view. People's morning brief lists what is waiting on them. Needs the `item_balls` table (migration `f3b9d5e7a2c4`, not yet run on the real database).

- **Language bridge:** each person has a language (English, Mandarin or Vietnamese; `PUT /api/v1/language/me`, managers can set anyone's). Comment threads come with a translation for every comment written in another language than the reader's (`translation`, `author_language`, `needs_translation`), so a Mandarin or Vietnamese engineer and an English manager each read and answer in their own. A form's questions, choices and instructions translate with `GET /forms/templates/{id}/translated?lang=zh|vi` (the answers stay in English). It uses the Translate tool's engine and glossary (`data/ai_glossary.json`: add the company's terms there), keeps results in `data/translation_cache.json` so nothing is translated twice, translates at most 8 new comments per read, and shows the original with a flag if the AI is busy. Needs `users.language` (migration `a4c6e8b1d3f5`, not yet run on the real database). Translation can be wrong: unsure terms come back in `unsure`, and the original is always shown.
- **Notes and comments together:** the updates thread of a job (`board_item`, `milestone` or `vendor_job`) now also contains management's engineering notes (`kind: "note"`, with urgency, unread flag, voice and the reader's translation), so one thread tells the whole story. Marking a note read is unchanged.
- **Project templates:** `GET/POST/PUT/DELETE /api/v1/workspace/project-templates` (managers edit). A template is a list of milestones with working days after the start, a default owner role and an optional form. `POST .../{id}/plan` gives the dated plan for a start date, and `.../plan/file` the same as an Excel file monday can import. The app creates nothing in monday. The starter template is an example with guessed durations.
- **My items and saved filters:** `GET /api/v1/workspace/my/items` gathers what's waiting on the signed-in person (ball, mentions, unread notes, their RFQs and vendor jobs, drafts to approve); `GET/POST/DELETE /api/v1/workspace/views` keeps named filters per screen (30 per person; managers can share one with everyone).

- **Reading messy real-world paperwork:** scanned pages and phone photos are put the right way up before reading (the phone's own rotation note, then the other three ways up if the first reading comes out as gibberish, and small fax-quality images are enlarged); a packet longer than the app reads says which pages were not read. A PO number is found as people write it ("PO 9921", "P.O. No. 99-23", "PO#9921", "Purchase Order: 4500123") and is never made up: with none found the field says NOT FOUND and a person enters it. An unknown customer stays unknown (it is no longer turned into Owens Corning), no line items are invented when none match the catalogue, a quantity is never read from a price, and payment terms come only from "Net 45"-style text (otherwise the customer's usual terms, marked unchecked). A customer PO that arrives again with different quantities is flagged to the reviewers as a revised PO (the existing order is not changed). Chinese vendors' replies are read in their formats (2026年11月20日, 11月20日, 2026/11/20, 12.11.2026, 已发货, 照片); a slash date that could be day-first or month-first (12/11) goes to a person instead of into the order.

- **RFQ email to approved drafts:** an RFQ email arrives, the AI reads it (customer, items, summary), and the app logs the RFQ, then queues two drafts for the reviewers (nothing leaves until a person approves each): the acknowledgement to the customer, and a row for the Open RFQs board filled in from what the AI read (name, customer, details in Notes; the person can edit every field in the queue). One grouped alert says how many drafts are waiting. Confirming the row creates it on monday only when monday is writable: while `MONDAY_READ_ONLY` is on, the answer says nothing was added and gives the row as text to add by hand. Once added, the row is linked to the RFQ so the board read doesn't make a second one. Settings: `MONDAY_RFQ_BOARD_ID`, `RFQ_BOARD_NEW_STATUS_LABEL` (a status that exists on the board; blank = none), `RFQ_BOARD_NOTES_COLUMN_ID`; reaction `rfq_board_row` (off / suggest / auto) under `AUTOPILOT_REACTIONS`. **If you ever turn monday writes on, this is a path that will add rows (after a person confirms each one).**
- **No automatic translation on outgoing mail:** acknowledgements and vendor drafts are plain English (vendors are mostly Chinese, some American; the app never assumes). Translation is for the two in-house engineers and on request only.
- **Compare CAD drawings:** `POST /api/v1/drawings/compare` (two files, optional page numbers) is for the engineers who compare the other side's drawings against Allied's own CAD drawings, which arrive in different formats. **Formats it reads: PDF (exported or printed from CAD), PNG, JPG, TIFF and DXF (any version; a slightly damaged one is repaired where possible).** **DWG cannot be read** (a closed format with no free reader: save or export it as DXF, or print to PDF) and **STEP/IGES 3D models cannot be compared yet** (export the 2D drawing views as DXF or PDF); each gets a 400 that names the file and says what works. A DXF is compared by what is in it, with every size converted to millimetres whatever units it was drawn in: overall size, each circle's diameter, each dimension value (reported as only-on-first / only-on-second, inches against millimetres included); note, revision and material text is compared too. Any two files get a red/blue overlay picture (red = only on the first, blue = only on the second, grey = the same; `/compare/overlay.png` returns just the picture) with the changed areas listed, after both are cropped to their content (paper size and position don't matter), fitted to the same size (scale), turned the right way up (a sheet sent sideways or upside down), and given a little slack for line-weight differences. Warnings call out the differences that cause mistakes: different units, first-angle against third-angle projection, a different revision, material, drawing number or scale, a different overall size. Overall size and each hole are only compared in millimetres between two DXFs; between a DXF and a PDF the picture, text and numbers are compared. No AI is used and nothing is saved.

- **Every outgoing email is a draft first:** the purchase order to a vendor (when an order is released with "Send PO" on the board) and the shipment notice to the customer (when it is dispatched) are queued in the approval queue like the RFQ acknowledgement and the price request to the vendor; reviewers hear once that drafts are waiting. The vendor's email carries the PO number and what to make, never our sales order number or the customer. `VENDOR_PO_MODE=auto` sends the purchase order at once, as before; the default is `suggest` (a draft). The whole flow ("hey, make me this part": RFQ, price request to the vendor, quote won, sales order and PO to the vendor, engineering thread and board updates, shipment and customer notice) is tested end to end in `tests/test_flow_make_me_this_part.py`.
- **Writing to monday, like git (off for now):** the app can write to monday, but nothing is sent until a manager pushes, and nothing is sent at all while `MONDAY_READ_ONLY` is on. Every change the app wants to make (a comment on an item, column values, a new row such as the RFQ row) is STAGED in the `monday_changes` table when monday is connected: a saved, plain-words to-do. `GET /api/v1/monday-push/status` says whether writing is on and how many changes wait; `GET /changes` lists them; `GET /preview` shows each one next to what monday shows now (from the app's last read of the board); `POST /push` (managers; body `{"ids": [...], "dry_run": true}` optional) sends them, one at a time, in the order they were made; `POST /changes/{id}/skip` drops one; `/retry` puts a failed one back. A push while read-only sends nothing and says so. Staging the same item's columns twice leaves one change with the latest values; the same comment twice is one comment; a change nobody pushes for `MONDAY_STAGE_DAYS` (30) days expires. `MONDAY_PUSH_MODE=manual` (default) waits for a push; `auto` sends as changes are made once writing is on (and stages them while it is off). Nothing in the app or the push endpoints can switch `MONDAY_READ_ONLY`: only the owner can, in `.env`. **To enable writing later:** set `MONDAY_READ_ONLY=False` (start with a test board), look at `GET /preview`, then `POST /push`. Needs the `monday_changes` table (migration `b5d7f9a1c3e2`, not yet run on the real database); until it exists, writes fall back to the old behaviour (blocked while read-only).

- **One search for everything:** `GET /api/v1/search?q=...` looks across orders, RFQs, vendor jobs, shipments, comments, people and the rows of the monday boards (the app's own copy, so it is instant and reads nothing from monday). Every word must match, in any order; case and accents are ignored for names; results come grouped by kind with where to open each.

- **File sharing:** attach files to any order, RFQ, vendor job, shipment, milestone or board item, or to a comment on it (`POST /api/v1/files`, multipart: `item_type`, `item_ref`, `upload` (up to 10 files), optional `update_id`). `GET /files?item_type=&item_ref=` lists them, `GET /files/{id}/download` downloads (always as an attachment with `nosniff`, so a browser never opens or runs it), `DELETE` hides one (whoever attached it, or a manager). `GET /files/allowed` gives the allowed kinds (`ATTACHMENT_EXTENSIONS`: PDF, DWG/DXF/STEP/IGES, SolidWorks, Office files, pictures, CSV/TXT, ZIP) and the size limit (`SUBMISSION_MAX_MB`, 50). Programs are refused whatever they are called, a name can't escape the attachment folder, the same file attached twice to an item is kept once, and 100 files per item is the cap. Comments in a thread carry their `files`; the one search finds files by name. Files stay on this server and are never sent to monday. Needs the `item_files` table (migration `c6e8a0b2d4f6`).
- **Formula columns that aren't blank:** monday can't hand over what its formula columns work out (they come through empty, and out of an Excel export blank). Where the app works the same thing out itself (VENDOR ETS, Target Date, Vendor Days Left, Customer Days Left on 2026 Orders), the empty cells are filled with the app's figure and the column is marked `computed_by_app`, in board views and in downloads. A value monday did give is never replaced.

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

- **What staff get:** a tab bar at the bottom with Boards, Today (with the waiting count), Orders and Projects. "More" opens the full menu. The assistant button sits above the tab bar.
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
| `PO_APPROVAL_LIMIT` | Purchase-order value above which only an ADMIN may approve (0 = no limit). Counts the number as written; it does not convert currencies. |
| `TRUSTED_PROXY_NETS` | Networks whose forwarded client address is believed, for the sign-in lockout. Narrow it if the office uses 172.16/12. |
| Sign-in protection | Per address and per account lockout (429 with Retry-After), password rules, and a password stamp in the token so a changed password ends old sessions. Held in memory: a restart clears the locks. |
| Startup checks | Production refuses to start with `AUTH_DEV_BYPASS` or default secrets. A non-production start warns about default secrets. Before a pending migration the database is copied to a `pre-migrate` folder (last 5 kept). |
| `MONDAY_API_TOKEN`, `MONDAY_SIMULATION_MODE` | monday stays in simulation (no reads or writes) until you turn it off. |
| `MONDAY_READ_ONLY` | On by default. With simulation off, the app reads monday boards but sends **no change of any kind**: every GraphQL mutation is blocked in the client (twice: before the call and at the transport) and logged as blocked. Writes back to monday (submission statuses, vendor dates, comments, order statuses) are then skipped. Turn it off only for a test board. |
| `MONDAY_WEBHOOK_TOKEN` / `MONDAY_WEBHOOK_SECRET` | Webhook authentication: a URL token, or the monday app's signing secret. |
| `MONDAY_PROJECTS_BOARD_ID` + `PROJECT_*` / `MILESTONE_*` columns | Which board holds projects, and which columns are owner, due date and status. Any column setting takes the column id or its title as shown on the board (e.g. `"Estimate finish"`). |
| `LEADERSHIP_EMAILS`, `NTFY_*`, `SMS_PROVIDER` / `TWILIO_*` | Who gets escalations, and the push and text channels. |
| `FORWARDER_DOMAINS` | Email domains whose messages are read as shipment updates. |
| `MONDAY_PUSH_MODE`, `MONDAY_STAGE_DAYS` | `manual` (default): every change the app wants to make on monday is staged until a manager pushes it; `auto` sends as they happen. Staged changes expire after 30 days. Nothing is sent while `MONDAY_READ_ONLY` is on. |
| `MONDAY_RFQ_BOARD_ID`, `RFQ_BOARD_NEW_STATUS_LABEL`, `RFQ_BOARD_NOTES_COLUMN_ID` | The Open RFQs board the reviewed RFQ row is staged for. |
| `FEDEX_*`, `CARRIER_RATE_STALE_DAYS` | FedEx connection (off, sandbox by default; prices only) and how old a rate-card price may be before it says "confirm". |
| `FX_LIVE_RATES`, `FX_CACHE_HOURS` | Live CAD/CNY rates from a free public service; the fixed `CAD_TO_USD_RATE` / `CNY_TO_USD_RATE` are the fallback. |
| `GROQ_FALLBACK_MODELS` | Groq models tried in order when the main one has used its free daily limit. |
| `BALL_NUDGE_DAYS`, `BALL_ESCALATE_DAYS`, `BALL_LEADERSHIP_DAYS` | Working days before whoever holds the ball is reminded, the owner is told, and leadership is told (2, 4, 7). |
| `ATTACHMENT_EXTENSIONS`, `SUBMISSION_MAX_MB` | Which files can be attached to items and how big (50 MB). Programs are always refused. |
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

## The database file: only the app opens it

**Never open `data/allied_enterprise.db` from Windows (a script, a smoke check, DB Browser, a test, `alembic`, `python -c "import app.main"`) while the app is running.** The file lives on a Docker bind mount. SQLite's write-ahead log keeps its shared memory in a side file that does not work across the Windows/container boundary, so two processes (one on the host, one in the container) can each believe they are the only writer. Pages are then lost and the file is damaged (a fresh opener fails with "malformed database schema" or "invalid rootpage"; the running app keeps working from its warm connections until it restarts).

- The **backend container is the only process allowed to open the real file.** `docker-compose.yml` sets `ALLIED_ALLOW_REAL_DB=1` for the backend service only. Without it the app, `alembic` and every helper refuse to open or migrate `data/allied_enterprise.db`, and they always refuse under pytest. Do not set that variable anywhere else.
- To make a backup, use the app's own (System > Backups, or `POST /api/v1/health/backups/run`), or run the script **inside** the container: `docker compose exec backend python scripts/backup.py`. Do not run it from Windows against the bind-mounted file while the app is up.
- For a smoke check or a script, point `DATABASE_URL` at a temporary file (`sqlite:///C:/temp/x.db`).
- At start-up, in `/api/v1/health` (as `database.file_check`, status `damaged`) and before each backup the app reads the schema, runs SQLite's quick check and reads one row of every table. A damaged file is reported loudly (a critical alert and a log line) and is never repaired automatically. If you see it: do not restart the app and do not rebuild the container; copy the newest good backup to a temporary folder, check it, and swap it in with the app stopped.

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

Results (16 Nov 2026): 2,394 passed, 2 skipped, 2 xfailed, about 14 minutes. The suite includes 18 adversarial rounds, whole-flow scenarios (`tests/test_flow_make_me_this_part.py`), import paperwork and startup-safety tests. The skips need a live local AI; the xfails are known, accepted limits. No test calls Groq, monday or email: they are faked, and any key in `.env` is ignored during tests.

```bash
python tests/run_end_to_end_use_cases.py
```

Results: scenarios 1 to 18 pass. It stops at scenario 19 (the monday webhook handshake) when a webhook token is set in `.env`; that check predates the November work. Run `alembic upgrade head` after pulling new code.

```bash
cd frontend && npm run lint && npm run build
```

Results: clean lint and a production build.

Tests and scenarios each use their own temporary database, data files and upload folders, never anything under `data/`.

### What is not in git

`.env` (secrets), `data/` (the live database, uploads, design-document submissions, backups), `output/` (packing slips), `.claude/` and Python caches are ignored. Copy `.env.example` to start a new install; back up `data/` with `scripts/backup.py`.

---

## Gmail: what IT does and what you do

Allied uses Google Workspace (Gmail). Two things can connect to it. The first works today. The second is designed and not built yet.

### 1. The shared mailbox (works today)

One Gmail account (for example an orders or office address) is read by the app and used to send approved drafts. Nothing is sent without a person approving it.

**IT does:**
1. Turn on 2-Step Verification for that Gmail account.
2. Create an **App password** for it (Google Account, Security, 2-Step Verification, App passwords). Give you the 16-character password once.
3. Check that IMAP is allowed for the account (Google Workspace Admin, Gmail, End user access, POP and IMAP).

**You do (in the `.env` file next to `docker-compose.yml`, never in chat or email):**
1. `IMAP_USERNAME` and `SMTP_USERNAME` = the full Gmail address. `IMAP_PASSWORD` and `SMTP_PASSWORD` = the app password.
2. `IMAP_POLL_ENABLED=True` to start reading mail. Leave it `False` until you are ready.
3. `MAIL_OUR_DOMAINS=alliedweldingsystems.com`, and `APP_BASE_URL` = the address staff use (phones need it for approval links).
4. Restart the backend (`docker compose up -d backend`). Mail older than `IMAP_ACT_ON_HOURS` is logged only, so old threads cannot create orders.

Until then mail stays in draft-only mode and nothing leaves the building.

### 2. Each person's own Gmail (planned, not built yet)

Each person connects their own mailbox so their drafts and "needs a reply" list come from their own mail. It reads only (`gmail.readonly`), never sends, and disconnecting deletes the saved token and the cached mail. Each person sees only their own mail.

**IT does (once):**
1. In Google Cloud Console, create a project for Allied, for example "Allied Operations".
2. Turn on the **Gmail API** for that project.
3. Set up the **OAuth consent screen** with User type **Internal** (only people in your Workspace domain can connect). Add the scope `gmail.readonly` only.
4. Create an **OAuth client ID** of type **Web application**. Add the redirect address: your app address followed by `/api/v1/me/mail/callback`, for example `https://allied.example.com/api/v1/me/mail/callback`. It must be a secure (https) address the staff's browsers can reach.
5. Hand you the **client ID** and **client secret** privately. If your IT prefers company-wide approval instead of each person clicking Allow, they can do that in Admin console, Security, API controls, App access control.

**You do (in `.env`, planned setting names, final names will be listed here when the feature ships):**
1. `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `GOOGLE_REDIRECT_URI` = the three values from IT.
2. A long random `MAIL_TOKEN_KEY` (the key that encrypts each person's saved sign-in). The app never makes or prints this key for you. Without it the feature stays off.
3. Restart the backend. Each person then presses **Connect my Gmail** and clicks Allow once.

**The AI part:** AI reply drafts send the text of a message to Groq (a US service). In the Groq console, under Data Controls, turn on **Zero Data Retention** if you want nothing stored there, then set `ALLIED_GROQ_ZDR_CONFIRMED=true`. Each person opts in under their own mail settings (off by default), and a sender, folder or word on a person's exclude list is never sent to the AI.

---

## Presenting it

Rehearse and present on a **demo copy**, never the live database. Nothing here touches `data/`, `output/` or `.env`.

```bash
python scripts/demo_seed.py --db C:/demo/allied_demo.db
```

```bash
python scripts/demo_run.py --db C:/demo/allied_demo.db --port 8010
```

- The seeder refuses the real database and anything inside `data/` or `output/`. It builds a demo database, its own uploads, output and catalogue copy, and a `.demo.env` that turns mail, monday, Sage and AI off, so approving a draft reaches nobody. Vendor addresses are `.test` addresses. `--replace` rebuilds a demo made by the script. It prints four sign-ins (`demo-admin@`, `demo-manager@`, `demo-operator@`, `demo-viewer@alliedweldingsystems.com`) and one throwaway password, shown once.
- It seeds customers, RFQs, jobs, purchase orders, a shipment, a sales order and packing slip, four drafts waiting for the manager and admin, and believable alerts (overdue quote, port free time ending, vendor late), so the Today inbox is full. `demo_run.py --check` shows what it will use without starting anything.
- Point the frontend at `http://localhost:8010` (`NEXT_PUBLIC_API_URL`), for example `NEXT_PUBLIC_API_URL=http://localhost:8010 npm run dev` in `frontend/`. The Docker frontend on the live port talks to the live backend, so it is not the demo.
- **Do not run `demo_reset.py`.** It deletes the live database and now refuses unless given `--delete-real-database`.
- The AI is off in the demo copy (no key), so skip the assistant. Everything else works.

**Before going on stage**
- Sign in as the **manager** and the **admin** in separate browsers first. Five wrong passwords lock an account for 15 minutes, and the right password is refused until it ends.
- Demo as **manager or admin**. Operators get a refusal on design approval and PO approval, and a viewer sees vendor names and PO numbers.
- Use a real catalogue part for a sales order (an invented part name is refused with the closest matches).
- The vendor email needs an address on the PO (the demo data has one). Mail is draft-only: approving records the send and sends nothing.
- Customs papers: choose the currency (`?currency=USD` or the picker) and fill tariff code, origin, unit and weight, or the invoice reads INCOMPLETE. That is a good thing to show: the system lists what's missing and guesses nothing.
- Avoid live: a monday item's comments (503 while monday is not connected).

**Suggested 15-minute path:** RFQ in, open the job, approve the design (manager), PO draft with prices, approve it (the demo copy sets `PO_APPROVAL_LIMIT=10000`, so the seeded PO above that needs the admin account), customs papers for China into Canada, shipment, the Today inbox (snooze, hand over), packing slip, then the audit trail. Close on what is not done (monday and Sage not live, customs not filed).

**Checked and not checked:** the whole job flow has an automated end-to-end test (`tests/test_flow_jobs_end_to_end.py`) and the demo seeder has its own (`tests/test_demo_seed.py`). Over-the-limit approval, the customs card, and alert snooze/hand-over were built in the screens but not clicked through on the deployed backend before this was written: rehearse those three once.

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
- **The assistant does what a person can.** About 37 tools cover the platform: orders and packing slips, material certificates, leads, the review queue, mail, bill matches, the audit trail, engineering notes, automations, vendor jobs and shipments, drafts, and a monday push. Each question is offered only the tools for the topics in it (about 14 to 22 of them), because the free AI tier can't carry them all on every step. It chains up to 8 steps, so one request can look things up, queue changes and draft an email. Reads run at once and **changes never do**: each becomes a proposal; the changes from one request form one plan with a "Confirm all", and a draft is saved at once because it sends nothing. Replies carry cards (`cards`: Open Record, Print Packing Slip, Edit & Send Draft, Push to Monday, Cancel), and `PATCH /api/v1/assistant/actions/{id}` edits a waiting draft. Pushing to monday only happens when asked, after a click, and while `MONDAY_READ_ONLY` is on the guard refuses it.
- **Voice agent.** `POST /api/v1/assistant/voice-command` takes a recording (or dictated words), writes it out (Groq Whisper), runs the same agent, and returns `{transcript, reply_text, spoken_text, spoken, tool_actions_executed, action_cards, plan}`. `spoken` is one short sentence for the device to read aloud (`VOICE_SPOKEN_REPLY=browser`, free); the full answer and cards stay on screen. Send `confirm_ids` with the waiting proposals and say "confirm" or "cancel" to decide them by voice. A bare "yes" cannot send an email or push to monday: those need "send it" and "confirm push to monday".
- **Engineering notes from management.** `POST /api/v1/engineering-notes` (managers): a note on one job (a monday item) goes to that job's engineers on the board, or to people picked; Mandarin (`zh`) and Vietnamese (`vi`) translations are optional, made with the welding glossary and attached under the English, which counts. Normal notes alert in the app, by email and by push; urgent ones are critical, so they also go by text and push at any hour. Engineers mark a note read (`POST /{id}/read`); once everyone has, its alert closes. `GET /api/v1/engineering-notes?item_id=|board_id=|mine=true` lists them. Texts need an SMS provider (Twilio) set up; push needs ntfy. **Voice:** `POST /api/v1/engineering-notes/voice` (a recording from a phone or desktop browser: webm, ogg, m4a/mp4, mp3, wav, up to 25 MB) is written out by speech-to-text (Groq Whisper), translated if asked, and kept: `GET /api/v1/engineering-notes/{id}/audio` plays it for anyone signed in. If it can't be written out, it still goes, marked "please play it". Any text box can dictate through `POST /api/v1/ai/transcribe`.
- **Point-and-click automations.** `GET /api/v1/autopilot/recipes/options?board_role=` gives a rule form every choice from the board (status labels in monday's colours, date columns, quotable fields, people, email targets); `POST /api/v1/autopilot/recipes/preview` shows what an unsaved rule would act on today.
- **Closing the loop with vendors.** A vendor's answer to a follow-up ("ships 12 Nov", "shipped yesterday", photos) is read from the mailbox (`coordination/replies.py`): matched to the order by Allied's PO number, read by the AI with a plain date reader as fallback, every date checked against the email, and offered as "KWS says PO 9277 ships 12 Nov. Update the order?" for one click. Replies with no clear date become an alert with the vendor's words. A date a person confirms is kept through board reads until someone changes VENDOR ETS on monday.
- **Approving from a phone.** Each new draft is pushed to ntfy with Send and Review buttons (`assistant/quick.py`). The links are signed per draft and reviewer and lapse with the draft; opening one decides nothing, only a POST does. The buttons need `APP_BASE_URL` to be an address phones can reach. `POST /api/v1/assistant/actions/batch` sends or cancels up to 50 drafts at once for keyboard review, and each waiting draft carries a `kind` for grouping.
- **Writing to monday works like git.** The monday client does not send a change when connected: it stages it (`monday_changes`), a manager previews it against what monday shows now, and pushes it, one change at a time. `MONDAY_READ_ONLY` still blocks every send at the transport, and nothing can change that setting but the owner.
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
    assistant/         chat agent, its tools, MCP connections, the confirm queue, voice
    coordination/      (also) updates (comments), ball (whose turn), forms, files, project_templates, secretary
    monday_push.py     staged changes to monday and the push; search.py, exports.py, bridge.py (languages),
                       fx.py, fedex.py, carrier_rates.py, drawing_compare.py + drawing_dxf.py, packing_slip_queue.py
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

Two accepted behaviours, kept as expected failures in the adversary tests (`tests/test_adversary_round16.py`, `round17.py`) so they stay visible:
- **Known-good sign-in addresses:** only a *correct* password adds an address to the table that lets a real user in during a lockout, and the table is capped, so a stranger cannot fill it; a very large flood from many real accounts could still evict an old entry. Accepted: it needs working passwords.
- **Snoozing an urgent alert:** one recipient snoozing a critical alert hides it from the other recipients and the managers' roll-up until the snooze ends. Mitigated: an urgent alert can be snoozed for 24 hours at most. A per-person snooze would need its own table; not built.


- **monday projects board:** not yet tested against Allied's real board; the board ID and column IDs need setting. If monday returns no items for a board the system already holds, the sync stops and removes nothing.
- **Sage writes:** import files only; nobody has yet done a test import into a Sage sample company.
- **Runs on one server only:** sales order numbers, the submission lock, every background worker and the in-memory state above live in the process. A second API worker or container would run every automation twice. Don't run several against one database.
- **After a restart:** the board copy and monday history are empty until the next board read (up to an hour). Screens that use them say "not read yet" rather than fail.
- **Indexes:** several often-filtered columns have no index yet (assistant_actions status/role/created, orders status/created, exceptions, audit log). Fine at today's size; worth a migration as history grows.
- **Texts:** carrier email-to-text gateways are shut down. Real texts need Twilio with A2P 10DLC registration; ntfy push is the free option.
- **CAD formats:** drawing compare reads PDF, pictures and DXF. DWG is a closed format with no free reader (export DXF or PDF) and STEP/IGES 3D models can't be compared yet.
- **Translation can be wrong:** it uses the free AI and the engineering glossary (`data/ai_glossary.json`; add the company's terms there). Unsure terms are marked and the original is always one tap away. Nothing is translated automatically on outgoing mail.
- **FedEx** has only been tested against a fake FedEx: enter the sandbox credentials, press Check connection, and compare one quote with FedEx's own before relying on it. FedEx Freight (LTL) isn't in FedEx's rates API, so freight prices come from the rate card, which someone must keep current.
- **Writing to monday is staged, not live:** nothing is pushed while `MONDAY_READ_ONLY` is on; the push has only been tested against a fake monday. Try it on a test board first.
- **PDF downloads** show Vietnamese accents only with the font in the Docker image (DejaVu); Excel and CSV always do.
- **Groq free tier:** about 30 requests a minute, 1,000 a day and 8,000 tokens a minute on the text model (check your Groq console for your exact limits). A long drawing's text can exceed one minute's tokens on its own. Busy periods get a plain "try again in N seconds" or the fallback engine. The scan-reading model is a Groq preview and may change.
- **Vendor board layout:** assumed one item per vendor PO with a connect-boards column to the projects board. Not yet tried against Allied's real vendor board.
- **Assistant on the free tier:** each question re-sends the tool list and conversation, so a long chat uses up Groq's 8,000 tokens a minute quickly; it keeps the last 10 messages. MCP servers are listed again every 10 minutes.
- **Bring in from Sage** is verified only against Sage's own Import/Export template field names (2023 help pages). Printed report headers (A/R and A/P aging, ledgers) and file encodings are not verified: try one real export with the owner before relying on it. Open-order mirrors close only when a newer full export of the same kind arrives.
- **Import paperwork is prepared, not filed:** no duty table, importer-of-record or ISF fields, and no landed cost. The currency is chosen by the person.
- **Approval limit ignores currency;** alert snooze is per alert, not per person (urgent alerts cap at 24 hours).
- **demo_reset.py deletes the live database file (after a backup copy).** Never run it to prepare a demo; use the demo copy described under Presenting it.
- **Large uploads without a size header:** a call recording sent without a Content-Length is still written to disk in full before the 25 MB limit applies.
