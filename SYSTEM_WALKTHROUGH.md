# Allied Operations: a plain-English walkthrough

**Who this is for:** Allied's leadership, engineers and office staff. No technical knowledge needed.

**In one sentence:** one system follows every job from the customer's first email to the delivered shipment, does the repeat office work, and stops mistakes before they cost money. monday.com stays the place where projects are listed and Sage 50 stays the books.

---

## Ready now, and one step away

| Ready now (shown in the demonstration) | One step away (needs a decision or a connection) |
|---|---|
| Follow each job from request to shipment on one page | Connect to your real monday boards (read only first) |
| Purchase orders drafted, checked and approved by the right person | Try a test import into a Sage sample company |
| Customs invoice and packing list prepared, with gaps listed | Your customs broker still files; we prepare, not file |
| Late vendors, late shipments and overdue quotes raised as alerts | Add everyone's name, email and phone for alerts |
| Every email waits for a person to approve | Set real passwords and the dollar limit for approvals |

---

## The big picture

Allied designs the parts and has them made overseas, mostly in China and some in Canada. Most of the work is coordination: has the customer's request been answered, is the drawing done and checked, has the vendor been told what to make, is the container moving, is anything late, and who needs to know.

The system does four jobs:

1. **Follows each job from start to finish.** Request for quote, design, vendor purchase order, customs papers, shipment, packing slip. One page per job shows where it stands and whose turn it is.
2. **Keeps projects on schedule.** It reads your monday projects board, sees what is late or coming up, and tells the right person.
3. **Makes checks happen properly.** A second person has to approve a design before a vendor is told to make it. A manager has to approve every purchase order, and large ones need an administrator.
4. **Does the office paperwork.** Purchase orders, the customs invoice and packing list for goods coming in, packing slips for customers, checking vendor bills, and the files Sage imports.

monday and Sage are where the information lives. The system is set to **read monday only**: it can look at the boards but cannot change anything on them until that is deliberately switched on. Every reminder, check and automatic step happens in this system, so Allied does not pay monday for automations.

**Three promises you can rely on:**
- **Nothing goes out by itself, as shipped.** Every email to a customer or vendor is a draft that a person approves first. A purchase order to a vendor always waits for a person. Managers and administrators can choose to let a particular routine step send on its own (Auto), for example a reminder to a vendor. Each one is a deliberate choice, and every automatic send is recorded in the audit trail with the reason.
- **Missing details are shown, not made up.** For example, if a part has no tariff code, the customs invoice is stamped INCOMPLETE and lists exactly what to fill in, instead of the system picking a code that looks right.
- **Allied's own sales order number stays inside Allied.** Customers and vendors never see it.

---

## One job, start to finish

This is the path to follow in a demonstration. Each step is a screen the staff really use.

| Step | What happens | Where |
|---|---|---|
| **1. A customer asks** | An email arrives asking for a quote ("make me this part"), or someone enters it by hand. It becomes a request with an owner and a deadline. A draft reply to the customer waits for a person to approve. | **RFQs** |
| **2. Start the job** | "Open the job" turns the request into one job page. | **RFQs**, then **Jobs** |
| **3. Research and design** | The engineers work on it. Comments, files and @mentions sit in one thread, and a Mandarin or Vietnamese speaker reads each comment in their own language. | **Jobs** |
| **4. Design approved** | A manager approves the design. Until that happens, no vendor can be sent a purchase order. | **Jobs** |
| **5. Purchase order to the vendor** | The order is drawn up with lines, prices and terms in Allied's own layout. A manager approves it and the PO number is assigned (a PO is Allied's written order to a vendor). The email to the vendor waits as a draft. It shows what to make, never the customer or Allied's sales order number. | **Jobs** (PO section) |
| **6. Customs papers** | For goods from China or Canada, the system prepares the **commercial invoice** and the **packing list** from the purchase order. It lists anything still missing (tariff code, country of origin, weights, currency). | **Jobs** (PO section) |
| **7. Shipment** | The container or air shipment is tracked. Late, slipping, missed sailing and port storage charges all raise alerts. | **Shipments** |
| **8. Sales order and packing slip** | Allied's sales order is created, and the packing slip is made. The customer sees their own reference number, never ours. | **Orders** |
| **9. Nothing falls through** | Anything late or stuck lands in the Today inbox, and the person whose turn it is gets told. | **Today** |

### Purchase orders: who may approve what

