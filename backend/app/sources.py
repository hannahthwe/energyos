"""Native schemas of the four simulated HelioHEMS source systems.

These deliberately do NOT share identifiers, field names, units, timestamp
formats or sign conventions. The adapters in `adapters.py` translate them into
the canonical model.

    CRM            crm_customer_ref, site_ref, asset_reg_id     ISO dates
    HEMS backend   home_uid, asset_key, requested_watts         epoch seconds / ms, watts
    Charger API    chargepoint_ref, session_state, power_kw     ISO-8601 with offset, kW, +export
    KB / audit     article_code, audit_seq, param_path          ISO strings
    Supplier       contract_no, supply_point_ref, steering_ref  local "YYYY-MM-DD HH:MM" + tz name, kW
    Market data    bidding_zone, delivery_hour                  local "YYYY-MM-DD HH:00" + tz name, EUR/MWh
"""
from __future__ import annotations

from sqlalchemy import Boolean, Float, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base


# ---------------------------------------------------------------- Source 1: CRM
class CrmAccount(Base):
    __tablename__ = "crm_accounts"
    crm_customer_ref: Mapped[str] = mapped_column(String, primary_key=True)
    tenant_code: Mapped[str] = mapped_column(String)
    full_name: Mapped[str] = mapped_column(String)
    email_addr: Mapped[str] = mapped_column(String)
    contact_pref: Mapped[str] = mapped_column(String)  # "EMAIL" | "PHONE" | "APP"


class CrmSite(Base):
    __tablename__ = "crm_sites"
    site_ref: Mapped[str] = mapped_column(String, primary_key=True)
    crm_customer_ref: Mapped[str] = mapped_column(String)
    tz: Mapped[str] = mapped_column(String)
    site_status: Mapped[str] = mapped_column(String)  # "LIVE" | "PENDING"
    address_line: Mapped[str] = mapped_column(String)
    installed_on: Mapped[str] = mapped_column(String)  # "2026-03-14"
    installer_name: Mapped[str] = mapped_column(String)


class CrmRegisteredAsset(Base):
    __tablename__ = "crm_registered_assets"
    asset_reg_id: Mapped[str] = mapped_column(String, primary_key=True)
    site_ref: Mapped[str] = mapped_column(String)
    product_category: Mapped[str] = mapped_column(String)  # "HEMS Gateway" | "Bidirectional Charger" | "Electric Vehicle" | "Home Battery"
    brand: Mapped[str] = mapped_column(String)
    product_model: Mapped[str] = mapped_column(String)
    serial_no: Mapped[str] = mapped_column(String)


class CrmCase(Base):
    __tablename__ = "crm_cases"
    crm_case_no: Mapped[str] = mapped_column(String, primary_key=True)
    crm_customer_ref: Mapped[str] = mapped_column(String)
    site_ref: Mapped[str] = mapped_column(String)
    subject: Mapped[str] = mapped_column(String)
    opened: Mapped[str] = mapped_column(String)
    closed_state: Mapped[str] = mapped_column(String)
    resolution_notes: Mapped[str] = mapped_column(Text)
    channel: Mapped[str] = mapped_column(String)


class CrmSupplyPoint(Base):
    """Verified link between a CRM site and the energy supplier's meter point.
    The only trusted way to attach supplier records to an installation."""

    __tablename__ = "crm_supply_points"
    supply_point_ref: Mapped[str] = mapped_column(String, primary_key=True)
    site_ref: Mapped[str] = mapped_column(String)
    supplier_name: Mapped[str] = mapped_column(String)
    verified_by: Mapped[str] = mapped_column(String)
    verified_at: Mapped[str] = mapped_column(String)


class CrmCommissioning(Base):
    """Installer commissioning record: the ONLY trusted source of cross-system
    device identity. Written when the installer scans each device on site."""

    __tablename__ = "crm_commissioning"
    commissioning_ref: Mapped[str] = mapped_column(String, primary_key=True)
    site_ref: Mapped[str] = mapped_column(String)
    asset_reg_id: Mapped[str] = mapped_column(String)
    hems_home_uid: Mapped[str] = mapped_column(String)
    hems_asset_key: Mapped[str] = mapped_column(String)
    chargepoint_ref: Mapped[str | None] = mapped_column(String, nullable=True)
    scanned_serial: Mapped[str] = mapped_column(String)
    verified_by: Mapped[str] = mapped_column(String)
    verified_at: Mapped[str] = mapped_column(String)


# ------------------------------------------------------- Source 2: HEMS backend
class HemsHome(Base):
    __tablename__ = "hems_homes"
    home_uid: Mapped[str] = mapped_column(String, primary_key=True)
    utc_offset_min: Mapped[int] = mapped_column(Integer)
    fleet: Mapped[str] = mapped_column(String)


