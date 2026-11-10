"""Homeowner -> support handoff and the support case workflow."""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import canonical as C
from . import orchestrator, state
from . import tools as T
from .visibility import allowed_levels, filter_evidence

STATUSES = ("New", "Investigating", "Escalated", "Resolved")


class NotFound(Exception):
    pass


class Invalid(Exception):
    pass


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _msg(s: Session, case_id: str, role: str, mtype: str, text: str, vis: str) -> dict:
    m = C.SupportMessage(message_id=f"MSG-{uuid.uuid4().hex[:8].upper()}", case_id=case_id, sender_role=role,
                         message_type=mtype, message=text, created_at=now(), visibility_level=vis)
    s.add(m)
    return {"message_id": m.message_id, "sender_role": role, "message_type": mtype, "message": text,
            "created_at": m.created_at, "visibility_level": vis}


def _run(s: Session, run_id: str) -> dict:
    r = s.get(C.DiagnosisRun, run_id)
    if not r:
        raise NotFound(f"diagnosis {run_id} not found")
    return json.loads(r.result_json)


def _case(s: Session, case_id: str) -> C.SupportCase:
    c = s.get(C.SupportCase, case_id)
    if not c:
        raise NotFound(f"case {case_id} not found")
    return c


# ------------------------------------------------------------ homeowner side
def share_preview(s: Session, run_id: str) -> dict:
    d = _run(s, run_id)
    if d["role"] != "homeowner":
        raise Invalid("only homeowner diagnoses can be shared from the app")
    return {
        "run_id": run_id, "question": d["question"], "summary": d["headline"],
        "what_happened": d["what_happened"], "why": d["why"],
        "evidence_to_share": d["evidence"],
        "also_available_to_support": "HelioHEMS support can already see your system's technical records. "
                                     "Approving shares this conversation and the summary above with them.",
    }


def create_case_from_homeowner(s: Session, run_id: str, approved: bool, note: str | None = None) -> dict:
    if not approved:
        raise Invalid("explicit homeowner approval is required to share the diagnostic summary")
    home = _run(s, run_id)
    if home["role"] != "homeowner":
        raise Invalid("only homeowner diagnoses can be shared from the app")
    support = orchestrator.diagnose(s, home["question"], role="support")

    n = (s.scalar(select(func.count()).select_from(C.SupportCase)) or 0) + 1001
    case_id = f"CASE-{n}"
    while s.get(C.SupportCase, case_id):
        n += 1
        case_id = f"CASE-{n}"
    title = {"soc_reserve": "V2H did not discharge: EV below minimum reserve",
             "charger_rejection": "V2H did not discharge: charger rejected session",
             "config_change_raised_reserve": "V2H blocked after unexpected reserve change"}.get(
        support["likely_cause"], "V2H did not discharge")
    c = C.SupportCase(case_id=case_id, tenant_id=state.TENANT_ID, customer_id=state.CUSTOMER_ID,
                      installation_id=state.INSTALLATION_ID, title=title,
                      description=f'Homeowner asked in the Helio app: "{home["question"]}"' + (f"\nNote: {note}" if note else ""),
                      status="New", created_at=now(), diagnostic_summary=support["headline"],
                      escalation_required=support["escalation"]["required"], scenario=support["scenario"]["code"],
                      origin="homeowner_app", snapshot_json=json.dumps(support))
    s.add(c)
    shared_ids = [e["evidence_id"] for e in home["evidence"]]
    for e in support["evidence"]:
        s.add(C.CaseEvidence(case_id=case_id, evidence_id=e["evidence_id"], shared_by_homeowner=e["evidence_id"] in shared_ids,
                             summary=e["summary"], visibility_level=e["visibility_level"]))
    for e in home["evidence"]:
        if e["evidence_id"] not in {x["evidence_id"] for x in support["evidence"]}:
            s.add(C.CaseEvidence(case_id=case_id, evidence_id=e["evidence_id"], shared_by_homeowner=True,
                                 summary=e["summary"], visibility_level=e["visibility_level"]))
    s.add(C.ConsentRecord(consent_id=f"CON-{uuid.uuid4().hex[:8].upper()}", customer_id=state.CUSTOMER_ID,
                          case_id=case_id, evidence_scope=json.dumps(shared_ids), approved_at=now()))
    _msg(s, case_id, "homeowner", "question", home["question"], "homeowner_safe")
    _msg(s, case_id, "system", "note",
         f"Homeowner approved sharing the diagnostic summary and {len(shared_ids)} evidence record(s). "
         f"AI diagnosis {support['run_id']} attached ({support['mode']} mode).", "support_internal")
    s.flush()
    return {"case_id": case_id, "status": "New", "title": title, "escalation_required": c.escalation_required,
            "shared_evidence_ids": shared_ids}