- The person who drafts a purchase order can edit it freely until it is approved.
- A **manager** approves it. Approval is refused until the design is approved.
- Allied can set a **dollar limit**. Above it, only an **administrator** can approve, and the screen says so plainly. A purchase order with no prices yet also needs an administrator once a limit is set. With no limit set, any manager can approve.
- If the person who drafted the order also approved it a while later, the job page shows a gentle warning so a second person can look it over.
- Once an order is sent, its lines cannot be changed. Customs details can still be corrected, and the page shows that they changed after the order was sent.
- A cancelled order needs a reason and says whether the vendor already has it, so someone tells them.
- If a send is interrupted halfway, an alert asks a manager whether it went. The job page also checks the quote is still valid, the customer's order matches the quote, the margin is sensible, and the vendor email does not leak the customer's name.
- Each line records which drawing revision the vendor acknowledged, so an old drawing is not made by mistake.

### Customs papers for goods coming in

For goods made in **China or Canada** and arriving in the **US or Canada**, the system prepares two documents from the approved purchase order:

- **Commercial invoice** (the bill-like paper customs uses to value the goods): vendor, buyer, each line with quantity, unit price, tariff code, country of origin and total, the shipping terms, and the document date (the day the order was approved).
- **Packing list:** quantity, weight and description only. It is built to carry **no vendor name, no prices, no customer and no sales order number**, because it travels on the outside of the shipment, and it warns if the vendor's or customer's name appears in a line's text.

What to say if asked: **the system never invents customs details.** If the tariff code, origin, unit of measure, weight, shipping terms or currency are blank, the document says INCOMPLETE across its face and the screen lists exactly what to fill in. The **currency is chosen by the person** (US dollars, Chinese yuan, Canadian dollars and so on) because the system will not assume it. A note on the page reminds the team to confirm the paperwork with the customs broker, and flags the two usual questions: whether Canadian goods qualify under the trade agreement, and the extra Canadian tax on some Chinese goods.

**What it does not do yet:** it does not file anything with customs, work out the duty, or say who is legally responsible for the import. Your customs broker (the agent who clears goods through customs) still does that.

---

## A normal week

**Monday, 7:00 am.** Leadership gets one summary email:

> *3 behind, 2 shipment issues, 1 waiting for review*
> - Vulcan wear plate kit: Fabrication drawings (6 days late, no design docs submitted)
> - Container TGHU7654321 (Shanghai to Houston): port free time ends Thursday
> - Due this week: Load calculations, Customer design review

That one email replaces chasing everyone for updates.

**Tuesday.** Eli's "Design drawings" milestone is due in two days, so Eli gets a reminder. Nobody else is bothered yet.

**Wednesday.** Eli uploads the drawings in the **Projects** screen with a short note. The project owner gets a request to review.

**Thursday.** The owner opens the drawings and either:
- **approves** them, so the milestone is marked done; or
- **sends them back** with a note saying exactly what to change (for example *"Hole spacing should be 800 mm, not 805"*). Eli gets that note straight away.

Nobody can approve their own drawings.

**Friday.** The forwarder emails "vessel delayed at Busan, new ETA the 14th". The system reads it, finds the container number, updates the shipment and tells its owner. If the email is unclear or matches no shipment, a person is asked to look instead of the system guessing.

---

## Vendors: catching lateness before it happens

Each vendor purchase order is a job. The system works backwards from the project's due date: take off the time at sea (about 45 days door to door from China) and a week's buffer, and that is the date the vendor **must ship by**. Then it compares that with when the vendor will really ship: the vendor's promised date, or, before there is one, what this vendor has actually taken on past jobs (not what it quoted).

It flags a job when:
- **it will make the project late:** leadership is told straight away, with the options (expedite, split the shipment, air freight);
- **the vendor is late:** the promised date has passed and nothing has shipped;
- **it is at risk:** only a few days to spare, or this vendor usually runs late;
- **the purchase order isn't confirmed:** no confirmation 2 business days after it went out;
- **it's a week from shipping:** a reminder to ask the vendor to reconfirm.

The **Vendors** screen also has a scorecard: how often each vendor ships by its *first* promise (a vendor that keeps moving the date doesn't get credit for the new one), how many days late it runs, and how its real lead time compares with what it quotes.

---

## RFQs: no request goes unanswered

Every request for a quote gets a number (RFQ-2026-0001), an owner and a deadline counted in working hours:

| | Deadline |
|---|---|
| Reply to the customer that it is being quoted | 4 working hours |
| Quote a repeat or catalogue part | 1 business day |
| Quote a standard job | 2 business days |
| Quote a new design or anything needing a vendor's price | 5 business days |

Waiting on a vendor's price still counts against the deadline (the customer doesn't care why). The owner is reminded to chase the vendor after 2 days, and it is escalated after 4. Waiting on the customer pauses the clock. Overdue quotes go to the owner and, after a day, to leadership. The **RFQs** screen shows how long quotes take, how many go out on time, and the win rate. A lost or not-quoted request needs a reason, so the win rate can be explained.

