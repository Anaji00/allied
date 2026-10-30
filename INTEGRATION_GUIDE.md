# monday.com → Sage 50 Automation: Plain-Language Guide

*Review and patch, 27 Oct 2026*

## 1. What this system does

People run jobs on a monday.com board. When someone changes an item's status, monday sends a message (a **webhook**) to this system. The system then does the office work: it raises the Sage 50 sales order, splits out the vendor purchase orders, prints the packing slip, and posts a note back on the monday item.

It works like a mailroom: monday sends the mail, this system opens it and does the paperwork, and Sage 50 is the filing cabinet.

---

## 2. What we found (most serious first)

| # | Problem | What it meant in real life |
|---|---|---|
| 1 | **Almost any change on the board created a new order.** The system only looked at the "Shipped" and "Approved" labels. Every other change (renaming an item, editing a date, posting a comment) fell through to "make a new sales order". | Junk sales orders in Sage. When an item name didn't fit the expected pattern, the customer defaulted to **Owens Corning**. |
| 2 | **The webhook security check could never work.** It expected a signature that monday doesn't send. | With the secret set, every real monday message was rejected. Without it, *anyone on the internet* could trigger orders and shipments. |
| 3 | **Messages could be lost or done twice.** Work started in memory after replying "OK" to monday. A restart lost it. monday re-sending a message did the work again. | Missing orders after a restart, and double shipments. |
| 4 | **Status labels were matched loosely.** Any label containing the letters "SHIP" counted as shipped. | A label like "Not shipped yet" would have **dispatched a shipment**. |
| 5 | **"Live Sage" mode reported success while writing nothing.** It returned a blank order number and marked the order "Synced to Sage". | Staff would believe orders were in Sage when they weren't. |
| 6 | **Packing slips never found their order.** The order was filed as `12930` but looked up as `SO-12930`. | Every packing slip was addressed to "Valued Allied Customer" with no line items. |
| 7 | **Email orders posted comments to random monday items.** The system made up a fake monday item number for each email order. | On a live account, comments would land on whatever real item happened to have that number. |
| 8 | **The monday connection used a retired API version (2024-01)** and gave up immediately when monday asked it to slow down. | Board updates would start failing silently. |
| 9 | **Going live on Sage also switched on board writes, and vice versa.** The two were tied together. | You couldn't test one without the other. |
| 10 | **The Sage import file used the wrong date format** (2026-10-27 instead of 10/27/26). Every export also included every order again. | Import errors, or the same sales order imported twice. |
| 11 | **"Approved for Production" claimed the POs were released** but did nothing. | False confidence that suppliers had their POs. |

---

## 3. What we changed

**A proper inbox for monday messages.** Every message is saved to the database *before* the system replies "OK". If monday sends the same message again (it retries every minute for 30 minutes), the system recognises it and doesn't redo the work. A background worker retries anything that failed, waiting longer each time. After six tries it stops, puts the item in the **Exceptions** queue, sends a Slack/Teams alert if one is set up, and posts a short note on the monday item.

**Clear rules instead of guesswork.** Only one column (the **Status** column, set in configuration) triggers anything, and only exact labels count:

| When Status becomes… | The system… |
|---|---|
| `Ready for Sage` / `New Order` | Creates the Sage sales order and vendor POs |
| `Approved for Production` / `Send PO` | Emails the POs to suppliers (or drafts them if email is off) |
| `Shipped` / `Dispatched` | Makes the packing list and dispatches the order. It does this **once per item**, even if someone flips the status back and forth |
| Anything else | Nothing. It's logged as "Ignored" so you can see it arrived |

Missing details (customer, PO number or item) now stop the order with a clear message. The system no longer guesses.

**Security that matches what monday sends.** There are two options:
- **Simple board webhook:** add a long secret to the web address you give monday (`...?token=XXXX`).
- **monday app webhook:** monday signs each message, and the system checks the signature.

