# Meeting prep: questions Allied will ask, what to ask them, and the risks

*Prepared 22 Nov 2026 from ALLIED_CONTEXT.md (the monday boards read view-only, a backup copy of the app's data, and the saved notes from the owner's talks with Karissa, Athish and Doug). Plain language. "Built" below means the code exists in the working copy and passes its own tests; none of it has been tried on Allied's real Sage export, a live monday test board or real vendor emails yet. I did not run the app to check this list, so confirm anything you plan to demo.*

**One-line summary for the room:** the app reads Allied's monday boards, keeps its own record of every job from customer PO to payment, prepares the paperwork and drafts the emails, and hands Sage 50 files to import. It never changes monday or Sage on its own, and a person approves anything that leaves the building.

---

## A. The 15 questions most likely to come up

**1. Will it change our monday boards?**
No. monday is read-only. The app copies what monday shows and keeps its own notes and tags beside it. Changes it would like to make are saved as "waiting changes" that a manager must send, and sending is switched off. Allied's board layout stays exactly as it is. *Pending: a test board, if you want to try sending anything.*

**2. Will it write into Sage 50?**
No. Sage stays the books. The app makes the import files (sales orders, purchase orders) and reads Sage exports (customers, vendors, invoices, payments). Someone imports the files in Sage, about two minutes a day. *Pending: one real Sage export to test against, and a trial import into a Sage sample company.*

**3. What does it do for us that monday and Sage don't?**
It follows one job from the plant's Customer PO# through the vendor POs, drawings, payments, shipping and the invoice, and it nags the right person. Today those steps live in separate boards and notes. For example, it knows 49 delivered jobs are not yet billed, and that one vendor contact holds 13 of the 31 open jobs.

**4. What about the 49 jobs waiting to be invoiced?**
The app lists them oldest first, shows whether the vendor is already paid, and drafts the reminder or invoice details for a person to approve. Allied confirmed that "awaiting invoice" means delivered and not yet billed. *Pending: the dollar value, which the boards do not show; that needs Sage.*

**5. How do vendor payments work in it?**
Allied pays China vendors 30% at order, 50% before shipping and 20% after arrival; US fabricators usually 100%. The app supports 30/50/20, 30/70, 50/50 and custom splits that add to 100%. Enter a payment once and it updates the job, a payment ledger file and a remittance email draft. *Pending: confirm on real jobs that the ledger and board stay in step (monday itself is only read).*

**6. Who approves a drawing or photos, and what happens if they do not?**
Two gates, as Athish described. Gate 1: the vendor's shop drawing is approved before production counts as started. Gate 2: the vendor's measurement photos are approved before crating, the 50% payment or carrier pickup. They are hard blocks. A manager can override only with a written reason, and every override is logged. Only Athish approves today (the list can be changed). *Pending: Allied to confirm the approver list.*

**7. How do you keep our customers hidden from vendors?**
Anything going to a vendor has the customer's name, logo, address, contact and customer PO numbers removed, and the cleaned copy is saved on the job. PDFs and DXF files are checked and cleaned automatically. A DWG can go only with an engineer's signed note tied to that exact file; if the file changes, the note is void. Unchecked vendor attachments are blocked. *Pending: test on Allied's real drawings, since scans and odd layouts are the hard cases.*

**8. How does the vendor packet work?**
Allied PO + the vendor's quote + the cleaned drawing + shipping instructions, as one package. The email is a draft until a person approves it. It shows what to make, never the customer or Allied's sales order number.

**9. How does it handle the three ways goods reach customers?**
Three paths, picked when the job is created. (1) US office parcel: re-pack, UPS, Allied's own packing list with the customer PO#. (2) US blind drop-ship by truck: bill of lading, no vendor marks. (3) China by freight forwarder: the vendor sends its invoice and packing list to the forwarder, Allied sends its own packing list, and a weekly digest updates delivery dates. *Pending: sample emails from Mountain Movers and the forwarder so the readers match real wording.*

