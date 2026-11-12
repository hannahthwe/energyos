"""Backend acceptance tests (offline mode, no API key needed)."""
import json
import os

os.environ["CONNECTEDCARE_OFFLINE"] = "1"

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import canonical as C
from app import cases, db, orchestrator, state
from app import sources as S

Q = "Why didn't my EV discharge last night?"
SUPPORT = {"X-Demo-Role": "support"}


@pytest.fixture()
def s(tmp_path):
    db.configure(f"sqlite:///{tmp_path / 'test.db'}")
    state.init_db(reset=True)
    with db.session_scope() as sess:
        yield sess


@pytest.fixture()
def client(tmp_path):
    os.environ["CONNECTEDCARE_DB_URL"] = f"sqlite:///{tmp_path / 'api.db'}"
    db.configure(os.environ["CONNECTEDCARE_DB_URL"])
    from app.main import app
    with TestClient(app) as c:
        yield c


def scenario(s, code):
    state.set_scenario(s, code)
    s.flush()


# ------------------------------------------------------------ data model
def test_canonical_mapping_units_and_signs(s):
    scenario(s, "B")
    cmd = s.scalars(select(C.DeviceCommand)).one()
    assert cmd.requested_value == -3.0 and cmd.command_status == "rejected_by_device"
    assert cmd.timestamp_utc == "2026-11-09T19:00:00Z"
    sched = s.scalars(select(C.EnergySchedule)).one()
    assert sched.requested_power_kw == -3.0 and sched.action == "v2h_discharge"
    sess = s.scalars(select(C.ChargingSession)).one()
    assert sess.session_status == "rejected" and sess.reported_error_code == "V2H_SESSION_REJECTED"
    assert sess.error_detail_available is False
    load = s.scalars(select(C.EnergyMeasurement).where(C.EnergyMeasurement.measurement_type == "household_load")).first()
    assert load.unit == "kW" and load.value < 10


def test_charger_sign_convention_is_flipped(s):
    scenario(s, "A")
    s.add(S.ChgMeterValue(mv_ref="mv_test", chargepoint_ref="CP-VOL-0045120", at="2026-11-09T20:00:00+00:00", power_kw=2.5))
    s.flush()
    from app import adapters
    adapters.ingest(s)
    m = s.get(C.EnergyMeasurement, "CC-MEA-mv_test")
    assert m.value == -2.5  # vendor +export -> canonical -supplies home


def test_every_canonical_record_has_provenance(s):
    scenario(s, "C")
    for t in (C.EnergyMeasurement, C.EnergySchedule, C.OperatingConstraint, C.DeviceCommand,
              C.DiagnosticEvent, C.ConfigurationChange, C.EvidenceRecord):
        for row in s.scalars(select(t)):
            assert row.source_system and row.source_record_id


def test_device_identity_uses_verified_commissioning_not_names(s):
    maps = list(s.scalars(select(C.SourceDeviceMapping)))
    charger = [m for m in maps if m.source_system == "charger_integration"]
    assert [m.source_device_id for m in charger] == ["CP-VOL-0045120"]
    assert all(m.verified for m in maps)
    assert "CP-VOL-0045121" not in {m.source_device_id for m in maps}  # decoy with similar nickname
    assert any(u["source_id"] == "CP-VOL-0045121" for u in state.unmapped_records(s))


def test_serial_mismatch_is_not_verified(s):
    s.get(S.HemsAsset, "ak_evse_9b77").serial = "VB11-23-999999"
    s.flush()
    from app import adapters
    adapters.ingest(s)
    m = s.scalars(select(C.SourceDeviceMapping).where(C.SourceDeviceMapping.source_device_id == "ak_evse_9b77")).one()
    assert m.verified is False
    assert s.scalars(select(C.DeviceCommand)).first() is None  # unverified records are not ingested


