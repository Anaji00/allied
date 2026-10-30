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

monday and Sage are where the information lives. All the reminders, checks and automatic steps happen in this system, so Allied doesn't pay monday for automations.

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

Each day someone downloads the **Sage import file** from **Orders & Sage** and imports it into Sage 50. Each order appears in exactly one file, so nothing is entered twice. Writing straight into Sage without the import step needs a small helper program on the Sage computer, which isn't built yet.

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

The AI runs on Allied's own computer (Ollama), so nothing is sent outside and there is no charge per use. It's slower than a paid service: an answer takes 15 to 60 seconds, and a drawing comparison 1 to 4 minutes.

**It helps with:**
- translating instructions for engineers;
- reading incoming emails and suggesting a reply;
- comparing a supplier's drawing with Allied's and listing every difference;
- summarising phone calls.

**It never acts alone.** Anything that matters goes to the **To review** list on the AI tools screen first: an order it read from an email, a call note for a job, a new part nickname. A person approves it before anything happens. When it isn't sure, it says so.

---

## What's not done yet

- **Your real monday projects board isn't connected yet.** It needs the board number and the names of the owner, due date and status columns. Until then the Projects screen has nothing to show.
- **The Sage import file hasn't been tried in a test company yet.** Do one test import before using it for real.
- **Alert contacts need adding.** Add everyone's name, email and phone on the Alerts screen. Real text messages need a Twilio account and a one-time carrier registration (about $60, a few weeks' wait). Phone push through the free ntfy app works without it.
- **The instruction check isn't built yet.** This is the step where an engineer repeats back a job brief in their own words and signs off before work starts. It's designed but not built.

---

## Starting it

Whoever looks after the system starts two programs; the steps are in the [README](README.md). Staff then open the web address in any browser, on a computer or a phone, and sign in with their own account.
