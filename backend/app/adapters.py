"""Source adapters: native records -> canonical model.

Identity is resolved ONLY through the installer commissioning record
(`crm_commissioning`). A record from any source whose identifier has no
verified mapping is skipped and reported as unmapped. Names and nicknames are
never used to guess identity.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from . import canonical as C
from . import sources as S

CRM, HEMS, CHG, KB, AUDIT = "crm", "hems_backend", "charger_integration", "knowledge_base", "config_audit"

SOURCE_LABELS = {
    CRM: "HelioHEMS CRM",
    HEMS: "HelioHEMS energy backend",
    CHG: "Voltara charger integration (authorised API)",
    KB: "Approved knowledge base",
    AUDIT: "Configuration audit log",
}

EVIDENCE_PREFIX = {CRM: "CRM", HEMS: "HEMS", CHG: "CHG", KB: "KB", AUDIT: "AUD"}
DEVICE_TYPE = {"HEMS Gateway": "hems", "Bidirectional Charger": "ev_charger", "Electric Vehicle": "ev"}
KNOWN_CLIENTS = {"inst-portal": "installer", "helio-app-ios": "homeowner", "helio-app-android": "homeowner"}


# ------------------------------------------------------------------ helpers
def utc_from_epoch(sec: int | float) -> str:
    return datetime.fromtimestamp(sec, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def utc_from_iso(value: str) -> str:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def customer_id(ref: str) -> str:
    return f"CC-CUS-{ref.split('-')[-1]}"


def installation_id(site_ref: str) -> str:
    return f"CC-INST-{site_ref.split('-')[-1]}"


def device_id(asset_reg_id: str) -> str:
    return f"CC-DEV-{asset_reg_id.split('-')[-1]}"


@dataclass
class Resolver:
    """Verified identifier lookups built from commissioning records."""

    hems_asset: dict[str, str] = field(default_factory=dict)
    chargepoint: dict[str, str] = field(default_factory=dict)
    hems_home: dict[str, str] = field(default_factory=dict)
    unmapped: list[dict] = field(default_factory=list)

    def skip(self, source: str, ref: str, reason: str) -> None:
        self.unmapped.append({"source_system": source, "source_id": ref, "reason": reason})


# ------------------------------------------------------------------ adapters
def _crm(s: Session, r: Resolver) -> None:
    for a in s.scalars(select(S.CrmAccount)):
        s.add(C.Customer(customer_id=customer_id(a.crm_customer_ref), tenant_id=a.tenant_code.lower(),
                         name=a.full_name, email=a.email_addr,
                         support_contact_preference={"APP": "in_app", "EMAIL": "email", "PHONE": "phone"}[a.contact_pref],
                         source_system=CRM, source_record_id=a.crm_customer_ref))
    accounts = {a.crm_customer_ref: a for a in s.scalars(select(S.CrmAccount))}
    for site in s.scalars(select(S.CrmSite)):
        s.add(C.Installation(installation_id=installation_id(site.site_ref),
                             tenant_id=accounts[site.crm_customer_ref].tenant_code.lower(),
                             customer_id=customer_id(site.crm_customer_ref), timezone=site.tz,
                             installation_status={"LIVE": "active", "PENDING": "commissioning"}[site.site_status],
                             address=site.address_line, installed_on=site.installed_on, installer=site.installer_name,
                             source_system=CRM, source_record_id=site.site_ref))
    for case in s.scalars(select(S.CrmCase)):
        s.add(C.PreviousInteraction(
            interaction_id=f"CC-INT-{case.crm_case_no.split('-')[-1]}", customer_id=customer_id(case.crm_customer_ref),
            installation_id=installation_id(case.site_ref), subject=case.subject,
            opened_utc=f"{case.opened}T00:00:00Z", status=case.closed_state.lower(), resolution=case.resolution_notes,
            channel=case.channel, source_system=CRM, source_record_id=case.crm_case_no))


def _identity(s: Session, r: Resolver) -> None:
    assets = {a.asset_reg_id: a for a in s.scalars(select(S.CrmRegisteredAsset))}
    hems_assets = {a.asset_key: a for a in s.scalars(select(S.HemsAsset))}
    cps = {c.chargepoint_ref: c for c in s.scalars(select(S.ChgChargepoint))}

    for com in s.scalars(select(S.CrmCommissioning)):
        reg = assets.get(com.asset_reg_id)
        if reg is None:
            r.skip(CRM, com.commissioning_ref, "commissioning row references unknown CRM asset")
            continue
        did = device_id(reg.asset_reg_id)
        inst = installation_id(com.site_ref)
        ha = hems_assets.get(com.hems_asset_key)
        cp = cps.get(com.chargepoint_ref) if com.chargepoint_ref else None

        # Serial numbers must agree across every source the commissioning row links.
        serials = {reg.serial_no, com.scanned_serial}
        if ha:
            serials.add(ha.serial)
        if com.chargepoint_ref:
            serials.add(cp.serial_number if cp else "<missing>")
        verified = len(serials) == 1

        s.add(C.SourceDeviceMapping(device_id=did, source_system=CRM, source_device_id=reg.asset_reg_id,
                                    mapping_method="crm_registration", verified=True, evidence_ref=com.commissioning_ref))
        if ha:
            s.add(C.SourceDeviceMapping(device_id=did, source_system=HEMS, source_device_id=ha.asset_key,
                                        mapping_method="installer_commissioning_scan", verified=verified,
                                        evidence_ref=com.commissioning_ref))
            if verified:
                r.hems_asset[ha.asset_key] = did
                r.hems_home[ha.home_uid] = inst
        if com.chargepoint_ref:
            s.add(C.SourceDeviceMapping(device_id=did, source_system=CHG, source_device_id=com.chargepoint_ref,
                                        mapping_method="installer_commissioning_scan", verified=verified,
                                        evidence_ref=com.commissioning_ref))
            if verified:
                r.chargepoint[com.chargepoint_ref] = did

        conn = "unknown"
        fw = ha.sw_ver if ha else "unknown"
        if cp and verified:
            conn, fw = cp.conn_state.lower(), cp.fw_rev
        elif ha:
            conn = "online" if ha.link_state == "UP" else "offline"
        s.add(C.Device(device_id=did, installation_id=inst, device_type=DEVICE_TYPE[reg.product_category],
                       manufacturer=reg.brand, model=reg.product_model, firmware_version=fw,
                       connectivity_status=conn, serial_number=reg.serial_no))

    for ref in cps:
        if ref not in r.chargepoint:
            r.skip(CHG, ref, "no verified commissioning record (similar nickname ignored)")


def _ev(s: Session, iid: str, src: str, rec: str, ts: str, etype: str, summary: str, vis: str) -> str:
    eid = f"EVD-{EVIDENCE_PREFIX[src]}-{rec}"
    s.add(C.EvidenceRecord(evidence_id=eid, installation_id=iid, source_system=src, source_record_id=rec,
                           timestamp_utc=ts, evidence_type=etype, summary=summary, visibility_level=vis))
    return eid


def _hems(s: Session, r: Resolver) -> None:
    def resolve(home: str, asset: str, kind: str, ref: str):
        inst, dev = r.hems_home.get(home), r.hems_asset.get(asset)
        if not inst or not dev:
            r.skip(HEMS, ref, f"unverified {kind} identifiers {home}/{asset}")
            return None, None
        return inst, dev

    for x in s.scalars(select(S.HemsSchedule)):
        inst, dev = resolve(x.home_uid, x.asset_key, "schedule", x.sched_uid)
        if not inst:
            continue
        action = {"V2H_DISCHARGE": "v2h_discharge", "SMART_CHARGE": "smart_charge"}[x.mode]
        kw = x.requested_watts / 1000 * (-1 if action == "v2h_discharge" else 1)
        st, en = utc_from_epoch(x.begins_epoch), utc_from_epoch(x.ends_epoch)
        s.add(C.EnergySchedule(schedule_id=f"CC-SCH-{x.sched_uid}", installation_id=inst, target_device_id=dev,
                               start_time_utc=st, end_time_utc=en, action=action, requested_power_kw=kw,
                               enabled=bool(x.active), source_system=HEMS, source_record_id=x.sched_uid))
        _ev(s, inst, HEMS, x.sched_uid, st, "energy_schedule",
            f"V2H discharge schedule {st[11:16]}-{en[11:16]} UTC at {abs(kw):.1f} kW, enabled={bool(x.active)}",
            "homeowner_safe")

    for x in s.scalars(select(S.HemsConstraint)):
        inst, dev = resolve(x.home_uid, x.asset_key, "constraint", x.constraint_uid)
        if not inst:
            continue
        vf = utc_from_epoch(x.effective_epoch)
        s.add(C.OperatingConstraint(constraint_id=f"CC-CON-{x.constraint_uid}", installation_id=inst, device_id=dev,
                                    constraint_type={"MIN_SOC_RESERVE": "min_reserve_soc"}[x.kind],
                                    threshold_value=x.threshold_pct, unit="%", valid_from_utc=vf,
                                    source_system=HEMS, source_record_id=x.constraint_uid))
        _ev(s, inst, HEMS, x.constraint_uid, vf, "operating_constraint",
            f"Minimum EV reserve SOC = {x.threshold_pct:.0f}% (valid from {vf})", "homeowner_safe")

    status_map = {"INHIBITED": "blocked_by_hems", "SENT": "sent", "NACK": "rejected_by_device", "ACK": "accepted"}
    for x in s.scalars(select(S.HemsCommand)):
        inst, dev = resolve(x.home_uid, x.asset_key, "command", x.cmd_uid)
        if not inst:
            continue
        ts = utc_from_epoch(x.issued_epoch)
        kw = -x.requested_watts / 1000
        st = status_map[x.outcome]
        s.add(C.DeviceCommand(command_id=f"CC-CMD-{x.cmd_uid}", installation_id=inst, target_device_id=dev,
                              timestamp_utc=ts, command_type="discharge_request", requested_value=kw,
                              command_status=st, source_system=HEMS, source_record_id=x.cmd_uid))
        _ev(s, inst, HEMS, x.cmd_uid, ts, "device_command",
            f"HEMS discharge request {kw:.1f} kW to charger -> status {st}", "support_internal")

    channel = {"ev_soc_pct": ("ev_soc", "%", 1), "house_load_w": ("household_load", "kW", 0.001),
               "pv_w": ("solar_production", "kW", 0.001)}
    for x in s.scalars(select(S.HemsTelemetry)):
        inst, dev = resolve(x.home_uid, x.asset_key, "telemetry", x.sample_uid)
        if not inst:
            continue
        mtype, unit, f = channel[x.channel]
        ts = utc_from_epoch(x.ts_epoch_ms / 1000)
        s.add(C.EnergyMeasurement(measurement_id=f"CC-MEA-{x.sample_uid}", installation_id=inst, device_id=dev,
                                  timestamp_utc=ts, measurement_type=mtype, value=round(x.reading * f, 3), unit=unit,
                                  source_system=HEMS, source_record_id=x.sample_uid))
        if mtype == "ev_soc":
            _ev(s, inst, HEMS, x.sample_uid, ts, "measurement", f"EV state of charge {x.reading:.0f}% at {ts[11:16]} UTC",
                "homeowner_safe")

    sev = {"I": "info", "W": "warning", "E": "error"}
    for x in s.scalars(select(S.HemsEvent)):
        inst, dev = resolve(x.home_uid, x.asset_key, "event", x.event_uid)
        if not inst:
            continue
        ts = utc_from_epoch(x.ts_epoch)
        s.add(C.DiagnosticEvent(event_id=f"CC-EVT-{x.event_uid}", installation_id=inst, device_id=dev,
                                timestamp_utc=ts, event_type=x.code.lower(), severity=sev[x.level], message=x.text,
                                source_system=HEMS, source_record_id=x.event_uid))
        _ev(s, inst, HEMS, x.event_uid, ts, "diagnostic_event", f"HEMS event {x.code}: {x.text}", "support_internal")


def _charger(s: Session, r: Resolver, inst_of_device: dict[str, str]) -> None:
    for x in s.scalars(select(S.ChgSession)):
        dev = r.chargepoint.get(x.chargepoint_ref)
        if not dev:
            r.skip(CHG, x.session_ref, "session on unmapped chargepoint")
            continue
        inst = inst_of_device[dev]
        st = utc_from_iso(x.started_at)
        status = x.session_state.lower()
        s.add(C.ChargingSession(session_id=f"CC-SES-{x.session_ref}", installation_id=inst, charger_device_id=dev,
                                start_time_utc=st, end_time_utc=utc_from_iso(x.ended_at) if x.ended_at else None,
                                session_type={"V2H": "v2h_discharge", "CHARGE": "charge"}[x.kind],
                                session_status=status, reported_error_code=x.err,
                                error_detail_available=bool(x.err_detail_exposed),
                                source_system=CHG, source_record_id=x.session_ref))
        _ev(s, inst, CHG, x.session_ref, st, "charging_session",
            f"Charger reported V2H session {status}" + (f" with code {x.err}" if x.err else "")
            + ("" if x.err_detail_exposed else "; internal rejection detail not exposed by integration"),
            "support_internal")

    for x in s.scalars(select(S.ChgMeterValue)):
        dev = r.chargepoint.get(x.chargepoint_ref)
        if not dev:
            r.skip(CHG, x.mv_ref, "meter value on unmapped chargepoint")
            continue
        inst = inst_of_device[dev]
        ts = utc_from_iso(x.at)
        kw = -x.power_kw + 0.0  # vendor: + export from EV  ->  canonical: - supplies the home
        s.add(C.EnergyMeasurement(measurement_id=f"CC-MEA-{x.mv_ref}", installation_id=inst, device_id=dev,
                                  timestamp_utc=ts, measurement_type="charger_power", value=kw, unit="kW",
                                  source_system=CHG, source_record_id=x.mv_ref))
        _ev(s, inst, CHG, x.mv_ref, ts, "measurement", f"Charger power {kw:.1f} kW at {ts[11:16]} UTC", "homeowner_safe")


def _kb_audit(s: Session, r: Resolver) -> None:
    for a in s.scalars(select(S.KbArticle)):
        vis = "homeowner_safe" if a.audience == "public" else "support_internal"
        s.add(C.KnowledgeArticle(article_id=a.article_code, title=a.title, body=a.body, tags=a.tags,
                                 doc_type=a.doc_type, visibility_level=vis, source_system=KB,
                                 source_record_id=a.article_code))

    actor = {"installer_portal": "installer", "homeowner_app": "homeowner", "api_client": "api_client"}
    for x in s.scalars(select(S.AuditConfigLog)):
        inst, dev = r.hems_home.get(x.home_uid), r.hems_asset.get(x.asset_key)
        if not inst or not dev:
            r.skip(AUDIT, str(x.audit_seq), "unverified identifiers")
            continue
        ts = utc_from_iso(x.logged_at)
        rec = f"audit-{x.audit_seq}"
        s.add(C.ConfigurationChange(change_id=f"CC-CHG-{x.audit_seq}", installation_id=inst, device_id=dev,
                                    timestamp_utc=ts, setting_name=x.param_path, old_value=x.prev_val or "(unset)",
                                    new_value=x.new_val, actor_type=actor[x.changed_by], actor_ref=x.client_id,
                                    source_system=AUDIT, source_record_id=rec))
        _ev(s, inst, AUDIT, rec, ts, "configuration_change",
            f"{x.param_path}: {x.prev_val or '(unset)'} -> {x.new_val} by {actor[x.changed_by]} client '{x.client_id}'",
            "restricted")


def ingest(s: Session) -> dict:
    """Rebuild every canonical table from the native sources."""
    for t in C.DERIVED_TABLES:
        s.execute(delete(t))
    s.flush()
    r = Resolver()
    _crm(s, r)
    _identity(s, r)
    s.flush()
    inst_of_device = {d.device_id: d.installation_id for d in s.scalars(select(C.Device))}
    _hems(s, r)
    _charger(s, r, inst_of_device)
    _kb_audit(s, r)
    s.flush()
    return {"unmapped": r.unmapped}