# ------------------------------------------------------------ diagnosis
def test_scenario_a_soc_reserve(s):
    scenario(s, "A")
    d = orchestrator.diagnose(s, Q, "support")
    assert d["likely_cause"] == "soc_reserve"
    assert d["escalation"]["required"] is False
    agents = {a["key"]: a for a in d["agents"]}
    assert agents["energy_behaviour"]["signals"]["reserve_blocks"] is True
    assert agents["technical_diagnostics"]["signals"]["command_verdict"] == "not_sent_blocked_by_hems"
    assert agents["technical_diagnostics"]["signals"]["faults"] == []
    assert any(a["article_id"] == "KB-V2H-014" for a in agents["support_resolution"]["signals"]["articles"])
    assert agents["security_correlation"]["used"] is False


def test_scenario_b_charger_rejection_discloses_missing_data(s):
    scenario(s, "B")
    d = orchestrator.diagnose(s, Q, "support")
    assert d["likely_cause"] == "charger_rejection"
    assert d["escalation"]["required"] is True and "Voltara" in d["escalation"]["target"]
    assert any("Voltara charger logs" in m for m in d["missing_information"])
    assert d["confidence"]["level"] == "medium"
    text = json.dumps(d["interpretation"]).lower()
    assert "does not guess" in text or "cannot access" in text
    labels = [t["label"] for t in d["timeline"]]
    assert labels[0].startswith("HEMS V2H discharge schedule") and any("rejected" in x for x in labels)
    times = [t["timestamp_utc"] for t in d["timeline"]]
    assert times == sorted(times)


def test_scenario_c_config_change_not_called_attack(s):
    scenario(s, "C")
    d = orchestrator.diagnose(s, Q, "support")
    assert d["likely_cause"] == "config_change_raised_reserve"
    sec = next(a for a in d["agents"] if a["key"] == "security_correlation")
    assert sec["used"] and sec["signals"]["needs_human_review"] is True
    assert sec["signals"]["security_conclusion"] == "not_determined"
    blob = json.dumps(d).lower()
    assert "not a confirmed cyberattack" in blob or "not evidence of a cyberattack" in blob
    assert "human" in d["escalation"]["target"].lower()


def test_findings_reference_existing_evidence(s):
    for code in "ABC":
        scenario(s, code)
        d = orchestrator.diagnose(s, Q, "support")
        known = {e["evidence_id"] for e in d["evidence"]}
        assert d["observed_facts"]
        for f in d["observed_facts"]:
            assert f["evidence_ids"] and set(f["evidence_ids"]) <= known
        for a in d["agents"]:
            if a["used"] and a["key"] != "support_resolution":
                assert a["evidence_ids"], a["key"]


def test_followup_questions(s):
    scenario(s, "B")
    assert "charger" in orchestrator.diagnose(s, "Which system rejected the command?", "support")["answer"].lower()
    assert orchestrator.diagnose(s, "Was the SOC reserve violated?", "support")["answer"].startswith("No")
    d = orchestrator.diagnose(s, "Was there a configuration change?", "support")
    assert next(a for a in d["agents"] if a["key"] == "security_correlation")["used"]


# ------------------------------------------------------------ visibility
def test_homeowner_never_sees_internal_or_restricted(s):
    scenario(s, "C")
    h = orchestrator.diagnose(s, Q, "homeowner")
    assert all(e["visibility_level"] == "homeowner_safe" for e in h["evidence"])
    assert all(t["visibility_level"] == "homeowner_safe" for t in h["timeline"])
    blob = json.dumps(h)
    for leak in ("cli-7d2e", "api_client", "EVD-AUD", "agents", "KB-SEC-003", "observed_facts"):
        assert leak not in blob


def test_homeowner_api_forces_homeowner_role(client):
    client.post("/api/demo/scenario", json={"scenario": "B"})
    r = client.post("/api/home/chat", json={"question": Q}, headers=SUPPORT)
    assert r.status_code == 200 and r.json()["role"] == "homeowner"
    assert "V2H_SESSION_REJECTED" not in r.text
    assert client.get("/api/support/cases").status_code == 403
    assert client.get("/api/support/cases", headers={"X-Demo-Role": "homeowner"}).status_code == 403


