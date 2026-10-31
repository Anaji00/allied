# Allied Operations: a plain-English walkthrough

**Who this is for:** Allied's leadership, engineers and office staff. No technical knowledge needed.

**In one sentence:** this system keeps everyone on schedule and does the repeat office work, while monday.com stays the place where projects are listed and Sage 50 stays the books.

---

## The big picture

Allied designs the parts and has them made overseas. That means most of the work is coordination: is the drawing done, has someone checked it, has the container left Qingdao, is anything running late, and who needs to know.

The system does three jobs:

1. **Keeps projects on schedule.** It reads your monday projects board, sees which milestones are late or coming up, and tells the right person.
2. **Makes design checks happen properly.** Engineers submit their drawings in the system, and a second person has to approve them before the milestone counts as done.
3. **Does the office paperwork.** Orders, supplier POs, packing slips, checking supplier bills, and the files Sage imports.

monday and Sage are where the information lives. The system is set to **read monday only**: it can look at the boards but cannot change anything on them until that is deliberately switched on. All the reminders, checks and automatic steps happen in this system, so Allied doesn't pay monday for automations.

---

## A normal week

**Monday, 7:00 am.** Leadership gets one summary email:

> *3 behind, 2 shipment issues, 1 waiting for review*
> - Vulcan wear plate kit: Fabrication drawings (6 days late, no design docs submitted)
> - Container TGHU7654321 (Shanghai to Houston): port free time ends Thursday
> - Due this week: Load calculations, Customer design review

That one email replaces chasing everyone for updates.

**Tuesday.** Eli's "Design drawings" milestone is due in two days, so Eli gets a reminder. Nobody else is bothered yet.

**Wednesday.** Eli uploads the drawings in the **Projects** screen with a short note. The monday item updates by itself ("submitted, waiting for review"), and the project owner gets an email asking for a review.

**Thursday.** The owner opens the drawings and either:
- **approves** them, so the milestone is marked done here and on monday; or
- **sends them back**, which needs a note saying exactly what to change (for example *"Hole spacing should be 800 mm, not 805"*). Eli gets that note straight away.

Nobody can approve their own drawings. Only the milestone's reviewer, the project owner, leadership or a manager can approve them.

**Friday.** The forwarder emails "vessel delayed at Busan, new ETA the 14th". The system reads it, finds the container number, updates the shipment and tells its owner the ETA moved. If the email is unclear or matches no shipment, a person is asked to look instead of the system guessing.

---

## Vendors: catching lateness before it happens

Each vendor PO is a job, read from the monday vendor board. The system works backwards from the project's due date: take off the time at sea (about 45 days door to door from China) and a week's buffer, and that is the date the vendor **must ship by**. Then it compares that with when the vendor will really ship: the vendor's promised date, or, before there is one, what this vendor has actually taken on past jobs (not what it quoted).

It flags a job when:
- **it will make the project late:** the vendor's date is after the ship-by date. Leadership is told straight away, with the options (expedite, split the shipment, air freight);
- **the vendor is late:** the promised date has passed and nothing has shipped;
- **it is at risk:** only a few days to spare, or the promise is in time but this vendor usually runs late;
- **the PO isn't confirmed:** no confirmation or date 2 business days after the PO went out;
- **it's a week from shipping:** a reminder to ask the vendor to reconfirm.

The **Vendors** screen also has a scorecard: how often each vendor ships by its *first* promise (a vendor that keeps moving the date doesn't get credit for the new one), how many days late it runs, and how its real lead time compares with what it quotes. The target is 95% on time.

---

## RFQs: no request goes unanswered

Every request for a quote is logged, whether it came by email (picked up automatically) or is entered by hand. Each one gets a number (RFQ-2026-0001), an owner and a deadline counted in working hours:

| | Deadline |
|---|---|
| Reply to the customer that it is being quoted | 4 working hours |
| Quote a repeat or catalogue part | 1 business day |
| Quote a standard job | 2 business days |
| Quote a new design or anything needing a vendor's price | 5 business days |

Waiting on a vendor's price still counts against the deadline (the customer doesn't care why). The owner is reminded to chase the vendor after 2 days, and it is escalated after 4. Waiting on the customer for missing details pauses the clock.

Overdue quotes go to the owner and, after a day, to leadership. The Monday summary lists them. The **RFQs** screen shows how long quotes take, how many go out on time, and the win rate, including whether fast quotes win more often. A lost or not-quoted RFQ needs a reason, so the win rate can be explained.

---

## The assistant

The **Ask** button at the bottom right of every screen opens a chat. Ask it things like *"Which vendors could make the Vulcan job late?"* or *"What RFQs are overdue?"* and it looks them up.

It can also prepare a change, for example *"Mark RFQ-2026-0004 as waiting on Zibo for pricing"* or *"Email the customer that the quote comes Friday"*. It never makes the change itself: it shows exactly what it would do, and nothing happens until you press **Do it**. People who can only view can't make changes this way.

It runs on the same free Groq AI, so it can be busy at times; the shortcut buttons (What is late? Vendor risks, Open RFQs, My alerts) work even then. **Connections** shows what it is linked to: monday, Sage, email, and any other service added through an MCP connection.

---

## Who gets told what

People hear about their own work first. Leadership is only told when something is seriously late or costs money.