In production the endpoint refuses all traffic until one of these is set up.

**No loops.** The system ignores changes it made itself, and refuses to write to the column that triggers it.

**Honest Sage status.** "Live" mode now fails loudly instead of pretending. The import file uses Sage's date format and includes the customer PO and line numbers. A new "only new orders" option makes sure each order reaches Sage once.

**A new screen: "monday.com link".** It shows every message from the board, what it did, what is stuck and why, and a **Replay** button for after you've fixed the item. It also has a setup checklist and a "send a test event" form.

**A simpler layout.** The navigation is lighter and grouped into four sections with plain names. Decorative badges that made unearned claims ("SOC 2", "ISO", "AI Scout") are gone. A yard switcher that didn't filter anything is gone too. The app now works on phones and tablets with a slide-out menu, and keyboard focus is visible. The inside of the older pages keeps its existing look; only the frame around them changed.

---

## 4. How it flows now

**Allied's system is the automation layer; monday and Sage only hold data.** Do not build automations inside monday: the Standard plan allows 250 automation actions a month. Besides webhooks, the system reads the board's status column every 5 minutes through the API (`MONDAY_POLL_SECONDS`, about 288 of the 1,000 daily calls). It records where every job stands on the first read, so switching it on never fires old jobs. A change seen by both the webhook and the reader is handled once.

```
monday board ──(webhook)──► check security ──► save to inbox ──► reply "OK" (fast)
                                                     │
                                       worker picks it up
                                                     │
                           which column? which exact label?
                    ┌────────────┬────────────┬─────────────┐
              Create order   Release POs     Dispatch      Ignore
                    │            │             │
                 Sage SO+POs   email POs    packing list
                    └────────────┴─────────────┘
                                 │
           note on the monday item (+ SO# in a column, if set up)
                                 │
        failed? → retry later → still failing → Exceptions queue + alert
```

---

## 5. Turning it on: checklist