class HemsAsset(Base):
    __tablename__ = "hems_assets"
    asset_key: Mapped[str] = mapped_column(String, primary_key=True)
    home_uid: Mapped[str] = mapped_column(String)
    asset_class: Mapped[str] = mapped_column(String)  # "gw" | "evse" | "ev" | "bat"
    vendor: Mapped[str] = mapped_column(String)
    hw_model: Mapped[str] = mapped_column(String)
    sw_ver: Mapped[str] = mapped_column(String)
    link_state: Mapped[str] = mapped_column(String)  # "UP" | "DOWN"
    serial: Mapped[str] = mapped_column(String)


class HemsSchedule(Base):
    __tablename__ = "hems_schedules"
    sched_uid: Mapped[str] = mapped_column(String, primary_key=True)
    home_uid: Mapped[str] = mapped_column(String)
    asset_key: Mapped[str] = mapped_column(String)
    begins_epoch: Mapped[int] = mapped_column(Integer)
    ends_epoch: Mapped[int] = mapped_column(Integer)
    mode: Mapped[str] = mapped_column(String)  # "V2H_DISCHARGE" | "SMART_CHARGE" (ends = departure) | "BATTERY_DISCHARGE"
    requested_watts: Mapped[int] = mapped_column(Integer)  # magnitude, always positive
    active: Mapped[int] = mapped_column(Integer)  # 1 / 0


class HemsConstraint(Base):
    __tablename__ = "hems_constraints"
    constraint_uid: Mapped[str] = mapped_column(String, primary_key=True)
    home_uid: Mapped[str] = mapped_column(String)
    asset_key: Mapped[str] = mapped_column(String)
    kind: Mapped[str] = mapped_column(String)  # "MIN_SOC_RESERVE"
    threshold_pct: Mapped[float] = mapped_column(Float)
    effective_epoch: Mapped[int] = mapped_column(Integer)


class HemsCommand(Base):
    __tablename__ = "hems_commands"
    cmd_uid: Mapped[str] = mapped_column(String, primary_key=True)
    home_uid: Mapped[str] = mapped_column(String)
    asset_key: Mapped[str] = mapped_column(String)
    issued_epoch: Mapped[int] = mapped_column(Integer)
    cmd: Mapped[str] = mapped_column(String)  # "DISCHARGE_REQUEST"
    requested_watts: Mapped[int] = mapped_column(Integer)
    outcome: Mapped[str] = mapped_column(String)  # "INHIBITED" | "SENT" | "NACK" | "ACK"


class HemsTelemetry(Base):
    __tablename__ = "hems_telemetry"
    sample_uid: Mapped[str] = mapped_column(String, primary_key=True)
    home_uid: Mapped[str] = mapped_column(String)
    asset_key: Mapped[str] = mapped_column(String)
    ts_epoch_ms: Mapped[int] = mapped_column(Integer)
    channel: Mapped[str] = mapped_column(String)  # "ev_soc_pct" | "house_load_w" | "pv_w" | "bat_w" (+ = discharging)
    reading: Mapped[float] = mapped_column(Float)


class HemsEvent(Base):
    __tablename__ = "hems_events"
    event_uid: Mapped[str] = mapped_column(String, primary_key=True)
    home_uid: Mapped[str] = mapped_column(String)
    asset_key: Mapped[str] = mapped_column(String)
    ts_epoch: Mapped[int] = mapped_column(Integer)
    code: Mapped[str] = mapped_column(String)
    level: Mapped[str] = mapped_column(String)  # "I" | "W" | "E"
    text: Mapped[str] = mapped_column(Text)


# --------------------------------------- Source 3: Charger platform integration
class ChgChargepoint(Base):
    __tablename__ = "chg_chargepoints"
    chargepoint_ref: Mapped[str] = mapped_column(String, primary_key=True)
    vendor_name: Mapped[str] = mapped_column(String)
    model_name: Mapped[str] = mapped_column(String)
    fw_rev: Mapped[str] = mapped_column(String)
    conn_state: Mapped[str] = mapped_column(String)  # "Online" | "Offline"
    last_seen: Mapped[str] = mapped_column(String)  # ISO-8601 with offset
    serial_number: Mapped[str] = mapped_column(String)
    nickname: Mapped[str] = mapped_column(String)


class ChgSession(Base):
    __tablename__ = "chg_sessions"
    session_ref: Mapped[str] = mapped_column(String, primary_key=True)
    chargepoint_ref: Mapped[str] = mapped_column(String)
    started_at: Mapped[str] = mapped_column(String)
    ended_at: Mapped[str | None] = mapped_column(String, nullable=True)
    kind: Mapped[str] = mapped_column(String)  # "V2H" | "CHARGE"
    session_state: Mapped[str] = mapped_column(String)  # "Rejected" | "Completed" | "Active"
    err: Mapped[str | None] = mapped_column(String, nullable=True)
    err_detail_exposed: Mapped[bool] = mapped_column(Boolean, default=False)


