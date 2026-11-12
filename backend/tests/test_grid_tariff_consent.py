"""Scenarios D (grid steering) and E (peak charging), the savings calculation,
the Data Act consent manager, demo mode and the read-only guarantee."""
import importlib
import os
from types import SimpleNamespace as NS

import anthropic
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, select

from app import agents as A
from app import canonical as C
from app import consents, datamodel, db, llm, orchestrator, state
from app import tools as T

QD = "Why did my home battery pause last night?"
QE = "Why was my charging so expensive last night?"
HOME = {"X-Demo-Role": "homeowner"}


@pytest.fixture()
def s(tmp_path, monkeypatch):
    monkeypatch.setenv("CONNECTEDCARE_OFFLINE", "1")
    db.configure(f"sqlite:///{tmp_path / 'grid.db'}")
    state.init_db(reset=True)
    with db.session_scope() as sess:
        yield sess


def scenario(s, code):
    state.set_scenario(s, code)
    s.flush()


def revoke(s, source):
    cid = next(c["consent_id"] for c in consents.list_consents(s) if c["source_system"] == source)
    consents.revoke(s, cid)


# ------------------------------------------------------------- data model
def test_supplier_and_market_mapping(s):
    scenario(s, "D")
    ev = s.scalars(select(C.GridSteeringEvent)).one()
    assert (ev.start_time_utc, ev.end_time_utc) == ("2026-11-09T18:00:00Z", "2026-11-09T19:00:00Z")
    assert ev.device_type == "home_battery" and ev.requested_max_kw == 0.0 and ev.reason == "grid congestion"
    assert ev.installation_id == state.INSTALLATION_ID
    assert ev.source_system == "energy_supplier" and ev.source_record_id == "GSR-44871"
    tar = s.scalars(select(C.TariffContract)).one()
    assert tar.contract_type == "fixed" and tar.fixed_price_eur_kwh == 0.32 and tar.grid_steering_agreement
    p = s.get(C.PriceSeries, "CC-PRC-DA-SYN1-20261109-18")
    assert p.price_eur_kwh == 0.254 and p.start_time_utc == "2026-11-09T18:00:00Z"  # 254 EUR/MWh -> EUR/kWh
    assert p.source_system == "market_data" and p.source_record_id == "DA-SYN1-20261109-18"
    bat = s.get(C.EnergyMeasurement, "CC-MEA-ht_bat_00")
    assert bat.value == -2.5  # HEMS + discharging -> canonical - supplies the home


def test_unverified_supply_point_is_not_mapped(s):
    from app import adapters
    from app import sources as S

    scenario(s, "D")
    s.add(S.SupContract(contract_no="NW-99999", supply_point_ref="SP-UNKNOWN", tariff_kind="FIX", fixed_rate_ct_kwh=30.0,
                        dyn_markup_ct_kwh=None, steering_agreement=False, steering_compensation=None, valid_from="2026-01-01"))
    s.flush()
    report = adapters.ingest(s)
    assert s.get(C.TariffContract, "CC-TAR-NW-99999") is None
    assert any(u["source_id"] == "NW-99999" for u in report["unmapped"])


# --------------------------------------------------------------- scenario D
def test_scenario_d_grid_steering_offline(s):
    scenario(s, "D")
    d = orchestrator.diagnose(s, QD, "support")
    assert d["likely_cause"] == "grid_steering" and not d["escalation"]["required"]
    used = {a["key"] for a in d["agents"] if a["used"]}
    assert used == {"grid_tariff", "support_resolution"}
    assert "EVD-SUP-GSR-44871" in d["evidence_ids"]
    steering = [t for t in d["timeline"] if t["kind"] == "steering"]
    assert [t["time"] for t in steering] == ["18:00", "19:00"]
    grid = next(a for a in d["agents"] if a["key"] == "grid_tariff")
    assert grid["tools_called"] == ["get_tariff_contract", "get_steering_events", "get_price_series",
                                    "compare_actual_vs_cheapest"]

    h = orchestrator.diagnose(s, QD, "homeowner")
    text = " ".join([h["headline"], h["what_happened"], *h["why"]]).lower()
    assert "grid" in text and "not a fault" in text and "compensated" in text
    assert "EVD-" not in str(h["why"]) + h["headline"]


def test_scenario_d_answers_v2h_question_too(s):
    scenario(s, "D")
    d = orchestrator.diagnose(s, "Why didn't my EV discharge last night?", "support")
    assert d["likely_cause"] == "grid_steering"  # data-driven: steering event in the window routes the agent


