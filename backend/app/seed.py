"""Synthetic HelioHEMS source-system data (SYNTHETIC DEMO DATA).

One homeowner, one installation, three scenarios. Each scenario only
changes the telemetry-type source tables; CRM, commissioning and the
knowledge base stay the same.

All incidents happen on the evening of 2026-11-09. The installation is in
Europe/London, which is UTC+0 in November, so local time == UTC.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import delete
from sqlalchemy.orm import Session

from . import sources as S

SCENARIOS = {
    "A": {
        "code": "A",
        "name": "SOC reserve prevents V2H",
        "summary": "EV at 35% with a 40% minimum reserve. The HEMS correctly holds back the discharge.",
        "expected": {
            "likely_cause": "soc_reserve",
            "escalation_required": False,
            "agents": ["energy_behaviour", "technical_diagnostics", "support_resolution"],
        },
    },
    "B": {
        "code": "B",
        "name": "Charger rejects V2H",
        "summary": "HEMS sends a 3 kW discharge request; the third-party charger rejects the session. Internal charger logs are not available.",
        "expected": {
            "likely_cause": "charger_rejection",
            "escalation_required": True,
            "agents": ["energy_behaviour", "technical_diagnostics", "support_resolution"],
        },
    },
    "C": {
        "code": "C",
        "name": "Unexpected configuration change",
        "summary": "The minimum reserve was raised from 40% to 60% by an unrecognised API client 13 minutes before the discharge window.",
        "expected": {
            "likely_cause": "config_change_raised_reserve",
            "escalation_required": True,
            "agents": ["security_correlation", "energy_behaviour", "technical_diagnostics", "support_resolution"],
        },
    },
}

# Demo "now": the morning after the incident.
DEMO_NOW_UTC = datetime(2026, 11, 10, 8, 30, tzinfo=timezone.utc)

HOME = "h_7f3a9c21"
GW, EVSE, EV = "ak_gw_01f2", "ak_evse_9b77", "ak_ev_3c10"
CP = "CP-VOL-0045120"
CP_DECOY = "CP-VOL-0045121"  # a neighbour's charger with a near-identical nickname


def ep(h: int, m: int, day: int = 9, month: int = 11) -> int:
    return int(datetime(2026, month, day, h, m, tzinfo=timezone.utc).timestamp())


def iso_off(h: int, m: int, day: int = 9) -> str:
    """Charger platform style: ISO-8601 with explicit offset."""
    return f"2026-11-{day:02d}T{h:02d}:{m:02d}:00+00:00"


# --------------------------------------------------------------- static data
def seed_static(s: Session) -> None:
    s.add(S.CrmAccount(crm_customer_ref="CUST-88412", tenant_code="HELIO", full_name="May",
                       email_addr="may@example.com", contact_pref="APP"))
    s.add(S.CrmSite(site_ref="SITE-UK-20931", crm_customer_ref="CUST-88412", tz="Europe/London",
                    site_status="LIVE", address_line="14 Orchard Row, Bristol (synthetic)",
                    installed_on="2026-03-14", installer_name="BrightSpark Installers Ltd (fictional)"))
    s.add_all([
        S.CrmRegisteredAsset(asset_reg_id="REG-55101", site_ref="SITE-UK-20931", product_category="HEMS Gateway",
                             brand="HelioHEMS", product_model="Hub H2", serial_no="HH2-2401-7781"),
        S.CrmRegisteredAsset(asset_reg_id="REG-55102", site_ref="SITE-UK-20931", product_category="Bidirectional Charger",
                             brand="Voltara", product_model="BiDi 11", serial_no="VB11-23-004512"),
        S.CrmRegisteredAsset(asset_reg_id="REG-55103", site_ref="SITE-UK-20931", product_category="Electric Vehicle",
                             brand="Kestrel Motors", product_model="e-Tourer 62", serial_no="KM62-0098812"),
    ])
    s.add_all([
        S.CrmCase(crm_case_no="CRM-10233", crm_customer_ref="CUST-88412", site_ref="SITE-UK-20931",
                  subject="App shows charger offline after router replacement", opened="2026-04-02",
                  closed_state="Resolved", channel="phone",
                  resolution_notes="Charger re-joined Wi-Fi after the installer updated the SSID. No hardware fault."),
        S.CrmCase(crm_case_no="CRM-10871", crm_customer_ref="CUST-88412", site_ref="SITE-UK-20931",
                  subject="Question about the V2H minimum reserve", opened="2026-09-18",
                  closed_state="Resolved", channel="email",
                  resolution_notes="Explained the minimum reserve. Homeowner chose to keep the 40% reserve set at commissioning."),
    ])
    s.add_all([
        S.CrmCommissioning(commissioning_ref="COM-20931-A", site_ref="SITE-UK-20931", asset_reg_id="REG-55101",
                           hems_home_uid=HOME, hems_asset_key=GW, chargepoint_ref=None, scanned_serial="HH2-2401-7781",
                           verified_by="installer:BrightSpark/J.Okafor", verified_at="2026-03-14T11:02:00Z"),
        S.CrmCommissioning(commissioning_ref="COM-20931-B", site_ref="SITE-UK-20931", asset_reg_id="REG-55102",
                           hems_home_uid=HOME, hems_asset_key=EVSE, chargepoint_ref=CP, scanned_serial="VB11-23-004512",
                           verified_by="installer:BrightSpark/J.Okafor", verified_at="2026-03-14T11:20:00Z"),
        S.CrmCommissioning(commissioning_ref="COM-20931-C", site_ref="SITE-UK-20931", asset_reg_id="REG-55103",
                           hems_home_uid=HOME, hems_asset_key=EV, chargepoint_ref=None, scanned_serial="KM62-0098812",
                           verified_by="installer:BrightSpark/J.Okafor", verified_at="2026-03-14T11:34:00Z"),
    ])

    s.add(S.HemsHome(home_uid=HOME, utc_offset_min=0, fleet="UK-South"))
    s.add_all([
        S.HemsAsset(asset_key=GW, home_uid=HOME, asset_class="gw", vendor="HelioHEMS", hw_model="HUB-H2",
                    sw_ver="4.2.1", link_state="UP", serial="HH2-2401-7781"),
        S.HemsAsset(asset_key=EVSE, home_uid=HOME, asset_class="evse", vendor="Voltara", hw_model="BIDI11",
                    sw_ver="2.8.3", link_state="UP", serial="VB11-23-004512"),
        S.HemsAsset(asset_key=EV, home_uid=HOME, asset_class="ev", vendor="Kestrel", hw_model="ETOURER62",
                    sw_ver="unknown", link_state="UP", serial="KM62-0098812"),
    ])

    s.add_all([
        S.ChgChargepoint(chargepoint_ref=CP, vendor_name="Voltara", model_name="BiDi 11", fw_rev="2.8.3",
                         conn_state="Online", last_seen=iso_off(19, 30), serial_number="VB11-23-004512",
                         nickname="May's driveway"),
        # Decoy: similar nickname, different serial, different site. Must NOT be mapped.
        S.ChgChargepoint(chargepoint_ref=CP_DECOY, vendor_name="Voltara", model_name="BiDi 11", fw_rev="2.8.1",
                         conn_state="Online", last_seen=iso_off(19, 28), serial_number="VB11-23-004513",
                         nickname="May's drive"),
    ])

    s.add_all([
        S.KbArticle(article_code="KB-V2H-014", title="Why V2H discharge pauses at the minimum reserve",
                    audience="public", doc_type="troubleshooting", tags="v2h,reserve,soc,discharge",
                    body=("The HEMS will not discharge the vehicle below the minimum reserve state of charge (SOC) "
                          "set for the home. If the EV's SOC is at or below the reserve when a discharge window "
                          "starts, the discharge is held back to protect your driving range. This is expected "
                          "behaviour, not a fault. To use V2H, charge the vehicle above the reserve or lower the "
                          "reserve in the Helio app (Settings > Vehicle > Minimum reserve).")),
        S.KbArticle(article_code="KB-V2H-022", title="Charger rejected a V2H session (V2H_SESSION_REJECTED)",
                    audience="internal", doc_type="troubleshooting", tags="v2h,charger,rejected,session,voltara",
                    body=("The Voltara integration exposes only the session state and the rejection code. The "
                          "underlying reason is recorded in Voltara's internal charger logs, which HelioHEMS cannot "
                          "access. Collect the session reference, timestamp, charger firmware and vehicle model, "
                          "confirm the charger was online, then escalate through the Voltara partner portal. Do not "
                          "ask the homeowner to power-cycle the charger more than once.")),
        S.KbArticle(article_code="KB-SEC-003", title="Reviewing an unexpected configuration change",
                    audience="internal", doc_type="troubleshooting", tags="security,configuration,audit,reserve,api",
                    body=("A changed setting is not, by itself, evidence of a compromise. Confirm with the homeowner "
                          "and installer whether either made the change. If the actor is an unrecognised API client, "
                          "open a security review, preserve the audit record, and consider revoking that client's "
                          "credentials. Do not tell the customer an attack occurred unless the review confirms it.")),
        S.KbArticle(article_code="MAN-HUB-H2-07", title="Helio Hub H2 manual, section 7: V2H schedules",
                    audience="public", doc_type="manual", tags="v2h,schedule,manual,discharge",
                    body=("A V2H schedule tells the Hub when to power the home from the vehicle. At the start of "
                          "each window the Hub checks the vehicle SOC against the minimum reserve, then asks the "
                          "charger to start a discharge session at the requested power.")),
        S.KbArticle(article_code="KI-0412", title="Known issue: intermittent V2H rejections on BiDi 11 firmware 2.8.x",
                    audience="internal", doc_type="known_issue", tags="v2h,rejected,voltara,firmware,known issue",
                    body=("Voltara has reported that some BiDi 11 units on firmware 2.8.x may reject V2H sessions "
                          "shortly after the vehicle wakes from sleep. Status: under investigation by Voltara. "
                          "Matching this pattern does not confirm the cause; only Voltara's logs can.")),
    ])


# ------------------------------------------------------------ scenario data
def _common(s: Session, soc: float, outcome: str) -> None:
    s.add(S.HemsSchedule(sched_uid="hs_5521", home_uid=HOME, asset_key=EVSE, begins_epoch=ep(18, 55),
                         ends_epoch=ep(22, 0), mode="V2H_DISCHARGE", requested_watts=3000, active=1))
    s.add(S.HemsCommand(cmd_uid="hc_90017", home_uid=HOME, asset_key=EVSE, issued_epoch=ep(19, 0),
                        cmd="DISCHARGE_REQUEST", requested_watts=3000, outcome=outcome))
    for i, (h, m) in enumerate([(18, 30), (18, 45), (19, 0), (19, 15), (19, 30), (19, 45), (20, 0)]):
        s.add(S.HemsTelemetry(sample_uid=f"ht_soc_{i}", home_uid=HOME, asset_key=EV,
                              ts_epoch_ms=ep(h, m) * 1000, channel="ev_soc_pct", reading=soc))
        s.add(S.HemsTelemetry(sample_uid=f"ht_load_{i}", home_uid=HOME, asset_key=GW,
                              ts_epoch_ms=ep(h, m) * 1000, channel="house_load_w",
                              reading=[1650, 2100, 2480, 2620, 2390, 2210, 1980][i]))
        s.add(S.HemsTelemetry(sample_uid=f"ht_pv_{i}", home_uid=HOME, asset_key=GW,
                              ts_epoch_ms=ep(h, m) * 1000, channel="pv_w", reading=0))
    for i, m in enumerate(range(50, 95, 5)):
        h, mm = 18 + m // 60, m % 60
        s.add(S.ChgMeterValue(mv_ref=f"mv_{i:03d}", chargepoint_ref=CP, at=iso_off(h, mm), power_kw=0.0))
    s.add(S.HemsEvent(event_uid="he_2001", home_uid=HOME, asset_key=EVSE, ts_epoch=ep(18, 55),
                      code="SCHED_ACTIVE", level="I", text="V2H discharge window started (requested 3000 W)."))
    # Audit history shared by every scenario.
    s.add(S.AuditConfigLog(audit_seq=1, home_uid=HOME, asset_key=EV, logged_at="2026-03-14T11:40:00Z",
                           param_path="v2h.reserve.min_soc_pct", prev_val="", new_val="40",
                           changed_by="installer_portal", client_id="inst-portal"))
    s.add(S.AuditConfigLog(audit_seq=2, home_uid=HOME, asset_key=EVSE, logged_at="2026-11-02T20:14:00Z",
                           param_path="v2h.schedule.window_end", prev_val="21:00", new_val="22:00",
                           changed_by="homeowner_app", client_id="helio-app-ios"))


def seed_scenario(s: Session, code: str) -> None:
    code = code.upper()
    if code not in SCENARIOS:
        raise ValueError(f"unknown scenario {code}")
    for t in S.SCENARIO_TABLES:
        s.execute(delete(t))

    if code == "A":
        _common(s, soc=35.0, outcome="INHIBITED")
        s.add(S.HemsConstraint(constraint_uid="hk_301", home_uid=HOME, asset_key=EV, kind="MIN_SOC_RESERVE",
                               threshold_pct=40.0, effective_epoch=ep(11, 40, day=14, month=3)))
        s.add(S.HemsEvent(event_uid="he_2002", home_uid=HOME, asset_key=EVSE, ts_epoch=ep(19, 0),
                          code="DISCHARGE_INHIBITED", level="W",
                          text="Discharge inhibited: EV SOC 35% is not above minimum reserve 40%."))

    elif code == "B":
        _common(s, soc=72.0, outcome="NACK")
        s.add(S.HemsConstraint(constraint_uid="hk_301", home_uid=HOME, asset_key=EV, kind="MIN_SOC_RESERVE",
                               threshold_pct=40.0, effective_epoch=ep(11, 40, day=14, month=3)))
        s.add_all([
            S.HemsEvent(event_uid="he_2003", home_uid=HOME, asset_key=EVSE, ts_epoch=ep(19, 0),
                        code="DISCHARGE_CMD_SENT", level="I", text="Discharge request 3000 W sent to EVSE."),
            S.HemsEvent(event_uid="he_2004", home_uid=HOME, asset_key=EVSE, ts_epoch=ep(19, 1),
                        code="EVSE_NACK", level="W", text="EVSE refused V2H start (reported code V2H_SESSION_REJECTED)."),
            S.HemsEvent(event_uid="he_2005", home_uid=HOME, asset_key=EVSE, ts_epoch=ep(19, 5),
                        code="DISCHARGE_NOT_OBSERVED", level="W",
                        text="Measured EVSE power 0.0 kW; expected -3.0 kW."),
        ])
        s.add(S.ChgSession(session_ref="VS-77120", chargepoint_ref=CP, started_at=iso_off(19, 1),
                           ended_at=iso_off(19, 1), kind="V2H", session_state="Rejected",
                           err="V2H_SESSION_REJECTED", err_detail_exposed=False))

    elif code == "C":
        _common(s, soc=55.0, outcome="INHIBITED")
        s.add(S.HemsConstraint(constraint_uid="hk_302", home_uid=HOME, asset_key=EV, kind="MIN_SOC_RESERVE",
                               threshold_pct=60.0, effective_epoch=ep(18, 47)))
        s.add_all([
            S.HemsEvent(event_uid="he_2006", home_uid=HOME, asset_key=EV, ts_epoch=ep(18, 47),
                        code="CONFIG_UPDATED", level="I", text="Setting v2h.reserve.min_soc_pct updated via API."),
            S.HemsEvent(event_uid="he_2007", home_uid=HOME, asset_key=EVSE, ts_epoch=ep(19, 0),
                        code="DISCHARGE_INHIBITED", level="W",
                        text="Discharge inhibited: EV SOC 55% is not above minimum reserve 60%."),
        ])
        s.add(S.AuditConfigLog(audit_seq=3, home_uid=HOME, asset_key=EV, logged_at="2026-11-09T18:47:12Z",
                               param_path="v2h.reserve.min_soc_pct", prev_val="40", new_val="60",
                               changed_by="api_client", client_id="cli-7d2e"))