def homeowner_cases(s: Session) -> list[dict]:
    rows = s.scalars(select(C.SupportCase).where(C.SupportCase.customer_id == state.CUSTOMER_ID,
                                                 C.SupportCase.origin == "homeowner_app"))
    return [{"case_id": c.case_id, "title": c.title, "status": c.status, "created_at": c.created_at}
            for c in sorted(rows, key=lambda c: c.created_at, reverse=True)]


# -------------------------------------------------------------- support side
def list_cases(s: Session) -> list[dict]:
    rows = sorted(s.scalars(select(C.SupportCase)), key=lambda c: c.created_at, reverse=True)
    cust = {c.customer_id: c for c in s.scalars(select(C.Customer))}
    return [{"case_id": c.case_id, "title": c.title, "status": c.status, "created_at": c.created_at,
             "customer_id": c.customer_id, "customer_name": cust[c.customer_id].name if c.customer_id in cust else "",
             "installation_id": c.installation_id, "escalation_required": c.escalation_required,
             "diagnostic_summary": c.diagnostic_summary, "scenario": c.scenario, "origin": c.origin} for c in rows]


def get_case(s: Session, case_id: str, role: str = "support") -> dict:
    c = _case(s, case_id)
    levels = allowed_levels(role)
    msgs = sorted(s.scalars(select(C.SupportMessage).where(C.SupportMessage.case_id == case_id)), key=lambda m: m.created_at)
    ev = list(s.scalars(select(C.CaseEvidence).where(C.CaseEvidence.case_id == case_id)))
    consent = s.scalars(select(C.ConsentRecord).where(C.ConsentRecord.case_id == case_id)).first()
    prof = T.get_customer_profile(s, c.installation_id)
    prev = T.get_previous_cases(s, c.customer_id)["cases"]
    snap = json.loads(c.snapshot_json or "{}")
    return {
        "case": {"case_id": c.case_id, "tenant_id": c.tenant_id, "customer_id": c.customer_id,
                 "installation_id": c.installation_id, "title": c.title, "description": c.description, "status": c.status,
                 "created_at": c.created_at, "diagnostic_summary": c.diagnostic_summary,
                 "escalation_required": c.escalation_required, "scenario": c.scenario, "origin": c.origin,
                 "live_data_matches": c.scenario == state.active_scenario(s)},
        "customer": prof["customer"], "installation": prof["installation"], "devices": prof["devices"],
        "previous_interactions": prev,
        "messages": [{"message_id": m.message_id, "sender_role": m.sender_role, "message_type": m.message_type,
                      "message": m.message, "created_at": m.created_at, "visibility_level": m.visibility_level}
                     for m in msgs if m.visibility_level in levels],
        "evidence": [{"evidence_id": e.evidence_id, "summary": e.summary, "visibility_level": e.visibility_level,
                      "shared_by_homeowner": e.shared_by_homeowner} for e in ev if e.visibility_level in levels],
        "consent": {"consent_id": consent.consent_id, "approved_at": consent.approved_at,
                    "evidence_scope": json.loads(consent.evidence_scope)} if consent else None,
        "diagnosis": snap or None,
        "escalation": json.loads(c.escalation_json) if c.escalation_json else None,
    }


def add_note(s: Session, case_id: str, text: str) -> dict:
    _case(s, case_id)
    if not text.strip():
        raise Invalid("note is empty")
    return _msg(s, case_id, "support", "note", text.strip(), "support_internal")


def set_status(s: Session, case_id: str, status: str) -> dict:
    c = _case(s, case_id)
    if status not in STATUSES:
        raise Invalid(f"status must be one of {STATUSES}")
    old, c.status = c.status, status
    _msg(s, case_id, "support", "status_change", f"Status {old} -> {status}", "support_internal")
    return {"case_id": case_id, "status": status}


def ask(s: Session, case_id: str, question: str) -> dict:
    c = _case(s, case_id)
    _msg(s, case_id, "support", "question", question, "support_internal")
    if c.scenario == state.active_scenario(s):
        d = orchestrator.diagnose(s, question, role="support")
        answer, src = d["answer"], {"run_id": d["run_id"], "mode": d["mode"], "mode_note": d["mode_note"],
                                    "agents_used": [a["name"] for a in d["agents"] if a["used"]],
                                    "evidence_ids": d["evidence_ids"][:8], "agents": d["agents"]}
    else:
        snap = json.loads(c.snapshot_json or "{}")
        answer = orchestrator.offline_followup_from_snapshot(snap, question) + \
            " (Answered from the snapshot saved at handoff; live data is on another demo scenario.)"
        src = {"run_id": snap.get("run_id"), "mode": "snapshot", "mode_note": None, "agents_used": [], "evidence_ids": [],
               "agents": None}
    m = _msg(s, case_id, "ai_copilot", "copilot_answer", answer, "support_internal")
    return {"question": question, "answer": answer, "message": m, **src}