# --------------------------------------------------------------- scenario E
def test_savings_calculation_is_deterministic(s):
    scenario(s, "E")
    w = orchestrator.window_for(QE)
    r = T.compare_actual_vs_cheapest(s, state.INSTALLATION_ID, w)
    (x,) = r["sessions"]
    assert x["energy_kwh"] == 22.2 and x["charging_kw"] == 7.4 and x["departure_utc"] == "2026-11-10T07:00:00Z"
    # actual: 7.4 kWh in each of 18, 19, 20 h at spot + 0.14 markup = 0.394, 0.377, 0.330
    assert x["actual_cost_eur"] == round(7.4 * (0.394 + 0.377 + 0.330), 2) == 8.15
    # cheapest whole hours between plug-in (17:55) and departure (07:00): 03:00 0.063, 04:00 0.065, 02:00 0.066
    assert [h["start_time_utc"][11:16] for h in x["cheapest_hours"]] == ["02:00", "03:00", "04:00"]
    assert x["cheapest_cost_eur"] == round(7.4 * (0.066 + 0.063 + 0.065 + 3 * 0.14), 2) == 4.54
    assert x["saving_eur"] == 3.60 and x["smart_charging_on"] is False
    assert all(h["evidence_id"].startswith("EVD-MKT-") for h in x["cheapest_hours"])


def test_savings_fixed_contract_and_missing_departure(s):
    scenario(s, "E")
    s.get(C.TariffContract, "CC-TAR-NW-55120").contract_type = "fixed"
    s.get(C.TariffContract, "CC-TAR-NW-55120").fixed_price_eur_kwh = 0.30
    w = orchestrator.window_for(QE)
    r = T.compare_actual_vs_cheapest(s, state.INSTALLATION_ID, w)
    assert r["sessions"][0]["saving_eur"] == 0.0 and r["total_actual_eur"] == round(22.2 * 0.30, 2)
    for p in s.scalars(select(C.EnergySchedule)):
        s.delete(p)
    s.flush()
    r = T.compare_actual_vs_cheapest(s, state.INSTALLATION_ID, w)
    assert r["sessions"][0]["cheapest_cost_eur"] is None and r["total_saving_eur"] is None
    assert any("Departure time unknown" in m for m in r["missing"])


def test_scenario_e_peak_charging_offline(s):
    scenario(s, "E")
    d = orchestrator.diagnose(s, QE, "support")
    assert d["likely_cause"] == "peak_charging" and not d["escalation"]["required"]
    assert "8.15" in d["headline"] and "4.54" in d["headline"]
    h = orchestrator.diagnose(s, QE, "homeowner")
    blob = " ".join([h["headline"], *h["why"]])
    assert "8.15" in blob and "4.54" in blob and "02:00" in blob
    assert "EVD-" not in blob


def test_home_savings_endpoint(s):
    scenario(s, "E")
    r = datamodel.home_savings(s, "7d")
    assert r["contract_type"] == "dynamic" and len(r["sessions"]) == 2
    assert r["actual_eur"] == 13.65 and r["saving_eur"] == 6.11 and r["complete"]
    scenario(s, "A")
    r = datamodel.home_savings(s, "7d")
    assert r["contract_type"] == "fixed" and r["sessions"] == [] and "No EV charging" in r["tip"]


# --------------------------------------------------------------- consents
def test_consent_revoke_makes_answers_say_unavailable(s):
    scenario(s, "D")
    revoke(s, "energy_supplier")
    assert "energy_supplier" not in consents.active_sources(s)
    out = T.get_steering_events(s, state.INSTALLATION_ID, orchestrator.window_for(QD))
    assert out["events"] == [] and out["unavailable_sources"][0]["source_system"] == "energy_supplier"
    d = orchestrator.diagnose(s, QD, "support")
    assert d["likely_cause"] == "data_unavailable" and "unavailable" in d["headline"]
    assert any("no active homeowner consent" in m for m in d["missing_information"])
    assert not any(e["evidence_id"].startswith("EVD-SUP") for e in d["evidence"])
    h = orchestrator.diagnose(s, QD, "homeowner")
    assert any("isn't available" in u for u in h["unknown"]) and h["unavailable_sources"] == ["Energy supplier"]

    consents.grant(s, "energy_supplier")
    assert orchestrator.diagnose(s, QD, "support")["likely_cause"] == "grid_steering"


def test_consent_revoke_charger_hides_charger_rows(s):
    scenario(s, "B")
    revoke(s, "charger_integration")
    w = orchestrator.window_for("last night")
    assert T.get_charger_sessions(s, state.INSTALLATION_ID, w)["sessions"] == []
    d = orchestrator.diagnose(s, "Why didn't my EV discharge last night?", "support")
    assert not any(e["source_system"] == "charger_integration" for e in d["evidence"])
    assert any("charger integration" in m and "no active homeowner consent" in m for m in d["missing_information"])