---

## Who gets told what

People hear about their own work first. Leadership is only told when something is seriously late or costs money.

| What happened | Who is told | How |
|---|---|---|
| Milestone due in the next 2 days | Its owner | App + email |
| Milestone late | Owner and project owner | App + email + phone push |
| Milestone 2+ days late | The same people, plus leadership (one message) | App + email + phone push |
| Drawings waiting 2+ days for review | The reviewers | App + email |
| Whole project past its due date | Project owners and leadership | Everything, including a text |
| Shipment late, slipping, missed its sailing, or the forwarder has gone quiet | Shipment owner (leadership too when late) | App + email + phone push |
| Port free time ending (daily storage charges start) | Shipment owner and leadership | Everything, including a text |
| Vendor's date will make a project late | Job owner, project owners and leadership | Everything, including a text |
| Vendor late, or PO not confirmed | Job owner and project owners | App + email + phone push |
| New RFQ, or one not acknowledged in 4 working hours | RFQ owner, or the sales team | App + email |
| Quote overdue | RFQ owner, plus leadership after a day | Everything |

**Rules that stop alerts becoming noise:**
- **Each alert is sent once**, not every time the system checks.
- **It closes itself** when the problem goes away.
- **Nights and weekends are quiet.** Texts and pushes wait until 7 am unless it's urgent.
- **Everything lands in the Today inbox.** Press **Mark handled** when it's dealt with.
- **Snooze and hand over:** an alert can be snoozed for a few hours or days (urgent ones for at most 24 hours), or handed to a named colleague, who is told.

---

## The assistant

The **Ask** button at the bottom right of every screen opens a chat. Ask it *"Which vendors could make the Vulcan job late?"* or *"What RFQs are overdue?"* and it looks them up.

It can also prepare a change, for example *"Email the customer that the quote comes Friday"*. It never makes the change itself: it shows exactly what it would do, and nothing happens until you press **Do it**. People who can only view can't make changes this way. It also takes **voice commands** and can read job, board and purchase-order information; any board change it suggests is only staged, never sent to monday. It runs on a free AI service, so it can be busy at times; the shortcut buttons work even then.

---

## The screens

| Screen | What you do there |
|---|---|
| **Boards** | Read each monday board as monday shows it. "Needs fixing" says how clean it is. Changes waiting to go to monday show in green and are not sent until a manager sends them. |
| **Jobs** | Follow one job from the customer's request to the shipment. Purchase orders, customs papers and the sales order all live here. |
| **Today** | **Inbox**: alerts folded into areas with "Done with all", problems and drafts to approve. **My day**: everything waiting on you. **Who has the ball**: whose turn it is and for how long. |
| **Orders** | Orders and packing slips. Order emails, supplier bills and suppliers are under More. |
| **Projects** | Milestones and what's late. Submit drawings or review someone else's. Plan a new job from a template. |
| **Shipments** | Every shipment and what's wrong with it. |
| **Vendors** | Every vendor order against its project's dates, and each vendor's on-time record. |
| **RFQs** | Every request for quote, who owns it, when it is due. |
| **Wear & quotes, Leads** | Equipment wear and replacement quotes; target accounts. |
| **Mill certificates** | Heat numbers and quarantines. |
| **People & alerts** | Alert history, who gets told and how, and user accounts. |
| **System** | Health, "Set these first", and **What runs by itself** (each automatic step set to Off, Suggest or Auto). |
| **Sage & imports, Audit trail** | Sage import files, and a record of who did what and when. |
| **Search** | Ctrl+K searches orders, RFQs, jobs, vendors, shipments, comments, files and people at once. |

Dropdowns for owners, customers, vendors and carriers suggest what the system already knows, so you pick instead of typing.

---

## Orders and Sage

Sage 50 stays the books. The system **prepares** what goes into it and can **read what Sage already holds**; it never writes into Sage.

**Bringing information in from Sage.** Someone exports a list or report from Sage 50 (customers, vendors, parts, invoices and payments, open orders, vendor bills) and drops the file on the **Sage & imports** screen. The screen first shows, in plain words, how many rows are new, changed, already there or a problem, and saves nothing. A manager then presses **Save these rows**. Sending the same file twice changes nothing. Afterwards each order shows **Paid, Part paid or Unpaid in Sage** and the amount due, a vendor bill Sage already holds is caught before it is paid twice, and an order already open in Sage is flagged so it is not created again. A file the system cannot read cleanly (a strange column name, an amount in another currency, a date that could be day-first or month-first) is listed as a problem for a person, never guessed.

