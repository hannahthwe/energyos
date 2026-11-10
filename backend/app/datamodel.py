"""Data-lineage view: how native records become the unified model, and the
homeowner energy overview."""
from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import canonical as C
from . import sources as S
from . import state
from . import tools as T
from .adapters import SOURCE_LABELS
from .seed import DEMO_NOW_UTC

NATIVE = {
    "crm": [S.CrmAccount, S.CrmSite, S.CrmRegisteredAsset, S.CrmCase, S.CrmCommissioning],
    "hems_backend": [S.HemsHome, S.HemsAsset, S.HemsSchedule, S.HemsConstraint, S.HemsCommand, S.HemsTelemetry, S.HemsEvent],
    "charger_integration": [S.ChgChargepoint, S.ChgSession, S.ChgMeterValue],
    "knowledge_base": [S.KbArticle],
    "config_audit": [S.AuditConfigLog],
}
KEY_FIELDS = {
    "crm": "crm_customer_ref, site_ref, asset_reg_id",
    "hems_backend": "home_uid, asset_key, requested_watts (W, +magnitude), epoch seconds",
    "charger_integration": "chargepoint_ref, session_state, power_kw (+ = export), ISO-8601 with offset",
    "knowledge_base": "article_code, audience",
    "config_audit": "audit_seq, param_path, changed_by, client_id",
}


def _count(s: Session, t) -> int:
    return s.scalar(select(func.count()).select_from(t)) or 0


def data_model(s: Session) -> dict:
    sources = [{"source_system": k, "label": SOURCE_LABELS[k], "key_fields": KEY_FIELDS[k],
                "tables": [{"table": t.__tablename__, "records": _count(s, t)} for t in tables]}
               for k, tables in NATIVE.items()]
    canonical = [{"entity": t.__name__, "table": t.__tablename__, "records": _count(s, t)} for t in C.DERIVED_TABLES]
    maps = [{"device_id": m.device_id, "source_system": m.source_system, "source_device_id": m.source_device_id,
             "mapping_method": m.mapping_method, "verified": m.verified, "evidence_ref": m.evidence_ref}
            for m in s.scalars(select(C.SourceDeviceMapping))]

    acct = s.scalars(select(S.CrmAccount)).first()
    site = s.scalars(select(S.CrmSite)).first()
    com = s.get(S.CrmCommissioning, "COM-20931-B")
    chain = [
        {"system": "CRM", "id": acct.crm_customer_ref, "label": "Customer"},
        {"system": "CRM", "id": site.site_ref, "label": "Installation site"},
        {"system": "CRM", "id": com.commissioning_ref, "label": f"Commissioning scan (serial {com.scanned_serial}), verified by installer"},
        {"system": "HEMS", "id": f"{com.hems_home_uid} / {com.hems_asset_key}", "label": "HEMS home + charger asset"},
        {"system": "Charger API", "id": com.chargepoint_ref, "label": "Charger platform chargepoint"},
        {"system": "ConnectedCare", "id": "CC-DEV-55102", "label": "Canonical device (ev_charger)"},
        {"system": "ConnectedCare", "id": "Unified timeline", "label": "Events from all sources on one UTC timeline"},
    ]

    transforms = []
    cmd = s.scalars(select(S.HemsCommand)).first()
    ccmd = s.scalars(select(C.DeviceCommand)).first()
    if cmd and ccmd:
        transforms.append({"source_system": "hems_backend", "native": f"hems_commands.requested_watts = {cmd.requested_watts}, "
                           f"issued_epoch = {cmd.issued_epoch}, outcome = '{cmd.outcome}'",
                           "canonical": f"DeviceCommand.requested_value = {ccmd.requested_value} kW, timestamp_utc = "
                           f"{ccmd.timestamp_utc}, command_status = '{ccmd.command_status}'",
                           "rule": "W -> kW, discharge is negative, epoch -> UTC ISO, vendor status -> canonical enum"})
    mv = s.scalars(select(S.ChgMeterValue)).first()
    if mv:
        cmv = s.get(C.EnergyMeasurement, f"CC-MEA-{mv.mv_ref}")
        transforms.append({"source_system": "charger_integration", "native": f"chg_meter_values.power_kw = {mv.power_kw} (+ = export), at = '{mv.at}'",
                           "canonical": f"EnergyMeasurement charger_power = {cmv.value} kW, timestamp_utc = {cmv.timestamp_utc}",
                           "rule": "vendor sign flipped to canonical (+ draws, - supplies home); offset time -> UTC"})
    sess = s.scalars(select(S.ChgSession)).first()
    if sess:
        transforms.append({"source_system": "charger_integration",
                           "native": f"chg_sessions.session_state = '{sess.session_state}', err = '{sess.err}', chargepoint_ref = '{sess.chargepoint_ref}'",
                           "canonical": f"ChargingSession.session_status = '{sess.session_state.lower()}', charger_device_id = 'CC-DEV-55102'",
                           "rule": "identity via verified commissioning mapping, not by name"})
    tel = s.scalars(select(S.HemsTelemetry).where(S.HemsTelemetry.channel == "house_load_w")).first()
    if tel:
        transforms.append({"source_system": "hems_backend", "native": f"hems_telemetry channel 'house_load_w' = {tel.reading}, ts_epoch_ms = {tel.ts_epoch_ms}",
                           "canonical": f"EnergyMeasurement household_load = {tel.reading / 1000} kW",
                           "rule": "W -> kW, ms epoch -> UTC ISO"})

    return {"sources": sources, "canonical_entities": canonical, "device_mappings": maps, "identity_chain": chain,
            "transforms": transforms, "unmapped_records": state.unmapped_records(s),
            "conventions": {"power": "kW; + = device draws energy (charging), - = device supplies the home (discharging)",
                            "soc": "percent", "time": "UTC ISO-8601"}}


