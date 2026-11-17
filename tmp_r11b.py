def edit(path, old, new):
    s = open(path, encoding="utf-8").read()
    assert old in s, (path, old[:80])
    open(path, "w", encoding="utf-8").write(s.replace(old, new, 1))


# ============================================================ monday "Send PO": cancelled and already-sent POs
E = "app/services/monday_events.py"
edit(E, '''    sent = _pos_sent(item_id) | outbound_email_service.really_sent("PURCHASE_ORDER")      # the send ledger, not the in-memory outbox
    wanted = {str(po) for po in (order["purchase_orders"] or {}).values()}
    if wanted and wanted <= sent:
        return {"skipped": "every purchase order for this item was already sent", "sent": sorted(wanted)}
''', '''    sent = _pos_sent(item_id) | outbound_email_service.really_sent("PURCHASE_ORDER")      # the send ledger, not the in-memory outbox
    from app.core.database import SessionLocal as _Session
    from app.models.db_models import PurchaseOrderDB as _PO

    with _Session() as _db:          # the purchase order records know about sends from before the ledger existed, and about cancellations
        _rows = _db.query(_PO.po_number, _PO.status).filter(_PO.po_number.isnot(None)).all()
    sent |= {str(n) for n, st in _rows if st in ("SENT", "ACKNOWLEDGED", "SENDING")}
    cancelled = {str(n) for n, st in _rows if st == "CANCELLED"}
    wanted = {str(po) for po in (order["purchase_orders"] or {}).values()}
    if wanted and (wanted - cancelled) <= sent:
        return {"skipped": "every purchase order for this item was already sent or cancelled", "sent": sorted(wanted & sent), "cancelled": sorted(wanted & cancelled)}
''')
edit(E, '''        if str(po_number) in sent:
            outcomes[vendor_code] = {"po": po_number, "status": "sent earlier"}
            continue
        vendor = vendors.get(vendor_code, {})''', '''        if str(po_number) in cancelled:
            outcomes[vendor_code] = {"po": po_number, "status": "cancelled, not sent"}
            continue
        if str(po_number) in sent:
            outcomes[vendor_code] = {"po": po_number, "status": "sent earlier"}
            continue
        vendor = vendors.get(vendor_code, {})''')

# ============================================================ Sage: line count, reasons, dates, per-thread skip list
S = "app/services/sage_export_service.py"
edit(S, '''    last_skipped: List[str] = []

    def _skip(self, why: str) -> None:
        """An order or line left out of a file is said so (log and `last_skipped`), never silently."""
        logger.warning(f"Sage export: {why}")
        self.last_skipped.append(why)''', '''    _tls = threading.local()

    @property
    def last_skipped(self) -> List[str]:
        """What the last export on THIS thread left out (two exports at once each see their own list)."""
        return self._tls.__dict__.setdefault("skipped", [])

    @last_skipped.setter
    def last_skipped(self, value: List[str]) -> None:
        self._tls.skipped = value

    def _skip(self, why: str) -> None:
        """An order or line left out of a file is said so (log and `last_skipped`), never silently."""
        logger.warning(f"Sage export: {why}")
        self.last_skipped.append(why)''')
edit(S, "import re\nfrom datetime", "import re\nimport threading\nfrom datetime")
edit(S, '''            orders = [o for o in orders if customer_filter.lower() in o.get("customer_name", "").lower()]''', '''            orders = [o for o in orders if customer_filter.lower() in str(o.get("customer_name") or "").lower()]''')
edit(S, '''            try:
                po_date = sage_date(o.get("po_date"))
                rate = self._rate(o.get("currency"))''', '''            if not o.get("po_date") or not str(o.get("po_date")).strip():
                self._skip(f"order {so_num} has no order date, so it was not exported (an invented date could put it in the wrong accounting period)")
                continue
            try:
                po_date = sage_date(o.get("po_date"))
                rate = self._rate(o.get("currency"))''')
edit(S, '''                if not item_id:
                    logger.warning(
                        f"Skipping a line on {so_num}: no item_id. Defaulting it to a "
                        f"wear-plate SKU would export the wrong part to Sage."
                    )
                    continue''', '''                if not item_id:
                    self._skip(f"a line on sales order {so_num} has no item id, so that line was left out")
                    continue''')
edit(S, '''                if price is None:
                    logger.warning(f"Skipping {item_id} on {so_num}: no unit price on the order line.")
                    continue
                qty_d = _dec(''', '''                if price is None:
                    self._skip(f"{safe_text(item_id, 40)} on sales order {so_num} has no unit price, so that line was left out")
                    continue
                qty_d = _dec(''')
edit(S, '''            if distribution:
                order_rows.append((so_num, rows))

        marked: List[str] = []''', '''            if distribution:
                for r in rows:                              # every row says how many lines the document REALLY has (lines left out above are not counted)
                    r[4] = distribution
                order_rows.append((so_num, rows))
            else:
                self._skip(f"sales order {so_num} has no usable line, so it was not exported")

        marked: List[str] = []''')