# ------------------------------------------------------------ cases
def test_handoff_requires_consent_and_persists(s):
    scenario(s, "B")
    h = orchestrator.diagnose(s, Q, "homeowner")
    with pytest.raises(cases.Invalid):
        cases.create_case_from_homeowner(s, h["run_id"], approved=False)
    r = cases.create_case_from_homeowner(s, h["run_id"], approved=True)
    c = s.get(C.SupportCase, r["case_id"])
    assert c.status == "New" and c.escalation_required
    assert Q in c.description
    consent = s.scalars(select(C.ConsentRecord).where(C.ConsentRecord.case_id == c.case_id)).one()
    assert set(json.loads(consent.evidence_scope)) == set(r["shared_evidence_ids"])
    detail = cases.get_case(s, c.case_id)
    assert detail["diagnosis"]["likely_cause"] == "charger_rejection"
    assert any(m["message"] == Q for m in detail["messages"])
    assert detail["diagnosis"]["timeline"]


def test_case_workflow_persists_across_sessions(client):
    client.post("/api/demo/scenario", json={"scenario": "B"})
    run = client.post("/api/home/chat", json={"question": Q}).json()
    prev = client.get(f"/api/home/support-request/{run['run_id']}/preview").json()
    assert prev["evidence_to_share"]
    case_id = client.post("/api/home/support-request", json={"run_id": run["run_id"], "approved": True}).json()["case_id"]
    assert case_id in [c["case_id"] for c in client.get("/api/support/cases", headers=SUPPORT).json()]
    client.post(f"/api/support/cases/{case_id}/notes", json={"text": "Called homeowner"}, headers=SUPPORT)
    client.patch(f"/api/support/cases/{case_id}/status", json={"status": "Escalated"}, headers=SUPPORT)
    assert client.patch(f"/api/support/cases/{case_id}/status", json={"status": "Bogus"}, headers=SUPPORT).status_code == 400
    draft = client.post(f"/api/support/cases/{case_id}/draft-response", headers=SUPPORT).json()["draft"]
    assert "V2H_SESSION_REJECTED" not in draft and "May" in draft
    esc = client.post(f"/api/support/cases/{case_id}/escalation", headers=SUPPORT).json()
    assert esc["external_partner"] and all(e["visibility_level"] != "restricted" for e in esc["evidence"])
    ans = client.post(f"/api/support/cases/{case_id}/ask", json={"question": "Which system rejected the command?"},
                      headers=SUPPORT).json()
    assert "charger" in ans["answer"].lower()

    # new session: everything is still there
    with db.session_scope() as s2:
        c = s2.get(C.SupportCase, case_id)
        assert c.status == "Escalated" and c.escalation_json
        types = {m.message_type for m in s2.scalars(select(C.SupportMessage).where(C.SupportMessage.case_id == case_id))}
        assert {"question", "note", "status_change", "draft_response", "copilot_answer"} <= types

    # case survives a scenario switch, follow-ups use the snapshot
    client.post("/api/demo/scenario", json={"scenario": "A"})
    detail = client.get(f"/api/support/cases/{case_id}", headers=SUPPORT).json()
    assert detail["diagnosis"]["likely_cause"] == "charger_rejection" and detail["case"]["live_data_matches"] is False


def test_data_model_endpoint(client):
    dm = client.get("/api/support/data-model", headers=SUPPORT).json()
    assert {x["source_system"] for x in dm["sources"]} == {"crm", "hems_backend", "charger_integration", "knowledge_base", "config_audit",
                                                          "energy_supplier", "market_data"}
    assert dm["identity_chain"][0]["id"] == "CUST-88412" and dm["transforms"]
