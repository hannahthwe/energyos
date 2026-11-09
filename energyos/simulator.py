"""Multi-vendor residential energy fleet simulator.

Sign convention: active_power_w > 0 means the device draws power from the home AC bus
(charging / consuming); < 0 means it injects power into the home bus (V2H discharge,
battery discharge, PV generation). Meter grid_power_w > 0 means import from the grid.

All vendors, models, firmware and installations are fictitious.
"""
from __future__ import annotations

import math
import random
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

VENDORS = {
    "voltcharge": {"name": "VoltCharge Systems", "type": "EV charger OEM"},
    "sunpeak": {"name": "SunPeak Inverters", "type": "Solar inverter OEM"},
    "storacell": {"name": "StoraCell Batteries", "type": "Battery OEM"},
    "homegrid": {"name": "HomeGrid HEMS", "type": "HEMS provider"},
    "meterwise": {"name": "MeterWise Metering", "type": "Smart meter OEM"},
}

MODELS = {
    "charger": {"vendor": "voltcharge", "model": "VC-Bidi 11 (V2H)", "rated_w": 11000},
    "inverter": {"vendor": "sunpeak", "model": "SP-5000", "rated_w": 5000},
    "battery": {"vendor": "storacell", "model": "SC-10", "rated_w": 5000},
    "gateway": {"vendor": "homegrid", "model": "HG-Hub 2", "rated_w": 0},
    "meter": {"vendor": "meterwise", "model": "MW-1", "rated_w": 0},
}

# installation -> charger firmware (other device firmware is fixed per model)
INSTALLATIONS = {
    "inst-001": {"name": "Residence 001 (Leeds)", "charger_fw": "3.2.1"},
    "inst-002": {"name": "Residence 002 (York)", "charger_fw": "3.2.1"},
    "inst-003": {"name": "Residence 003 (Bristol)", "charger_fw": "3.2.1"},
    "inst-004": {"name": "Residence 004 (Cardiff)", "charger_fw": "3.3.0"},
}
OTHER_FW = {"inverter": "1.9.2", "battery": "4.0.3", "gateway": "5.1.0", "meter": "2.0.1"}

KNOWN_CLIENTS = {"hems-optimiser", "homegrid-cloud"} | {f"app-user-{i[-3:]}" for i in INSTALLATIONS}

EV_CAPACITY_WH = 60000.0
BAT_CAPACITY_WH = 10000.0
EFFICIENCY = 0.93


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class Device:
    id: str
    installation_id: str
    kind: str
    firmware: str
    true_power_w: float = 0.0
    setpoint_w: float = 0.0
    soc_pct: float | None = None
    temperature_c: float = 25.0
    online: bool = True
    # scenario overrides
    silent_until: float = 0.0
    report_override_w: float | None = None
    sensor_fault: bool = False

    @property
    def vendor(self) -> str:
        return MODELS[self.kind]["vendor"]

    @property
    def model(self) -> str:
        return MODELS[self.kind]["model"]

    @property
    def rated_w(self) -> float:
        return MODELS[self.kind]["rated_w"]


@dataclass
class ScenarioRun:
    key: str
    started_at: float  # simulated seconds
    steps: list = field(default_factory=list)  # (offset, fn)
    duration: float = 45.0
    ground_truth: dict = field(default_factory=dict)
    done: bool = False


