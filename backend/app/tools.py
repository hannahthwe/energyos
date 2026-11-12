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
from . import consents
from .adapters import AUDIT, CHG, HEMS, KNOWN_CLIENTS, MKT, SOURCE_LABELS, EVIDENCE_PREFIX, SUP

RELEVANT_SETTINGS = ("v2h.reserve.min_soc_pct", "v2h.schedule.enabled", "v2h.schedule.power_w")


def parse(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def fmt(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def eid(source_system: str, source_record_id: str) -> str:
    return f"EVD-{EVIDENCE_PREFIX[source_system]}-{source_record_id}"


def _rows(s: Session, model, installation_id: str, *criteria) -> list:
    """Canonical rows for an installation, minus rows from sources without active consent."""
    allowed = consents.active_sources(s)
    rows = s.scalars(select(model).where(model.installation_id == installation_id, *criteria))
    return [r for r in rows if r.source_system not in consents.GATED or r.source_system in allowed]


def _gaps(s: Session, *sources: str) -> list[dict]:
    return consents.unavailable(s, sources)


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
    rows = [x for x in _rows(s, C.ChargingSession, installation_id) if w.contains(x.start_time_utc)]
    return {
        "unavailable_sources": _gaps(s, CHG),
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
    rows = [x for x in _rows(s, C.DiagnosticEvent, installation_id) if w.contains(x.timestamp_utc)]
    rows.sort(key=lambda r: r.timestamp_utc)
    return {
        "unavailable_sources": _gaps(s, HEMS),
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
    cmds = [x for x in _rows(s, C.DeviceCommand, installation_id) if w.contains(x.timestamp_utc)]
    sessions = [x for x in _rows(s, C.ChargingSession, installation_id) if x.session_type == "v2h_discharge"]
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
    return {"commands": out, "evidence_ids": [e for c in out for e in c["evidence_ids"]],
            "unavailable_sources": _gaps(s, HEMS, CHG)}


# ============================================================ Energy Behaviour
def get_energy_schedule(s: Session, installation_id: str, w: Window) -> dict:
    rows = [x for x in _rows(s, C.EnergySchedule, installation_id)
            if parse(x.start_time_utc) <= w.end and parse(x.end_time_utc) >= w.start]
    return {
        "unavailable_sources": _gaps(s, HEMS),
        "schedules": [
            {"schedule_id": x.schedule_id, "action": x.action, "start_time_utc": x.start_time_utc,
             "end_time_utc": x.end_time_utc, "requested_power_kw": x.requested_power_kw, "enabled": x.enabled,
             "evidence_id": eid(x.source_system, x.source_record_id)}
            for x in rows
        ],
        "evidence_ids": [eid(x.source_system, x.source_record_id) for x in rows],
    }


def get_soc_history(s: Session, installation_id: str, w: Window) -> dict:
    rows = [x for x in _rows(s, C.EnergyMeasurement, installation_id, C.EnergyMeasurement.measurement_type == "ev_soc")
            if w.contains(x.timestamp_utc)]
    rows.sort(key=lambda r: r.timestamp_utc)
    return {
        "unavailable_sources": _gaps(s, HEMS),
        "samples": [{"timestamp_utc": x.timestamp_utc, "soc_pct": x.value,
                     "evidence_id": eid(x.source_system, x.source_record_id)} for x in rows],
        "evidence_ids": [eid(x.source_system, x.source_record_id) for x in rows],
    }


def soc_at(s: Session, installation_id: str, at: datetime) -> dict | None:
    """Latest SOC sample at or before `at` (deterministic)."""
    rows = [x for x in _rows(s, C.EnergyMeasurement, installation_id, C.EnergyMeasurement.measurement_type == "ev_soc")
            if parse(x.timestamp_utc) <= at]
    if not rows:
        return None
    x = max(rows, key=lambda r: r.timestamp_utc)
    return {"timestamp_utc": x.timestamp_utc, "soc_pct": x.value, "evidence_id": eid(x.source_system, x.source_record_id)}


def get_operating_constraints(s: Session, installation_id: str, at: datetime) -> dict:
    rows = [x for x in _rows(s, C.OperatingConstraint, installation_id) if parse(x.valid_from_utc) <= at]
    return {
        "unavailable_sources": _gaps(s, HEMS),
        "constraints": [
            {"constraint_id": x.constraint_id, "constraint_type": x.constraint_type, "threshold_value": x.threshold_value,
             "unit": x.unit, "valid_from_utc": x.valid_from_utc, "evidence_id": eid(x.source_system, x.source_record_id)}
            for x in rows
        ],
        "evidence_ids": [eid(x.source_system, x.source_record_id) for x in rows],
    }


def compare_expected_actual_power(s: Session, installation_id: str, w: Window) -> dict:
    sched = get_energy_schedule(s, installation_id, w)["schedules"]
    meas = [x for x in _rows(s, C.EnergyMeasurement, installation_id,
                             C.EnergyMeasurement.measurement_type == "charger_power") if w.contains(x.timestamp_utc)]
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
        "unavailable_sources": _gaps(s, HEMS, CHG),
    }


# ========================================================= Security Correlation
def get_configuration_changes(s: Session, installation_id: str, since: datetime) -> dict:
    rows = [x for x in _rows(s, C.ConfigurationChange, installation_id) if parse(x.timestamp_utc) >= since]
    rows.sort(key=lambda r: r.timestamp_utc)
    return {
        "unavailable_sources": _gaps(s, AUDIT),
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
        "unavailable_sources": _gaps(s, AUDIT),
    }


# =============================================================== Grid & Tariff
SAMPLE_HOURS = 0.25  # charger meter values arrive every 15 minutes; each covers the following 15 minutes


def get_tariff_contract(s: Session, installation_id: str) -> dict:
    rows = sorted(_rows(s, C.TariffContract, installation_id), key=lambda r: r.valid_from_utc)
    c = rows[-1] if rows else None
    return {
        "contract": {"contract_id": c.contract_id, "contract_type": c.contract_type,
                     "fixed_price_eur_kwh": c.fixed_price_eur_kwh, "dynamic_markup_eur_kwh": c.dynamic_markup_eur_kwh,
                     "grid_steering_agreement": c.grid_steering_agreement,
                     "steering_compensation": c.steering_compensation, "valid_from_utc": c.valid_from_utc,
                     "evidence_id": eid(c.source_system, c.source_record_id)} if c else None,
        "evidence_ids": [eid(c.source_system, c.source_record_id)] if c else [],
        "unavailable_sources": _gaps(s, SUP),
    }


def get_steering_events(s: Session, installation_id: str, w: Window) -> dict:
    """Grid-steering events from the supplier, with the HEMS-measured device response during each one."""
    rows = [x for x in _rows(s, C.GridSteeringEvent, installation_id)
            if parse(x.start_time_utc) <= w.end and parse(x.end_time_utc) >= w.start]
    rows.sort(key=lambda r: r.start_time_utc)
    battery = sorted(_rows(s, C.EnergyMeasurement, installation_id, C.EnergyMeasurement.measurement_type == "battery_power"),
                     key=lambda m: m.timestamp_utc)
    out = []
    for x in rows:
        st, en = parse(x.start_time_utc), parse(x.end_time_utc)
        during = [m for m in battery if st <= parse(m.timestamp_utc) < en] if x.device_type == "home_battery" else []
        before = [m for m in battery if parse(m.timestamp_utc) < st][-1:] if during else []
        after = [m for m in battery if parse(m.timestamp_utc) >= en][:1] if during else []
        out.append({
            "steering_id": x.steering_id, "device_type": x.device_type, "start_time_utc": x.start_time_utc,
            "end_time_utc": x.end_time_utc, "duration_min": int((en - st).total_seconds() // 60),
            "requested_max_kw": x.requested_max_kw, "reason": x.reason, "grid_operator": x.grid_operator,
            "evidence_id": eid(x.source_system, x.source_record_id),
            "device_response": {
                "samples_during": len(during),
                "max_abs_kw_during": round(max([abs(m.value) for m in during], default=0.0), 3) if during else None,
                "limit_respected": all(abs(m.value) <= x.requested_max_kw + 0.05 for m in during) if during else None,
                "kw_before": before[0].value if before else None, "kw_after": after[0].value if after else None,
                "evidence_ids": [eid(m.source_system, m.source_record_id) for m in before + during[:1] + after],
            },
        })
    return {"events": out,
            "evidence_ids": [e["evidence_id"] for e in out] + [i for e in out for i in e["device_response"]["evidence_ids"]],
            "unavailable_sources": _gaps(s, SUP) + (_gaps(s, HEMS) if rows else [])}


def _prices(s: Session, start: datetime, end: datetime) -> list[C.PriceSeries]:
    rows = [x for x in s.scalars(select(C.PriceSeries)) if parse(x.start_time_utc) < end and parse(x.end_time_utc) > start]
    return sorted(rows, key=lambda r: r.start_time_utc)


def get_price_series(s: Session, w: Window) -> dict:
    rows = _prices(s, w.start, w.end)
    items = [{"start_time_utc": x.start_time_utc, "end_time_utc": x.end_time_utc, "price_eur_kwh": x.price_eur_kwh,
              "evidence_id": eid(x.source_system, x.source_record_id)} for x in rows]
    lo = min(items, key=lambda p: p["price_eur_kwh"]) if items else None
    hi = max(items, key=lambda p: p["price_eur_kwh"]) if items else None
    return {"market": rows[0].market if rows else None, "prices": items, "cheapest": lo, "most_expensive": hi,
            "evidence_ids": [p["evidence_id"] for p in items], "unavailable_sources": []}


def compare_actual_vs_cheapest(s: Session, installation_id: str, w: Window) -> dict:
    """Actual EV charging cost vs the cheapest hours between plug-in and departure.

    Deterministic: energy per hour comes from charger meter values (15-min samples), the unit price from the
    supplier contract (fixed, or day-ahead price + markup), and the cheapest plan fills the cheapest whole hours
    between plug-in and the departure time, at the session's own charging power, until the same energy is met.
    Anything that cannot be established is returned as None with a reason in `missing`.
    """
    contract = get_tariff_contract(s, installation_id)["contract"]
    sessions = [x for x in _rows(s, C.ChargingSession, installation_id)
                if x.session_type == "charge" and w.contains(x.start_time_utc)]
    sessions.sort(key=lambda r: r.start_time_utc)
    power = [m for m in _rows(s, C.EnergyMeasurement, installation_id, C.EnergyMeasurement.measurement_type == "charger_power")
             if m.value > 0]
    plans = [x for x in _rows(s, C.EnergySchedule, installation_id) if x.action == "smart_charge"]
    missing: list[str] = []
    if not contract:
        missing.append("Tariff contract unknown, so prices per kWh cannot be established.")

    def unit_price(hour_start: datetime) -> tuple[float | None, str | None]:
        if not contract:
            return None, None
        if contract["contract_type"] == "fixed":
            return contract["fixed_price_eur_kwh"], contract["evidence_id"]
        p = _prices(s, hour_start, hour_start + timedelta(hours=1))
        if not p:
            return None, None
        return round(p[0].price_eur_kwh + (contract["dynamic_markup_eur_kwh"] or 0.0), 5), eid(p[0].source_system, p[0].source_record_id)

    out = []
    for x in sessions:
        st = parse(x.start_time_utc)
        en = parse(x.end_time_utc) if x.end_time_utc else w.end
        samples = [m for m in power if m.device_id == x.charger_device_id and st <= parse(m.timestamp_utc) < en]
        by_hour: dict[datetime, float] = {}
        for m in samples:
            h = parse(m.timestamp_utc).replace(minute=0, second=0)
            by_hour[h] = by_hour.get(h, 0.0) + m.value * SAMPLE_HOURS
        energy = round(sum(by_hour.values()), 3)
        max_kw = max((m.value for m in samples), default=0.0)
        plan = next((p for p in plans if parse(p.start_time_utc) <= st <= parse(p.end_time_utc)), None)
        departure = parse(plan.end_time_utc) if plan else None
        plug_in = parse(plan.start_time_utc) if plan else st
        ev_ids = [eid(x.source_system, x.source_record_id)] + ([eid(plan.source_system, plan.source_record_id)] if plan else [])

        actual_hours, actual_cost = [], 0.0
        for h, kwh in sorted(by_hour.items()):
            price, pid = unit_price(h)
            actual_hours.append({"start_time_utc": fmt(h), "kwh": round(kwh, 3), "price_eur_kwh": price, "evidence_id": pid})
            actual_cost = None if price is None or actual_cost is None else actual_cost + kwh * price
        if actual_cost is None or not contract:
            actual_cost = None
            if contract:
                missing.append(f"Price missing for at least one charging hour of {x.session_id}.")

        cheapest_hours, cheapest_cost = [], None
        if departure is None:
            missing.append(f"Departure time unknown for {x.session_id}, so the cheapest plan cannot be calculated.")
        elif contract and max_kw > 0:
            first = plug_in.replace(minute=0, second=0) + (timedelta(hours=1) if plug_in.minute or plug_in.second else timedelta(0))
            candidates = []
            h = first
            while h + timedelta(hours=1) <= departure:
                price, pid = unit_price(h)
                if price is not None:
                    candidates.append((price, h, pid))
                h += timedelta(hours=1)
            left, cheapest_cost = energy, 0.0
            for price, h, pid in sorted(candidates, key=lambda c: (c[0], c[1])):
                if left <= 1e-9:
                    break
                kwh = min(max_kw, left)
                cheapest_hours.append({"start_time_utc": fmt(h), "kwh": round(kwh, 3), "price_eur_kwh": price, "evidence_id": pid})
                cheapest_cost += kwh * price
                left -= kwh
            if left > 1e-6:
                cheapest_cost = None
                missing.append(f"Not enough priced hours before departure for {x.session_id}.")
            cheapest_hours.sort(key=lambda c: c["start_time_utc"])

        r2 = lambda v: round(v, 2) if v is not None else None  # noqa: E731
        saving = r2(actual_cost - cheapest_cost) if actual_cost is not None and cheapest_cost is not None else None
        out.append({
            "session_id": x.session_id, "start_time_utc": x.start_time_utc, "end_time_utc": x.end_time_utc,
            "energy_kwh": energy, "charging_kw": max_kw, "plug_in_utc": fmt(plug_in),
            "departure_utc": fmt(departure) if departure else None, "smart_charging_on": bool(plan.enabled) if plan else None,
            "actual_cost_eur": r2(actual_cost), "actual_hours": actual_hours,
            "cheapest_cost_eur": r2(cheapest_cost), "cheapest_hours": cheapest_hours, "saving_eur": saving,
            "evidence_ids": ev_ids + [a["evidence_id"] for a in actual_hours if a["evidence_id"]]
                            + [c["evidence_id"] for c in cheapest_hours if c["evidence_id"]],
        })

    known = [o for o in out if o["saving_eur"] is not None]
    total = lambda k: round(sum(o[k] for o in known), 2) if known else None  # noqa: E731
    return {
        "contract_type": contract["contract_type"] if contract else None,
        "contract_evidence_id": contract["evidence_id"] if contract else None,
        "sessions": out,
        "total_energy_kwh": round(sum(o["energy_kwh"] for o in out), 3),
        "total_actual_eur": total("actual_cost_eur"), "total_cheapest_eur": total("cheapest_cost_eur"),
        "total_saving_eur": total("saving_eur"),
        "complete": bool(out) and len(known) == len(out),
        "missing": [m for m in dict.fromkeys(missing) if m],
        "evidence_ids": list(dict.fromkeys(([contract["evidence_id"]] if contract else []) + [i for o in out for i in o["evidence_ids"]])),
        "unavailable_sources": _gaps(s, SUP, CHG, HEMS),
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