Each day someone downloads the two **Sage import files** (sales orders and vendor purchase orders) from **Sage & imports** and imports them into Sage 50. Each file holds only what hasn't been sent to Sage yet, so nothing is entered twice, as long as every downloaded file is imported. If a download didn't save, press **Put them back** and those orders will be in the next file.

**Vendor bills** are checked against what Allied ordered and agreed to pay: the price per unit, each line's total, freight, the bill's total, that the vendor is the one on the PO, and that the invoice hasn't been submitted before. If everything matches, the bill is approved. If anything is off, it's held and the difference is shown. A dispute letter goes to the vendor at most once. A bill that matches no PO goes to a person, never back to the vendor.

---

## Where AI helps, and where it doesn't

The AI runs on Groq's free service. It costs nothing and answers in a few seconds, but it has daily limits, and when it is busy the screen says so. What it reads (emails, drawings, call recordings) is sent to Groq's US servers.

**It helps with:** translating instructions for the two engineers, reading incoming emails and suggesting a reply, comparing drawings, and summarising phone calls.

**It never acts alone.** Anything that matters is a draft or a review item until a person approves it. When it isn't sure, it says so.

---

## Safety, in plain words

- **Sign-in is protected.** Repeated wrong passwords lock that account and address for a short while, passwords must be strong, and changing a password signs out the old sessions.
- **Roles are checked every time.** Viewers read. Operators prepare and submit. Managers approve. Administrators approve large orders and manage accounts. A removed or demoted person loses access within 30 seconds.
- **Everything is on the record.** The audit trail shows who did what and when, including purchase order approvals and edits to customs details after sending.
- **The database is backed up** before any upgrade changes it, and a copy is kept.
- **Passwords must be changed before real use.** The system ships with a placeholder administrator password. It warns until a real one is set, and an install marked as production refuses to start without one.

---

## What's not done yet (say this plainly)

- **The Sage import is tested on sample files made to Sage's own export layout, not yet on Allied's real exports.** Try one real export first.
- **monday and Sage are not connected to live data yet.** The system reads monday only once connected, writes nothing, and has not yet had a trial import into a Sage sample company.
- **Customs papers are prepared, not filed.** No duty calculation, importer-of-record or advance shipping notice yet; the customs broker still files.
- **The approval limit does not convert currencies.** If the limit is 10,000, an order of 50,000 yuan (about 7,000 dollars) is still held for an administrator, because only the number is compared. That errs on the safe side; making it currency-aware is a small change if you want it.
- **Alert contacts need adding**, and real text messages need a Twilio account. Phone push works for free.
- **The instruction check** (an engineer repeats a brief back and signs off) is designed but not built.
- **It runs on one computer.** If that computer fails, the system stops until it is restored from the copy made before each upgrade (plus any second copy you set up); your monday boards and Sage are not affected. The person who looks after the system restores it. A written restore guide and a named backup person are still to agree.

---

## Plain meanings

| Word | Means |
|---|---|
| **RFQ** | A customer asking "what would you charge to make this?" |
| **Vendor / forwarder** | The factory that makes the part / the freight company that ships it |
| **Port free time** | Days the shipping line lets a container sit at the port before charging daily storage |
| **Missed its sailing** | The ship left without the container (the booked departure passed with no word the cargo was loaded) |
| **Quiet forwarder** | The freight company hasn't sent an update for several days |
| **Tariff code (HTS)** | A number customs uses to decide what the goods are and what duty applies |
| **Incoterm** | Who pays for shipping, and where the risk passes (for example FOB) |
| **Suggest / Auto** | Suggest: the system writes a draft and waits for a person. Auto: it does the routine step itself |
| **Groq** | The company whose free AI service reads emails and drafts replies |
| **Docker / ODBC** | Technical plumbing that only the person looking after the system needs |

## Not yet answered here (the presenter should be ready)

- **What it costs to run each month and who looks after it.** The AI is free; the system runs on one office computer. The maintenance arrangement is for you to agree, so no figure is given in this document.
- **How much time it saves.** The saving depends on how long an order takes by hand today. Measure one real week before quoting a number.
- **Translation is a help, not a ruling.** Comments are translated for the Mandarin and Vietnamese engineers, the original is always one tap away, and uncertain terms are marked.
- **The AI reads email text on Groq's US servers.** Groq can be set to keep nothing (zero data retention). Decide the policy before real customer mail is read.
- **Who holds which role** (viewer, operator, manager, administrator) is decided by Allied; the demonstration accounts are examples.

---

## Starting it

Whoever looks after the system starts it with Docker (steps in the [README](README.md)). Staff open the web address in any browser, on a computer or phone, and sign in with their own account. **Rehearse on the demo copy, never the live database** (see "Presenting it" in the README).