class Simulator:
    """Deterministic (seeded) fleet simulator. tick() advances by dt seconds and
    returns (telemetry, events) produced during the tick."""

    def __init__(self, seed: int = 42):
        self.seed = seed
        self.rng = random.Random(seed)
        self.t = 0.0  # simulated seconds since start
        self.devices: dict[str, Device] = {}
        for inst, cfg in INSTALLATIONS.items():
            n = inst[-3:]
            for kind in ("gateway", "meter", "charger", "inverter", "battery"):
                fw = cfg["charger_fw"] if kind == "charger" else OTHER_FW[kind]
                d = Device(id=f"{kind[:3]}-{n}", installation_id=inst, kind=kind, firmware=fw)
                if kind == "charger":
                    d.soc_pct = self.rng.uniform(40, 70)
                    d.setpoint_w = self.rng.choice([3700.0, 7400.0])
                if kind == "battery":
                    d.soc_pct = self.rng.uniform(40, 70)
                self.devices[d.id] = d
        self.house_load = {i: self.rng.uniform(350, 600) for i in INSTALLATIONS}
        self.cloud = {i: self.rng.uniform(0.6, 1.0) for i in INSTALLATIONS}
        self.scenario: ScenarioRun | None = None
        self._pending_events: list[dict] = []

    # ---------- helpers ----------
    def dev(self, inst: str, kind: str) -> Device:
        return self.devices[f"{kind[:3]}-{inst[-3:]}"]

    def _event(self, d: Device, etype: str, severity: str = "info", **kw) -> dict:
        ev = {
            "id": str(uuid.UUID(int=self.rng.getrandbits(128), version=4)),
            "kind": "event",
            "t": self.t,
            "event_time": None,  # filled by caller with wall-clock
            "installation_id": d.installation_id,
            "device_id": d.id,
            "device_kind": d.kind,
            "vendor": d.vendor,
            "event_type": etype,
            "severity": severity,
            "client_id": kw.pop("client_id", None),
            "provenance": "simulated",
            "scenario": self.scenario.key if self.scenario and not self.scenario.done else None,
            "details": kw,
        }
        self._pending_events.append(ev)
        return ev

    def issue_command(self, inst: str, target_kind: str, value_w: float, client_id: str, bypass_gateway=False):
        """HEMS gateway forwards a set_power command to a device."""
        gw, target = self.dev(inst, "gateway"), self.dev(inst, target_kind)
        cmd_id = str(uuid.UUID(int=self.rng.getrandbits(128), version=4))[:8]
        if not bypass_gateway:
            self._event(gw, "command_issued", client_id=client_id, target=target.id,
                        command="set_power", value_w=value_w, command_id=cmd_id)
        if value_w < 0 and target.kind == "charger" and target.soc_pct is not None and target.soc_pct < 20:
            self._event(target, "command_rejected", "medium", client_id=client_id, reason="soc_limit")
            return
        target.setpoint_w = value_w
        self._event(target, "command_received", client_id=client_id, command="set_power",
                    value_w=value_w, command_id=cmd_id, via="gateway" if not bypass_gateway else "local_api")

    # ---------- scenarios ----------
    def start_scenario(self, key: str) -> dict:
        from .scenarios import SCENARIOS
        sc = SCENARIOS[key]
        run = ScenarioRun(key=key, started_at=self.t, duration=sc["duration"])
        self.scenario = run
        sc["setup"](self, run)
        return {"key": key, "ground_truth": run.ground_truth}

    def _run_scenario_steps(self):
        run = self.scenario
        if not run or run.done:
            return
        elapsed = self.t - run.started_at
        remaining = []
        for off, fn in run.steps:
            if elapsed >= off:
                fn(self)
            else:
                remaining.append((off, fn))
        run.steps = remaining
        if elapsed >= run.duration and not run.steps:
            run.done = True

    # ---------- physics ----------
    def tick(self, dt: float = 1.0) -> tuple[list[dict], list[dict]]:
        self.t += dt
        self._run_scenario_steps()
        rng = self.rng
        telemetry: list[dict] = []

        # periodic benign background behaviour
        for inst in INSTALLATIONS:
            gw = self.dev(inst, "gateway")
            if rng.random() < 0.02:
                self._event(gw, "auth_success", client_id=f"app-user-{inst[-3:]}", method="oauth_token")
            if rng.random() < 0.03:
                self._event(gw, "api_access", client_id="homegrid-cloud", endpoint="/api/v1/status")
            if int(self.t) % 30 == 0:  # HEMS self-consumption setpoint for the battery
                pv = -self.dev(inst, "inverter").true_power_w
                surplus = pv - self.house_load[inst] - max(self.dev(inst, "charger").true_power_w, 0)
                bat = self.dev(inst, "battery")
                sp = max(-bat.rated_w, min(bat.rated_w, surplus))
                if abs(sp - bat.setpoint_w) > 200:
                    self.issue_command(inst, "battery", round(sp, -1), "hems-optimiser")

        for d in self.devices.values():
            if d.kind == "inverter":
                c = self.cloud[d.installation_id] = min(1.0, max(0.3, self.cloud[d.installation_id] + rng.gauss(0, 0.01)))
                d.true_power_w = -(d.rated_w * 0.75 * c * (0.9 + 0.1 * math.sin(self.t / 300)))
                d.temperature_c = 35 + 0.004 * -d.true_power_w + rng.gauss(0, 0.2)
            elif d.kind in ("charger", "battery"):
                cap = EV_CAPACITY_WH if d.kind == "charger" else BAT_CAPACITY_WH
                target = d.setpoint_w
                if target > 0 and d.soc_pct >= 99.5:
                    target = 0
                if target < 0 and d.soc_pct <= 10:
                    target = 0
                if not d.online:
                    target = 0
                d.true_power_w += (target - d.true_power_w) * (1 - math.exp(-dt / 3.0))
                p = d.true_power_w
                delta_wh = (p * EFFICIENCY if p > 0 else p / EFFICIENCY) * dt / 3600
                d.soc_pct = min(100.0, max(0.0, d.soc_pct + delta_wh / cap * 100))
                d.temperature_c += ((25 + abs(p) / 1000 * 1.8) - d.temperature_c) * 0.05

        for inst in INSTALLATIONS:
            meter = self.dev(inst, "meter")
            ch = self.dev(inst, "charger")
            others = sum(self.dev(inst, k).true_power_w for k in ("charger", "inverter", "battery"))
            meter.true_power_w = self.house_load[inst] + rng.gauss(0, 25) + others

        for d in self.devices.values():
            if self.t < d.silent_until:
                continue
            m: dict = {}
            reported = d.true_power_w * (1 + rng.gauss(0, 0.005))
            if d.report_override_w is not None:
                reported = d.report_override_w + rng.gauss(0, 20)
            if d.kind == "meter":
                ch = self.dev(d.installation_id, "charger")
                m = {"grid_power_w": round(reported, 1),
                     "ev_circuit_power_w": round(ch.true_power_w * (1 + rng.gauss(0, 0.01)), 1)}
                power = None
            elif d.kind == "gateway":
                m = {"cpu_pct": round(12 + rng.gauss(0, 2), 1), "connected_devices": 4}
                power = None
            else:
                power = round(reported, 1)
                m = {"active_power_w": power, "commanded_power_w": d.setpoint_w if d.kind != "inverter" else None,
                     "voltage_v": round(230 + rng.gauss(0, 1.2), 1)}
                m["current_a"] = round(abs(power) / m["voltage_v"], 2)
            soc = d.soc_pct
            temp = d.temperature_c
            if d.sensor_fault:
                temp = rng.choice([412.0, -88.0, temp])
                soc = rng.choice([3.0, 99.0, soc]) if soc is not None else None
            if soc is not None:
                m["soc_pct" if d.kind == "battery" else "soc_estimate_pct"] = round(soc, 2)
            m["temperature_c"] = round(temp, 1)
            state = "online"
            if d.kind in ("charger", "battery"):
                p = d.true_power_w
                state = "charging" if p > 150 else "discharging" if p < -150 else "idle"
            telemetry.append({
                "kind": "telemetry", "t": self.t, "installation_id": d.installation_id, "device_id": d.id,
                "device_kind": d.kind, "vendor": d.vendor, "state": state, "measurements": m,
                "provenance": "simulated",
                "scenario": self.scenario.key if self.scenario and not self.scenario.done else None,
            })
        events, self._pending_events = self._pending_events, []
        return telemetry, events
