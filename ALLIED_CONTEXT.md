# Allied Welding Systems: what the monday boards show, and what the app should do next

*Written 17 Nov 2026 by a read-only research session. Nothing was changed anywhere: no monday calls, no database writes, no Sage, no `.env` read.*

## How this was built, and what I could not see

**What I read (all read-only):**
- The repo write-ups: `INTEGRATION_GUIDE.md`, `SYSTEM_WALKTHROUGH.md`, `README.md`, `app/config.py` (column and label settings), the owner's saved notes (memory files).
- The app's master data in `app/data/` (customers, vendors, items, supplier agreements, estimates).
- **One backup copy of the app's database** (`data/allied_enterprise.backup-20261117-before-third-deploy.db`, opened read-only, never the live file). It holds the app's own record of what it read from monday: 269 order-board items with their status, 49 vendor jobs, 17 shipments, 13 RFQs, 369 engineering milestones.
- Web research (list of sources at the end).

**Update, later on 17 Nov: the live boards.** Through the app's own Boards screen in the signed-in browser pane (view only: I clicked board tabs and one "Show finished" filter, nothing that stages, edits, sends or pushes, and I never read the sign-in token), I read **2026 Orders (262 items), Engineering Team Board (371), Open RFQs (6), 2025 Orders (371, first screen only), Athish's Board (106), Daily Work Schedule (5), Project Follow Up (3)**. Sections below now say what the live boards show; "(live)" marks facts read there, "(backup)" marks facts from the database copy. The app's copy was "last updated from monday: about 57 minutes ago".

