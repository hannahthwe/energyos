"""ConnectedCare canonical information model.

Every record carries a stable identifier and explicit provenance
(`source_system` + `source_record_id`). Units and sign conventions:

    power      kW, signed from the home's point of view of the device:
               + = device draws energy (charging), - = device supplies the home (discharging)
    SOC        percent (0-100)
    time       UTC, ISO-8601 string with 'Z'
"""
from __future__ import annotations

from sqlalchemy import Boolean, Float, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base

VISIBILITY_LEVELS = ("homeowner_safe", "support_internal", "restricted")


class Customer(Base):
    __tablename__ = "cc_customers"
    customer_id: Mapped[str] = mapped_column(String, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String)
    name: Mapped[str] = mapped_column(String)
    email: Mapped[str] = mapped_column(String)
    support_contact_preference: Mapped[str] = mapped_column(String)
    source_system: Mapped[str] = mapped_column(String)
    source_record_id: Mapped[str] = mapped_column(String)


class Installation(Base):
    __tablename__ = "cc_installations"
    installation_id: Mapped[str] = mapped_column(String, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String)
    customer_id: Mapped[str] = mapped_column(String)
    timezone: Mapped[str] = mapped_column(String)
    installation_status: Mapped[str] = mapped_column(String)
    address: Mapped[str] = mapped_column(String)
    installed_on: Mapped[str] = mapped_column(String)
    installer: Mapped[str] = mapped_column(String)
    source_system: Mapped[str] = mapped_column(String)
    source_record_id: Mapped[str] = mapped_column(String)


class Device(Base):
    __tablename__ = "cc_devices"
    device_id: Mapped[str] = mapped_column(String, primary_key=True)
    installation_id: Mapped[str] = mapped_column(String)
    device_type: Mapped[str] = mapped_column(String)  # hems | ev_charger | ev
    manufacturer: Mapped[str] = mapped_column(String)
    model: Mapped[str] = mapped_column(String)
    firmware_version: Mapped[str] = mapped_column(String)
    connectivity_status: Mapped[str] = mapped_column(String)  # online | offline | unknown
    serial_number: Mapped[str] = mapped_column(String)


class SourceDeviceMapping(Base):
    __tablename__ = "cc_source_device_mappings"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    device_id: Mapped[str] = mapped_column(String)
    source_system: Mapped[str] = mapped_column(String)
    source_device_id: Mapped[str] = mapped_column(String)
    mapping_method: Mapped[str] = mapped_column(String)
    verified: Mapped[bool] = mapped_column(Boolean)
    evidence_ref: Mapped[str] = mapped_column(String)


class EnergyMeasurement(Base):
    __tablename__ = "cc_energy_measurements"
    measurement_id: Mapped[str] = mapped_column(String, primary_key=True)
    installation_id: Mapped[str] = mapped_column(String)
    device_id: Mapped[str] = mapped_column(String)
    timestamp_utc: Mapped[str] = mapped_column(String)
    measurement_type: Mapped[str] = mapped_column(String)  # ev_soc | charger_power | household_load | solar_production
    value: Mapped[float] = mapped_column(Float)
    unit: Mapped[str] = mapped_column(String)
    source_system: Mapped[str] = mapped_column(String)
    source_record_id: Mapped[str] = mapped_column(String)


class EnergySchedule(Base):
    __tablename__ = "cc_energy_schedules"
    schedule_id: Mapped[str] = mapped_column(String, primary_key=True)
    installation_id: Mapped[str] = mapped_column(String)
    target_device_id: Mapped[str] = mapped_column(String)
    start_time_utc: Mapped[str] = mapped_column(String)
    end_time_utc: Mapped[str] = mapped_column(String)
    action: Mapped[str] = mapped_column(String)  # v2h_discharge | smart_charge
    requested_power_kw: Mapped[float] = mapped_column(Float)  # signed
    enabled: Mapped[bool] = mapped_column(Boolean)
    source_system: Mapped[str] = mapped_column(String)
    source_record_id: Mapped[str] = mapped_column(String)