class ChgMeterValue(Base):
    __tablename__ = "chg_meter_values"
    mv_ref: Mapped[str] = mapped_column(String, primary_key=True)
    chargepoint_ref: Mapped[str] = mapped_column(String)
    at: Mapped[str] = mapped_column(String)
    power_kw: Mapped[float] = mapped_column(Float)  # vendor convention: + = export from EV, - = charging


# ------------------------------------------------ Source 4: Knowledge + audit
class KbArticle(Base):
    __tablename__ = "kb_articles"
    article_code: Mapped[str] = mapped_column(String, primary_key=True)
    title: Mapped[str] = mapped_column(String)
    body: Mapped[str] = mapped_column(Text)
    tags: Mapped[str] = mapped_column(String)  # comma-separated
    audience: Mapped[str] = mapped_column(String)  # "public" | "internal"
    doc_type: Mapped[str] = mapped_column(String)  # "troubleshooting" | "manual" | "known_issue"


class AuditConfigLog(Base):
    __tablename__ = "audit_config_log"
    audit_seq: Mapped[int] = mapped_column(Integer, primary_key=True)
    home_uid: Mapped[str] = mapped_column(String)
    asset_key: Mapped[str] = mapped_column(String)
    logged_at: Mapped[str] = mapped_column(String)
    param_path: Mapped[str] = mapped_column(String)
    prev_val: Mapped[str] = mapped_column(String)
    new_val: Mapped[str] = mapped_column(String)
    changed_by: Mapped[str] = mapped_column(String)  # "installer_portal" | "homeowner_app" | "api_client"
    client_id: Mapped[str] = mapped_column(String)


# ------------------------------------------------- Source 5: Energy supplier
class SupContract(Base):
    __tablename__ = "sup_contracts"
    contract_no: Mapped[str] = mapped_column(String, primary_key=True)
    supply_point_ref: Mapped[str] = mapped_column(String)
    tariff_kind: Mapped[str] = mapped_column(String)  # "FIX" | "DYN"
    fixed_rate_ct_kwh: Mapped[float | None] = mapped_column(Float, nullable=True)  # cents/kWh
    dyn_markup_ct_kwh: Mapped[float | None] = mapped_column(Float, nullable=True)  # cents/kWh on top of spot
    steering_agreement: Mapped[bool] = mapped_column(Boolean, default=False)
    steering_compensation: Mapped[str | None] = mapped_column(String, nullable=True)
    valid_from: Mapped[str] = mapped_column(String)  # "2026-01-01"


class SupSteeringEvent(Base):
    __tablename__ = "sup_steering_events"
    steering_ref: Mapped[str] = mapped_column(String, primary_key=True)
    contract_no: Mapped[str] = mapped_column(String)
    local_start: Mapped[str] = mapped_column(String)  # "2026-11-09 18:00"
    local_end: Mapped[str] = mapped_column(String)
    tz_name: Mapped[str] = mapped_column(String)
    controllable_unit: Mapped[str] = mapped_column(String)  # "HOME_BATTERY" | "WALLBOX" | "HEAT_PUMP"
    max_kw: Mapped[float] = mapped_column(Float)
    reason: Mapped[str] = mapped_column(String)
    grid_operator: Mapped[str] = mapped_column(String)


# ---------------------------------------------- Source 6: Public market data
class MktDayAheadPrice(Base):
    __tablename__ = "mkt_day_ahead_prices"
    price_ref: Mapped[str] = mapped_column(String, primary_key=True)
    bidding_zone: Mapped[str] = mapped_column(String)
    delivery_hour: Mapped[str] = mapped_column(String)  # local "2026-11-09 18:00"
    tz_name: Mapped[str] = mapped_column(String)
    price_eur_mwh: Mapped[float] = mapped_column(Float)


SOURCE_TABLES = [
    CrmAccount, CrmSite, CrmRegisteredAsset, CrmCase, CrmCommissioning,
    CrmSupplyPoint,
    HemsHome, HemsAsset, HemsSchedule, HemsConstraint, HemsCommand, HemsTelemetry, HemsEvent,
    ChgChargepoint, ChgSession, ChgMeterValue,
    KbArticle, AuditConfigLog,
    SupContract, SupSteeringEvent, MktDayAheadPrice,
]

# Telemetry-type tables that change with the selected scenario.
SCENARIO_TABLES = [
    HemsSchedule, HemsConstraint, HemsCommand, HemsTelemetry, HemsEvent,
    ChgSession, ChgMeterValue, AuditConfigLog, SupContract, SupSteeringEvent,
]
