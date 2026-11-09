"""Cyber-physical correlation: groups detections by installation and time window, links
commands to physical effects, merges fleet-wide patterns that share firmware, and ranks
competing hypotheses. Correlation is evidence of association, not proof of causation."""
from __future__ import annotations

import itertools

SEV_W = {"info": 0, "low": 1, "medium": 3, "high": 5, "critical": 8}
SEV_ORDER = ["info", "low", "medium", "high", "critical"]
FLEET_RULES = {"SENSITIVE_API_PROBE", "COMMAND_REJECTED", "METER_CHARGER_MISMATCH", "TELEMETRY_SILENCE"}
WINDOW_S = 180
CMD_RULES = {"UNEXPECTED_V2H_DISCHARGE", "GATEWAY_BYPASS"}
_gid = itertools.count(1)

HYPOTHESIS_LABELS = {
    "unauthorised_command": "Possible unauthorised command",
    "cybersecurity_compromise": "Possible cybersecurity compromise",
    "legitimate_user_activity": "Legitimate user activity",
    "communication_fault": "Communication fault",
    "sensor_fault": "Sensor / metering fault",
    "firmware_defect": "Firmware defect",
    "integration_failure": "Integration failure",
}


def _new_group(d):
    return {"id": next(_gid), "kind": "installation", "status": "open", "first_t": d["t"], "last_t": d["t"],
            "installations": set(), "devices": set(), "vendors": set(), "clients": set(), "detections": [],
            "severity": "info", "score": 0, "hypotheses": [], "links": [], "incident_id": None, "merged_into": None}


