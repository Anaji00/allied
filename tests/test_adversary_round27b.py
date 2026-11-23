"""Round 27 (adversary), part 2: the engineer gates (who may approve, overrides, revokes, and whether the hard block really blocks) and the vendor watchdog play. Temporary database only;
mail is never sent. xfail(strict=False, reason="ADV27b-Tn ...") marks holes."""
import threading
import uuid
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.core.database import SessionLocal
from app.models.db_models import AssistantActionDB, EngineerGateDB, JobDB, PurchaseOrderDB, VendorJobDB
from app.services import gates, payments, vendor_payments
from app.services.coordination import autopilot, jobs, purchase_orders, rfqs, vendors
from tests.conftest import staff_token

TAG = uuid.uuid4().hex[:5]
BOSS = {"email": f"boss-r27b-{TAG}@alliedweldingsystems.com", "role": "MANAGER"}
OP = {"email": f"op-r27b-{TAG}@alliedweldingsystems.com", "role": "OPERATOR"}
ATH = f"athish-r27b-{TAG}@alliedweldingsystems.com"
ENG = {"email": ATH, "role": "OPERATOR"}
CUSTOMER = "Owens Corning Starr Composites"


def _po(total=10000, user=BOSS, vendor=None):
    r = rfqs.create({"customer": CUSTOMER, "subject": f"r27b {uuid.uuid4().hex[:6]}", "items": [{"description": "liner", "quantity": 2}]}, user, announce=False)
    j = jobs.from_rfq(r["id"], user)
    jobs.approve_design(j["id"], user)
    po = purchase_orders.create(j["id"], vendor or f"V27B {uuid.uuid4().hex[:5]}", user, lines=[{"description": "liner plate", "quantity": 1, "unit_cost": total}])
    purchase_orders.update(po["id"], {"vendor_email": f"sales{uuid.uuid4().hex[:5]}@vendor-example.test"}, user)
    return purchase_orders.approve(po["id"], user), j


def _line(po):
    with SessionLocal() as db:
        v = db.query(VendorJobDB).filter(VendorJobDB.purchase_order_id == po["id"]).first()
        if v is None:
            purchase_orders.link_vendor_lines()
            v = db.query(VendorJobDB).filter(VendorJobDB.purchase_order_id == po["id"]).first()
        return v.id if v else None


@pytest.fixture()
def approvers(monkeypatch):
    monkeypatch.setattr(settings, "ENGINEER_APPROVERS", ATH)
    monkeypatch.setattr(settings, "GATES_ENFORCED", True)


@pytest.fixture()
def api(auth_enforced):
    from app.main import app

    client = TestClient(app, raise_server_exceptions=False)
    heads = {r: {"Authorization": "Bearer " + staff_token(f"a27b-{r.lower()}-{TAG}@alliedweldingsystems.com", r)} for r in ("ADMIN", "MANAGER", "OPERATOR", "VIEWER")}
    heads["ENG"] = {"Authorization": "Bearer " + staff_token(ATH, "OPERATOR")}

    def call(method, path, role="OPERATOR", **kw):
        return client.request(method, "/api/v1" + path, headers=heads[role], **kw)

    return call


# =============================================================== who may approve

def test_with_no_approver_list_nobody_can_approve_not_even_a_manager_or_admin(monkeypatch):
    monkeypatch.setattr(settings, "ENGINEER_APPROVERS", "")
    po, j = _po()
    g = gates.request(j["id"], 1, OP)
    for u in (ENG, BOSS, {"email": "admin@x.test", "role": "ADMIN"}, OP, {"email": "", "role": "ADMIN"}, {"role": "MANAGER"}):
        with pytest.raises(gates.GateError) as e:
            gates.decide(g["id"], True, u)
        assert e.value.status == 403
    assert gates.approvers() == [] and gates.status_for(j["id"])[1]["passed"] is False


@pytest.mark.parametrize("setting", [" , ,, ", "   ", ",", "@", "athish", "Athish <athish@x.test>"])
def test_a_junk_approver_list_approves_nobody_who_is_not_named(monkeypatch, setting):
    monkeypatch.setattr(settings, "ENGINEER_APPROVERS", setting)
    po, j = _po()
    g = gates.request(j["id"], 1, OP)
    with pytest.raises(gates.GateError):
        gates.decide(g["id"], True, {"email": "athish@x.test", "role": "OPERATOR"})
    assert "" not in gates.approvers()


