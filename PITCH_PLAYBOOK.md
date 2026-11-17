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

## Known limitations — say these before they find them

- Not yet plugged into live Sage or the live monday board (monday is read-only). It produces the Sage files
  correctly and records what it *would* post to the board. Connecting both is what the
  proposal is for.
- The 2,394 automatic checks all pass, but they're my checks. Stage 1 proves it against
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