def test_consent_api_and_case_view(tmp_path, monkeypatch):
    monkeypatch.setenv("CONNECTEDCARE_OFFLINE", "1")
    db.configure(f"sqlite:///{tmp_path / 'api.db'}")
    from app.main import app

    with TestClient(app) as c:
        rows = c.get("/api/home/consents", headers=HOME).json()
        by = {r["source_system"]: r for r in rows}
        assert set(by) == {"hems_backend", "charger_integration", "energy_supplier", "config_audit", "market_data"}
        assert by["energy_supplier"]["legal_basis"] == "EU Data Act - user-designated recipient"
        assert by["hems_backend"]["legal_basis"] == "contract" and by["energy_supplier"]["expires_at"]
        assert by["market_data"]["toggleable"] is False
        r = c.post(f"/api/home/consents/{by['charger_integration']['consent_id']}/revoke", headers=HOME).json()
        assert r["active"] is False and r["revoked_at"]
        assert c.post("/api/home/consents/CON-NOPE/revoke", headers=HOME).status_code == 404
        assert c.post("/api/home/consents", json={"source_system": "crm"}, headers=HOME).status_code == 400

        c.post("/api/demo/scenario", json={"scenario": "B"})
        run = c.post("/api/home/chat", json={"question": "Why didn't my EV discharge last night?"}, headers=HOME).json()
        case = c.post("/api/home/support-request", json={"run_id": run["run_id"], "approved": True}, headers=HOME).json()
        detail = c.get(f"/api/support/cases/{case['case_id']}", headers={"X-Demo-Role": "support"}).json()
        dc = {x["source_system"]: x["active"] for x in detail["data_consents"]}
        assert dc["charger_integration"] is False and dc["hems_backend"] is True
        assert detail["consent"]["evidence_scope"]  # the case-share consent is still separate

        g = c.post("/api/home/consents", json={"source_system": "charger_integration"}, headers=HOME).json()
        assert g["active"] is True
        sv = c.get("/api/home/savings?window=7d", headers=HOME)
        assert sv.status_code == 200 and c.get("/api/home/savings?window=99d").status_code == 400


# ----------------------------------------------------- fake Claude (D and E)
def block(name, inp, i):
    return NS(type="tool_use", name=name, input=inp, id=f"tu_{i}")


class FakeClient:
    def __init__(self, script):
        self.script, self.calls = list(script), []
        self.beta = NS(messages=NS(create=self._create))

    def _create(self, **kw):
        self.calls.append(kw)
        content = self.script.pop(0)
        return NS(stop_reason="tool_use" if any(b.type == "tool_use" for b in content) else "end_turn", content=content)


