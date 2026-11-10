"""Deterministic diagnostic tools over the canonical model.

Tools never interpret intent or guess causes. They return records,
comparisons and calculations, each with the evidence IDs they relied on.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import canonical as C
from .adapters import KNOWN_CLIENTS, SOURCE_LABELS, EVIDENCE_PREFIX

RELEVANT_SETTINGS = ("v2h.reserve.min_soc_pct", "v2h.schedule.enabled", "v2h.schedule.power_w")


def parse(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def fmt(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def eid(source_system: str, source_record_id: str) -> str:
    return f"EVD-{EVIDENCE_PREFIX[source_system]}-{source_record_id}"


@dataclass
class Window:
    start: datetime
    end: datetime

    def contains(self, ts: str) -> bool:
        return self.start <= parse(ts) <= self.end

    def as_dict(self) -> dict:
        return {"start_utc": fmt(self.start), "end_utc": fmt(self.end)}


# ======================================================== Technical Diagnostics
def get_device_status(s: Session, installation_id: str) -> dict:
    devices = list(s.scalars(select(C.Device).where(C.Device.installation_id == installation_id)))
    maps = list(s.scalars(select(C.SourceDeviceMapping)))
    return {
        "devices": [
            {"device_id": d.device_id, "device_type": d.device_type, "manufacturer": d.manufacturer,
             "model": d.model, "firmware_version": d.firmware_version, "connectivity_status": d.connectivity_status,
             "source_ids": {m.source_system: m.source_device_id for m in maps if m.device_id == d.device_id and m.verified}}
            for d in devices
        ],
        "evidence_ids": [],
    }


def get_charger_sessions(s: Session, installation_id: str, w: Window) -> dict:
    rows = [x for x in s.scalars(select(C.ChargingSession).where(C.ChargingSession.installation_id == installation_id))
            if w.contains(x.start_time_utc)]
    return {
        "sessions": [
            {"session_id": x.session_id, "start_time_utc": x.start_time_utc, "session_type": x.session_type,
             "session_status": x.session_status, "reported_error_code": x.reported_error_code,
             "error_detail_available": x.error_detail_available, "source_system": x.source_system,
             "evidence_id": eid(x.source_system, x.source_record_id)}
            for x in sorted(rows, key=lambda r: r.start_time_utc)
        ],
        "evidence_ids": [eid(x.source_system, x.source_record_id) for x in rows],
    }


def get_device_events(s: Session, installation_id: str, w: Window) -> dict:
    rows = [x for x in s.scalars(select(C.DiagnosticEvent).where(C.DiagnosticEvent.installation_id == installation_id))
            if w.contains(x.timestamp_utc)]
    rows.sort(key=lambda r: r.timestamp_utc)
    return {
        "events": [
            {"event_id": x.event_id, "timestamp_utc": x.timestamp_utc, "device_id": x.device_id,
             "event_type": x.event_type, "severity": x.severity, "message": x.message,
             "evidence_id": eid(x.source_system, x.source_record_id)}
            for x in rows
        ],
        "faults": [x.event_type for x in rows if x.severity == "error"],
        "evidence_ids": [eid(x.source_system, x.source_record_id) for x in rows],
    }


def correlate_command_response(s: Session, installation_id: str, w: Window) -> dict:
    """Match each HEMS command with the charger's response within 3 minutes."""
    cmds = [x for x in s.scalars(select(C.DeviceCommand).where(C.DeviceCommand.installation_id == installation_id))
            if w.contains(x.timestamp_utc)]
    sessions = list(s.scalars(select(C.ChargingSession).where(C.ChargingSession.installation_id == installation_id)))
    out = []
    for c in sorted(cmds, key=lambda r: r.timestamp_utc):
        t = parse(c.timestamp_utc)
        resp = [x for x in sessions if timedelta(0) <= parse(x.start_time_utc) - t <= timedelta(minutes=3)]
        r = resp[0] if resp else None
        if c.command_status == "blocked_by_hems":
            verdict, system = "not_sent_blocked_by_hems", "HEMS (HelioHEMS)"
        elif r and r.session_status == "rejected":
            verdict, system = "rejected_by_charger", "Charger (Voltara)"
        elif r and r.session_status in ("active", "completed"):
            verdict, system = "accepted_by_charger", None
        elif c.command_status == "rejected_by_device":
            verdict, system = "rejected_by_charger_no_session_record", "Charger (Voltara)"
        else:
            verdict, system = "no_response_recorded", None
        ev = [eid(c.source_system, c.source_record_id)] + ([eid(r.source_system, r.source_record_id)] if r else [])
        out.append({
            "command_id": c.command_id, "timestamp_utc": c.timestamp_utc, "requested_kw": c.requested_value,
            "hems_status": c.command_status, "charger_session_id": r.session_id if r else None,
            "charger_response_utc": r.start_time_utc if r else None,
            "charger_error_code": r.reported_error_code if r else None,
            "charger_error_detail_available": r.error_detail_available if r else None,
            "verdict": verdict, "rejecting_system": system, "evidence_ids": ev,
        })
    return {"commands": out, "evidence_ids": [e for c in out for e in c["evidence_ids"]]}


