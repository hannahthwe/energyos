"""Repeatable security scenarios. Each scenario only changes simulated device behaviour;
the detection and correlation engines process the resulting events independently."""
from __future__ import annotations


def _normal(sim, run):
    run.ground_truth = {"label": "benign", "devices": []}


def _legit_change(sim, run):
    inst = "inst-002"
    gw = sim.dev(inst, "gateway")
    client = "app-user-002"

    def step(s):
        s._event(gw, "auth_success", client_id=client, method="oauth_token", user="resident-002")
        s._event(gw, "schedule_changed", client_id=client, user="resident-002",
                 change="boost charging to 11 kW until 07:00")
        s.issue_command(inst, "charger", 11000.0, client)

    run.steps = [(3, step)]
    run.ground_truth = {"label": "benign", "devices": ["cha-002"], "expected_hypothesis": "legitimate_user_activity"}


def _suspicious_v2h(sim, run):
    inst = "inst-001"
    gw = sim.dev(inst, "gateway")
    bad = "unknown-7f3a91"

    def fails(s):
        for _ in range(3):
            s._event(gw, "auth_failure", "low", client_id=bad, method="local_api_password", source_ip="10.0.8.77")

    def success(s):
        s._event(gw, "auth_success", client_id=bad, method="local_api_password", source_ip="10.0.8.77")

    def discharge(s):
        s.issue_command(inst, "charger", -9000.0, bad)

    run.steps = [(3, fails), (6, fails), (9, success), (12, discharge)]
    run.ground_truth = {"label": "malicious", "devices": ["gat-001", "cha-001"],
                        "expected_hypothesis": "unauthorised_command"}


def _comm_failure(sim, run):
    ch = sim.dev("inst-003", "charger")
    gw = sim.dev("inst-003", "gateway")

    def drop(s):
        ch.silent_until = s.t + 25
        s._event(gw, "communication_lost", "medium", target=ch.id, reason="no heartbeat")

    def restore(s):
        s._event(gw, "communication_restored", target=ch.id)

    run.steps = [(3, drop), (29, restore)]
    run.ground_truth = {"label": "fault", "devices": ["cha-003"], "expected_hypothesis": "communication_fault"}


def _telemetry_mismatch(sim, run):
    ch = sim.dev("inst-004", "charger")

    def start(s):
        ch.report_override_w = 7400.0
        ch.setpoint_w = 300.0

    def stop(s):
        ch.report_override_w = None

    run.steps = [(3, start), (35, stop)]
    run.ground_truth = {"label": "integrity_anomaly", "devices": ["cha-004", "met-004"],
                        "expected_hypothesis": "sensor_fault"}


def _sensor_fault(sim, run):
    bat = sim.dev("inst-002", "battery")

    def start(s):
        bat.sensor_fault = True

    def stop(s):
        bat.sensor_fault = False

    run.steps = [(3, start), (25, stop)]
    run.ground_truth = {"label": "fault", "devices": ["bat-002"], "expected_hypothesis": "sensor_fault"}


def _firmware_fleet(sim, run):
    def probe(inst, n):
        def f(s):
            gw = s.dev(inst, "gateway")
            for i in range(2):
                s._event(gw, "api_access", "low", client_id=f"scanner-{n}", endpoint="/ocpp/v16/SetChargingProfile",
                         target=s.dev(inst, "charger").id, response="200")
        return f

    targets = [d.installation_id for d in sim.devices.values() if d.kind == "charger" and d.firmware == "3.2.1"]
    run.steps = [(3 + i * 4, probe(inst, i)) for i, inst in enumerate(targets)]
    run.ground_truth = {"label": "suspicious_probe", "devices": [f"cha-{i[-3:]}" for i in targets],
                        "expected_hypothesis": "cybersecurity_compromise"}


SCENARIOS = {
    "normal": {"name": "Normal operation", "duration": 30, "setup": _normal,
               "description": "All devices operate normally.",
               "expected": "No incidents. Background commands from known clients only."},
    "legit_change": {"name": "Legitimate EV charging change", "duration": 30, "setup": _legit_change,
                     "description": "Resident boosts charging via the app (inst-002).",
                     "expected": "Context observation only; top hypothesis: legitimate user activity. No incident."},
    "suspicious_v2h": {"name": "Suspicious V2H discharge command", "duration": 40, "setup": _suspicious_v2h,
                       "description": "Unknown client brute-forces the gateway, then commands a -9 kW V2H discharge (inst-001).",
                       "expected": "High-severity cyber-physical incident; hypothesis: unauthorised command; charger firmware exposure attached."},
    "comm_failure": {"name": "Communication failure", "duration": 35, "setup": _comm_failure,
                     "description": "EV charger stops reporting for 25 s (inst-003).",
                     "expected": "Incident with communication-fault hypothesis; no cyber indicators."},
    "telemetry_mismatch": {"name": "Telemetry inconsistency", "duration": 40, "setup": _telemetry_mismatch,
                           "description": "Charger reports 7.4 kW while the meter's EV circuit sees ~0.3 kW (inst-004).",
                           "expected": "Cross-device mismatch incident; sensor fault vs integrity issue for human review."},
    "sensor_fault": {"name": "Battery sensor fault", "duration": 30, "setup": _sensor_fault,
                     "description": "Battery reports physically implausible temperature and SOC jumps (inst-002).",
                     "expected": "Incident with sensor-fault hypothesis; no cyber indicators."},
    "firmware_fleet": {"name": "Fleet probe on vulnerable firmware", "duration": 30, "setup": _firmware_fleet,
                       "description": "Unknown clients probe the OCPP endpoint on every charger running firmware 3.2.1.",
                       "expected": "Single fleet-level incident across installations sharing firmware 3.2.1, enriched with HYPO-2026-0001."},
}
