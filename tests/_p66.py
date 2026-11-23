def edit(p, pairs):
    s = open(p, encoding="utf-8").read()
    for a, b in pairs:
        assert a in s, (p, a[:90])
        s = s.replace(a, b, 1)
    open(p, "w", encoding="utf-8").write(s)


edit("app/api/v1/endpoints/orders.py", [
    ('''    db: Session = Depends(get_db),
    _user: dict = Depends(require_viewer),
) -> OrderPage:
    """The orders table. Reads the database, so it survives restarts, unlike the cache."""''', '''    db: Session = Depends(get_db),
    _user: dict = Depends(require_viewer),
) -> OrderPage:
    """The orders table. Reads the database, so it survives restarts, unlike the cache. Cost and margin are for managers; everyone else gets null."""'''),
    ('''    _attach_job(page["items"])
    return OrderPage(''', '''    _attach_job(page["items"])
    from app.core.money_view import hide_orders

    hide_orders(page["items"], _user)
    return OrderPage('''),
    ('''    order["min_margin_pct"] = float(settings.MIN_GROSS_MARGIN_PCT)
    return order''', '''    order["min_margin_pct"] = float(settings.MIN_GROSS_MARGIN_PCT)
    from app.core.money_view import hide_order

    return hide_order(order, _user)'''),
])
edit("app/api/v1/endpoints/jobs.py", [
    ('''def list_pos(job: str, _user: dict = Depends(require_viewer)) -> List[Dict[str, Any]]:
    try:
        return purchase_orders.for_job(jobs.get(job)["id"])''', '''def list_pos(job: str, _user: dict = Depends(require_viewer)) -> List[Dict[str, Any]]:
    from app.core.money_view import hide_pos

    try:
        return hide_pos(purchase_orders.for_job(jobs.get(job)["id"]), _user)'''),
    ('''    try:
        return jobs.get(job, staff=_staff(user))
    except (LookupError, jobs.JobError, ValueError) as exc:
        _fail(exc)
''', '''    from app.core.money_view import hide_pos

    try:
        out = jobs.get(job, staff=_staff(user))
        if isinstance(out.get("purchase_orders"), list):
            hide_pos(out["purchase_orders"], user)
        return out
    except (LookupError, jobs.JobError, ValueError) as exc:
        _fail(exc)
'''),
])
edit("app/api/v1/endpoints/purchase_orders.py", [
    ('''def one(po_id: int, _user: dict = Depends(require_viewer)) -> Dict[str, Any]:
    try:
        return purchase_orders.get(po_id)''', '''def one(po_id: int, _user: dict = Depends(require_viewer)) -> Dict[str, Any]:
    from app.core.money_view import hide_po

    try:
        return hide_po(purchase_orders.get(po_id), _user)'''),
    ('''    return purchase_orders.unified(q, vendor, status_text, staff=user.get("role") in ("ADMIN", "MANAGER", "OPERATOR"))''', '''    from app.core.money_view import hide_pos

    return hide_pos(purchase_orders.unified(q, vendor, status_text, staff=user.get("role") in ("ADMIN", "MANAGER", "OPERATOR")), user)'''),
])
edit("app/api/v1/endpoints/inbound_email.py", [
    ('''    try:
        return inbound_email_daemon.process_inbound_email(email)''', '''    from app.config import settings

    if not settings.INBOX_SIMULATION_ENABLED:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This does not rehearse anything: it would make a real sales order and real vendor purchase orders. Practice orders are switched off on this system. Use the demo copy, or ask the owner to turn on INBOX_SIMULATION_ENABLED on a test system.",
        )
    try:
        return inbound_email_daemon.process_inbound_email(email)'''),
])
edit("app/config.py", [
    ('    ENGINEER_APPROVERS: str = ""', '    INBOX_SIMULATION_ENABLED: bool = False    # POST /inbox/simulate makes a REAL order and vendor POs; off unless this is a test or demo system\n    ENGINEER_APPROVERS: str = ""'),
])
edit("app/services/backups.py", [
    ('''    if items:
        return {"state": "unknown", "text": f"The newest backup was made at {when(items[0]['made'])}. No backup has run since the app started.", "details": "", "last_good": items[0]["made"]}''', '''    if items:                                                                           # no run since the app started: judged by the age of the newest file, not called a problem
        age_h = (datetime.now(timezone.utc) - datetime.fromisoformat(items[0]["made"])).total_seconds() / 3600
        fresh = age_h <= max(2, settings.BACKUP_EVERY_HOURS * 2)
        return {"state": "ok" if fresh else "stale", "text": f"The newest backup was made at {when(items[0]['made'])}." + ("" if fresh else f" That is {int(age_h)} hours ago; the next one runs on schedule."), "details": "", "last_good": items[0]["made"]}'''),
])
print("ok")