# ============================================================ Energy Behaviour
def get_energy_schedule(s: Session, installation_id: str, w: Window) -> dict:
    rows = [x for x in s.scalars(select(C.EnergySchedule).where(C.EnergySchedule.installation_id == installation_id))
            if parse(x.start_time_utc) <= w.end and parse(x.end_time_utc) >= w.start]
    return {
        "schedules": [
            {"schedule_id": x.schedule_id, "action": x.action, "start_time_utc": x.start_time_utc,
             "end_time_utc": x.end_time_utc, "requested_power_kw": x.requested_power_kw, "enabled": x.enabled,
             "evidence_id": eid(x.source_system, x.source_record_id)}
            for x in rows
        ],
        "evidence_ids": [eid(x.source_system, x.source_record_id) for x in rows],
    }


def get_soc_history(s: Session, installation_id: str, w: Window) -> dict:
    rows = [x for x in s.scalars(select(C.EnergyMeasurement).where(
        C.EnergyMeasurement.installation_id == installation_id, C.EnergyMeasurement.measurement_type == "ev_soc"))
        if w.contains(x.timestamp_utc)]
    rows.sort(key=lambda r: r.timestamp_utc)
    return {
        "samples": [{"timestamp_utc": x.timestamp_utc, "soc_pct": x.value,
                     "evidence_id": eid(x.source_system, x.source_record_id)} for x in rows],
        "evidence_ids": [eid(x.source_system, x.source_record_id) for x in rows],
    }


def soc_at(s: Session, installation_id: str, at: datetime) -> dict | None:
    """Latest SOC sample at or before `at` (deterministic)."""
    rows = [x for x in s.scalars(select(C.EnergyMeasurement).where(
        C.EnergyMeasurement.installation_id == installation_id, C.EnergyMeasurement.measurement_type == "ev_soc"))
        if parse(x.timestamp_utc) <= at]
    if not rows:
        return None
    x = max(rows, key=lambda r: r.timestamp_utc)
    return {"timestamp_utc": x.timestamp_utc, "soc_pct": x.value, "evidence_id": eid(x.source_system, x.source_record_id)}


def get_operating_constraints(s: Session, installation_id: str, at: datetime) -> dict:
    rows = [x for x in s.scalars(select(C.OperatingConstraint).where(C.OperatingConstraint.installation_id == installation_id))
            if parse(x.valid_from_utc) <= at]
    return {
        "constraints": [
            {"constraint_id": x.constraint_id, "constraint_type": x.constraint_type, "threshold_value": x.threshold_value,
             "unit": x.unit, "valid_from_utc": x.valid_from_utc, "evidence_id": eid(x.source_system, x.source_record_id)}
            for x in rows
        ],
        "evidence_ids": [eid(x.source_system, x.source_record_id) for x in rows],
    }