class OperatingConstraint(Base):
    __tablename__ = "cc_operating_constraints"
    constraint_id: Mapped[str] = mapped_column(String, primary_key=True)
    installation_id: Mapped[str] = mapped_column(String)
    device_id: Mapped[str] = mapped_column(String)
    constraint_type: Mapped[str] = mapped_column(String)  # min_reserve_soc
    threshold_value: Mapped[float] = mapped_column(Float)
    unit: Mapped[str] = mapped_column(String)
    valid_from_utc: Mapped[str] = mapped_column(String)
    source_system: Mapped[str] = mapped_column(String)
    source_record_id: Mapped[str] = mapped_column(String)


class DeviceCommand(Base):
    __tablename__ = "cc_device_commands"
    command_id: Mapped[str] = mapped_column(String, primary_key=True)
    installation_id: Mapped[str] = mapped_column(String)
    target_device_id: Mapped[str] = mapped_column(String)
    timestamp_utc: Mapped[str] = mapped_column(String)
    command_type: Mapped[str] = mapped_column(String)  # discharge_request
    requested_value: Mapped[float] = mapped_column(Float)  # kW, signed
    command_status: Mapped[str] = mapped_column(String)  # blocked_by_hems | sent | rejected_by_device | accepted
    source_system: Mapped[str] = mapped_column(String)
    source_record_id: Mapped[str] = mapped_column(String)


class ChargingSession(Base):
    __tablename__ = "cc_charging_sessions"
    session_id: Mapped[str] = mapped_column(String, primary_key=True)
    installation_id: Mapped[str] = mapped_column(String)
    charger_device_id: Mapped[str] = mapped_column(String)
    start_time_utc: Mapped[str] = mapped_column(String)
    end_time_utc: Mapped[str | None] = mapped_column(String, nullable=True)
    session_type: Mapped[str] = mapped_column(String)  # v2h_discharge | charge
    session_status: Mapped[str] = mapped_column(String)  # rejected | completed | active
    reported_error_code: Mapped[str | None] = mapped_column(String, nullable=True)
    error_detail_available: Mapped[bool] = mapped_column(Boolean, default=False)
    source_system: Mapped[str] = mapped_column(String)
    source_record_id: Mapped[str] = mapped_column(String)


class DiagnosticEvent(Base):
    __tablename__ = "cc_diagnostic_events"
    event_id: Mapped[str] = mapped_column(String, primary_key=True)
    installation_id: Mapped[str] = mapped_column(String)
    device_id: Mapped[str] = mapped_column(String)
    timestamp_utc: Mapped[str] = mapped_column(String)
    event_type: Mapped[str] = mapped_column(String)
    severity: Mapped[str] = mapped_column(String)  # info | warning | error
    message: Mapped[str] = mapped_column(Text)
    source_system: Mapped[str] = mapped_column(String)
    source_record_id: Mapped[str] = mapped_column(String)


class ConfigurationChange(Base):
    __tablename__ = "cc_configuration_changes"
    change_id: Mapped[str] = mapped_column(String, primary_key=True)
    installation_id: Mapped[str] = mapped_column(String)
    device_id: Mapped[str] = mapped_column(String)
    timestamp_utc: Mapped[str] = mapped_column(String)
    setting_name: Mapped[str] = mapped_column(String)
    old_value: Mapped[str] = mapped_column(String)
    new_value: Mapped[str] = mapped_column(String)
    actor_type: Mapped[str] = mapped_column(String)  # installer | homeowner | api_client
    actor_ref: Mapped[str] = mapped_column(String)
    source_system: Mapped[str] = mapped_column(String)
    source_record_id: Mapped[str] = mapped_column(String)


class KnowledgeArticle(Base):
    __tablename__ = "cc_knowledge_articles"
    article_id: Mapped[str] = mapped_column(String, primary_key=True)
    title: Mapped[str] = mapped_column(String)
    body: Mapped[str] = mapped_column(Text)
    tags: Mapped[str] = mapped_column(String)
    doc_type: Mapped[str] = mapped_column(String)
    visibility_level: Mapped[str] = mapped_column(String)
    source_system: Mapped[str] = mapped_column(String)
    source_record_id: Mapped[str] = mapped_column(String)