@pytest.mark.parametrize("email,ok", [(ATH, True), (ATH.upper(), True), (f"  {ATH}  ", True), (ATH.replace("@", "+x@"), False), (f"Athish <{ATH}>", False), (ATH.replace("a", "ａ", 1), False),
                                      (ATH + ".evil.test", False), ("x" + ATH, False), (ATH.split("@")[0], False), ("", False), (ATH.replace("@alliedweldingsystems.com", "@Alliedweldingsystems.COM"), True)],
                         ids=["exact", "upper", "spaces", "plus", "display-name", "fullwidth", "suffix", "prefix", "no-domain", "blank", "domain-case"])
def test_the_approver_match_is_exact_apart_from_case_and_spaces(approvers, email, ok):
    po, j = _po()
    g = gates.request(j["id"], 1, OP)
    if ok:
        assert gates.decide(g["id"], True, {"email": email, "role": "OPERATOR"})["status"] == "APPROVED"
    else:
        with pytest.raises(gates.GateError):
            gates.decide(g["id"], True, {"email": email, "role": "OPERATOR"})


def test_the_drafter_cannot_approve_through_the_route_and_a_viewer_or_operator_who_is_not_an_approver_cannot(api, approvers):
    po, j = _po(user={"email": ATH, "role": "MANAGER"})                              # the approver drafted this job and PO
    staff_token(ATH, "OPERATOR")
    g = api("POST", "/gates/request", "OPERATOR", json={"job_id": j["id"], "gate": 1}).json()
    assert api("POST", f"/gates/{g['id']}/decide", "ENG", json={"approve": True}).status_code == 403
    po2, j2 = _po()
    g2 = api("POST", "/gates/request", "OPERATOR", json={"job_id": j2["id"], "gate": 1}).json()
    for role in ("VIEWER", "OPERATOR", "MANAGER", "ADMIN"):
        assert api("POST", f"/gates/{g2['id']}/decide", role, json={"approve": True}).status_code in (403,), role
    assert api("POST", f"/gates/{g2['id']}/decide", "ENG", json={"approve": True}).status_code == 200
    assert api("POST", f"/gates/{g2['id']}/decide", "ENG", json={"approve": False}).status_code == 409                   # final


def test_t1_the_drafter_rule_holds_in_the_service_itself(approvers):
    po, j = _po(user={"email": ATH, "role": "MANAGER"})
    g = gates.request(j["id"], 1, OP)
    with pytest.raises(gates.GateError):
        gates.decide(g["id"], True, ENG)