# ============================================================ packing slip: redact the same variants the blind check looks for, names fetched once
K = "app/services/packing_slips.py"
a = open(K, encoding="utf-8").read()
start = a.index("def _without_vendor_names")
end = a.index("class PackingSlipGenerator")
new = '''def _vendor_name_set(extra: object = None) -> set:
    """Every vendor name Allied knows (fetched once per slip) plus any extra ones."""
    names = {str(extra or "").strip()}
    try:
        from app.services.coordination import blind

        names |= set(blind.vendor_names())
    except Exception:  # noqa: BLE001 - the vendor list is a help; the line's own vendor is always removed
        pass
    return names


def _without_vendor_names(text: str, line_vendor: object = None, names: object = None) -> str:
    """A description for the customer: any vendor's name typed into it is taken out, in the usual short forms ("KWS" for "KWS Ltd"), the same ways
    the blind-shipment check looks for them. A blind shipment must never tell the customer who makes the part."""
    import re

    pool = set(names) if names else _vendor_name_set()
    pool.add(str(line_vendor or "").strip())
    out = str(text or "")
    forms = set()
    try:
        from app.services.coordination import blind

        for n in pool:
            if n and n.upper() != "STOCK":
                forms |= {v for v, _floor in blind._variants(n) if len(v.replace(" ", "")) >= 3}
    except Exception:  # noqa: BLE001
        forms = {n for n in pool if len(n) >= 3}
    for name in sorted(forms | {n for n in pool if len(n) >= 3 and n.upper() != "STOCK"}, key=len, reverse=True):
        out = re.sub(r"(?<!\\w)" + re.escape(name) + r"(?!\\w)", "", out, flags=re.I)
    return " ".join(out.split())


'''
open(K, "w", encoding="utf-8").write(a[:start] + new + a[end:])
edit(K, "        for item in order.line_items:", "        _vendor_names = _vendor_name_set()\n        for item in order.line_items:")
edit(K, "_without_vendor_names(item.description or item.raw_item_text, item.preferred_vendor)", "_without_vendor_names(item.description or item.raw_item_text, item.preferred_vendor, _vendor_names)")

# ============================================================ PO fields
O = "app/services/order_documents.py"
edit(O, '''        if i.get("cert_required") in (True, "true", "True", "yes", 1):
            extra["cert_required"] = True''', '''        if i.get("cert_required") is True or i.get("cert_required") == 1 or str(i.get("cert_required") or "").strip().casefold() in ("true", "yes", "y", "1", "required", "x"):
            extra["cert_required"] = True''')
edit(O, '''            v = " ".join("".join(ch for ch in re.sub(r"[\\x00-\\x1f\\x7f]+", " ", str(i.get(key) or "")) if unicodedata.category(ch) != "Cf").split())[:limit]
            if v:
                extra[key] = v''', '''            v = " ".join("".join(ch for ch in re.sub(r"[\\x00-\\x1f\\x7f]+", " ", str(i.get(key) or "")) if unicodedata.category(ch) != "Cf").split())[:limit]
            if key == "hts_code" and v and not re.fullmatch(r"\\d{4}(?:\\.\\d{2}(?:\\.\\d{2,4})?)?|\\d{6,10}", v, re.ASCII):
                v = ""                              # a tariff number only: "banana", "12" and "N/A" are not kept and printed for customs
            if v:
                extra[key] = v''')

P = "app/services/coordination/purchase_orders.py"
edit(P, '''        if "incoterm" in changes:
            row.incoterm = re.sub(r"[^A-Za-z0-9 ]+", "", str(changes["incoterm"] or "")).strip().upper()[:16] or None''', '''        if "incoterm" in changes:
            term = re.sub(r"[^A-Za-z0-9 ]+", "", str(changes["incoterm"] or "")).strip().upper()[:16]
            if term and term not in _INCOTERMS:
                raise jobs.JobError("The Incoterm must be one of: " + ", ".join(sorted(_INCOTERMS)) + " (or left blank).")
            row.incoterm = term or None''')
