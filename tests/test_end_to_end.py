"""End-to-end acceptance: suspicious V2H command → correlated incident → OEM coordination → closure."""
import pytest

from energyos.api_core import dispatch
from energyos.platform import Platform
from energyos.scenarios import SCENARIOS
from energyos.supply_chain import version_key


def call(p, tok, method, path, body=None, query=None):
    return dispatch(p, method, path, query or {}, body or {}, tok)


def login(p, uid):
    return call(p, None, "POST", "/api/login", {"user_id": uid})[1]["token"]


@pytest.fixture
def plat():
    p = Platform(seed=7)
    p.step(60)
    return p


@pytest.mark.parametrize("key", list(SCENARIOS))
def test_every_scenario_matches_ground_truth(key):
    p = Platform(seed=11)
    p.step(60)
    p.start_scenario("u-analyst", key)
    p.step(SCENARIOS[key]["duration"] + 10)
    assert p.run_results()[0]["hypothesis_match"], p.run_results()[0]


def test_background_operation_raises_no_incidents():
    p = Platform(seed=3)
    p.step(600)
    assert p.incidents == {}


def test_v2h_acceptance_workflow(plat):
    analyst, oem, other_oem, exec_ = (login(plat, u) for u in ("u-analyst", "u-voltcharge", "u-storacell", "u-exec"))
    assert call(plat, analyst, "POST", "/api/scenarios/suspicious_v2h/start")[0] == 200
    plat.step(45)

    incs = call(plat, analyst, "GET", "/api/incidents")[1]
    assert len(incs) == 1
    inc = call(plat, analyst, "GET", f"/api/incidents/{incs[0]['id']}")[1]
    assert inc["severity"] == "critical"
    assert inc["hypotheses"][0]["key"] == "unauthorised_command"
    assert inc["links"] and "Unexpected V2H discharge command" in inc["links"][0]["text"]
    assert any(e["type"] == "exposure" and "HYPO-2026-0001" in e["title"] for e in inc["evidence"])
    rules = {e["rule"].split()[0] for e in inc["evidence"] if e["type"] == "detection"}
    assert {"AUTH_BRUTE_FORCE", "UNEXPECTED_V2H_DISCHARGE", "POWER_STEP_CHANGE"} <= rules

    # OEM sees nothing before anything is shared
    assert call(plat, oem, "GET", "/api/incidents")[1] == []
    assert call(plat, oem, "GET", f"/api/incidents/{inc['id']}")[0] == 404

    share = [e["id"] for e in inc["evidence"] if e["device"] == "cha-001" and e["type"] == "detection"][:2]
    s, task = call(plat, analyst, "POST", f"/api/incidents/{inc['id']}/tasks",
                   {"org": "voltcharge", "title": "Review OCPP logs", "evidence_ids": share})
    assert s == 200

    view = call(plat, oem, "GET", f"/api/incidents/{inc['id']}")[1]
    assert view["restricted_view"] and {e["id"] for e in view["evidence"]} == set(share)
    assert "installations" not in view and "hypotheses" not in view
    assert call(plat, other_oem, "GET", f"/api/incidents/{inc['id']}")[0] == 404

    # OEM cannot make security decisions; it can respond to its task
    assert call(plat, oem, "POST", f"/api/incidents/{inc['id']}/decision", {"decision": "confirm_attack", "rationale": "x"})[0] == 403
    assert call(plat, oem, "POST", f"/api/tasks/{task['id']}", {"status": "acknowledged", "comment": "On it"})[0] == 200
    assert call(plat, exec_, "GET", "/api/incidents")[0] == 403

    # human decisions and closure
    assert call(plat, analyst, "POST", f"/api/incidents/{inc['id']}/decision", {"decision": "confirm_attack", "rationale": ""})[0] == 400
    assert call(plat, analyst, "POST", f"/api/incidents/{inc['id']}/decision",
                {"decision": "confirm_attack", "rationale": "Resident did not request discharge"})[0] == 200
    for st in ("mitigation_planned", "monitoring", "resolved"):
        assert call(plat, analyst, "POST", f"/api/incidents/{inc['id']}/status", {"status": st, "reason": "ok"})[0] == 200
    assert call(plat, analyst, "POST", f"/api/incidents/{inc['id']}/status", {"status": "closed"})[0] == 400
    assert call(plat, analyst, "POST", f"/api/incidents/{inc['id']}/status", {"status": "closed", "reason": "Credentials revoked"})[0] == 200

    audit = call(plat, analyst, "GET", "/api/audit")[1]
    actions = {r["action"] for r in audit["records"]}
    assert audit["chain_valid"]
    assert {"incident.created", "task.created", "evidence.shared", "task.updated",
            "incident.decision.confirm_attack", "incident.status_changed"} <= actions


def test_fleet_probe_creates_one_fleet_incident(plat):
    plat.start_scenario("u-analyst", "firmware_fleet")
    plat.step(40)
    active = [i for i in plat.incidents.values() if i["status"] != "duplicate"]
    assert len(active) == 1
    g = plat._group(active[0])
    assert g["kind"] == "fleet" and len(g["installations"]) == 3


def test_audit_chain_detects_tampering(plat):
    tok = login(plat, "u-analyst")
    call(plat, tok, "POST", "/api/scenarios/normal/start")
    assert plat.verify_audit_chain()
    plat.audit[0]["action"] = "tampered"
    assert not plat.verify_audit_chain()


def test_vex_only_for_own_products(plat):
    oem = login(plat, "u-voltcharge")
    ok = call(plat, oem, "POST", "/api/supply-chain/decision", {"vulnerability": "HYPO-2026-0002", "model": "HG-Hub 2",
                                                                 "version": "5.1.0", "status": "not_affected"})
    assert ok[0] == 403
    ok = call(plat, oem, "POST", "/api/supply-chain/decision", {"vulnerability": "HYPO-2026-0001", "model": "VC-Bidi 11 (V2H)",
                                                                 "version": "3.2.1", "status": "confirmed_affected", "note": "PSIRT"})
    assert ok[0] == 200
    assert plat.sc.applicability("VC-Bidi 11 (V2H)", "3.2.1")[0]["status"] == "confirmed_affected"


def test_version_ordering_and_sbom_validation(plat):
    assert version_key("1.1.1k") < version_key("1.1.1w") < version_key("3.0.0")
    assert version_key("1.4.2") < version_key("1.5.0")
    with pytest.raises(ValueError):
        plat.sc.add_sbom(b'{"bomFormat": "SPDX"}', "x.json")