| What happened | Who is told | How |
|---|---|---|
| Milestone due in the next 2 days | Its owner | App + email |
| Milestone late | Owner and project owner | App + email + phone push |
| Milestone 2+ days late | The same people, plus leadership (one message, not two) | App + email + phone push |
| Drawings waiting 2+ days for review | The reviewers | App + email |
| Whole project past its due date | Project owners and leadership | Everything, including a text |
| Shipment late, slipping, missed its sailing, or the forwarder has gone quiet | Shipment owner (leadership too when late) | App + email + phone push |
| Port free time ending (daily storage charges start) | Shipment owner and leadership | Everything, including a text |
| Vendor's date will make a project late | Job owner, project owners and leadership | Everything, including a text |
| Vendor late, or PO not confirmed | Job owner and project owners (leadership when 2+ days late) | App + email + phone push |
| New RFQ, or one not acknowledged in 4 working hours | RFQ owner, or the sales team if nobody owns it | App + email |
| Quote overdue | RFQ owner, plus leadership after a day | Everything |

**Rules that stop alerts becoming noise:**
- **Each alert is sent once**, not every time the system checks.
- **It closes itself** when the problem goes away: the milestone is done, the ship arrives, the item is removed from monday.
- **Nights and weekends are quiet.** Texts and phone pushes wait until 7 am unless it's urgent. Then they go out, unless someone already dealt with it.
- **Everything lands in the in-app Alerts inbox.** Press **Mark handled** once it's dealt with, so nobody chases it again.

Each person chooses how they're reached (email, phone push, text) on the **Alerts** screen. The **Send me a test alert** button shows whether alerts reach them.

---

## The screens

| Screen | What you do there |
|---|---|
| **Overview** | See what needs attention today. |
| **Projects** | See every project's milestones and what's late. Submit drawings, or review someone else's. "My milestones" and "Waiting for my review" show only your own. |
| **Shipments** | See every shipment and what's wrong with it. Add a shipment, record a change, or paste in a forwarder's email. |
| **Vendors** | Every vendor PO against its project's dates, what to chase, and each vendor's on-time record. |
| **RFQs** | Every request for quote, who owns it, when the quote is due, and how quickly quotes go out. |
| **Alerts** | Your alerts, the daily summary, and (for managers) who gets told and how. |
| **Orders & Sage** | Order emails, supplier bill checks, the Sage import files, catalogue updates, and anything that needs a person. |
| **Documents** | Drop in a customer PO, drawing or mill certificate to have it read. |
| **AI tools** | Translate to and from Vietnamese and Mandarin, sort and answer emails, compare a supplier's drawing with ours, and summarise a phone call. |
| **monday.com link** | What monday sent, what it did, and anything stuck. |
| **What runs by itself** | Which steps are automatic and which still need a person. |
| **System health** | Whether everything is up and connected. |
| **Mill certificates, Audit trail, Ask the assistant** | Heat numbers, a record of who did what, and a search across specs and past orders. |

---

## Orders and Sage

When a job on the monday jobs board is set to **Ready for Sage**, the system:
1. creates the Allied sales order;
2. splits out a purchase order for each supplier;
3. notes it on the monday item.

Later steps follow the same pattern:
- **Approved for Production** sends the POs to the suppliers.
- **Shipped** produces the packing slip. Supplier names and Allied's costs never appear on what the customer sees.

Each day someone downloads the two **Sage import files** (sales orders and supplier POs) from **Orders & Sage** and imports them into Sage 50. Each file holds only what hasn't been sent to Sage yet, so nothing is entered twice, as long as every downloaded file is imported. If a download didn't save, press **Put them back** on the same screen and those orders will be in the next file. Writing straight into Sage without the import step needs a small helper program on the Sage computer, which isn't built yet.

**Supplier bills** are checked against what Allied ordered and agreed to pay:
- the price per unit;
- each line's total;
- freight;
- the bill's total;
- that the supplier is the one on the PO;
- that the invoice hasn't been submitted before.

If everything matches, the bill is approved. If anything is off, it's held and the difference is shown. A dispute letter goes to the supplier at most once. A bill that matches no PO goes to a person, never back to the supplier.

---

## Where AI helps, and where it doesn't

The AI runs on Groq's free service. It costs nothing and answers in a few seconds, but the free tier allows about 30 requests a minute and 1,000 a day. When it's busy the screen says so, or a local backup model takes over if one is set up. What it reads (emails, drawings, call recordings) is sent to Groq's US servers. Groq doesn't keep it by default, and Allied can switch on zero data retention so nothing is ever stored.

**It helps with:**
- translating instructions for engineers;
- reading incoming emails and suggesting a reply;
- comparing a supplier's drawing with Allied's and listing every difference;
- summarising phone calls.

**It never acts alone.** Anything that matters goes to the **To review** list on the AI tools screen first: an order it read from an email, a call note for a job, a new part nickname. A person approves it before anything happens. When it isn't sure, it says so.

---

## What's not done yet

- **The monday vendor board isn't connected yet either.** It needs the board number and which columns hold the vendor, project and dates. Until then, vendor jobs can be added on the Vendors screen.
- **Your real monday projects board isn't connected yet.** It needs the board number and the names of the owner, due date and status columns. Until then the Projects screen has nothing to show.
- **The Sage import file hasn't been tried in a test company yet.** Do one test import before using it for real.
- **Alert contacts need adding.** Add everyone's name, email and phone on the Alerts screen. Real text messages need a Twilio account and a one-time carrier registration (about $60, a few weeks' wait). Phone push through the free ntfy app works without it.
- **The instruction check isn't built yet.** This is the step where an engineer repeats back a job brief in their own words and signs off before work starts. It's designed but not built.

---

## Starting it

Whoever looks after the system starts two programs; the steps are in the [README](README.md). Staff then open the web address in any browser, on a computer or a phone, and sign in with their own account.