edit(P, "STATUSES = (", '_INCOTERMS = {"EXW", "FCA", "CPT", "CIP", "DAP", "DPU", "DDP", "FAS", "FOB", "CFR", "CIF"}       # the eleven Incoterms 2020\n_CUSTOMS_KEYS = ("hts_code", "origin", "uom", "net_kg", "cert_required")\nSTATUSES = (')
edit(P, '''        if "lines" in changes:
            if row.status != "DRAFT":
                raise jobs.JobError("The lines can only change while the purchase order is a draft, before it is approved.")''', '''        if "lines" in changes and row.status != "DRAFT":
            # After approval the lines are fixed, except the customs details (HTS code, origin, unit, weight, certificate), which are often only known later.
            new_lines = _typed_lines(changes["lines"]) if isinstance(changes["lines"], list) else None
            old_lines = [ln for ln in (row.lines or []) if isinstance(ln, dict)]
            core = ("qty", "item", "description", "unit_cost", "amount", "revision")
            if row.status != "APPROVED" or new_lines is None or len(new_lines) != len(old_lines) or any(
                    any(a.get(k) != b.get(k) for k in core) for a, b in zip(old_lines, new_lines)):
                raise jobs.JobError("The lines can only change while the purchase order is a draft, before it is approved (only the customs details can be added later).")
            row.lines = new_lines
            changes = {k: v for k, v in changes.items() if k != "lines"}
        if "lines" in changes:
            if row.status != "DRAFT":
                raise jobs.JobError("The lines can only change while the purchase order is a draft, before it is approved.")''')

# ============================================================ migration downgrade, prune spelling, cloud folders, inbox, sales order bounds
M = "migrations/versions/l5b7d9e1f3a5_po_after_cancel_and_overseas.py"
edit(M, '''    op.execute("DROP INDEX IF EXISTS ux_po_job_vendor")
    try:
        op.execute("CREATE UNIQUE INDEX IF NOT EXISTS ux_po_job_vendor ON purchase_orders (job_id, lower(vendor))")
    except Exception:  # noqa: BLE001 - a new PO after a cancel can leave two rows for one vendor: the plain index cannot come back until one is removed
        pass
    op.drop_column''', '''    op.execute("DROP INDEX IF EXISTS ux_po_job_vendor")
    dupes = op.get_bind().execute(sa.text("SELECT 1 FROM purchase_orders GROUP BY job_id, lower(vendor) HAVING count(*) > 1 LIMIT 1")).fetchone()
    # A new PO after a cancel can leave two rows for one vendor and job. Then the old rule cannot come back as a UNIQUE index (nothing is deleted); a plain
    # index of the same name keeps lookups fast, and one live PO per vendor per job is then kept by the application.
    op.execute(("CREATE INDEX IF NOT EXISTS ux_po_job_vendor ON purchase_orders (job_id, lower(vendor))" if dupes else
                "CREATE UNIQUE INDEX IF NOT EXISTS ux_po_job_vendor ON purchase_orders (job_id, lower(vendor))"))
    op.drop_column''')
edit(S.replace("sage_export_service", "outbound_email_service"), "OutboundMailDB.category.notin_(tuple(_FAIL_CLOSED))", "func.upper(func.trim(OutboundMailDB.category)).notin_(tuple(_FAIL_CLOSED))")
edit("app/services/outbound_email_service.py", "        from app.core.database import SessionLocal\n        from app.models.db_models import OutboundMailDB\n\n        cutoff", "        from sqlalchemy import func\n\n        from app.core.database import SessionLocal\n        from app.models.db_models import OutboundMailDB\n\n        cutoff")
edit("app/services/backups.py", '''        low = str(there).casefold()
        if not settings.BACKUP_COPY_ALLOW_SYNCED and any(w in low for w in ("onedrive", "dropbox", "google drive", "googledrive", "icloud", "box sync")):''', '''        synced = ("onedrive", "dropbox", "google drive", "googledrive", "icloud", "box", "pcloud", "mega", "nextcloud", "sharepoint", "sync", "tresorit")
        if not settings.BACKUP_COPY_ALLOW_SYNCED and any(part.casefold().startswith(synced) for part in there.parts):''')
edit("app/services/assistant/agent.py", "for r in rows if workdays.as_utc(r.created_at) >= now - _ttl(r)][:100]", "for r in rows if workdays.as_utc(r.created_at) >= now - _ttl(r)][:500]")
edit("app/services/assistant/agent.py", ".order_by(AssistantActionDB.created_at.desc()).limit(100).all())", ".order_by(AssistantActionDB.created_at.desc()).limit(500).all())")

J = "app/services/coordination/jobs.py"
edit(J, '''            if qty is None or qty <= 0 or qty > 1_000_000:
                raise JobError(f"Line {n}: the quantity must be a plain number above zero, like 2 or 12.5.")''', '''            if qty is None or qty <= 0 or qty > 1_000_000 or qty != qty.to_integral_value():
                raise JobError(f"Line {n}: the quantity must be a whole number of pieces above zero, like 2.")''')
edit(J, '''            if price < 0 or price > 10_000_000:
                raise JobError(f"Line {n}: the unit price must be between 0 and 10,000,000.")''', '''            if price < 0 or price > 10_000_000:
                raise JobError(f"Line {n}: the unit price must be between 0 and 10,000,000.")
            if price != price.quantize(price.__class__("0.01")):
                raise JobError(f"Line {n}: the unit price must be to the cent (two decimals at most), like 3772.91.")''')
print("ok")
