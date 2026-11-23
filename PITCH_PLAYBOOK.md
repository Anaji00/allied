# Pitch Playbook

Your notes, not Allied's. Three documents do the explaining; this is what you say.

---

## The 30-second version

> "Every order we take costs about two hours of somebody's day — reading it, keying it into
> Sage, working out who supplies what, raising the POs, finding the mill certs, updating the
> board. I built a system that does all of that in a fraction of a second and hands you a
> five-minute review instead.
>
> And while it's doing that, it checks every supplier bill against the order we actually
> raised — line by line, freight included. We don't do that today. That's where the money is.
>
> It's built and tested. Six thousand to connect it. At ten orders a week it pays for itself
> in ten weeks."

Then stop talking.

---

## The four numbers, in order

| | |
|---|---|
| **2 hours → 5 minutes** | per order |
| **10 weeks** | to pay back the $6,000, at 10 orders/week. 5 weeks at 20. |
| **Every supplier bill checked** | line by line, freight included. Doesn't happen today. |
| **94%** | of the busy work can go. Connecting it gets 82%. Three more small pieces get the rest. |

---

## If they only ask one question

**"What's the catch?"**

> "Two. First, Sage 50 has no proper way for outside software to write into it unless you're
> on the desktop version. If you're on the online one, the system makes the import files and
> somebody clicks import — still removes all the typing, just not the last click. I check
> which you have in about a minute, before anyone commits to anything.
>
> Second, the two-hour figure is mine, not measured. If an order really takes you forty
> minutes, every number I've given you shrinks. The table in the proposal shows four
> volumes so you can find your own row."

Leading with your own weak point is the move. It buys you the rest of the meeting.

---

## Selling the rate, if it comes up

Don't get defensive about $95. Reframe it — the comparison in their head is the wrong one.

> "A consultancy would charge $150 an hour and send somebody who has never met any of you.
> A freelancer off a website would charge half and be on another contract by March. I'm
> neither. I know who's in this business and what everybody actually does with their day,
> which is why the thing is built the way it is — and I'm still going to be here when
> something looks odd on a Tuesday afternoon."

If they push harder, the honest version beats a discount:

> "Honestly? If this works, Allied runs better and everyone knows I built it. If it doesn't,
> I'm still sat here and everyone knows that too. That's a stronger guarantee than anything
> you'd get in a support contract."

Naming your own stake sounds risky and is the opposite. Every outside vendor is implicitly
asking Allied to trust a stranger's goodwill. You're the only option where the incentives are
visible and permanent.

---

## The one thing not to undersell

Time savings are easy to explain and easy to discount — everyone has heard "this will save
you time."

**The invoice checking is the part nobody else is offering**, because it only works if the
sales order and the purchase orders are tied together, which is exactly what this system
builds. Allied is a brokerage. The margin is the gap between buy and sell. A dollar a
supplier overbills doesn't come off that invoice — it comes off the only thing the business
actually sells.

Say that sentence out loud. It's the one that lands.

---

## Running the demo

**Never run `demo_reset.py`: it deletes the live database.** Build a separate demo copy instead (details in the README under "Presenting it"):

```bash
python scripts/demo_seed.py --db C:/demo/allied_demo.db
```

```bash
python scripts/demo_run.py --db C:/demo/allied_demo.db --port 8010
```

Frontend in a second terminal, pointed at the demo API:

```bash
cd frontend && NEXT_PUBLIC_API_URL=http://localhost:8010 npm run dev
```

The seeder prints four demo sign-ins and a one-time password. Sign in as manager and admin in two browsers before you start: five wrong passwords lock an account for 15 minutes.

### The three things to actually show

1. **Drop in an order.** It resolves the customer, matches the lines to the catalogue,
   splits across suppliers, produces the sales order, the POs and the packing slip. Narrate
   the fact that this just took a few hundredths of a second.
2. **Feed it a bad supplier bill.** One priced above the PO. Watch it refuse, show the
   variance, show the dispute letter it drafted. *This is the demo.* Spend time here.
3. **Try to break a rule.** A mill cert with a missing reading, or a price quoted off
   two-month-old steel. It stops and routes to the review list rather than guessing.

Don't demo the AI assistant unless asked. It's the least important part and it invites the
wrong conversation.

---

## Meeting day runbook (one page)

**Where to run it.** Only on the demo copy (`demo_seed.py` then `demo_run.py`, see above), never on the live system. The demo copy has made-up data, mail is draft-only and monday and Sage are off. The live backend is already on the new version; only the five newest frontend screens wait for one frontend-only rebuild. Still show everything from the demo copy.

**The day before.** Sign in as manager and admin in two browsers (five wrong passwords lock an account for 15 minutes). Check the three settings (README, "Before the meeting"): at least `ENGINEER_APPROVERS` if you want to show a gate being approved. Open every screen below once. The backend-dependent steps all work. Only if one of the five newest frontend screens is not in the build on the day, skip that step and do not apologise for it.

**The 8 to 10 minute path** (manager or admin sign-in):