def home_overview(s: Session) -> dict:
    iid = state.INSTALLATION_ID
    w = T.Window(DEMO_NOW_UTC.replace(day=9, hour=17, minute=0), DEMO_NOW_UTC)
    soc = T.get_soc_history(s, iid, w)["samples"]
    latest = soc[-1]["soc_pct"] if soc else None
    cons = T.get_operating_constraints(s, iid, DEMO_NOW_UTC)["constraints"]
    reserve = cons[0]["threshold_value"] if cons else None
    devices = T.get_device_status(s, iid)["devices"]
    charger = next((d for d in devices if d["device_type"] == "ev_charger"), {})
    sched = T.get_energy_schedule(s, iid, w)["schedules"]
    loads = [m for m in s.scalars(select(C.EnergyMeasurement).where(C.EnergyMeasurement.measurement_type == "household_load"))]
    loads.sort(key=lambda m: m.timestamp_utc)
    power = T.compare_expected_actual_power(s, iid, w)
    inst = s.get(C.Installation, iid)
    cust = s.get(C.Customer, inst.customer_id)
    available = bool(latest is not None and reserve is not None and latest > reserve and charger.get("connectivity_status") == "online")
    last_diag = s.scalars(select(C.DiagnosisRun).where(C.DiagnosisRun.role == "homeowner",
                                                       C.DiagnosisRun.scenario == state.active_scenario(s))
                          .order_by(C.DiagnosisRun.created_at.desc())).first()
    import json
    recent = []
    if last_diag:
        d = json.loads(last_diag.result_json)
        recent.append({"run_id": d["run_id"], "headline": d["headline"], "created_at": d["created_at"],
                       "support_recommended": d["support_recommended"]})
    return {
        "customer_first_name": cust.name.split()[0],
        "address": inst.address,
        "ev": {"soc_pct": latest, "reserve_pct": reserve, "plugged_in": True,
               "usable_for_home_kwh": round(max(0.0, (latest or 0) - (reserve or 0)) / 100 * 62, 1)},
        "v2h": {"available": available,
                "reason": ("Ready to power your home" if available else
                           f"Paused: car at {latest:.0f}% is not above your {reserve:.0f}% reserve" if latest is not None and reserve is not None and latest <= reserve
                           else "Unavailable"),
                "next_window": (sched[0]["start_time_utc"][11:16] + "-" + sched[0]["end_time_utc"][11:16]) if sched else None,
                "last_night_delivered": power["discharge_observed"]},
        "charger": {"status": charger.get("connectivity_status", "unknown"), "model": f"{charger.get('manufacturer', '')} {charger.get('model', '')}".strip()},
        "home": {"load_kw": loads[-1].value if loads else None, "solar_kw": 0.0,
                 "load_series": [{"t": m.timestamp_utc[11:16], "kw": m.value} for m in loads]},
        "recent_findings": recent,
    }