def draft_response(s: Session, case_id: str) -> dict:
    """Customer-facing draft. Uses only homeowner-safe facts."""
    c = _case(s, case_id)
    snap = json.loads(c.snapshot_json or "{}")
    cust = s.get(C.Customer, c.customer_id)
    first = cust.name.split()[0]
    cause = snap.get("likely_cause")
    safe = filter_evidence(snap.get("evidence", []), "homeowner")
    soc = next((e["summary"] for e in safe if e["evidence_type"] == "measurement" and "state of charge" in e["summary"]), None)
    body = {
        "soc_reserve": ("We checked your system's records from last night. Your car's battery was below the minimum "
                        "reserve you set, so the Hub kept that charge for your driving instead of powering the house. "
                        "Nothing is faulty. To use V2H tonight, charge the car above the reserve before 18:55, or lower "
                        "the reserve in the Helio app under Settings > Vehicle > Minimum reserve."),
        "charger_rejection": ("We checked your system's records from last night. Your Helio Hub worked correctly and asked "
                              "the charger to power your home at 19:00, but the charger declined to start. Only the "
                              "charger's manufacturer can see why, so we have passed the details to them and will update "
                              "you as soon as we hear back. Please leave the car plugged in and keep your settings as they are."),
        "config_change_raised_reserve": ("We checked your system's records from last night. Your minimum reserve setting "
                                         "was set to 60% shortly before your evening schedule, which is higher than your "
                                         "car's charge at the time, so the Hub did not use the car. Could you let us know "
                                         "whether you or your installer changed this setting yesterday evening? Until we "
                                         "hear from you, please don't share your app login with anyone."),
    }.get(cause, "We have reviewed your system's records and will follow up shortly.")
    text = f"Hi {first},\n\n{body}\n\nBest regards,\nHelioHEMS Support"
    msg = _msg(s, case_id, "support", "draft_response", text, "homeowner_safe")
    return {"draft": text, "based_on_evidence": [e["evidence_id"] for e in safe], "soc_reference": soc, "message": msg}


def prepare_escalation(s: Session, case_id: str) -> dict:
    c = _case(s, case_id)
    snap = json.loads(c.snapshot_json or "{}")
    esc = snap.get("escalation") or {"required": False, "target": None}
    target = esc.get("target") or "HelioHEMS tier-2 support"
    external = "Voltara" in target
    audience = "partner" if external else "support"
    evidence = filter_evidence(snap.get("evidence", []), audience)
    devices = {d["device_type"]: d for d in T.get_device_status(s, c.installation_id)["devices"]}
    chg = devices.get("ev_charger", {})
    ask_for = {
        "charger_rejection": ["Internal charger log for the rejected session, including the rejection reason",
                              "Whether known issue KI-0412 applies to this unit",
                              "Recommended remediation (firmware update or configuration)"],
        "config_change_raised_reserve": ["Owner of the API client credential that made the change",
                                         "API gateway authentication logs for that client (last 7 days)",
                                         "Homeowner/installer confirmation of authorisation"],
    }.get(snap.get("likely_cause"), ["Tier-2 review of the attached evidence"])
    sessions = [e["source_record_id"] for e in evidence if e["evidence_type"] == "charging_session"]
    pkg = {
        "case_id": case_id, "target": target, "external_partner": external, "prepared_at": now(),
        "summary": snap.get("headline"),
        "device": {"charger_model": f"{chg.get('manufacturer', '')} {chg.get('model', '')}".strip(),
                   "charger_firmware": chg.get("firmware_version"),
                   "charger_ref": (chg.get("source_ids") or {}).get("charger_integration")},
        "session_refs": sessions,
        "timeline": [t for t in snap.get("timeline", []) if t["visibility_level"] in allowed_levels(audience)],
        "evidence": evidence,
        "requested_information": ask_for,
        "excluded": ("Restricted security audit records and customer contact details are excluded from external "
                     "partner packages.") if external else "Internal review: restricted audit records included.",
    }
    c.escalation_json = json.dumps(pkg)
    _msg(s, case_id, "support", "note", f"Escalation package prepared for {target}.", "support_internal")
    return pkg