@pytest.fixture()
def claude(s, monkeypatch):
    monkeypatch.delenv("CONNECTEDCARE_OFFLINE", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")

    def install(script):
        fake = FakeClient(script)
        monkeypatch.setattr(anthropic, "Anthropic", lambda: fake)
        return fake

    return install


def test_claude_mode_scenario_d(s, claude):
    scenario(s, "D")
    fake = claude([
        [block("run_grid_tariff_agent", {}, 1)],
        [block("run_support_resolution_agent", {"search_terms": ["grid", "steering"]}, 2)],
        [block("submit_diagnosis", {
            "answer": "Grid steering paused the battery 18:00-19:00 UTC; not a fault.",
            "observed_facts": [{"text": "Steering event 18:00-19:00.", "evidence_ids": ["EVD-SUP-GSR-44871"]},
                               {"text": "Invented", "evidence_ids": ["EVD-SUP-FAKE"]}],
            "interpretation": [{"text": "Compensated under contract.", "evidence_ids": ["EVD-SUP-NW-55120"]}],
            "missing_information": [], "recommended_actions": ["Send KB-GRID-005"]}, 3)],
    ])
    d = orchestrator.diagnose(s, QD, "support")
    assert d["mode"] == "claude" and d["likely_cause"] == "grid_steering"
    assert [f["text"] for f in d["observed_facts"]] == ["Steering event 18:00-19:00."]
    names = {t["name"] for t in fake.calls[0]["tools"]}
    assert "run_grid_tariff_agent" in names and all(t["strict"] for t in fake.calls[0]["tools"])


def test_claude_mode_scenario_e_homeowner_and_leak(s, claude):
    scenario(s, "E")
    claude([
        [block("run_grid_tariff_agent", {}, 1)],
        [block("submit_diagnosis", {"headline": "You paid EUR 8.15; the cheapest hours would have cost EUR 4.54.",
                                    "what_happened": "Your car charged at the evening peak.",
                                    "why": ["Smart charging was off."], "unknown": [],
                                    "next_steps": ["Turn on smart charging."]}, 2)],
    ])
    h = orchestrator.diagnose(s, QE, "homeowner")
    assert h["mode"] == "claude" and h["headline"].startswith("You paid EUR 8.15")

    for leak in ("Based on EVD-MKT-DA-SYN1-20261110-02.", "Steering ref GSR-44871 applied.", "Contract NW-55120."):
        claude([
            [block("run_grid_tariff_agent", {}, 1)],
            [block("submit_diagnosis", {"headline": "x", "what_happened": leak, "why": [], "unknown": [],
                                        "next_steps": []}, 2)],
        ])
        h = orchestrator.diagnose(s, QE, "homeowner")
        assert h["mode"] == "offline" and "leaked" in h["mode_note"]
        assert leak not in h["what_happened"]


def test_claude_homeowner_answer_keeps_unavailable_notice(s, claude):
    scenario(s, "D")
    revoke(s, "energy_supplier")
    claude([
        [block("run_grid_tariff_agent", {}, 1)],
        [block("submit_diagnosis", {"headline": "We can't see your supplier data.", "what_happened": "x",
                                    "why": [], "unknown": ["Supplier records."], "next_steps": []}, 2)],
    ])
    h = orchestrator.diagnose(s, QD, "homeowner")
    assert h["mode"] == "claude" and any("isn't available" in u for u in h["unknown"])


# ---------------------------------------------------------- read-only tools
def test_no_tool_writes_to_any_table(s):
    scenario(s, "E")
    statements = []

    def spy(conn, cursor, statement, *a):
        statements.append(statement.split()[0].upper())

    s.flush()
    engine = s.get_bind()
    event.listen(engine, "before_cursor_execute", spy)
    try:
        iid = state.INSTALLATION_ID
        w = orchestrator.window_for("this week")
        at = T.parse("2026-11-09T19:00:00Z")
        calls = [
            lambda: T.get_device_status(s, iid), lambda: T.get_charger_sessions(s, iid, w),
            lambda: T.get_device_events(s, iid, w), lambda: T.correlate_command_response(s, iid, w),
            lambda: T.get_energy_schedule(s, iid, w), lambda: T.get_soc_history(s, iid, w),
            lambda: T.get_operating_constraints(s, iid, at), lambda: T.compare_expected_actual_power(s, iid, w),
            lambda: T.get_configuration_changes(s, iid, w.start),
            lambda: T.correlate_changes_with_incident(s, iid, at, 24, 50.0),
            lambda: T.get_customer_profile(s, iid), lambda: T.get_previous_cases(s, state.CUSTOMER_ID),
            lambda: T.search_knowledge_base(s, ["tariff", "grid"]),
            lambda: T.get_tariff_contract(s, iid), lambda: T.get_steering_events(s, iid, w),
            lambda: T.get_price_series(s, w), lambda: T.compare_actual_vs_cheapest(s, iid, w),
        ]
        for fn in calls:
            fn()
        ctx = orchestrator.make_ctx(s, QE)
        for agent in (A.TECH, A.ENERGY, A.GRID):
            agent.run(ctx)
        A.SECURITY.run(ctx, 50.0)
        A.SUPPORT.run(ctx, ["tariff"], True)
        assert not s.new and not s.dirty and not s.deleted
        s.flush()
    finally:
        event.remove(engine, "before_cursor_execute", spy)
    assert len(calls) == 17  # every tool exercised
    assert statements and set(statements) <= {"SELECT"}, set(statements)


# --------------------------------------------------------------- demo mode
def test_demo_mode_hides_internals(tmp_path, monkeypatch):
    import app.main as main

    monkeypatch.setenv("CONNECTEDCARE_OFFLINE", "1")
    monkeypatch.setenv("CONNECTEDCARE_DEMO_MODE", "1")
    db.configure(f"sqlite:///{tmp_path / 'demo.db'}")
    demo = importlib.reload(main)
    try:
        with TestClient(demo.app) as c:
            assert c.get("/api/demo").json()["demo_mode"] is True
            assert "swagger" not in c.get("/docs").text.lower()
            assert c.get("/openapi.json").headers.get("content-type", "").startswith("text/html") or \
                c.get("/openapi.json").status_code == 404
            assert c.get("/api/support/data-model", headers={"X-Demo-Role": "support"}).status_code == 404
    finally:
        monkeypatch.delenv("CONNECTEDCARE_DEMO_MODE")
        importlib.reload(main)
    with TestClient(main.app) as c:
        assert c.get("/api/demo").json()["demo_mode"] is False
        assert "swagger" in c.get("/docs").text.lower()


def test_homeowner_forbidden_list_covers_new_ids():
    for ref in ("EVD-SUP-GSR-44871", "EVD-MKT-DA-SYN1-20261109-18", "CC-GSE-GSR-44871", "CC-TAR-NW-55120"):
        assert any(f.lower() in ref.lower() for f in llm.HOMEOWNER_FORBIDDEN)