1. **Control room (1 min).** One page: what is late, what is waiting, drafts waiting, backups. Say: this replaces five screens of checking.
2. **Drop in a pasted email (2 min).** Secretary or Toolkit: paste an RFQ email, press Read. Show the fields, the "how sure" and "where from" on each, and the list "check these". Say: it fills in, a person checks, nothing is saved until you press Save. Press nothing that saves.
3. **Type to the Secretary (1 min).** "Reply to <a name in the demo mail>" shows a draft waiting for approval. Say: drafts only; nothing is sent.
4. **A job: gates and drawings (2 min).** Open a demo job. Show Gate 1 waiting for the engineer and the cleaned-drawing card (customer details removed, verified by a person). Say: no drawing goes to a vendor unless it is cleaned and checked, and production does not start without the engineer. If the approver is set, approve Gate 1 as the engineer on the demo copy.
5. **Payments in steps (1 min).** Show a payment schedule 30/50/20 with amounts that add up exactly and which step opens when. Read it; do not record a payment.
6. **Landed cost (1 min).** Enter a quote with no freight: show "unknown", not zero, and the markup tiers.
7. **Logistics (1 min).** Show the quiet-shipment flag and the forwarder update **preview** (not Apply).
8. **Close on the safety rules (30 s).** Everything is a draft; monday and Sage are read-only and export-only; nothing here has been tried on Allied's real data yet.

**Do NOT click** (even on the demo copy, so the habit is safe): anything called Send, Approve and send, Record payment, Mark delivered or Close freight, Apply (forwarder update), Push to monday, Commit or Bring in (Sage), Undo import, Send nudge (use **Practice** if you show nudges), Save on an extracted form, bulk Apply (use the dry run). Never open the live address during the demo.

**If something breaks.** Stay calm and say: "That is the demo copy, and this is exactly why we rehearse on a copy first." Then (a) refresh once; (b) if a screen shows a plain-sentence error, read it out, they are written to be read; (c) if a screen is blank, move to the next step; (d) never fix anything live in front of them; (e) if the AI is slow or busy, say the free tier has limits and the rule-based parts do not depend on it.

**Honest limits, say them before they ask.**
- Sage is export-only: the system prepares files; it never writes into Sage. A trial import into a Sage sample company has not been done.
- monday is read-only: the system reads boards and prepares notes; nothing is written until a manager pushes and the switch is deliberately turned on.
- The gates and the redaction check need the owner to name the engineer (`ENGINEER_APPROVERS`); until then nobody can approve and the screens say so.
- The AI is Groq's free tier: limits per minute and per day, and mail text goes to Groq's servers. `AI_LOCAL_ONLY` turns the AI off entirely; everything described above works without it, by rules.
- Reading scanned pages needs Tesseract; the live Docker image has it. A figure read from a picture always says "needs a person to confirm against the image".
- Nothing has been tested on real data. About 4,200 automatic checks pass on made-up data (re-run before quoting a number).
- It runs on one office computer: the backend rebuild has been done once (with a safety copy made before the migration); the rebuild and rollback procedure is written in the README.

---

## Known limitations — say these before they find them

- Not yet plugged into live Sage or the live monday board (monday is read-only). It produces the Sage files
  correctly and records what it *would* post to the board. Connecting both is what the
  proposal is for.
- About 4,200 automatic checks pass (re-run before you quote a number), but they're my checks. Stage 1 proves it against
  Allied's real parts and customers, which is the test that counts.
- Everything lives in one file on one machine. The system copies the database before any upgrade and has a backup script. A second copy to another disk or folder is built in (`BACKUP_COPY_DIR`) but must be set up; say that, not that it exists.
- Customs papers (commercial invoice, packing list) are prepared, never filed, and the system lists what is missing instead of guessing. Your customs broker still files.

---

## Questions to expect (and honest answers)

- **"What if it breaks on a busy day, and who fixes it?"** Say: I fix it; the data is one file with automatic copies before upgrades, and restoring it on another machine takes about an hour. There is no written restore runbook yet, and it depends on one person. Offer to write it in stage 1. (Do not promise a response time you won't keep.)
- **"Who can see our prices and vendors?"** Every signed-in staff account sees vendor names and PO numbers; costs and margins are manager-only; a customer sees only their own reference and never our sales order number. Any signed-in viewer can download an attached file if they know its number. Accounts are removed or demoted instantly.
- **"What does it cost to keep running?"** The AI runs on a free tier (no per-use bill today, but limits can change). Hosting is one office PC. Your time to maintain it is the real cost; agree it before you are asked.
- **"Can it send emails by itself?"** Not as shipped. Every email waits for a person, and vendor POs always do.

---

## The documents

| Document | Who it's for | What it does |
|---|---|---|
| `Allied_Operating_Guide.pdf` | Anyone at Allied | How it works, what it saves, what it takes to run |
| `Allied_Proposal.pdf` | Whoever signs | The ask, the three stages, the payback, the signature block |
| `Allied_Cost_Basis.pdf` | Whoever challenges the price | Where every dollar comes from, including the AI running costs |
| `Allied_Pitch_Playbook.pdf` | **You only** | This document, as a PDF. Don't hand it over. |

Hand over the guide first. Let the proposal follow it, not lead it.
