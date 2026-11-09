"""Explainable detection: rule-based cyber rules, physical rules and rolling statistics.
Every detection names its rule and the evidence that triggered it. Scores are not attack
probabilities. The scenario label on records is never used for detection."""
from __future__ import annotations

import itertools
import statistics
from collections import defaultdict, deque

from .simulator import KNOWN_CLIENTS

RULE_VERSION = "1.0"
_ids = itertools.count(1)


class DetectionEngine:
    def __init__(self):
        self.auth_fail: dict[str, deque] = defaultdict(deque)  # installation -> (t, client, event_id)
        self.schedule_ctx: dict[str, float] = {}  # installation -> last legit schedule change t
        self.last_cmd: dict[str, tuple] = {}  # device -> (t, value, client, event_id)
        self.last_seen: dict[str, float] = {}
        self.offline: set[str] = set()
        self.power_hist: dict[str, deque] = defaultdict(lambda: deque(maxlen=60))
        self.reported_charger: dict[str, float] = {}
        self.mismatch_streak: dict[str, int] = defaultdict(int)
        self.prev_soc: dict[str, float] = {}
        self.cooldown: dict[tuple, dict] = {}
        self.last_label: dict[str, str | None] = {}

    def _emit(self, out, t, rec, rule, title, category, severity, explanation, evidence, method="rule",
              score=None, client=None, cooldown=60.0):
        key = (rule, rec["device_id"], client)
        prev = self.cooldown.get(key)
        if prev and t - prev["last_t"] < cooldown:
            prev["occurrences"] += 1
            prev["last_t"] = t
            prev["evidence"] = (prev["evidence"] + evidence)[-20:]
            return
        d = {"id": next(_ids), "rule": rule, "rule_version": RULE_VERSION, "title": title, "category": category,
             "method": method, "severity": severity, "score": score, "t": t, "last_t": t,
             "installation_id": rec["installation_id"], "device_id": rec["device_id"],
             "device_kind": rec["device_kind"], "vendor": rec["vendor"], "client_id": client,
             "explanation": explanation, "evidence": evidence, "occurrences": 1,
             "provenance": rec.get("provenance"), "scenario": rec.get("scenario")}
        self.cooldown[key] = d
        out.append(d)

    # ---------------- cyber events ----------------
    def process_event(self, ev, out):
        t, inst, et, client = ev["t"], ev["installation_id"], ev["event_type"], ev.get("client_id")
        det = ev["details"]
        ref = [{"type": "event", "id": ev["id"], "summary": f"{et} client={client}"}]
        unknown = client is not None and client not in KNOWN_CLIENTS and not client.startswith("app-user-")

        if et == "auth_failure":
            q = self.auth_fail[inst]
            q.append((t, client, ev["id"]))
            while q and t - q[0][0] > 60:
                q.popleft()
            if len(q) >= 5:
                self._emit(out, t, ev, "AUTH_BRUTE_FORCE", "Repeated authentication failures", "cyber", "high",
                           f"{len(q)} failed logins at the gateway within 60 s.",
                           [{"type": "event", "id": e[2], "summary": f"auth_failure client={e[1]}"} for e in q], client=client)
        if et == "auth_success":
            fails = [e for e in self.auth_fail[inst] if e[1] == client and t - e[0] <= 120]
            if len(fails) >= 3:
                self._emit(out, t, ev, "AUTH_SUCCESS_AFTER_FAILURES", "Login succeeded after repeated failures",
                           "cyber", "high", f"Client {client} authenticated after {len(fails)} failures in 120 s "
                           "(possible credential guessing).", ref, client=client)
        if unknown and et in ("auth_success", "command_issued", "command_received", "api_access"):
            self._emit(out, t, ev, "UNRECOGNISED_CLIENT", "Unrecognised client identity", "cyber", "high",
                       f"Client '{client}' is not in the installation's authorised client registry.", ref,
                       client=client, cooldown=300)
        if et == "schedule_changed" and not unknown:
            self.schedule_ctx[inst] = t
            self._emit(out, t, ev, "AUTHORISED_SCHEDULE_CHANGE", "Authorised schedule change (context)",
                       "context", "info", f"Authenticated known client {client} changed the charging schedule: "
                       f"{det.get('change')}.", ref, client=client)
        if et == "command_received":
            self.last_cmd[ev["device_id"]] = (t, det.get("value_w"), client, ev["id"])
            v = det.get("value_w") or 0
            if ev["device_kind"] == "charger" and v < 0 and t - self.schedule_ctx.get(inst, -1e9) > 300:
                self._emit(out, t, ev, "UNEXPECTED_V2H_DISCHARGE", "Unexpected V2H discharge command", "cyber",
                           "high", f"Charger received a {v/1000:.1f} kW discharge setpoint with no authorised "
                           "schedule change in the previous 5 minutes.", ref, client=client)
            if det.get("via") == "local_api":
                self._emit(out, t, ev, "GATEWAY_BYPASS", "Command bypassed the HEMS gateway", "cyber", "high",
                           "Device accepted a command that the gateway never issued.", ref, client=client)
        if et == "command_rejected":
            self._emit(out, t, ev, "COMMAND_REJECTED", "Device rejected a command", "cyber", "medium",
                       f"Device rejected command from {client}: {det.get('reason')}.", ref, client=client)
        if et == "api_access" and unknown and "/ocpp/" in str(det.get("endpoint", "")):
            self._emit(out, t, ev, "SENSITIVE_API_PROBE", "Unrecognised client accessed charging-control API",
                       "cyber", "medium", f"{client} called {det.get('endpoint')} targeting {det.get('target')}.",
                       ref, client=client)
        if et == "communication_lost":
            self._emit(out, t, ev, "GATEWAY_REPORTED_COMM_LOSS", "Gateway reports device unreachable", "physical",
                       "medium", f"Gateway lost heartbeat with {det.get('target')}.", ref)

    # ---------------- physical telemetry ----------------
    def process_telemetry(self, tm, out):
        t, dev, m, kind = tm["t"], tm["device_id"], tm["measurements"], tm["device_kind"]
        self.last_seen[dev] = t
        self.last_label[dev] = tm.get("scenario")  # evaluation metadata only, never a detection input
        if dev in self.offline:
            self.offline.discard(dev)
        ref = [{"type": "telemetry", "device": dev, "t": t, "summary": ", ".join(f"{k}={v}" for k, v in m.items())}]

        temp = m.get("temperature_c")
        if temp is not None and not (-40 <= temp <= 120):
            self._emit(out, t, tm, "IMPLAUSIBLE_TEMPERATURE", "Physically implausible temperature", "physical",
                       "medium", f"Reported {temp} °C is outside the plausible range -40…120 °C.", ref)
        soc = m.get("soc_pct", m.get("soc_estimate_pct"))
        if soc is not None:
            prev = self.prev_soc.get(dev)
            if prev is not None and abs(soc - prev) > 10:
                self._emit(out, t, tm, "IMPLAUSIBLE_SOC_JUMP", "Implausible state-of-charge jump", "physical",
                           "medium", f"SOC moved {prev:.1f}% → {soc:.1f}% in one sample; at rated power this "
                           "requires minutes, not seconds.", ref)
            self.prev_soc[dev] = soc

        p = m.get("active_power_w")
        if p is not None and kind in ("charger", "battery"):
            h = self.power_hist[dev]
            if len(h) >= 10:
                ago = h[-6]
                if abs(p - ago) > 3000:
                    c = self.last_cmd.get(dev)
                    linked = c and t - c[0] <= 20
                    known = linked and c[2] in KNOWN_CLIENTS | {f"app-user-{tm['installation_id'][-3:]}"}
                    self._emit(out, t, tm, "POWER_STEP_CHANGE", "Large power-flow change", "physical",
                               "low" if known else "medium",
                               f"Active power changed {ago/1000:.1f} → {p/1000:.1f} kW in 5 s" +
                               (f"; follows command {c[1]} W from {c[2]} ({t-c[0]:.0f} s earlier)." if linked
                                else "; no recent command explains it."), ref, client=c[2] if linked else None)
            if len(h) >= 30:
                mu, sd = statistics.fmean(h), statistics.pstdev(h)
                z = abs(p - mu) / max(sd, 200.0)
                if z > 6 and abs(p - mu) > 2000:
                    self._emit(out, t, tm, "BASELINE_DEVIATION", "Deviation from device baseline", "physical", "low",
                               f"Power {p:.0f} W vs rolling mean {mu:.0f} W (z={z:.1f}, 60-sample window).", ref,
                               method="statistical", score=round(z, 2))
            h.append(p)
            if kind == "charger":
                self.reported_charger[tm["installation_id"]] = p

        if kind == "meter":
            inst = tm["installation_id"]
            rep = self.reported_charger.get(inst)
            ev_c = m.get("ev_circuit_power_w")
            if rep is not None and ev_c is not None:
                gap = abs(rep - ev_c)
                self.mismatch_streak[inst] = self.mismatch_streak[inst] + 1 if gap > max(1000, 0.15 * abs(ev_c)) else 0
                if self.mismatch_streak[inst] >= 3:
                    rec = {**tm, "device_id": f"cha-{inst[-3:]}", "device_kind": "charger", "vendor": "voltcharge"}
                    self._emit(out, t, rec, "METER_CHARGER_MISMATCH", "Charger telemetry disagrees with meter",
                               "physical", "high", f"Charger reports {rep:.0f} W; independent meter EV circuit "
                               f"measures {ev_c:.0f} W (gap {gap:.0f} W for 3+ samples).", ref)

    def check_liveness(self, t, devices, out):
        for d in devices.values():
            last = self.last_seen.get(d.id)
            if last is not None and t - last > 5 and d.id not in self.offline:
                self.offline.add(d.id)
                rec = {"installation_id": d.installation_id, "device_id": d.id, "device_kind": d.kind,
                       "vendor": d.vendor, "provenance": "simulated", "scenario": self.last_label.get(d.id)}
                self._emit(out, t, rec, "TELEMETRY_SILENCE", "Device stopped reporting", "physical", "medium",
                           f"No telemetry from {d.id} for {t-last:.0f} s (expected every 1 s).",
                           [{"type": "telemetry", "device": d.id, "t": last, "summary": "last sample received"}])

    def process(self, t, telemetry, events, devices):
        out: list[dict] = []
        for ev in events:
            self.process_event(ev, out)
        # meters last so charger and meter samples from the same tick are compared
        for tm in sorted(telemetry, key=lambda r: r["device_kind"] == "meter"):
            self.process_telemetry(tm, out)
        self.check_liveness(t, devices, out)
        return out