def compare_expected_actual_power(s: Session, installation_id: str, w: Window) -> dict:
    sched = get_energy_schedule(s, installation_id, w)["schedules"]
    meas = [x for x in s.scalars(select(C.EnergyMeasurement).where(
        C.EnergyMeasurement.installation_id == installation_id, C.EnergyMeasurement.measurement_type == "charger_power"))
        if w.contains(x.timestamp_utc)]
    meas.sort(key=lambda r: r.timestamp_utc)
    rows = []
    for m in meas:
        t = parse(m.timestamp_utc)
        active = [x for x in sched if x["enabled"] and parse(x["start_time_utc"]) <= t <= parse(x["end_time_utc"])]
        expected = active[0]["requested_power_kw"] if active else 0.0
        rows.append({"timestamp_utc": m.timestamp_utc, "expected_kw": expected, "measured_kw": m.value,
                     "shortfall_kw": round(abs(expected - m.value), 3),
                     "evidence_id": eid(m.source_system, m.source_record_id)})
    in_window = [r for r in rows if r["expected_kw"] != 0]
    delivered = [r for r in in_window if abs(r["measured_kw"]) >= 0.1]
    return {
        "samples": rows,
        "samples_in_discharge_window": len(in_window),
        "samples_with_discharge": len(delivered),
        "max_discharge_kw": round(min([r["measured_kw"] for r in rows] + [0.0]), 3),
        "discharge_observed": bool(delivered),
        "evidence_ids": [r["evidence_id"] for r in rows],
    }


# ========================================================= Security Correlation
def get_configuration_changes(s: Session, installation_id: str, since: datetime) -> dict:
    rows = [x for x in s.scalars(select(C.ConfigurationChange).where(C.ConfigurationChange.installation_id == installation_id))
            if parse(x.timestamp_utc) >= since]
    rows.sort(key=lambda r: r.timestamp_utc)
    return {
        "changes": [
            {"change_id": x.change_id, "timestamp_utc": x.timestamp_utc, "setting_name": x.setting_name,
             "old_value": x.old_value, "new_value": x.new_value, "actor_type": x.actor_type, "actor_ref": x.actor_ref,
             "actor_recognised": x.actor_ref in KNOWN_CLIENTS, "evidence_id": eid(x.source_system, x.source_record_id)}
            for x in rows
        ],
        "evidence_ids": [eid(x.source_system, x.source_record_id) for x in rows],
    }


def correlate_changes_with_incident(s: Session, installation_id: str, incident_at: datetime,
                                    lookback_hours: int = 24, soc_pct: float | None = None) -> dict:
    changes = get_configuration_changes(s, installation_id, incident_at - timedelta(days=30))["changes"]
    out = []
    for c in changes:
        t = parse(c["timestamp_utc"])
        minutes_before = round((incident_at - t).total_seconds() / 60)
        in_lookback = 0 <= minutes_before <= lookback_hours * 60
        relevant = c["setting_name"] in RELEVANT_SETTINGS
        effect = None
        if c["setting_name"] == "v2h.reserve.min_soc_pct" and soc_pct is not None:
            try:
                old, new = float(c["old_value"]), float(c["new_value"])
                if old < soc_pct <= new:
                    effect = f"change moved reserve from {old:.0f}% (below SOC {soc_pct:.0f}%) to {new:.0f}% (at/above SOC): this alone blocks discharge"
                else:
                    effect = "change does not alter the discharge decision at the observed SOC"
            except ValueError:
                effect = "previous value unset"
        out.append({
            **c, "minutes_before_incident": minutes_before, "within_lookback": in_lookback,
            "affects_discharge_decision": relevant, "effect_on_incident": effect,
            "needs_human_review": bool(in_lookback and relevant and not c["actor_recognised"]),
        })
    flagged = [c for c in out if c["within_lookback"] and c["affects_discharge_decision"]]
    return {
        "incident_utc": fmt(incident_at), "lookback_hours": lookback_hours,
        "changes": out, "correlated_changes": flagged,
        "needs_human_review": any(c["needs_human_review"] for c in out),
        "security_conclusion": "not_determined",  # tools never conclude an attack
        "evidence_ids": [c["evidence_id"] for c in flagged] or [c["evidence_id"] for c in out],
    }