**On the server** (`.env`; every setting is explained in `.env.example`):
1. `MONDAY_API_KEY`: a token from a monday user who can edit the order board.
2. `MONDAY_BOARD_ID`: the order board's id, so other boards are ignored.
3. `MONDAY_WEBHOOK_TOKEN`: a long random value (or `MONDAY_WEBHOOK_SECRET` if you build a monday app).
4. `MONDAY_BOT_USER_ID`: the monday user id of the token owner, so the system ignores its own changes.
5. Optional: `MONDAY_SO_COLUMN_ID` (a text column for the Sage SO #) and `MONDAY_SYNC_STATUS_COLUMN_ID` (a *different* status column).
6. Check the label lists match your board exactly (`MONDAY_LABELS_*`).
7. Set `MONDAY_SIMULATION_MODE=False` only after a test event shows correctly on the new screen.
8. Set `APP_ENV=production` on the real server.

**In monday:** board → Integrate → Webhooks → "When a column changes, send a webhook". Use the URL `https://<your-server>/api/v1/webhooks/monday?token=<the same value>`. The server must be reachable from the internet over HTTPS.

**Item naming:** `Customer - PO number - Item`, for example `Owens Corning - PO-77812 - AR400 wear plate`. You can map dedicated columns instead (`MONDAY_CUSTOMER_COLUMN_ID` and similar).

---

## 6. Sage 50: the honest picture

Sage 50 US is desktop software. Our research found three ways to put data into it:

| Way in | Can it write orders? | Notes |
|---|---|---|
| **Import files (CSV)** | Yes, manually | What this system produces today. Someone clicks *File → Import* once a day, which takes about 2 minutes. |
| **Sage .NET SDK** | Yes | The only supported automatic way. It needs a Windows PC with Sage installed, a 32-bit .NET 4.8 program, and an application ID from Sage (sdk.50us@sage.com). Sage doesn't support it from Python. |
| **ODBC database link** | No (read-only) | Good for reading prices and stock. Don't use it to write. |

Zapier doesn't support Sage 50 US desktop, and Make's Sage 50 connector is for the UK product. Products that do this (like Zynk) run a **small agent program on the Sage PC**. If full hands-off posting is worth it, that's the next build: a small C# Windows service beside Sage that picks up the orders this system has already numbered and checked. Until then, the daily CSV import is the reliable path.

> **Before the first real import:** in Sage's import screen, set the field order to match the CSV header, or save a template that does. Test with one order in a **sample company** first. Sage's published list of required fields is known to be out of date, so a trial import is the only reliable check.

---

## 7. Where AI helps, and where to keep it out

**Rule:** AI reads, listens, translates and drafts. A person approves anything that commits money, a date or a specification. Fixed rules, not AI, decide what reaches Sage.

| Area | What AI does | What a person still does | Recommendation |
|---|---|---|---|
| Voice to text | Dictated inspection and NCR notes, call notes, meeting minutes, quick monday updates | Reads it back before it becomes an official record | **Start now** (use headset microphones on the shop floor) |
| Translation (Vietnamese, Mandarin) | Emails, drawing notes, work instructions, supplier notes into English, questions answered in the reader's language | Keeps one controlling version of safety-critical documents; a bilingual reviewer checks those | **Start now** as a reading aid, with a glossary engineering owns |
| Inbound email | Sorts mail (order, quote request, bill, complaint, junk); pulls customer, PO, lines and dates from the email and PDF, with confidence scores | Reviews anything below the confidence bar; Sage entry stays rule-based | **Start now** (builds on the existing mailbox reader) |
| Outbound email | Drafts acknowledgements, chasers, delivery updates and disputes, in the recipient's language | Clicks Send on anything stating a price, date or commitment | **Pilot**: drafts first, auto-send routine mail once accuracy is known |
| Inbound phone calls | Answers after hours or when the line is busy; takes messages; read-only order status; transcribes and summarises; creates or updates the monday job | Calls back on price, dates, changes, complaints; AI never takes orders or quotes by voice | **Pilot**, after hours first, with a recording notice and "press 0 for a person" |
| CAD drawing check (Chinese suppliers) | Lists every difference between the supplier drawing and Allied's master: drawing number, revision, dimensions, tolerances, material, welds, notes; translates Chinese notes; maps GB grades using an engineering-approved table | An engineer signs off; AI never approves a drawing | **Pilot** on about 20 past drawings with known answers; go live only if it catches every real difference |
| Explaining stuck jobs | One plain sentence and the suggested fix per exception | Fixes the job and presses Replay | **Start now** |
| Messy part names | Suggests the catalogue item when the fuzzy match is unsure | Confirms the match; it is remembered | **Start now** |
| Order routing and Sage writes | Nothing | Nothing; fixed rules decide | **Keep AI out** (done) |
| Technical AI Copilot | Specs, procedures, past orders; a natural home for Vietnamese and Mandarin questions | Checks answers against cited sources | **Measure, then decide**; the local Llama 3.2 model is too weak for drawings or translation |
| Lead Engine / AI Scout | Scrapes search results | Not applicable | **Cut** |

**CAD check, in two layers:**
1. When both drawings are native CAD files (DWG, DXF or STEP), use the CAD software's own compare feature. It is exact and needs no AI.
2. For PDFs and scans with Chinese notes and standards, AI builds a side-by-side checklist marked match, different or couldn't read. "Couldn't read" counts as a difference.

Today's drawing reader (`vision_parser.py`) only pulls out the drawing number, revision, SAP code and weight with text patterns. It does not compare drawings.

**Rules for every AI feature:**
- AI drafts and people approve anything binding.
- Every answer shows its source: the email line, the drawing area or the recording timestamp.
- A confidence bar decides what goes to a person.
- AI suggestions and their approvals go in the audit trail.
- Use an AI service under a business agreement that does not train on your data, and never paste drawings into consumer chat apps.
- Track minutes saved and errors caught for each feature.

**Built (Oct 2026), running on local Ollama at no inference cost.** All of the tools above are on the **AI tools** screen (`/ai`); results someone must act on wait in its **To review** tab. Code: `app/services/ai/` (providers, features, drawings, speech, review store) and `app/api/v1/endpoints/ai.py`; settings in `.env.example` under "AI". Measured here: CPU-only, 15-60 s per answer; scanned drawings need `ollama pull qwen2.5vl:7b`; recordings need `pip install faster-whisper`.

**Order:**
1. Voice to text and translation.
2. Inbound email.
3. Explaining stuck jobs.
4. CAD check pilot.
5. Phone pilot.
6. Automatic sending of routine email.

---

## 8. Further improvements (roadmap)

1. **Nightly catch-up check.** monday gives up on a message after 30 minutes of retries. A nightly job should read the board and flag any item whose status says "Ready for Sage" but has no order.
2. **Sage agent on the Sage PC** (section 6) to remove the last manual click.
3. **Webhooks created through a monday app**, so every message carries a signature. monday is retiring its older "custom trigger" features, so build any app against monday *workflows*.
4. **Postgres instead of SQLite** if more than one server runs this system. The inbox design already supports it.
5. **Tidy the older screens** so they use the new lighter style. Seven existing pages also carry pre-existing code-quality warnings.
6. **Rename `SAGE_SIMULATION_MODE`.** "Simulation" is really the supported import-file mode, and the name confuses people.

---

## 9. What was checked, and what wasn't

- ✅ 169 automated tests pass: all 152 that existed before, plus 17 new ones. The new tests cover duplicates, exact-label routing, ignoring the system's own changes, both security modes, production lock-down, retries that eventually give up, one-time dispatch, the Sage date format and no-duplicate export, and packing slips after a restart.
- ✅ A second, independent code review looked for race conditions and found real ones. All of these are fixed and tested:
  - two triggers on the same item at once could ship twice
  - a slow run could be overwritten by its own retry
  - replaying a finished event re-ran it
  - an event that crashed every time looped forever
  - a database save failure was hidden
  - a partial PO release re-emailed suppliers
  - a signed message with no expiry was accepted
- ✅ The web app builds for production. The new screen was checked on desktop and phone width.
- ⚠️ **Still open:**
  - The webhook is only locked down when `APP_ENV=production`. Any other server name with no token set accepts messages from anyone, so set the token everywhere except your own laptop.
  - The *purchase order* import file doesn't yet have the line-count columns or the "only new" option that the sales order file now has.
  - Whether Sage wants sales amounts as positive or negative numbers needs confirming in a test import.
- ⚠️ **Not yet tested against a real monday account or a real Sage company.** The first live run should use a test board and a Sage sample company.
- ⚠️ monday doesn't officially document that its signature uses HS256, or that the message id stays the same when it re-sends a message. Both are confirmed only by community sources. The system also guards against repeats at the order level (one order per PO number, one dispatch per item), so a changed message id still can't cause a double order.

## Sources and playbooks used

- monday webhooks (challenge, JWT, retries): developer.monday.com/api-reference/reference/webhooks
- monday integration authorization: developer.monday.com/apps/docs/integration-authorization
- monday API versions, rate limits, errors, idempotency: developer.monday.com/api-reference/docs/api-versioning, /rate-limits, /errors, /idempotency
- Sage 50 US .NET SDK quick start (v2026.0): Sage Community Hub, SDK forum
- Sage 50 Sales Journal import fields: help-sage50.na.sage.com (Import/Export Fields)
- Webhook receiver best practices: docs.stripe.com/webhooks; Svix "idempotency and deduplication"; microservices.io "transactional outbox"
- Hookdeck guide to monday webhooks (why plain board webhooks are unsigned)