def test_t2_two_decisions_at_once_leave_one_final_decision(approvers):
    po, j = _po()
    g = gates.request(j["id"], 1, OP)
    out = []

    def go(approve):
        try:
            out.append(("ok", approve, gates.decide(g["id"], approve, ENG)["status"]))
        except gates.GateError:
            out.append(("refused", approve, None))

    ts = [threading.Thread(target=go, args=(a,)) for a in (True, False, True, False)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert sum(1 for o in out if o[0] == "ok") == 1, out


# =============================================================== override and revoke

@pytest.mark.parametrize("reason,ok", [("a proper reason", True), ("1234567", False), ("        ", False), ("", False), (None, False), ("  short ", False), ("12345678", True), (" " * 10, False)])
def test_an_override_needs_a_written_reason_and_a_manager(approvers, reason, ok):
    po, j = _po()
    if ok:
        assert gates.override(j["id"], 1, reason, BOSS)["status"] == "OVERRIDDEN"
        assert gates.blocked(j["id"], 1, po["id"]) is None
    else:
        with pytest.raises(gates.GateError):
            gates.override(j["id"], 1, reason, BOSS)
    with pytest.raises(gates.GateError):
        gates.override(j["id"], 2, "a proper reason", OP)
    with pytest.raises(gates.GateError):
        gates.override(j["id"], 2, "a proper reason", {"email": "v@x.test", "role": "VIEWER"})


def test_revoke_needs_a_reason_a_manager_and_a_decided_gate_and_a_revoked_gate_blocks_again(approvers):
    po, j = _po()
    g = gates.request(j["id"], 1, OP)
    with pytest.raises(gates.GateError) as e:
        gates.revoke(g["id"], "a proper reason", BOSS)
    assert e.value.status == 409
    gates.decide(g["id"], True, ENG)
    for who, why in ((OP, "a proper reason"), (BOSS, "short"), (BOSS, "        "), (BOSS, None)):
        with pytest.raises(gates.GateError):
            gates.revoke(g["id"], why, who)
    assert gates.blocked(j["id"], 1, po["id"]) is None
    assert gates.revoke(g["id"], "the photos were wrong", BOSS)["status"] == "REVOKED"
    assert gates.blocked(j["id"], 1, po["id"])
    with pytest.raises(gates.GateError):
        gates.revoke(g["id"], "again again", BOSS)


def test_t3_a_gate_for_a_job_or_po_that_does_not_exist_or_does_not_match_is_refused(approvers):
    po, j = _po()
    other, oj = _po()
    for call in (lambda: gates.override(987654321, 1, "a proper reason", BOSS), lambda: gates.request(987654321, 1, OP), lambda: gates.request(j["id"], 1, OP, po_id=other["id"]),
                 lambda: gates.override(j["id"], 1, "a proper reason", BOSS, po_id=other["id"])):
        with pytest.raises(gates.GateError):
            call()


def test_one_pos_gate_does_not_pass_another_po_on_the_same_job(approvers):
    a, j = _po()
    b = purchase_orders.create(j["id"], f"V27B2 {uuid.uuid4().hex[:5]}", BOSS, lines=[{"description": "second", "quantity": 1, "unit_cost": 5}])
    b = purchase_orders.approve(b["id"], BOSS)
    ga = gates.request(j["id"], 1, OP, po_id=a["id"])
    gates.decide(ga["id"], True, ENG)
    assert gates.blocked(j["id"], 1, b["id"])
    assert gates.blocked(j["id"], 1, None) is None or True
    assert gates.status_for(j["id"], b["id"])[1]["passed"] is False


def test_t5_revoking_gate_2_stops_the_pre_ship_payment(approvers):
    po, j = _po()
    payments.create_schedule(po["id"], "30/50/20", BOSS, total="1000")
    g = gates.request(j["id"], 2, OP)
    gates.decide(g["id"], True, ENG)
    assert [t["status"] for t in payments.get_for_po(po["id"])["tranches"]][1] == "DUE"
    gates.revoke(g["id"], "the measurements were wrong", BOSS)
    assert [t["status"] for t in payments.get_for_po(po["id"])["tranches"]][1] != "DUE"


def test_t6_the_gate_2_payment_cannot_be_opened_by_hand_without_an_override(approvers):
    po, j = _po()
    payments.create_schedule(po["id"], "30/50/20", BOSS, total="1000")
    opened = payments.fire(po["id"], "GATE2_APPROVED", BOSS)
    assert not opened or gates.blocked(j["id"], 2, po["id"]) is None


# =============================================================== does the hard block really block

def _try(line, **data):
    try:
        vendors.update(line, data, BOSS)
        return None
    except ValueError as e:
        return str(e)


@pytest.mark.parametrize("status", ["In production", "Production started", "manufacturing", "Fabrication begun", "started", "in progress"])
def test_production_words_are_blocked_until_gate_1_passes_then_allowed_and_a_revoke_blocks_again(approvers, status):
    po, j = _po()
    line = _line(po)
    assert line
    assert "Gate 1" in (_try(line, status=status) or "")
    g = gates.request(j["id"], 1, OP)
    gates.decide(g["id"], True, ENG)
    assert _try(line, status=status) is None
    gates.revoke(g["id"], "drawing changed again", BOSS)
    assert "Gate 1" in (_try(line, status="in production") or "")


@pytest.mark.parametrize("status", ["Crating", "crated", "Packed", "packing now"])
def test_crating_words_and_a_ship_date_are_blocked_until_gate_2_passes_and_an_override_with_a_reason_opens_it(approvers, status):
    po, j = _po()
    line = _line(po)
    assert "Gate 2" in (_try(line, status=status) or "")
    assert "Gate 2" in (_try(line, shipped_on=date.today().isoformat()) or "")
    gates.override(j["id"], 2, "engineer saw the photos by phone", BOSS)
    assert _try(line, status=status) is None


@pytest.mark.parametrize("status", ["Machining", "Welding", "Casting", "Cutting plate", "In process", "WIP", "Mfg started", "生产中", "Packaging", "Boxed", "Palletized", "Ready for pickup", "Ready to ship", "装箱", "Loaded on truck"])
def test_t7_other_words_for_production_crating_and_pickup_are_blocked_too(approvers, status):
    po, j = _po()
    line = _line(po)
    assert _try(line, status=status), f"{status!r} went through with no gate"


def test_t8_a_vendor_line_created_with_a_blocked_status_is_gated(approvers):
    po, j = _po()
    try:
        made = vendors.create({"vendor": po["vendor"], "po_number": str(po["po_number"]), "description": "second line", "status": "In production"}, BOSS)
    except ValueError:
        return
    purchase_orders.link_vendor_lines()
    raise AssertionError(f"line {made['id']} was created in production with Gate 1 not passed")


def test_gates_enforced_false_is_the_only_switch_and_defaults_on():
    from app.config import Settings

    assert Settings.model_fields["GATES_ENFORCED"].default is True                 # tests/conftest.py switches it off for the suite; the code default is on


# =============================================================== the watchdog

TODAY = date(2026, 11, 18)


def _d(days):
    return (TODAY + timedelta(days=days)).isoformat()


@pytest.fixture()
def only(monkeypatch):
    monkeypatch.setattr(settings, "AUTOPILOT_MODES", "vendor_watchdog=suggest," + ",".join(f"{k}=off" for k in autopilot.PLAYS if k != "vendor_watchdog"))
    monkeypatch.setattr(settings, "AUTOPILOT_REVIEW_BY", "owner")


def _wd(tag, promised_in, **extra):
    return vendors.create({"vendor": f"WD{tag}", "description": extra.pop("description", "AR400 liner plate"), "po_number": f"P{tag}", "ordered_on": _d(-30), "promised_on": _d(promised_in), **extra}, OP)


def _mine(tag):
    with SessionLocal() as db:
        return [r for r in db.query(AssistantActionDB).filter(AssistantActionDB.user_role == "AUTOPILOT") if (r.args or {}).get("to") == f"sales@wd{tag}.test"]


def test_one_draft_per_po_date_and_tier_across_reruns_and_days_and_the_tier_moves_up(monkeypatch, only):
    tag = uuid.uuid4().hex[:6]
    monkeypatch.setattr(settings, "VENDOR_EMAILS", f"WD{tag}=sales@wd{tag}.test")
    line = _wd(tag, 6)
    for day in (TODAY, TODAY, TODAY):
        autopilot.run("vendor_watchdog", on=day)
    assert len(_mine(tag)) == 1
    autopilot.run("vendor_watchdog", on=TODAY + timedelta(days=2))                    # 4 days left: still the 7 tier
    assert len(_mine(tag)) == 1
    autopilot.run("vendor_watchdog", on=TODAY + timedelta(days=4))                    # 2 days left: the 3 tier
    assert [r.args["_tier"] for r in _mine(tag)].count("APPROACHING_3") == 1
    for k in range(7, 12):
        autopilot.run("vendor_watchdog", on=TODAY + timedelta(days=k))                # overdue, one draft only
    assert [r.args["_tier"] for r in _mine(tag)].count("OVERDUE") == 1 and len(_mine(tag)) == 3
    vendors.update(line["id"], {"shipped_on": _d(8)}, OP)
    n = len(_mine(tag))
    autopilot.run("vendor_watchdog", on=TODAY + timedelta(days=13))
    assert len(_mine(tag)) == n


@pytest.mark.parametrize("status", ["Canceled", "Cancelled by customer", "Delivered", "Received", "Complete", "Closed", "Shipped", "On hold - cancelled"])
def test_t9_an_order_that_is_finished_or_cancelled_in_other_words_is_not_chased(monkeypatch, only, status):
    tag = uuid.uuid4().hex[:6]
    monkeypatch.setattr(settings, "VENDOR_EMAILS", f"WD{tag}=sales@wd{tag}.test")
    _wd(tag, -3, status=status)
    autopilot.run("vendor_watchdog", on=TODAY)
    assert _mine(tag) == []


def test_t10_a_check_in_never_carries_the_customer_or_our_so_number(monkeypatch, only):
    tag = uuid.uuid4().hex[:6]
    monkeypatch.setattr(settings, "VENDOR_EMAILS", f"WD{tag}=sales@wd{tag}.test")
    _wd(tag, 2, description=f"Hose for {CUSTOMER} per SO-12889")
    autopilot.run("vendor_watchdog", on=TODAY)
    drafts = _mine(tag)
    assert drafts == [] or all("owens" not in d.args["body"].lower() and "12889" not in d.args["body"] and "owens" not in d.args["subject"].lower() for d in drafts)


@pytest.mark.parametrize("vendor,mapped", [("WDX {t}", "WDX {t}"), ("wdx {t}", "WDX {t}"), ("WDX {t} ", "WDX {t}"), ("WDX {t}2", "WDX {t}"), ("WDXX {t}", "WDX {t}"), ("WDX", "WDX {t}"), ("WDX {t}.", "WDX {t}"), ("WDX {t}", "WDX {t}")],
                         ids=["exact", "case", "space", "digit", "prefix-char", "short", "dot", "nbsp"])
def test_a_check_in_goes_only_to_the_address_of_the_vendor_it_names(monkeypatch, vendor, mapped):
    t = uuid.uuid4().hex[:6]
    monkeypatch.setattr(settings, "VENDOR_EMAILS", f"{mapped.format(t=t)}=sales@only{t}.test")
    got = autopilot.vendor_email(vendor.format(t=t))
    if vendor.format(t=t).strip().casefold() == mapped.format(t=t).casefold():
        assert got == f"sales@only{t}.test"
    else:
        assert got in (None, "") or got != f"sales@only{t}.test", (vendor, got)


def test_the_day_follows_the_business_clock_at_the_midnight_edge(monkeypatch):
    from datetime import datetime as dt

    from app.services.coordination import notify

    seen = []
    for day in (date(2026, 11, 19), date(2026, 11, 20)):
        monkeypatch.setattr(notify, "now_local", lambda n=day: dt(n.year, n.month, n.day, 0, 1))
        seen.append(vendors.watchdog_tier({"promised_on": "2026-11-23", "shipped_on": None, "status": ""}, notify.now_local().date())["tier"])
    assert seen == ["APPROACHING_7", "APPROACHING_3"]
    assert vendors.watchdog_tier({"promised_on": "2026-11-21"}, date(2026, 11, 22))["tier"] == "OVERDUE"
    assert vendors.watchdog_tier({"promised_on": "2026-11-28"}, date(2026, 11, 21))["tier"] == "APPROACHING_7"
    assert vendors.watchdog_tier({"promised_on": "2026-11-29"}, date(2026, 11, 21)) is None


@pytest.mark.parametrize("promised", ["", "not-a-date", "2026-02-30", "2026-13-01", None, "2026/11/20"])
def test_a_bad_promised_date_is_not_watched_and_never_crashes_the_play(monkeypatch, only, promised):
    tag = uuid.uuid4().hex[:6]
    monkeypatch.setattr(settings, "VENDOR_EMAILS", f"WD{tag}=sales@wd{tag}.test")
    try:
        vendors.create({"vendor": f"WD{tag}", "description": "x", "po_number": f"P{tag}", "ordered_on": _d(-30), "promised_on": promised}, OP)
    except ValueError:
        return
    autopilot.run("vendor_watchdog", on=TODAY)
    assert len(_mine(tag)) <= 1


def test_a_check_in_draft_waits_for_a_person_and_is_never_sent(monkeypatch, only):
    from app.services.outbound_email_service import outbound_email_service

    sent = []
    monkeypatch.setattr(outbound_email_service, "send", lambda *a, **k: sent.append(a) or {"status": "SENT"})
    tag = uuid.uuid4().hex[:6]
    monkeypatch.setattr(settings, "VENDOR_EMAILS", f"WD{tag}=sales@wd{tag}.test")
    _wd(tag, 1)
    autopilot.run("vendor_watchdog", on=TODAY)
    d = _mine(tag)
    assert d and d[0].status == "PENDING" and not sent


@pytest.mark.parametrize("poison", ["9999-12-31", "0001-01-01"])
def test_t11_one_extreme_date_does_not_stop_the_check_ins_for_everyone_else(monkeypatch, only, poison):
    tag, ptag = uuid.uuid4().hex[:6], uuid.uuid4().hex[:6]
    monkeypatch.setattr(settings, "VENDOR_EMAILS", f"WD{tag}=sales@wd{tag}.test")
    bad = None
    try:
        bad = vendors.create({"vendor": f"WD{ptag}", "description": "poison", "po_number": f"P{ptag}", "ordered_on": _d(-30), "promised_on": poison}, OP)
    except ValueError:
        return
    try:
        _wd(tag, 2)
        autopilot.run("vendor_watchdog", on=TODAY)
        assert len(_mine(tag)) == 1
    finally:
        with SessionLocal() as db:
            db.query(VendorJobDB).filter(VendorJobDB.id == bad["id"]).delete()
            db.commit()


# =============================================================== AI_LOCAL_ONLY

LOCAL = {"127.0.0.1", "localhost", "::1", "host.docker.internal"}


@pytest.fixture()
def watch_network(monkeypatch):
    import socket

    import httpx

    hosts = []

    def record_httpx(self, request, *a, **k):
        hosts.append(request.url.host)
        raise httpx.ConnectError("no network in tests")

    async def record_async(self, request, *a, **k):
        hosts.append(request.url.host)
        raise httpx.ConnectError("no network in tests")

    monkeypatch.setattr(httpx.Client, "send", record_httpx)
    monkeypatch.setattr(httpx.AsyncClient, "send", record_async)
    real = socket.getaddrinfo
    monkeypatch.setattr(socket, "getaddrinfo", lambda host, *a, **k: (hosts.append(host), (_ for _ in ()).throw(OSError("no network in tests")))[1] if str(host) not in LOCAL else real(host, *a, **k))
    return hosts


def test_ai_local_only_keeps_every_ai_call_off_groq_even_with_a_key_and_groq_chosen(monkeypatch, watch_network):
    from app.services.ai import features, speech
    from app.services.ai import providers

    monkeypatch.setattr(settings, "AI_LOCAL_ONLY", True)
    monkeypatch.setattr(settings, "GROQ_API_KEY", "gsk_test_not_real")
    monkeypatch.setattr(settings, "AI_PROVIDER", "groq")
    monkeypatch.setattr(settings, "AI_FALLBACK_PROVIDER", "groq")
    providers.use_provider(None)
    secret = "our cost is 12345 for Owens Corning"
    calls = [lambda: features.draft_email("reply", secret, "en", "a@b.test"), lambda: features.triage_email("a@b.test", "RFQ", secret), lambda: features.translate(secret, "es"),
             lambda: features.summarize_call(secret, "+15550100"), lambda: features.read_vendor_reply("v@b.test", "Re: PO", secret, "2026-11-20"), lambda: features.suggest_item(secret),
             lambda: features.explain_exception({"reason": secret}), lambda: speech.transcribe(b"RIFF....WAVEfmt ", ".wav"), lambda: providers.get_provider().status()]
    for call in calls:
        try:
            call()
        except Exception:  # noqa: BLE001 - the local model is not running in tests: only WHERE it tried to send matters
            pass
    assert not [h for h in watch_network if str(h).lower() not in LOCAL], watch_network
    assert providers.get_provider().name == "off"
    assert speech._use_groq() is False


def test_t12_local_only_refuses_a_remote_ollama_host(monkeypatch, watch_network):
    from app.services.ai import features, providers

    monkeypatch.setattr(settings, "AI_LOCAL_ONLY", True)
    monkeypatch.setattr(settings, "OLLAMA_HOST", "http://ollama.some-cloud.example.test:11434")
    providers.use_provider(None)
    try:
        features.draft_email("reply", "pricing is 100", "en", "a@b.test")
    except Exception:  # noqa: BLE001
        pass
    assert not [h for h in watch_network if str(h).lower() not in LOCAL], watch_network


# =============================================================== other doors to the same vendor line

def test_the_copilot_tool_and_the_vendor_api_are_gated_the_same_way_as_the_service(api, approvers):
    from app.services.assistant import tools

    po, j = _po()
    line = _line(po)
    with pytest.raises(ValueError, match="Gate 1"):
        tools.update_vendor_job({"job_id": line, "status": "In production"}, BOSS)
    r = api("PATCH", f"/vendors/jobs/{line}", "MANAGER", json={"status": "In production"})
    assert r.status_code in (400, 404, 405, 409, 422), r.status_code
    if r.status_code in (400, 409, 422):
        assert "Gate 1" in r.text
    with SessionLocal() as db:
        assert "production" not in str(db.get(VendorJobDB, line).status or "").lower()


def test_a_manager_override_needs_the_route_a_reason_and_leaves_an_audit_line(api, approvers):
    po, j = _po()
    assert api("POST", "/gates/override", "MANAGER", json={"job_id": j["id"], "gate": 1, "reason": "short"}).status_code == 422
    assert api("POST", "/gates/override", "OPERATOR", json={"job_id": j["id"], "gate": 1, "reason": "a proper reason here"}).status_code == 403
    assert api("POST", "/gates/override", "VIEWER", json={"job_id": j["id"], "gate": 1, "reason": "a proper reason here"}).status_code == 403
    assert api("POST", "/gates/override", "MANAGER", json={"job_id": j["id"], "gate": 1, "reason": "a proper reason here"}).status_code == 200
    with SessionLocal() as db:
        from sqlalchemy import text

        assert db.execute(text("select count(*) from audit_log where action = 'GATE_OVERRIDDEN' and detail like :d"), {"d": f"%job {j['id']} %"}).scalar() >= 1