# ========================================================== Support Resolution
def get_customer_profile(s: Session, installation_id: str) -> dict:
    inst = s.get(C.Installation, installation_id)
    cust = s.get(C.Customer, inst.customer_id)
    return {
        "customer": {"customer_id": cust.customer_id, "tenant_id": cust.tenant_id, "name": cust.name,
                     "email": cust.email, "support_contact_preference": cust.support_contact_preference,
                     "crm_ref": cust.source_record_id},
        "installation": {"installation_id": inst.installation_id, "timezone": inst.timezone,
                         "installation_status": inst.installation_status, "address": inst.address,
                         "installed_on": inst.installed_on, "installer": inst.installer, "crm_ref": inst.source_record_id},
        "devices": get_device_status(s, installation_id)["devices"],
        "evidence_ids": [],
    }


def get_previous_cases(s: Session, customer_id: str) -> dict:
    rows = list(s.scalars(select(C.PreviousInteraction).where(C.PreviousInteraction.customer_id == customer_id)))
    rows.sort(key=lambda r: r.opened_utc, reverse=True)
    return {
        "cases": [{"interaction_id": x.interaction_id, "crm_ref": x.source_record_id, "subject": x.subject,
                   "opened": x.opened_utc[:10], "status": x.status, "resolution": x.resolution, "channel": x.channel}
                  for x in rows],
        "evidence_ids": [],
    }


def search_knowledge_base(s: Session, terms: list[str], include_internal: bool = True, limit: int = 3) -> dict:
    terms = [t.lower() for t in terms]
    generic = {"v2h", "discharge"}
    specific = [t for t in terms if t not in generic]
    scored = []
    for a in s.scalars(select(C.KnowledgeArticle)):
        if not include_internal and a.visibility_level != "homeowner_safe":
            continue
        tags = set(a.tags.split(","))
        score = sum(2 for t in terms if t in tags) + sum(1 for t in terms if t in a.title.lower())
        # When the query has specific terms, an article must match one of them (manuals excepted).
        if specific and a.doc_type != "manual" and not any(t in tags or t in a.title.lower() for t in specific):
            continue
        if score:
            scored.append((score, a))
    scored.sort(key=lambda p: (-p[0], p[1].article_id))
    hits = [a for _, a in scored[:limit]]
    return {
        "articles": [{"article_id": a.article_id, "title": a.title, "doc_type": a.doc_type, "excerpt": a.body[:240],
                      "visibility_level": a.visibility_level, "evidence_id": f"EVD-KB-{a.article_id}"} for a in hits],
        "evidence_ids": [f"EVD-KB-{a.article_id}" for a in hits],
    }


def evidence_lookup(s: Session, ids: list[str]) -> list[dict]:
    out = []
    for i in dict.fromkeys(ids):
        e = s.get(C.EvidenceRecord, i)
        if e is None and i.startswith("EVD-KB-"):
            a = s.get(C.KnowledgeArticle, i[len("EVD-KB-"):])
            if a:
                out.append({"evidence_id": i, "source_system": a.source_system, "source_label": SOURCE_LABELS[a.source_system],
                            "source_record_id": a.article_id, "timestamp_utc": None, "evidence_type": "knowledge_article",
                            "summary": a.title, "visibility_level": a.visibility_level})
            continue
        if e:
            out.append({"evidence_id": e.evidence_id, "source_system": e.source_system,
                        "source_label": SOURCE_LABELS[e.source_system], "source_record_id": e.source_record_id,
                        "timestamp_utc": e.timestamp_utc, "evidence_type": e.evidence_type, "summary": e.summary,
                        "visibility_level": e.visibility_level})
    return out