**10. Can it tell us what a job really costs, with tariffs?**
It is built to add the vendor quote, tariffs and freight into a landed cost before markup, and to show "unknown" instead of guessing when a duty rate or code is missing. It does not file customs or choose HTS codes. Tariff rules moved twice in 2026 and sources disagree, so the broker has the final word. *Pending: HTS codes and origin for real parts, and the broker's rates.*

**11. Is our pricing safe? What about the free AI?**
Be direct: the free Groq AI service reads email text and scans, so anything in them, including prices, goes to Groq's US servers. Groq keeps nothing by default, and there is a setting to switch on zero data retention in Groq's console, which the owner has not yet confirmed. The owner chose to leave it as it is for now. Customer lists, markup tiers and supplier pricing are meant to stay local; this is the one place that rule is not fully enforced. There is an optional local-only switch (off by default). *Pending: Allied's decision on whether pricing may go to Groq.*

**12. What if the AI gets something wrong?**
The AI only reads, translates and drafts. Nothing it produces is sent or saved as final without a person approving it, and every approval is logged. Order routing and anything that reaches Sage follow fixed rules, not AI.

**13. Who can do what?**
Four roles: viewers read, operators prepare and submit, managers approve, administrators approve large orders and manage accounts. Purchase orders need a manager, and above a dollar limit an administrator. *Pending: the limit and the names.* Everything is on an audit trail.

**14. How long until it is live, and what does it cost to run?**
The AI is free (daily limits apply). It runs on one office computer, so it needs a named person to look after it and a written restore guide. The build order agreed with the owner is: (1) lead-time watchdog and vendor check-ins, (2) payment terms, (3) one-entry payment sync, (4) landed cost, readers and gate checks. I have no honest monthly cost or date to give; say so.

**15. What if the computer fails or someone makes a mistake?**
The database is copied before every upgrade, and monday and Sage are not affected because they hold the real records. Wrong entries are fixed in the app, and sending is gated. *Pending: a second backup location and a named backup person.*

---

## B. Five things to ask THEM

1. **Real samples:** a few emails from Mountain Movers and from the China forwarder (names and prices may be blanked), so the readers match real wording and the three shipping paths.
2. **One real Sage export** (customers, open orders, invoices and payments) and permission to test an import into a Sage sample company, never the live books.
3. **The approver list:** who approves Gate 1 and Gate 2 besides Athish, who approves purchase orders, and who takes over when Athish is away.
4. **The PO approval limit** in dollars (above it only an administrator approves), and whether yuan or Canadian-dollar POs should use the same number.
5. **A monday test board** and a yes or no on ever letting the app send changes to it; until then monday stays read-only.

*If time allows:* what `SEND TO JANE` means (nobody knew), whether the 52 old "Quote Sent" rows should be chased or closed, and what the 49 unbilled jobs are worth.

---

## C. The three risks to be upfront about

1. **Not yet tested on Allied's real files and accounts.** The Sage import has been tried only on sample files, monday has been read but never written to, and the drawing cleaning has not seen Allied's messy scans. Expect fixes after the first real week. We start with a Sage sample company and a monday test board.
2. **Pricing goes to a free AI service.** Groq's free tier reads email text and scans, so prices and customer details in them leave Allied for Groq's US servers. Zero data retention is available but not yet confirmed, and the owner chose not to block it. Allied should decide this now, not discover it later. The fix is local-only reading or a "no prices" rule.
3. **It depends on people keeping monday tidy and on one office computer.** Boards already hold duplicates (two POs shown twice), stale rows (5 old "fabrication" rows on the 2025 board, 52 old quotes), spelling variants and no owners on many items, and there are no forwarder or container details on any shipment yet. The app will show these gaps rather than hide them, but the data has to be fixed by the team. And if the single computer fails, the app stops until restored from the backup.

---

*Honest limits of this note: it is built from the boards and notes, not from running the app; the "will make project late" flag is the owner's test code and should not be shown as real; board values for what plants pay are not visible, only vendor payments and quoted prices.*