class PreviousInteraction(Base):
    __tablename__ = "cc_previous_interactions"
    interaction_id: Mapped[str] = mapped_column(String, primary_key=True)
    customer_id: Mapped[str] = mapped_column(String)
    installation_id: Mapped[str] = mapped_column(String)
    subject: Mapped[str] = mapped_column(String)
    opened_utc: Mapped[str] = mapped_column(String)
    status: Mapped[str] = mapped_column(String)
    resolution: Mapped[str] = mapped_column(Text)
    channel: Mapped[str] = mapped_column(String)
    source_system: Mapped[str] = mapped_column(String)
    source_record_id: Mapped[str] = mapped_column(String)


class EvidenceRecord(Base):
    __tablename__ = "cc_evidence_records"
    evidence_id: Mapped[str] = mapped_column(String, primary_key=True)
    installation_id: Mapped[str] = mapped_column(String)
    source_system: Mapped[str] = mapped_column(String)
    source_record_id: Mapped[str] = mapped_column(String)
    timestamp_utc: Mapped[str] = mapped_column(String)
    evidence_type: Mapped[str] = mapped_column(String)
    summary: Mapped[str] = mapped_column(Text)
    visibility_level: Mapped[str] = mapped_column(String)


# ------------------------------------------------------------ support workflow
class SupportCase(Base):
    __tablename__ = "cc_support_cases"
    case_id: Mapped[str] = mapped_column(String, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String)
    customer_id: Mapped[str] = mapped_column(String)
    installation_id: Mapped[str] = mapped_column(String)
    title: Mapped[str] = mapped_column(String)
    description: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String)  # New | Investigating | Escalated | Resolved
    created_at: Mapped[str] = mapped_column(String)
    diagnostic_summary: Mapped[str] = mapped_column(Text)
    escalation_required: Mapped[bool] = mapped_column(Boolean)
    scenario: Mapped[str] = mapped_column(String)
    origin: Mapped[str] = mapped_column(String)  # homeowner_app | crm_import
    snapshot_json: Mapped[str] = mapped_column(Text, default="{}")  # support-level diagnosis at handoff
    escalation_json: Mapped[str | None] = mapped_column(Text, nullable=True)


class CaseEvidence(Base):
    __tablename__ = "cc_case_evidence"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    case_id: Mapped[str] = mapped_column(String)
    evidence_id: Mapped[str] = mapped_column(String)
    shared_by_homeowner: Mapped[bool] = mapped_column(Boolean)
    summary: Mapped[str] = mapped_column(Text)
    visibility_level: Mapped[str] = mapped_column(String)


class SupportMessage(Base):
    __tablename__ = "cc_support_messages"
    message_id: Mapped[str] = mapped_column(String, primary_key=True)
    case_id: Mapped[str] = mapped_column(String)
    sender_role: Mapped[str] = mapped_column(String)  # homeowner | support | ai_copilot | system
    message_type: Mapped[str] = mapped_column(String)  # question | note | draft_response | copilot_answer | status_change
    message: Mapped[str] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(String)
    visibility_level: Mapped[str] = mapped_column(String)


class ConsentRecord(Base):
    __tablename__ = "cc_consent_records"
    consent_id: Mapped[str] = mapped_column(String, primary_key=True)
    customer_id: Mapped[str] = mapped_column(String)
    case_id: Mapped[str] = mapped_column(String)
    evidence_scope: Mapped[str] = mapped_column(Text)  # JSON list of evidence ids
    approved_at: Mapped[str] = mapped_column(String)


class DiagnosisRun(Base):
    __tablename__ = "cc_diagnosis_runs"
    run_id: Mapped[str] = mapped_column(String, primary_key=True)
    role: Mapped[str] = mapped_column(String)
    question: Mapped[str] = mapped_column(Text)
    scenario: Mapped[str] = mapped_column(String)
    created_at: Mapped[str] = mapped_column(String)
    result_json: Mapped[str] = mapped_column(Text)


class DemoState(Base):
    __tablename__ = "cc_demo_state"
    key: Mapped[str] = mapped_column(String, primary_key=True)
    value: Mapped[str] = mapped_column(String)


# Canonical tables that are rebuilt from the sources on every ingestion.
DERIVED_TABLES = [
    Customer, Installation, Device, SourceDeviceMapping, EnergyMeasurement, EnergySchedule,
    OperatingConstraint, DeviceCommand, ChargingSession, DiagnosticEvent, ConfigurationChange,
    KnowledgeArticle, PreviousInteraction, EvidenceRecord,
]