class Correlator:
    def __init__(self, charger_fw_lookup):
        self.groups: list[dict] = []
        self.charger_fw = charger_fw_lookup  # installation -> firmware string

    def open_groups(self):
        return [g for g in self.groups if g["status"] == "open" and g["merged_into"] is None]

    def add(self, detections, now_t):
        touched = set()
        for d in detections:
            g = next((g for g in reversed(self.open_groups())
                      if d["installation_id"] in g["installations"] and d["t"] - g["last_t"] <= WINDOW_S), None)
            if g is None:
                g = _new_group(d)
                self.groups.append(g)
            self._attach(g, d)
            touched.add(g["id"])
        touched |= self._fleet_merge(now_t)
        for g in self.groups:
            if g["status"] == "open" and now_t - g["last_t"] > WINDOW_S:
                g["status"] = "closed_window"
        out = [g for g in self.groups if g["id"] in touched and g["merged_into"] is None]
        for g in out:
            self.assess(g)
        return out

    def _attach(self, g, d):
        g["detections"].append(d)
        d["group_id"] = g["id"]
        g["installations"].add(d["installation_id"])
        g["devices"].add(d["device_id"])
        g["vendors"].add(d["vendor"])
        if d.get("client_id"):
            g["clients"].add(d["client_id"])
        g["first_t"], g["last_t"] = min(g["first_t"], d["t"]), max(g["last_t"], d["t"])

    def _fleet_merge(self, now_t):
        """Merge open groups in different installations that show the same fleet rule on chargers
        running the same firmware (shared firmware/component ⇒ possible fleet-level issue)."""
        touched = set()
        groups = self.open_groups()
        for a in groups:
            for b in groups:
                if a is b or a["merged_into"] or b["merged_into"] or a["id"] > b["id"]:
                    continue
                if abs(a["last_t"] - b["last_t"]) > WINDOW_S or a["installations"] & b["installations"]:
                    continue
                ra = {d["rule"] for d in a["detections"]} & FLEET_RULES
                rb = {d["rule"] for d in b["detections"]} & FLEET_RULES
                fwa = {self.charger_fw(i) for i in a["installations"]}
                fwb = {self.charger_fw(i) for i in b["installations"]}
                common = ra & rb
                if common and fwa & fwb:
                    for d in b["detections"]:
                        self._attach(a, d)
                    b["merged_into"] = a["id"]
                    a["kind"] = "fleet"
                    a["fleet_key"] = {"rules": sorted(common), "charger_firmware": sorted(fwa & fwb)}
                    touched.add(a["id"])
        return touched

    def assess(self, g):
        dets = g["detections"]
        rules = {d["rule"] for d in dets}
        cyber = [d for d in dets if d["category"] == "cyber"]
        known_ctx = "AUTHORISED_SCHEDULE_CHANGE" in rules
        cyber_clients = {d["client_id"] for d in cyber if d.get("client_id")}
        # cyber-physical links: a physical effect attributed to a command from a client that also has cyber findings
        links = []
        for p in dets:
            if p["rule"] == "POWER_STEP_CHANGE" and p.get("client_id"):
                prior = [c for c in cyber if c.get("client_id") == p["client_id"] and c["t"] <= p["t"]]
                cause = max(prior, key=lambda c: (c["t"], c["rule"] in CMD_RULES)) if prior else None
                if cause:
                    links.append({"cyber": cause["id"], "physical": p["id"], "lag_s": round(p["t"] - cause["t"], 1),
                                  "text": f"{cause['title']} ({cause['client_id']}) → {p['title']} on "
                                          f"{p['device_id']} {p['t']-cause['t']:.0f} s later"})
        g["links"] = links
        f = {
            "unknown": "UNRECOGNISED_CLIENT" in rules,
            "auth": bool(rules & {"AUTH_BRUTE_FORCE", "AUTH_SUCCESS_AFTER_FAILURES"}),
            "unexpected": bool(rules & {"UNEXPECTED_V2H_DISCHARGE", "GATEWAY_BYPASS"}),
            "effect": bool(links),
            "legit": known_ctx,
            "comm": bool(rules & {"TELEMETRY_SILENCE", "GATEWAY_REPORTED_COMM_LOSS"}),
            "implausible": bool(rules & {"IMPLAUSIBLE_TEMPERATURE", "IMPLAUSIBLE_SOC_JUMP"}),
            "mismatch": "METER_CHARGER_MISMATCH" in rules,
            "probe": "SENSITIVE_API_PROBE" in rules,
            "rejected": "COMMAND_REJECTED" in rules,
            "fleet": len(g["installations"]) >= 2,
            "cyber": bool(cyber),
        }
        raw = {
            "unauthorised_command": 2 * f["unexpected"] + 1.5 * f["unknown"] + f["auth"] + 1.5 * (f["effect"] and f["unexpected"]) - 2 * f["legit"],
            "cybersecurity_compromise": f["auth"] + f["unknown"] + 1.5 * f["probe"] + (f["fleet"] and f["probe"]) + 0.5 * f["unexpected"] + 0.5 * f["mismatch"],
            "legitimate_user_activity": 2.5 * f["legit"] - 2 * f["unknown"] - 1.5 * f["auth"] + 0.5 * (f["legit"] and not f["cyber"]),
            "communication_fault": 2 * f["comm"] - f["cyber"],
            "sensor_fault": 2 * f["implausible"] + 1.5 * f["mismatch"] - f["cyber"],
            "firmware_defect": (f["fleet"] and (f["rejected"] or f["mismatch"] or f["comm"])) + 0.5 * f["rejected"],
            "integration_failure": f["rejected"] + 0.5 * (f["comm"] and f["fleet"]),
        }
        why = {
            "unauthorised_command": [("unexpected", "command not preceded by an authorised schedule change"),
                                     ("unknown", "issuing client is not in the authorised registry"),
                                     ("effect", "command followed by a measured power-flow change")],
            "cybersecurity_compromise": [("auth", "authentication anomalies"), ("unknown", "unrecognised client"),
                                         ("probe", "charging-control API probed"), ("fleet", "pattern repeats across installations")],
            "legitimate_user_activity": [("legit", "authenticated resident changed the schedule")],
            "communication_fault": [("comm", "device stopped reporting / gateway lost heartbeat")],
            "sensor_fault": [("implausible", "physically implausible readings"),
                             ("mismatch", "device telemetry disagrees with independent meter")],
            "firmware_defect": [("fleet", "same behaviour on devices sharing firmware")],
            "integration_failure": [("rejected", "devices rejected forwarded commands")],
        }
        against = {
            "legitimate_user_activity": [("unknown", "unrecognised client involved"), ("auth", "authentication anomalies")],
            "communication_fault": [("cyber", "cyber indicators present")],
            "sensor_fault": [("cyber", "cyber indicators present")],
            "unauthorised_command": [("legit", "authorised schedule change on record")],
        }
        pos = {k: max(0.0, v) for k, v in raw.items()}
        total = sum(pos.values()) or 1.0
        hyps = []
        for k, v in sorted(pos.items(), key=lambda kv: -kv[1]):
            if v <= 0:
                continue
            hyps.append({"key": k, "label": HYPOTHESIS_LABELS[k], "support": round(v / total, 2),
                         "for": [txt for flag, txt in why.get(k, []) if f.get(flag)],
                         "against": [txt for flag, txt in against.get(k, []) if f.get(flag)]})
        g["hypotheses"] = hyps
        g["features"] = f
        sev = max((d["severity"] for d in dets), key=SEV_ORDER.index)
        if f["unexpected"] and f["effect"]:
            sev = "critical"
        g["severity"] = sev
        g["score"] = sum(SEV_W[d["severity"]] for d in dets)
        if len(hyps) >= 2 and hyps[0]["support"] - hyps[1]["support"] < 0.15:
            g["confidence"] = "low — competing explanations remain plausible"
        elif hyps and hyps[0]["support"] >= 0.5:
            g["confidence"] = "moderate — evidence favours the top hypothesis"
        else:
            g["confidence"] = "low"
        g["promote"] = SEV_ORDER.index(sev) >= SEV_ORDER.index("high") or g["score"] >= 5
        return g