**What I could NOT see:**
- **Customer Visit Tracker** (a monday Doc): not opened. **2025 Orders:** only the top of the page; the rest is truncated.
- **Comments, files, activity log, status times, dashboard:** not read. Cell notes (the "Notes" column) are visible; monday "updates" threads are not.
- **Prices customers pay.** The boards show *vendor* payments and *quoted prices per part* (Athish's Board), but not invoices to plants or margins. Margins in the repo's sample files are illustrative; do not quote them.
- **Real item ages.** The app's "last changed" stamp began on 6 Nov 2026. Use the dates printed on the board instead.
- **Sage 50.** No access, no data.
- The app also showed "4 changes waiting" (staged, not sent; `monday is read-only`). I left them alone.

---

## Company in one page

**What Allied is.** A design-and-sell firm in Dallas, Texas. Allied draws the parts, has them made by outside shops (mostly in China, some in the US, some in Canada), and sells them to industrial plants. It owns no factory. The value it adds is engineering, vendor choice, quality follow-up, shipping and paperwork.

**What it sells.** Wear-resistant parts for plants that move abrasive material (sand, glass batch, granules, ore, cement):
- Parts made from Allied's own hard-faced wear plate, **"Allied 300XL"** (a chromium-carbide overlay on steel; the 200 and 20-35XL grades also appear). Examples: hammermill screens, hoppers, chutes, auger troughs, tab deflectors.
- **Ceramic-lined pipe, elbows, tees and flex hose** (alumina or silicon-carbide liners), mostly from the Zibo, China ceramics partner and from a vendor called KINGCERA.
- **Screw conveyors and augers** (KWS), structural pieces, plate, and hard-surfacing welding wire sold from stock.
- Engineering services: scanning a worn part and modelling it ("Scan & model assembly").

**Who buys.** Industrial plants, mainly insulation, roofing and building-materials makers and mining. On the 2026 order board, **Owens Corning sites are 132 of 269 items (49%)**: Irving, Kansas City, Newark, Amarillo, Starr, Edmonton, Delmar, Toronto, Lakeland and more. Others: Mosaic (24), Hecla (14), IKO (14), CT Portland/Ennis (13), Atalco (11), GAF (7), Knauf (6), Nol-Tec (5), Intrepid Potash (4), USG (2).

**How a job runs, in one breath.** A plant asks for a quote. Allied checks the drawing (often a scan of a worn part), asks one or more vendors for a price, quotes, and the plant sends its own purchase order. Allied issues a purchase order to a vendor, usually by mail with strict "blind shipment" instructions (no vendor markings; the packing list rides outside). The vendor makes the part (about 50 days from order to ship), sends photos, ships by sea, and Allied invoices the plant. Plants pay late: Owens Corning's terms in the customer file are **Net 150 days**.

**How the work is split.** monday is where the team plans (engineering drawing jobs, open RFQs, orders). Sage 50 is the books. The app in this repo sits between them and does the alerts, drafts and paperwork. By the owner's rule, all automation lives in the app, not in monday.

**People who matter (first names only).** Management: Athish and Doug. The owner describes the main pain as **communication and holdups between management and the engineers**. Two in-house engineers work in Mandarin and Vietnamese. A sales rep (Karissa) is on every customer record.

---

## Boards one by one

*The board ids come from the database. Roles come from the app's config. "(inferred)" means I worked it out from data, not from reading the board.*

| Board | What it is for | What I know |
|---|---|---|
| **2026 Orders** (id 18394243717) "Vendor orders" | One row per **vendor purchase order**, named `CUSTOMER-SITE`, with the part in "Description". The center of the business. | **262 items** (live): 161 done, 49 awaiting invoice, 23 fabrication, plus shipped and waiting. Four groups: **Manufacturing 31, Shipping 21, Payment 50, Done 160.** Columns: Name, Description, Customer PO#, Vendor, Allied PO#, Status, ETA, PO received, Notes, DWG Approval, Vendor PO Issued, Lead Start, Vendor Lead (days), VENDOR ETS, Vendor Days Left, Target Date, Cust Lead (weeks), Customer Days Left, **Payment** (e.g. "30% - $576.00 - 11/9"), **Payment Status**, **Vendor Type** (Fabricator or Supplier). PO numbers 9046 to 9316 this year. |
| **Engineering Team Board** (id 1657858464) "Projects" | The drawing queue: one row per part to draw, all in one group "Drawing Needed". | **371 items** (live), since 15 Apr 2024: 322 complete, 12 scanned, 5 sent for review, 32 other (working, paused, a "to follow" status for a senior staff member, unset). Columns: Name, Customer, Engineer (one or two), Scan Date, Status, Estimate finish, Actual Completion. 7 engineers. |
| **Open RFQs** (id 1657738509) "Quote requests" | Quote requests from plants. | **6 items** (live): D cone liner (Knauf-Shasta Lake), Flint Line Pipe (OC-Starr), Shark Fins (IKO), Dust Collection 3 Pieces (OC-Newark), Mag Sep Hopper (OC-Irving), Tails Tank Bottom Cone (Intrepid Potash). Columns: Customer, Bidding vendors, Last follow up, Status, Notes. |
| **2025 Orders** (id 8256958924) | Last year's orders, same layout as 2026 (ETS, ETA, Timeline). | 371 items (live): 337 done, 19 fabrication, 15 shipped. PO numbers 8764 to 9044. **Still holds 19 items "in fabrication" from 2025** (see Gaps). |
| **Athish's Board** (id 7384682515) "Scanned Project Tracker 2024" | The **quote ledger** for scanned parts: what was quoted per part and whether the plant bought. | **106 items** (live): **52 quote sent, 20 purchased, 13 not going to purchase**, the rest in engineering/RFQ. Columns: Name, Customer, Status, Scan Date, **Cost (each)** (the quoted price), Comments. 90% "clean" by the app's check. |
| **Daily Work Schedule** (id 18395246042) | A daily to-do board ("DMS OPS"). | **5 items** (live), last touched 4 Aug 2026; tasks: Inspection, Create Quote, Get DWG Completed, Waiting on Vendor, Admin, Lunch. Effectively unused. |
| **Project Follow Up** (id 7409330692) | A short open-projects list. | **3 items** (live), due Jul to Aug 2025, never closed. Unused. |
| **Customer Visit Tracker** (a monday Doc) | Visits to plants. | Not read. |

### 2026 Orders: the status labels (live, all 17)
`FABRICATION`, `WAITING ON QUOTE`, `STUCK`, `WAITING ON RECEIPT`, `WAITING ON PI`, `WAITING ON DRAWING APPROVAL`, `SHIPPED`, `AWAITING INVOICE`, `ON HOLD`, `NEED PICS APPROVED`, `NEED UPDATED PO`, `NEED MARK UP`, `NEED PO`, `EXPECTING PICS`, `WAITING ON PO`, `DONE`, `SEND TO JANE`. Open counts today (live): FABRICATION 23 (of 31 in Manufacturing), WAITING ON DRAWING APPROVAL 3, WAITING ON PI 2, EXPECTING PICS 3 (POs 9234 and 9264 also appear a second time as SHIPPED in the Shipping group: duplicate rows), plus 49 AWAITING INVOICE. "PI" is a proforma invoice the vendor wants paid before starting (still inferred, but PI rows are all "Needs Full Payment" or have a fresh deposit). `SEND TO JANE` is a hand-off label whose meaning is unknown (the owner does not know either); treat it as a generic "hand-off" status and do not build rules on it.
**Payment Status** (live): `Paid in Full`, `Partially Paid`, `Needs Full Payment`.

### Engineering board: the status labels (live)
`Need to Scan` → `Scanned` → `Working on it` → `Sent for Review` → `Sent to Shop` → `Complete`; also a "to follow" status (a senior staff member follows that item; the app should treat it as a generic "senior follow" flag, not tied to one name) and `Paused`.

### Open RFQs: the status labels (live)
`Waiting on drawing`, `Waiting on vendor quote`, `Waiting on Freight`, `Need revised quote`, `Need markup`, `Stuck`, `Need quote checked`. The app flags 5 of 6 as **overdue**; the oldest are "D cone liner" (last follow-up 6 Feb) and "Flint Line Pipe" (18 Mar): about 9 and 8 months with no follow-up on the board.

---

## Job life-cycle

This is the path the live boards support. Steps 1 to 3 are the usual flow; the 2024 quote ledger shows it clearly.

1. **A plant asks** (email, or a visit). Often the plant has only a worn part, so someone **scans** it (Engineering board: `Need to Scan` → `Scanned`, with a Scan Date). An engineer draws it (`Working on it`), a second person checks it (`Sent for Review`), and it goes to the vendor (`Sent to Shop`) and then `Complete`. Typical scan-to-complete in 2024 was 1 to 6 weeks.
2. **Quote.** The part goes to **Athish's Board** (quote ledger, price per part, e.g. $371 to $177,011 each) and/or **Open RFQs** (`Waiting on vendor quote`, `Need markup`, `Need quote checked`). The vendors asked are listed in "Bidding vendors".
3. **The plant buys, or doesn't.** Ledger statuses: `Quote Sent` → `Purchased` or `Not Going to Purchase`. Comments show plants buy on **capital approval** cycles ("waiting on Capital end of 12/2024", "Plan for Feb", "Did not put in CA in time"), so quotes can sit for months.
4. **Order row on 2026 Orders**, with the plant's number in "Customer PO#" and Allied's own vendor PO in "Allied PO#" (9046 to 9316 this year; roughly 270 POs in 10.5 months, **about 26 a month**; the 2025 board's PO numbers run 8764 to 9044, so at least 280). Several vendor POs can share one customer PO (e.g. OC-Kansas City 4518574577 has 6 vendors; OC-Newark 4518359150 has 7).
5. **Vendor starts only when paid.** `WAITING ON PI` (proforma invoice) → deposit paid → "Lead Start" date → `WAITING ON DRAWING APPROVAL` (vendor's shop drawing back for Allied to approve) → `FABRICATION`. "DWG Approval" is its own date column.
6. **Vendor makes it.** Staff chase by hand: dated notes like "11/16 - Asked for update" in the Notes column (up to 6 chasers on one PO). Crate photos: `EXPECTING PICS` / `NEED PICS APPROVED`.
7. **Vendor payments follow a fixed pattern** (live "Payment" column): China ceramics (HELEN, KINGCERA, NANCY): **30% at order, 50% before shipping, 20% after arrival**; some 30/70 or 30/60/10; US fabricators and plate (KWS, NIBROCK, BURT): **100% ("Needs Full Payment")**. Payment Status then shows `Partially Paid` or `Paid in Full`.
8. **Ships** (`SHIPPED`, group "Shipping"). Goods very often **come to Allied's Dallas office first** ("Shipping DDU to Dallas", "Shipped from AWS", "Arrived @ AWS 9/9 shipped out to customer"), then go on to the plant. Some jobs fly ("shipping via air").
9. **`AWAITING INVOICE`** (group "Payment", 49 rows). Strongest reading: the vendor is already **paid in full** and Allied has not yet billed the plant, because most of these rows show "Paid in Full" yet are still in this status, and the oldest are from January 2026. **Confirmed by the owner (17 Nov): `AWAITING INVOICE` means work delivered and not yet billed to the plant.**
10. `DONE`, then the row sits in the Done group (160 in 2026).

**Typical timing (49 vendor jobs in the backup, Aug to Nov 2026):**

| Measure | Result |
|---|---|
| Vendor's quoted lead time | median 35 days (range 7 to 103) |
| Order to actual ship, 17 shipped jobs | **median 50 days** (range 8 to 103) |
| Ship date vs the vendor's FIRST promise | **median 13 days late**; only 2 of 17 shipped early, 4 of 17 within 3 days |
| Promise moved after the first one | 17 of 49 jobs moved their date at least once, by up to 41 days |
| PO date to customer's due date | median 66 days (about 9.5 weeks), range 14 to 124 |

---

## People and partners

### Customers (what differs between them)
- **Owens Corning** (about half the board). Many plants, each its own ship-to and often its own contact. Net 150 days, freight by truck (motor freight). Canadian plants (Edmonton, Toronto) mean orders in Canadian dollars and customs on the way out as well as in.
- **Knauf, IKO, GAF, USG** (insulation and roofing). Net 30. IKO also buys jigs and base plates.
- **Mosaic, Hecla, Intrepid Potash, Atalco** (mining and minerals). Heavier parts: cyclones, feed pots, elbows, ceramic hose.
- **CT (Portland, Ennis), Nol-Tec** (material-handling and equipment). Smaller names.
- A few buyers seem to be other fabricators (Quality Machine Works, Tretter Manufacturing, The RDI Group).

### Vendors: the board writes first names, not company names
The board's vendor column mostly holds a **contact's first name**. The app has to map these to companies (HELEN to the Zibo ceramics firm; MAX to KWS; VICKI to Eutectic).

| Name on board | Jobs (of 49) | Notes |
|---|---|---|
| **HELEN** | **14 (29%)** | Ceramic elbows, flex hose, pipes for Mosaic, Hecla, OC, GAF, TECO. China (Zibo). Quoted median 30 days. 4 date slips. |
| **KINGCERA** | 7 | Ceramic-lined pipe and cyclones. Quoted 71 days. **7 slips on 7 jobs.** China. |
| **KWS** | 5 | Augers, elevator boots. US (Dallas). Strict blind shipment. |
| **NIBROCK** | 5 | Wear plate, screens, ground plates. US (Alabama). Blind shipment. |
| **BURT** | 4 | Scrap diverter plates, base plates. |
| GRACE 3, NANCY 2, WILLIAM 2, and one each of COR-MET, DUKE, HOLLY FAB, LEILA, LEWIS, REPAREX, UNIVERSAL | 15 | Long tail of 11 more names. DUKE quoted 95 days. |

**Vendor country is not on the board.** The app guesses sea freight for everyone and treats named US vendors as truck. KWS and NIBROCK jobs are still marked sea in the database, so their transit time is probably overstated (inferred).

**More vendors on the live boards (not in the app's vendor master):** EUTECTIC, COR-MET and POSTLE (welding wire, rod and rolls: Allied-branded 20-27M, 20-27T, 20-35XL, 20-65XL, 70-07, 70-06XL, 70-60XL), WILLIAM (tungsten blades), REPAREX (repairs), UNIVERSAL, INDUSTRIAL REPS, METALS DEPOT, MCMASTER-CARR, TRI FAB, MASS FLOW SOURCING, CONTROL CONCEPTS, AADFW (a lab: "chemistry analysis on blade"), and contact names JOHN, MAX, HEIDI, YIMING, ANNA, JIM, VICKY, ORTHMAN. **Vendor Type** column: `Fabricator` or `Supplier`. Parts often pass through a **third-party grinder** ("Commerce Grinding": plate from NIBROCK goes there, then to KWS for the auger) or a second fabricator ("ship to Tri-Fab", "ship to Nibrock").

**Second product line (live): Allied-branded welding wire, rod and rolls.** Rows like "ALLIED 20-27M .045"" for ATALCO, USG, San Miguel, Quality Machine Works. Vendor lead 2 to 21 days, usually 100% payment, often "in stock" and shipped from Dallas ("Had in stock - shipped 9/23"). It needs no drawing and no sea freight, so it is a very different job from a ceramic elbow.

### Forwarders and carriers
**None are recorded** in the app (all 17 shipments lack forwarder, carrier, container, free-time date). The boards never name a forwarder either; shipping notes talk about "DDU to Dallas", "shipped from AWS" and air.

### Allied's people
- **Engineering owners** (from the milestone owner column): Paul 112 milestones, Taras 65, J. Shane 48, Quan 45, Allen 42, Erin 38, Shawn 14.
- **Open work now:** Taras 9, Shane 9, Paul 8, Quan 4.
- **Reviewer pattern:** all 5 items in `Sent for Review` belong to Shane. That is one person gating every drawing, and there is no "reviewer" column on the board.
- **Management:** Athish and Doug (the app's notes name them as the ones who hand instructions to engineers).

---

## Jargon glossary (use the team's words in the app)

| Word | Means here |
|---|---|
| **OC** | Owens Corning. `OC-IRVING` = the Irving plant. Item names follow `PLANT-SITE - PART`. |
| **300XL / 200 / 20-35XL** | Allied's hard-faced wear plate and wire grades |
| **PO / SO** | PO = Allied's order to a **vendor**. SO = Allied's internal sales order. The plant's own number is "Customer PO#" |
| **PI** | Proforma invoice from the vendor; `WAITING ON PI` = money before work (inferred) |
| **Pics** | Photos of crates or parts before shipping; `EXPECTING PICS`, `NEED PICS APPROVED` |
| **Blind ship / dropship** | The plant never sees the vendor. No vendor logos, papers or Chinese writing; only Allied's packing slip on the outside |
| **Scanned** | A worn part was 3-D scanned and is waiting to be drawn |
| **Sent to Shop** | The drawing has gone to the vendor |
| **ETS** | Estimated ship date (VENDOR ETS) |
| **Cust Lead** | Weeks the customer expects, counted from the vendor PO date (default 9; open jobs run 4 to 19) |
| **CT / CertainTeed, JM, NEG, WF, CNS, TECO** | Customer short names. CT = CertainTeed (spelled "Certainteed" on the Engineering board, "CT" on orders, and **"OC-Portland" on Athish's Board for what is CT-Portland**). NEG = probably Nippon Electric Glass (inferred). The others are as written |
| **CA** | Capital authorization: plants buy when their budget is approved ("did not put in CA in time") |
| **AWS** | Allied's own Dallas office/warehouse ("shipped from AWS", "arrived @ AWS") |
| **DDU / DWG** | Delivered duty unpaid / drawing; **"DWG Approval"** is the date a vendor's shop drawing was approved |
| **20-27M, 20-27T, 20-35XL, 20-65XL, 70-07, 70-06XL, 70-60XL** | Allied welding wire/rod grades; the number is the product code, "M"/"T" the form |
| **SiSiC / SISIC, CLR** | Silicon-carbide ceramic liner; ceramic-lined rubber |
| **Victualic** | Grooved pipe coupling style (spelled this way on the board) |
| **Crate credit** | Money taken off a vendor payment for a damaged or replaced crate |
| **Deposit lines** | "30% - $576.00 - 11/9" = percent, amount paid, date paid |
| **Motor frt** | Truck freight to the plant |
| **Net 150** | Payment 150 days after invoice |
| **No bid** | Allied declines to quote |
| **Target Date / Days Left** | Formula columns the app has to recompute |
| **Elbow, tee, cyclone, hopper, auger, diverter, deflector, spool** | Common part names |
| Typos the team lives with | "AMARLLO", "ATLACO", "RECEIVEING", "KINGCERA" vs "King Ceramic": the app's name matching must forgive them |

---

## What the data shows (numbers)

### Live boards (read 17 Nov 2026 through the app, view only)

**2026 Orders (262 items; Manufacturing 31, Shipping 21, Payment 50, Done 160)**
- **Pace:** POs 9046 to 9316 this year = about 26 vendor POs a month. 2025 board: PO numbers 8764 to 9044, at least 280.
- **Open jobs (31 in Manufacturing), the app's own flags:** 21 "Will make project late", 4 "At risk", 3 "Reconfirm date", 1 "Vendor late", 2 "On track". **These flags come from the owner's test code in the app, not from Allied's data: they say nothing about whether a job will really be late.** Ignore them when judging the business.
- **Open job mix by vendor (31):** **HELEN 13 (42%)**, NIBROCK 3, KWS 3, BURT 3, KINGCERA 2, NANCY 2, and one each for DUKE, GRACE, LEILA, LEWIS, HOLLY FAB.
- **Statuses of the 31:** 23 fabrication, 3 waiting on drawing approval, 3 expecting pics, 2 waiting on PI.
- **Vendor lead times on open jobs (column "Vendor Lead", days):** China ceramics 20 to 45, US plate and fabricators 14 to 42, KWS 28 to 74, KINGCERA 62 to 103 (97 on one job), DUKE 95.
- **Money paid to vendors, as the Payment column shows it.** 17 of the 31 open jobs have a deposit logged (typically $200 to $1,000, so vendor POs of about $700 to $3,500; the biggest are $4,017 for a 30% deposit on transition chutes, $4,110 on a cyclone). Biggest finished vendor payments: a 6" transport line for OC-Delmar $44,503, spools for Nol-Tec $35,016, a tungsten carbide ring for CT-Portland $20,382. **These are Allied's costs; the boards do not show what plants pay.**
- **Payment group (50 rows):** 49 `AWAITING INVOICE`, 1 `DONE`. The vendor side is mostly `Paid in Full`. About 17 of the 49 carry PO numbers 9051 to 9167 (placed Jan to early Jun), so they have waited **5 to 10 months** with no invoice recorded. At least 4 have no dates at all (PO "9183 POSTLE", "9167", "9156", "9059").
- **Duplicates:** POs 9234 and 9264 are on the board twice (Manufacturing with `EXPECTING PICS` / `FABRICATION`, Shipping as `SHIPPED`). PO 9235 and 9234 share a Kansas City job. Shipping rows still show "Partially Paid" because the last 20% is due after arrival.
- **Chasing is manual and heavy.** One KINGCERA job carries six dated "asked for update" notes (11 Sep to 26 Oct); several jobs show 3 to 5. Notes also record problems: parts that "came in wrong", a guiding bar "has to be remade", "missing bag of nuts", "part did not satisfy customer", replacements, crate credits of $450 and $114, a vendor "lost" and the part flown instead, typhoon flooding.
- **Missing fields:** several rows (stock and wire jobs) have no Allied PO# or no Customer PO#, and many finished rows have no ETS or dates, so on-time history for the whole year cannot be rebuilt from the board.

**2025 Orders (371 items)**
- 337 done, 15 shipped, **19 still "fabrication"**. 14 are one NEG-Shelby tungsten-blade contract (8,000 blades, in 20-odd numbered lots, `8904-x`, `8906-x`) shipping month by month, which is not stale. **5 are Oct to Dec 2025 orders with no ETS** (Mosaic mag separator chute, OC-Irving waste auger, two OC-Fairburn cullet jobs, an OC-Kansas City screw conveyor) that are either finished and not updated or ten months late.
- The board says "92% clean" and "29 of 371 open".

**Engineering Team Board (371 items since 15 Apr 2024, about 19 a month)**
- 322 complete (87%), 12 scanned, 5 sent for review. **Customer mix is again Owens Corning plants and CertainTeed sites**; others include Mosaic, Hecla, GAF, Knauf, Black Mountain, JM, Intrepid.
- 2024 items show scan date, estimated finish and actual completion; **the newest rows (Oct 2026) have none of the finish dates**, so cycle time cannot be measured for recent work.
- Typical 2024 turnaround (scan to done): about 1 to 6 weeks; a plant's multi-part job (e.g. OC-Portland, 12 parts) was drawn in 2 to 7 weeks.
- Engineers: Allen, Erin, Paul, Quan, Shane, Shawn, Taras. Many rows list two (a drafter and a checker).
- The board's own check: "0% clean, 47 of 47 open items" need fixing (missing owner, date or customer).

**Athish's Board, "Scanned Project Tracker 2024" (106 items): the only record of quote results**
- **52 quote sent, 20 purchased, 13 not going to purchase**, 21 earlier-stage rows. Of the 33 decided quotes **20 were won (61%)**. But **52 of 106 (49%) are "Quote Sent" with no outcome** more than a year later.
- Quoted prices per part run **$371 to $177,011** (a material elevator); most are $2,000 to $20,000. Larger jobs are split into many parts (OC-Portland: 20+ parts quoted, $941 to $55,165 each).
- Lost-reason comments are rare and human: "Cannot Fab out of 300XL", "Did not put in CA in time", "Going to purchase in 2025", "2025 Project", "waiting on Capital". Plants buy on capital cycles, often months after the quote.

**Open RFQs (6 items):** 5 of 6 flagged overdue; "D cone liner" last followed up 6 Feb, "Flint Line Pipe" 18 Mar, "Shark Fins" 2 Nov, two more 6 Nov. Only one has a recent follow-up (16 Nov). 67% clean.

**Unused boards:** Daily Work Schedule (5 rows, last touched 4 Aug 2026; "Book Trips", "Email Ben", "Project Update Email" all `Waiting on Vendor`) and Project Follow Up (3 rows due Jul to Aug 2025, never closed).

**What changed from the backup picture (also sent to the parent session):**
- The backup showed 269 orders and a one-group Engineering board of 369; live is **262 and 371**.
- The live **order board has a vendor-payment column** (30/50/20 and similar) I could not see before; "Net 30 to 60" in the vendor master is not how the China ceramics vendors are paid.
- `AWAITING INVOICE` means "delivered, plant not yet invoiced" (owner confirmed).
- The pace is **about 26 POs a month**, not 14.
- Open RFQs on the live board are **6**, not 13 (the rest had already left the board).
- **HELEN is 13 of the 31 open jobs (42%)**, higher than the 29% of the backup.
- The Engineering "Customer" and "Engineer" columns exist; the backup only had owners.
- Athish's Board is the real quote ledger.
- There is a second product line (welding wire) and a multi-stop supply chain (plate to grinder to fabricator).

### Backup database picture (before the live read; still useful)

All counts below are from the 17 Nov 2026 backup copy.

**Order board (269 items, backup)**
- 165 done (61%), 48 awaiting invoice (18%), 27 in fabrication (10%), 22 shipped (8%), 6 waiting on PI, photos or drawing approval (2%).
- Purchase-order numbers run from 9233 to 9316 in the jobs seen (Aug to Nov 2026). At least 14 jobs a month are tracked (done jobs drop out of the tracker, so real volume is higher).
- The 49 tracked jobs are: 26 in fabrication, 14 shipped, 3 awaiting invoice, 3 waiting on PI, 2 expecting pics, 1 waiting on drawing approval.
- **Open jobs already past the customer's due date:** 2 in fabrication (a ceramic-lined pipe job due 20 Oct at KINGCERA, a ground plate due 30 Oct at NIBROCK). Two more passed their date but have shipped and wait on invoice.
- Vendor promised date passed with nothing shipped: at least 3 (including a cyclone promised 16 Oct due to the customer 2 Dec).

**Shipments**
- 17 shipments, all sea, all "departed". 4 have no arrival estimate. 0 have forwarder, container or free-time date. So demurrage, "missed sailing" and "quiet forwarder" alerts have nothing to work with.

**RFQs (13)**
- Received: Jan 1, Mar 1, Oct 4, Nov 7. Six of 13 are "no bid"; for 1 the app wrote "outcome not recorded" because the row just left the board. None has a quote amount, quote number or owner. The win rate cannot be computed.

**Engineering board (369 items)**
- 322 complete (87%). 102 ever had a due date and **the newest due date is 2 June 2025**. Nothing since then has a date.
- 31 items (8%) have no owner. All 9 `Scanned` items are unowned, which means scanned parts are waiting for someone to pick them up. 23 have no status.
- Owner load is uneven (Paul about 30% of all milestones).

**Master data and money**
- The customer master holds **5 customers** (OC Edmonton, OC Starr, Knauf Lanett, IKO Southwest, Vulcan Materials). The board shows well over 40 plants. The vendor master holds **4** (Nibrock, Zibo, KWS, internal stock). The jobs table shows 15 names. Name matching therefore falls back on guessing for most rows.
- **Sample files, not real money:** the 13 order rows in the database are test or seed rows (one is called TEST-ORD-9901; Knauf Lanett appears billed in Chinese yuan). Their margins (50% to 82%) and the four estimates in `historical_orders_and_estimates.json` (margins 46% to 60%, a 3.4x wear-life win, one lost bid to cheaper AR400 steel) are illustrative. The supplier-agreements file also disagrees with the master (Nibrock "Mining & Wear Solutions" vs "Nibrock, Inc."; Zibo Net 60 vs Net 45; KWS "Ltd" vs "LLC"). Check them with the owner before using them.
- Terms: customers **Net 30 to Net 150**; vendors **Net 30 to Net 60** (Allied pays most vendors well before Owens Corning pays Allied).

**The app's own record of itself**
- 76 monday events read, 11 dispatches done, 4 failed (all four posted a "could not complete dispatch" note that is staged, not sent), 61 ignored (status not mapped).
- 37 exceptions logged (stale vendor price, held inbound order emails) and 230 alert records.

---

## Gaps and risks

1. **Shipping data is empty.** No forwarder, container or free-time date on any shipment. The demurrage alert (about $75 to $300 a day) and the 24-hour ISF filing clock cannot run without them.
2. **Vendors are people, not companies.** One contact (HELEN) carries **13 of the 31 open jobs (42%)**: a single point of failure for vendor quality, price and email. Board names do not match the vendor master, and country is missing.
3. **Vendors beat their own promises rarely.** Only 2 of 17 shipped on or before the first promise, and the median miss is 13 days. A job quoted at 35 days really takes about 50. Customer due dates (median 66 days) leave less slack than they look.
4. **Cash gap and unbilled work.** Allied pays China vendors **30% at order, 50% before shipping and 20% after arrival**, and US fabricators in full, while Owens Corning's terms are Net 150. **49 items sit at `AWAITING INVOICE`**, most with the vendor already paid in full, and about 17 are 5 to 10 months old. The owner confirmed this means work delivered and not yet billed to the plant, so it is cash held back and the first thing to fix.
5. **Idle scans and no owner.** 9 of 9 scanned parts have no owner. 31 milestones have none. Reviews pile up on one person (Shane).
6. **No due dates on engineering work since mid-2025**, so the app's late alerts for engineering have nothing to compare to.
7. **Quotes leave no trail.** On Athish's quote ledger **52 of 106 quotes (49%) are still "Quote Sent" with no outcome**, some for a year or more. Open RFQs: 5 of 6 are overdue, two untouched since February and March. In the backup, 6 of 13 RFQs ended no-bid with no reason and none had a quote amount. Win rate (61% of decided ledger quotes), reasons and price-by-customer are only partly recoverable.
7b. **Quiet duplicates and stale rows.** POs 9234 and 9264 appear twice with different statuses; the 2025 board holds 5 old "fabrication" rows with no ETS; two side boards (Daily Work Schedule, Project Follow Up) are abandoned. The app's own "100% clean" checks do not catch these.
7c. **Quality problems live in free-text notes.** Wrong parts, remakes, missing hardware, damaged crates and credits are written in Notes or Payment text, not in fields, so vendor defect rates cannot be measured.
7d. **The "will make project late" flag (21 of 31 open jobs) is test code**, per the owner. It is not evidence of lateness; keep it out of leadership alerts until it is checked against real dates.
8. **Names that nearly match.** AMARLLO, ATLACO, "KNAUF-SHASTA LAKE" vs "KNAUF". Two reads of the same plant can become two customers.
9. **Master data far behind the boards** (5 customers vs 40+ plants; 4 vendors vs 15 names). Documents (customs invoice, PO, sales order) will be INCOMPLETE until it is filled.
10. **Tariff rules changed in 2026.** Section 232 was restructured in April and June 2026 and the sources disagree on the rate for pipe fittings and structures. An out-of-date quote is a real risk (details below).
11. **Everything runs on one office computer** (per the walkthrough), and the monday and Sage links are untested against live accounts.
12. **A write-safety reminder:** the app has paths that would add rows to monday (RFQ rows, staged pushes). They are blocked while `MONDAY_READ_ONLY` is on. Keep it on.

---

## Research findings with sources

*Findings are what the sources say, not advice. Several sources are vendors' blogs; where they disagree I say so. Anything with money riding on it should be checked with Allied's customs broker.*

### (a) How comparable firms run this
- **Quality of overseas parts.** Inspection firms and sourcing agents recommend three checks, not one: before production (is the factory ready?), during production (around 20 to 50% complete; one provider prefers 20% because rework is cheaper then) and before shipping (when at least 80% is packed). For new parts, a **first-article inspection** on 5 to 10 pieces, with a report that maps each toleranced feature to the drawing. Sources are third-party inspection companies with something to sell, and their failure-rate claims (one says about 30%) are unsourced. [proqc.com](https://proqc.com/china/inspection-and-audit-services/), [maplesourcing](https://www.maplesourcing.com/how-to-perform-quality-control-checks-in-chinese-factories.html), [cosmosourcing](https://www.cosmosourcing.com/blog/quality-control-china)
- **Vendor scorecards for small shops.** Anchor on on-time delivery; add quality, order-complete rate and lead-time consistency; track 3 or 4 measures; define "on time" first (one source scores on-time 30%, PO acknowledgement and date accuracy too). Start from receiving records, not a big system. Allied's app already scores on the first promise, which is the stricter and better way. [sourceday](https://sourceday.com/blog/supplier-performance-metrics/), [netstock](https://www.netstock.com/blog/supplier-performance-management-for-smbs/)
- **Landed cost.** The usual formula is product cost + international freight + insurance + all duty layers + merchandise processing fee + harbor fee + broker fee + bond + inland freight. Broker fees quoted at $125 to $400 per entry. Section 232 duties **stack on top** of other duties, and since April 2026 are charged on the full customs value, not just the metal. Quotes that show only freight leave the customer open to surprises. [ship4wd](https://ship4wd.com/logistics-shipping/how-to-calculate-landed-cost), [clearitusa](https://clearitusa.com/?p=11069), [gingercontrol](https://gingercontrol.com/blog/how-to-estimate-landed-cost)
- **Software such firms use** (from what the searches showed, not a market study): an accounting package (here Sage 50), a work tracker (monday), a spreadsheet for quotes and landed cost, and a broker's portal for entries. I found nothing that links all four for a firm this size, which is the gap this app fills.

### (b) What monday and Sage 50 users commonly ask for
- **monday:** better reporting across boards, dependencies, a Gantt view, and complaints that the 250-action automation cap on the Standard plan and features behind higher tiers force third-party tools. No manufacturing-specific requests came up. This supports the owner's choice to keep automation in the app. [peerspot](https://peerspot.com/questions/what-needs-improvement-with-monday-com), [monday community](https://community.monday.com/t/third-party-apps-shouldn-t-be-the-answer-to-fundamental-functionality-gaps-attn-prospective-monday-customers/103852)
- **Sage 50 US:** the only direct hit was one forum request to put import costs into inventory (landed cost), plus multi-currency and multiple warehouses. Evidence is thin and old; I found no strong, current complaint about PO tracking. Sage 50 US cannot be written to from outside except by CSV import or the .NET SDK on the Sage PC (already covered in the integration guide). [Sage community](https://communityhub.sage.com/us/sage50_us/f/general-discussion/180192/product-update), [CPA Practice Advisor](https://www.cpapracticeadvisor.com/?p=15536)

### (c) Importing China and Canada steel parts: questions the app should help ask
*These are prompts for a person and the broker, not legal advice.*
1. **HTS code.** What is each part really? Ceramic-lined steel pipe might classify as steel pipe (7306), pipe fittings (7307), a ceramic article (69xx) or a structure (7308); hard-faced plate is another question. The sources show conflicting rates by heading, so each code needs a broker's call and maybe a binding ruling. Does the part have a code on file before the PO is approved?
2. **Section 232 (steel).** The April 2, 2026 proclamation (effective 6 April) moved duties to the full customs value with tiers by code and steel content, and a June 8 revision changed it again. One source puts 7307 and 7308 at 50%; another at 25%; Canadian derivatives were reported at 25% or 15%. Products with 15% or less steel are reported exempt. Where was the steel **melted and poured**, and how much of the part is steel by value? Sources: [Foley](https://www.foley.com/insights/publications/2026/05/what-every-multinational-should-know-about-the-new-rules-for-section-232-tariffs-on-steel-aluminum-and-copper-derivatives/), [Thompson Coburn](https://www.thompsoncoburn.com/insights/74-april-2-2026-new-proclamation-makes-major-revisions-to-section-232-tariffs-on-steel-aluminum-copper/), [Mondaq](https://www.mondaq.com/unitedstates/international-trade-investment/1776076/new-section-232-tariff-overhaul-winners-losers-and-unintended-consequences), [PwC Canada](https://www.pwc.com/ca/en/services/tax/publications/tax-insights/us-tariffs-steel-aluminum-copper-imports-update-2026.html).
3. **Section 301 (China).** Which list is the code on and at what rate? Sources disagreed (7.5% to 100%; one reported a separate 12.5% layer I could not confirm; another reported a Section 122 surcharge). Do any exclusions apply? Is the Section 232 article exempt from the extra universal tariff (one source says yes)? Sources: [ecovadis](https://ecovadis.com/blog/section-301-china-tariffs/), [tariffcenter](https://tariffcenter.ai/chapter/69), [greatlakescustomslaw](https://greatlakescustomslaw.com/?p=5133). **IEEPA status was reported inconsistently; confirm on CBP before quoting.**
4. **Anti-dumping / countervailing duty.** Do any of Allied's pipe fittings, flanges or forged fittings fall under the China orders on butt-weld pipe fittings, forged steel fittings and welded pipe? Scope has exclusions (for example, structural-only use, or low pressure ratings) and is being re-examined, so a scope check per code is a question for the broker. [govinfo (2023 notice)](https://www.govinfo.gov/content/pkg/FR-2023-10-10/html/2023-22368.htm), [govinfo (2018 CVD order)](https://www.govinfo.gov/content/pkg/FR-2018-11-26/html/2018-25704.htm).
5. **Canada.** Is the part USMCA-qualifying? Canadian steel still faced the full 50% in the sources, with a 25% rate reported for qualifying derivatives from 8 June 2026 and a "melted and poured in Canada or Mexico" test for relief. The US declined to renew USMCA on 1 July 2026, which starts annual reviews. [steelorbis](https://www.steelorbis.com/steel-news/latest-news/us-opens-section-232-tariff-adjustment-pathway-for-north-american-steel-producers-1450594.htm), [PwC Canada](https://www.pwc.com/ca/en/services/tax/publications/tax-insights/us-tariffs-steel-aluminum-copper-imports-update-2026.html).
6. **Country-of-origin marking.** Is each piece marked in English, permanently as the item allows? (19 CFR 134; unmarked goods can face a 10% marking duty.) This sits against the vendors' "no marking or Chinese writing" blind-ship rule, so someone must decide what is still allowed on the part itself. [19 CFR 134](https://chanrobles.com/usa/uslaws/cfr/title19/19-1.0.1.1.28.php), [exfreight](https://www.exfreight.com/what-is-isf-102-complete-guide-to-importer-security-filing-for-us-imports/).
7. **ISF (10+2).** Has the importer security filing been sent at least 24 hours before loading in China? Penalties up to $5,000 per violation are reported. Who files it: Allied or the forwarder?
8. **Mill test certificates and heat numbers.** Is there one per heat for each steel part, in English, matching the part? The app already keeps heat numbers and a quarantine list. Does the mill cert prove where the steel was melted and poured? (I found no source that settled the exact CBP rule; ask the broker.)
9. **Who is importer of record**, and does Allied hold a customs bond? Records should be kept five years (19 CFR 163.4). One source flags importer-of-record enforcement as newly tight.
10. **Buy America.** The federal rule (Build America, Buy America) applies to federally funded public infrastructure, where steel must be made in the US from melting through coating. I found nothing that reaches a private industrial plant with no federal money. The question for the app is simply "is any federal money, state grant or government customer in this job?" before a quote goes out. [archives.gov](https://www.archives.gov/grants/baba), [Delaware summary](https://dnrec.delaware.gov/climate-coastal-energy/energy-office/baba).

### (d) Simple automation ideas, ranked by hours saved for a team this size
*Hours are my rough guesses for a team of about 10 handling around 15 to 25 jobs a month. Measure one real week before quoting a number.*

| Rank | Idea | Rough hours saved a month | Notes |
|---|---|---|---|
| 1 | Weekly "invoice now / paid / overdue" list from the board plus Sage | 6 to 10 | 49 items at `AWAITING INVOICE` (confirmed unbilled) |
| 2 | Customer status digest, one email per plant, drafted for approval | 6 to 10 | Owens Corning alone is half the board |
| 3 | Vendor chase on missed promises (already built, in Suggest mode) | 5 to 8 | 15 of 17 shipments missed the first promise |
| 4 | Shipment details captured once, then tracking and demurrage run themselves | 4 to 6 | All 17 shipments lack them |
| 5 | Landed-cost and duty sheet on each quote | 4 to 6 | Also avoids mispriced quotes |
| 6 | Import-paper pack from the PO (invoice, packing list, ISF data) | 3 to 5 | Largely built; needs HTS and origin |
| 7 | Drawing-check checklist (format differences, not translation) | 3 to 5 | Already designed |
| 8 | Scanned-and-unowned alerts on the engineering board | 2 to 3 | 9 of 9 unowned |
| 9 | Photo request and approval trail | 2 to 3 | 2 now; more as volume grows |
| 10 | RFQ outcome capture | 1 to 2 | Pays back in learning, not hours |

---

## Top 10 recommended improvements for the app (best value per effort first)

Each has a one-line done-test. "Staged" means shown to a person; nothing here writes to monday.

1. **Capture shipment details once, at the moment a job turns `SHIPPED`.** Ask for forwarder, container or air-waybill number, sailing date, arrival and port free-time end, from one form or from the forwarder's email.
   *Done when* a test job set to shipped cannot be closed until those five fields are filled, and the shipment then raises the "free time ends in 2 days" alert in the Today inbox.
2. **Unbilled-work and cash list.** One page of the 49 `AWAITING INVOICE` items: vendor paid or not, days since shipped, customer terms (Net 30 to Net 150), the date cash is expected, and a drafted "please invoice" nudge for approval. The label's meaning (plant not yet invoiced) is confirmed.
   *Done when* the page lists all 49 items, oldest first, each with vendor-paid status and a "cash expected on" date, and a manager can approve one reminder draft.
3. **Import check on every China or Canada PO.** A short checklist on the job page: HTS code present, country of origin, steel melted-and-poured country, mill cert on file, ISF sent (due 24 hours before loading), anti-dumping scope checked, USMCA claim, Buy America question. Missing items are listed, never filled in.
   *Done when* a China PO with no HTS code shows "Not ready: 3 questions open" and the ISF-due date appears on the job.
4. **Names table that the team can edit.** Map board names to companies (HELEN to the Zibo firm, `OC-AMARILLO` to Owens Corning Amarillo, AMARLLO to AMARILLO), mark each vendor's country, and list board names the app could not match.
   *Done when* the unmatched-names list for the 2026 Orders board is empty or short, and a vendor job shows its company and country rather than a first name.
5. **Landed-cost sheet on every quote and PO.** Product cost, freight, duty layers (232, 301, regular), fees and the expected landed cost per part. Show "unknown" where a rate is not set and why.
   *Done when* a quote with HTS and origin filled shows the full cost build-up, and one with a blank code shows "duty unknown" instead of zero.
6. **Quote follow-up and outcomes.** Treat Athish's ledger as the quote record: list the 52 "Quote Sent" rows by age with a "chase / won / lost / plant waiting on capital" choice, and require a reason and owner on every no-bid or lost RFQ.
   *Done when* the 52 old quotes each show age and a next step, the 6 open RFQs have an owner, and the page shows a win rate with reasons for losses.
7. **Show real lead time next to the vendor's quote.** On the quote and PO screens, show "vendor says 35 days; this vendor's last 5 jobs took 50", and suggest the customer date from the real number.
   *Done when* a draft quote for HELEN's firm shows both numbers and warns if the customer date is shorter than the real lead time.
8. **Engineering-board hygiene list.** Unowned `Scanned` items, items with no status, open items with no due date, items waiting on one reviewer. Staged owner and due-date suggestions only.
   *Done when* the list shows today's 9 unowned scans and 31 unowned milestones, and picking an owner stages a change (does not send it).
9. **Photo and first-article checklist.** For a first order from a vendor, or a new part, require crate photos before and after packing and a first-article measurement sheet against the drawing, with an approve or reject step.
   *Done when* a first-time-vendor job cannot move to shipped in the app until photos and a signed sheet are attached.
10. **Plant-by-plant weekly status email** for Owens Corning first: every open job for each plant, ship dates, delays and next step, drafted for approval.
   *Done when* a manager sees one draft per plant on Monday and nothing is sent until it is approved.

**Before any of these:** items 1, 2 and 8 can be built read-only. Item 4's table and items 3, 5 and 9 need new tables, so they need the owner's go-ahead for a migration. None of them writes to monday or Sage.

---

## Leads and visits (live read, 17 Nov 2026, view only)

**There is no lead or prospect board.** Three places hold pieces of the sales path; none has a "lead" status. Contact names and phone numbers appear in the visit doc; I have not copied them here.

| Where | What it holds | Columns (type as shown) | Statuses | Rows / use today |
|---|---|---|---|---|
| **Customer Visit Tracker** (monday **Doc** table, id 7385110079) | Plant visits | Item (text, e.g. "Initial Visit: 06/18 Scan Date: 07/15"), Date (same text), Customer (text), Contact (text: name and phone), Status (free text, really visit notes) | None: no status labels or colours; the "status" cell is a paragraph of notes | **6 rows**, all June to Aug 2024. Not used since. 0% "clean" in the app's check. |
| **Athish's Board** (id 7384682515), group "Scanned Project Tracker 2024" | The opportunity and quote ledger | Name, Customer, Status, Scan Date, Cost (each), Comments | `In Engineering`, `RFQ`, `Quote Sent`, `Purchased`, `Not Going to Purhcase` (sic) | **106 rows**; 52 Quote Sent, 20 Purchased, 13 Not Going to Purchase. Last scan dates are 2024; low use now. Label colours not captured. |
| **Open RFQs** (id 1657738509) | Live quote requests | Name, Customer, Bidding vendors, Last follow up, Status, Notes | `Waiting on drawing`, `Waiting on vendor quote`, `Waiting on Freight`, `Need revised quote`, `Need markup`, `Stuck`, `Need quote checked` | **6 rows**, in use (latest follow-up 16 Nov). |
| Engineering Team Board (id 1657858464) | Work after a scan | Name, Customer, Engineer, Scan Date, Status, Estimate finish, Actual Completion | `Need to Scan`, `Scanned`, `Working on it`, `Sent for Review`, `Sent to Shop`, `Complete`, `Athish to Follow`, `Paused` | 371 rows, in use. |

**Companies in the visit tracker:** CT-Kansas City, OC-Brookville, OC-Summit, JM-Winder, GAF-Savannah, OC-Savannah. Notes name a **competitor, Kalenborn** (spelled "Kallenborn" and "Kalenborn"), already in use at OC-Summit and GAF-Savannah, and a follow-up date for most rows ("Will send follow-up email again 08/19", "follow up week of Aug 26", "week of 08/22").

**One lead traced end to end:** OC-Savannah. Visit 25 Jul 2024, scan 26 Aug 2024 (visit row), the same parts (2.36" and 2.5" elbows, material feed box, sand dust collection) appear on the Engineering board and on Athish's ledger on 26 Aug 2024, several as `Purchased`. So a visit that leads to a scan becomes a quote within days.

**Duplicates and naming drift:** the same plant is written several ways across boards: CT-Kansas City / CT-KC / CertainTeed-KC; OC-Savannah / Savannah; CertainTeed Norwood vs Boston for the same "Crusher #2 Dust Collection Pipes" job; "OC-Portland" on Athish's Board for what the Engineering board calls CertainTeed-Portland; OC-Mt Vernon / Mt. Vernon / MT VERNON. No company appears twice within one board in a way that is clearly a duplicate lead (each part is its own row), but a lead list must merge plants before counting.

**How a lead could map to a status** (a suggestion, using the existing words so monday needs no change):
| Lead step | Existing label to reuse |
|---|---|
| Plant identified, not yet visited | none today (new app-side "Prospect") |
| Visited | visit-tracker row with Initial Visit date (no label) |
| Worn part scanned | Engineering `Scanned` / Scan Date |
| Drawing in progress | `In Engineering` / `Working on it` |
| Quote requested | `RFQ` / RFQ board statuses |
| Quote sent | `Quote Sent` |
| Won / lost | `Purchased` / `Not Going to Purhcase` |

**What `SEND TO JANE` and `Athish to Follow` mean:** the data does not say. `SEND TO JANE` is only an available label on the 2025 and 2026 Orders status lists; I saw no row using it, and the owner does not know it. `Athish to Follow` is a label on the Engineering board; the owner says it marks items a senior staff member follows. Treat both as generic flags.

## Feature backlog (running list; started 17 Nov 2026)

*Beyond the top 10. "Evidence" says where the idea comes from: the live boards (B), the backup data (D) or research (R). Value and effort are rough: H/M/L. "Read-only" means it needs no new table and writes nothing to monday or Sage. Monday's setup stays as it is; every item reads from it or works beside it.*

### Cash and billing
| # | Feature | Evidence | Value / Effort | Notes |
|---|---|---|---|---|
| A1 | **Unbilled-work queue** with a one-click draft of the Sage invoice details (customer, Customer PO#, lines, ship date), oldest first | B: 49 delivered-not-billed rows, ~17 older than 5 months | H / M | Owner confirmed the meaning. Sage 50 has no native reminder automation, so the app is the place; its aging report can seed it. [Cleverence](https://www.cleverence.com/articles/sage-documentation/aged-receivables-report-sage-50-resources-4832) |
| A2 | **"Ship to billed" clock:** alert when a job has been `SHIPPED` for N days with no invoice | B | H / L | Read-only. Pairs with A1. |
| A3 | **Vendor payment calendar:** from the Payment column ("30% - $576 - 11/9") build the due dates for the 50% (before ship) and 20% (after arrival), and a weekly "pay this" list for managers | B | H / M | Parsing the text is the risky part; start by reading the three dated lines. |
| A4 | **Cash-in vs cash-out view per job:** deposit paid, balance due, customer terms (Net 30 to Net 150), expected receipt | B, D | M / M | Exposes the float Allied carries on Net 150 plants. |
| A5 | **Pay-only-after-check gate on the 50% payment:** a reminder to confirm photos or an inspection before releasing the pre-ship 50% | R | H / L | Sources note the 50% is the largest exposure, so inspect before releasing it. [qualityinspection](https://qualityinspection.org/payment-credit-chinese-factory/), [justchinait](https://www.justchinait.com/?p=11053) |
| A6 | **Credit and crate-credit ledger:** record credits taken off vendor payments (e.g. $450 crate credit) | B | L / L | Today these live in text. |

### Quotes and sales
| # | Feature | Evidence | Value / Effort | Notes |
|---|---|---|---|---|
| Q1 | **Stale-quote chaser** for the 52 "Quote Sent" rows on Athish's ledger: a polite check-in draft on a 2 / 5 / 10 / 21 / 45-day pattern, slower for plants that buy on capital cycles | B, R | H / M | The cadence is a vendor-blog suggestion and unproven; test it on your own win rate. [ymeadows](https://ymeadows.com/en/insights/newsletter/when-quotes-go-cold-resurrecting-100k-in-lost-opportunities/), [zigaflow](https://zigaflow.com/insights/quotes-dont-fail-on-price-they-fail-on-follow-up) |
| Q2 | **Capital-cycle tag** on a quote: "buys after budget approval in <month>", with a reminder a few weeks before | B | M / L | Comments already say "waiting on Capital end of 12/2024", "Plan for Feb". |
| Q3 | **Win/loss capture** with a reason list (price, lead time, budget, cannot fabricate in 300XL, no response) | B | M / L | 20 of 33 decided quotes were won; reasons are only in free text. |
| Q4 | **Past-price lookup:** type a part and see what Allied quoted for similar parts and plants | B | M / M | The ledger holds 106 priced parts; $371 to $177,011. |
| Q5 | **Repeat-order finder:** many parts recur (support plates, hammermill screens, ceramic elbows). Offer "reorder this" from the last PO | B | M / M | Cuts RFQ work for repeat parts, which the app already treats as a 1-day quote. |
| Q6 | **Quote validity and landed-cost check** before sending (links to top-10 item 5) | R | H / M | Tariff rules moved twice in 2026. |

### Vendors, production and quality
| # | Feature | Evidence | Value / Effort | Notes |
|---|---|---|---|---|
| V1 | **Vendor directory:** company, contact names, country, payment pattern (30/50/20, 100%), usual lead time, special PO rules (no markings, photos before and after packing) | B, D | H / M | Top-10 item 4 covers the names; this adds the profile. |
| V2 | **Single-contact risk panel:** show vendors that hold more than ~25% of open jobs (HELEN: 13 of 31) | B | M / L | Read-only. |
| V3 | **Defect and remake log** as fields (wrong part, missing hardware, crate damage, remake, credit) tied to PO and vendor | B | H / M | Today in Notes text. Feeds a real scorecard. |
| V4 | **Three-point inspection plan** per vendor and part: before production, during (around 20 to 50% done), before shipping; first-article sheet for new parts | R | M / M | [proqc](https://proqc.com/china/inspection-and-audit-services/), [maplesourcing](https://www.maplesourcing.com/how-to-perform-quality-control-checks-in-chinese-factories.html). Vendor blogs; sources have a commercial interest. |
| V5 | **Photo approval queue:** `EXPECTING PICS` / `NEED PICS APPROVED` rows with the photos attached and an approve/reject | B | M / M | Needs file storage (already exists in the app). |
| V6 | **Supply-chain stops:** model "plate to grinder to fabricator" as steps with the next address (Commerce Grinding, Tri-Fab, Nibrock) | B | M / M | Today a note. |
| V7 | **Drawing-approval waiting list:** `WAITING ON DRAWING APPROVAL` rows by age and who must approve | B | M / L | 3 open now. |
| V8 | **Weld/wire product line view:** stock, short lead time, 100% payment, ship from Dallas, with reorder levels | B, D | M / M | Different from fabricated parts; master items already hold stock. |

### Shipping, customs and compliance
| # | Feature | Evidence | Value / Effort | Notes |
|---|---|---|---|---|
| S1 | **Container tracking by bill of lading** using a free tier (project44 Flex: 50 containers free; Terminal49: free developer key for 100 containers) | R | H / M | Needs the B/L number on the job (top-10 item 1). Check each carrier is supported. Vendor marketing pages; confirm terms. [project44](https://www.project44.com/blog/container-tracking), [Terminal49](https://www.terminal49.com/blog/launch-week-free-global-container-tracking-is-here) |
| S2 | **Receiving log at the Dallas office:** what arrived, condition, photos, re-marking done (strip foreign marks, apply Allied tags), forward to plant | B | M / M | Goods often pass through "AWS"; the Zibo agreement asks for markings to be stripped. |
| S3 | **Tariff change watch:** a monthly "has anything changed for the codes we use?" digest from Federal Register / CBP notices for the HTS codes on file | R | M / M | 232 changed in April and again in June 2026. A person confirms; the app only lists. |
| S4 | **Duty-saving questions list** for the broker: first-sale valuation, FTZ or bonded deferral, classification review. Duty drawback looks largely closed for 232 metals | R | M / L | Sources conflict; broker decides. [Ginger Control](https://gingercontrol.com/industries/metals), [Kroll](https://www.kroll.com/en/publications/managing-us-tariff-costs-options-for-importers) |
| S5 | **Mill-cert and heat-number match** at receiving (cert says which heat; mark the part and the PO) | D | M / M | The app already keeps heat numbers. |

### Customer-facing and service
| # | Feature | Evidence | Value / Effort | Notes |
|---|---|---|---|---|
| C1 | **Wear-life and reorder planner** per plant: install date, part, expected life, next plant outage; suggest reordering one lead time before the outage | R, D | H / M | Field reports put ceramic-lined parts at several times the life of rubber-lined, and recommend replacing at ~80% wall so the swap lands in a planned shutdown. Vendor material, so measure with Allied's own plants. [Beaver](https://www.beaverprocess.com.au/wear-monitors-fimiston-gold-mine), [oxmaint](https://oxmaint.com/industries/cement-plant/remaining-useful-life-cement-plant-refractory-liner) |
| C2 | **Numbered, tagged parts:** give each shipped hose or elbow a serial that plants can drop into their maintenance system | R | M / M | The same source credits numbering for easier reordering. |
| C3 | **Plant status page or weekly digest** per customer: open jobs, ship dates, delays | B | M / M | Top-10 item 10 covers the email draft; a page is the next step. |
| C4 | **Customer visit notes** (the Visit Tracker doc) linked to the plant and its open jobs | B | L / L | The doc was not readable here. |
| C5 | **Customer's own PO intake:** read the plant's PO (often a scanned PDF) and propose the order row | D | H / M | Matches the owner's desired RFQ flow; always reviewed by a person. |

### Team and housekeeping
| # | Feature | Evidence | Value / Effort | Notes |
|---|---|---|---|---|
| T1 | **Data-quality sweep** (read-only list): duplicate PO rows (9234, 9264), rows with no ETS, old "fabrication" rows, near-duplicate names (AMARLLO, ATLACO), "OC-Portland" vs "CT-Portland" | B | M / L | The board's own "clean" score misses these. |
| T2 | **Generic status mapping:** treat unknown labels (`SEND TO JANE`, "to follow") as neutral flags the team can name in the app | B | L / L | Owner does not know what the first means. |
| T3 | **Workload view for the engineers:** open drawings per engineer, scans waiting, reviews waiting on one person | B | M / L | 12 scanned, 5 in review now. |
| T4 | **"What changed since I looked"** per person (design already in the shared notes folder) | D | M / M | Needs a migration; wait for the owner. |
| T5 | **Retire dead boards in the app's view** (Daily Work Schedule, Project Follow Up) without touching monday | B | L / L | App-side hiding only. |

### Research notes behind the list
- **Container tracking:** several vendors offer free or cheap B/L tracking; coverage varies by shipping line, so test your carriers first.
- **Payment structure:** 30% / 50% / 20 is a common, defensible pattern; the biggest risk is the 50% paid before shipping, so inspect before release. Letters of credit and platform assurance exist but are mostly for new suppliers.
- **Quote follow-up:** sources agree a structured multi-touch sequence beats one send, but exact day counts and lift figures are unsourced marketing numbers.
- **Collections:** Sage 50's aging report is useful as a worklist; I found no Sage 50 feature that sends reminders on its own.
- **Spares planning:** the suppliers who win on wear parts tie delivery to plant outage dates and give life data; this fits Allied's design role.
- **Duty cost:** after April 2026, valuation, classification and country of melt matter most; drawback looks mostly closed for 232 metals; FTZ or bond only defers cost.

---

## Open questions for the owner

1. **Answered:** `AWAITING INVOICE` = not yet billed to the plant. Still open: what are the 49 items worth?
2. **Answered:** nobody knows what `SEND TO JANE` means; the "to follow" status marks a senior staff member's items. Both are treated as generic labels. Monday's setup stays as it is.
2a. The 5 Oct to Dec 2025 "fabrication" rows on 2025 Orders, and POs 9234 and 9264 shown twice: finished, or real?
2b. Do the plants really buy on capital cycles, and should the 52 old "Quote Sent" rows be chased or closed?
3. Which vendor names are companies, which are contacts, and which are in the US, Canada or China?
4. Who is importer of record and who files the ISF: Allied, the forwarder or the broker?
5. Are the supplier-agreement and estimate files real or sample? Their terms disagree with the vendor master.

---

*Sources used (web, standard search; all read 17 Nov 2026):* Section 232 reference and law-firm briefings ([Green Worldwide guide](https://www.greenworldwide.com/wp-content/uploads/2026/10/Updated-Section-232-Metals-Reference-Guide-V3.0-UPDATED-6.8.26.pdf), [Mondaq](https://www.mondaq.com/unitedstates/international-trade-investment/1776076/new-section-232-tariff-overhaul-winners-losers-and-unintended-consequences), [Foley](https://www.foley.com/insights/publications/2026/05/what-every-multinational-should-know-about-the-new-rules-for-section-232-tariffs-on-steel-aluminum-and-copper-derivatives/), [Thompson Coburn](https://www.thompsoncoburn.com/insights/74-april-2-2026-new-proclamation-makes-major-revisions-to-section-232-tariffs-on-steel-aluminum-copper/), [Thomson Reuters](https://www.thomsonreuters.com/en-us/help/onesource-global-trade/regulatory-insights/2026/june-25th/new-us-sec-232-301-tariffs-ior-reform)); Canada and USMCA ([PwC Canada](https://www.pwc.com/ca/en/services/tax/publications/tax-insights/us-tariffs-steel-aluminum-copper-imports-update-2026.html), [SteelOrbis](https://www.steelorbis.com/steel-news/latest-news/us-opens-section-232-tariff-adjustment-pathway-for-north-american-steel-producers-1450594.htm)); Section 301 ([EcoVadis](https://ecovadis.com/blog/section-301-china-tariffs/), [Tariff Center ch. 69](https://tariffcenter.ai/chapter/69)); ISF and origin marking ([ExFreight](https://www.exfreight.com/what-is-isf-102-complete-guide-to-importer-security-filing-for-us-imports/), [19 CFR 134](https://chanrobles.com/usa/uslaws/cfr/title19/19-1.0.1.1.28.php)); anti-dumping ([Federal Register 2023](https://www.govinfo.gov/content/pkg/FR-2023-10-10/html/2023-22368.htm)); inspection ([Pro QC](https://proqc.com/china/inspection-and-audit-services/), [Maple Sourcing](https://www.maplesourcing.com/how-to-perform-quality-control-checks-in-chinese-factories.html)); landed cost ([Ship4wd](https://ship4wd.com/logistics-shipping/how-to-calculate-landed-cost), [Clearit](https://clearitusa.com/?p=11069)); scorecards ([SourceDay](https://sourceday.com/blog/supplier-performance-metrics/)); monday ([PeerSpot](https://peerspot.com/questions/what-needs-improvement-with-monday-com)); Sage ([Sage Community Hub](https://communityhub.sage.com/us/sage50_us/f/general-discussion/180192/product-update)); Buy America ([National Archives](https://www.archives.gov/grants/baba)). Most are vendor or law-firm summaries; confirm every rate against the Federal Register, HTSUS and CBP notices.
