"""Platform state and human-in-the-loop workflow (in-memory prototype store)."""
from __future__ import annotations

import hashlib
import itertools
import json
import threading
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone

from .correlation import Correlator
from .detection import DetectionEngine
from .scenarios import SCENARIOS
from .simulator import INSTALLATIONS, VENDORS, Simulator
from .supply_chain import SupplyChain

ORGS = {
    "aegis": {"name": "Aegis Grid Security Operations", "type": "security_provider"},
    "homegrid": {"name": "HomeGrid HEMS", "type": "hems_operator", "vendor": "homegrid"},
    "voltcharge": {"name": "VoltCharge Systems", "type": "oem", "vendor": "voltcharge"},
    "storacell": {"name": "StoraCell Batteries", "type": "oem", "vendor": "storacell"},
    "sunpeak": {"name": "SunPeak Inverters", "type": "oem", "vendor": "sunpeak"},
}
VENDOR_ORG = {v["vendor"]: k for k, v in ORGS.items() if "vendor" in v}

USERS = {
    "u-analyst": {"name": "May", "role": "security_analyst", "org": "aegis"},
    "u-operator": {"name": "Sam", "role": "fleet_operator", "org": "homegrid"},
    "u-voltcharge": {"name": "Andy", "role": "oem_security", "org": "voltcharge"},
    "u-storacell": {"name": "Kay", "role": "oem_security", "org": "storacell"},
    "u-exec": {"name": "Jo", "role": "executive", "org": "homegrid"},
}
PERMS = {
    "security_analyst": {"view_fleet", "view_incidents_all", "triage", "decide", "assign", "share", "simulate", "audit", "comment"},
    "fleet_operator": {"view_fleet", "view_incidents_all", "comment", "task_respond"},
    "oem_security": {"view_shared", "task_respond", "vex", "comment"},
    "executive": {"overview"},
}
STATUSES = ["new", "triaged", "investigating", "awaiting_oem", "mitigation_planned", "monitoring",
            "resolved", "closed", "reopened"]
TRANSITIONS = {
    "new": {"triaged", "investigating", "awaiting_oem"}, "triaged": {"investigating", "awaiting_oem", "resolved"},
    "investigating": {"awaiting_oem", "mitigation_planned", "monitoring", "resolved"},
    "awaiting_oem": {"investigating", "mitigation_planned"}, "mitigation_planned": {"monitoring", "resolved"},
    "monitoring": {"resolved", "investigating"}, "resolved": {"closed", "reopened"},
    "closed": {"reopened"}, "reopened": {"investigating", "triaged"}, "duplicate": {"reopened"},
}
FUNCTIONS = {"charger": ["EV charging", "V2H discharge"], "battery": ["Battery management"],
             "inverter": ["Solar inverter monitoring"], "gateway": ["HEMS command integrity", "Device communications"],
             "meter": ["Metering"]}


class PermissionDenied(Exception):
    pass


class NotFound(Exception):
    pass


class Platform:
    def __init__(self, seed: int = 42):
        self.lock = threading.RLock()
        self.seed = seed
        self.audit: list[dict] = []
        self._reset_state()

    # ------------------------------------------------------------ lifecycle
    def _reset_state(self):
        self.wall0 = time.time()
        self.sim = Simulator(self.seed)
        self.engine = DetectionEngine()
        self.corr = Correlator(lambda inst: self.sim.dev(inst, "charger").firmware)
        self.sc = SupplyChain()
        self.hist: dict[str, deque] = defaultdict(lambda: deque(maxlen=300))
        self.events: deque = deque(maxlen=3000)
        self.detections: list[dict] = []
        self.incidents: dict[int, dict] = {}
        self.tasks: dict[int, dict] = {}
        self.runs: list[dict] = []
        self._inc_ids = itertools.count(1)
        self._task_ids = itertools.count(1)

    def reset(self, actor):
        with self.lock:
            self._require(actor, "simulate")
            self._reset_state()
            self._audit(actor, "simulation.reset", "platform", "-", {})

    def ts(self, t: float) -> str:
        return datetime.fromtimestamp(self.wall0 + t, timezone.utc).isoformat(timespec="seconds")

    def step(self, n: int = 1):
        with self.lock:
            for _ in range(n):
                tel, evs = self.sim.tick(1.0)
                for r in tel + evs:
                    r["ts"] = self.ts(r["t"])
                for tm in tel:
                    self.hist[tm["device_id"]].append(tm)
                self.events.extend(evs)
                dets = self.engine.process(self.sim.t, tel, evs, self.sim.devices)
                for d in dets:
                    d["ts"] = self.ts(d["t"])
                self.detections.extend(dets)
                for g in self.corr.add(dets, self.sim.t):
                    self._sync_incident(g)

    def run_forever(self, interval: float = 1.0):
        while True:
            start = time.time()
            try:
                self.step()
            except Exception as e:  # keep the demo loop alive; surface in logs
                print("tick error:", repr(e), flush=True)
            time.sleep(max(0.0, interval - (time.time() - start)))

    # ------------------------------------------------------------ audit
    def _audit(self, actor, action, rtype, rid, details):
        prev = self.audit[-1]["hash"] if self.audit else "0" * 64
        rec = {"seq": len(self.audit) + 1, "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
               "actor": actor or "system", "actor_name": USERS.get(actor, {}).get("name", "EnergyOS engine"),
               "action": action, "resource": f"{rtype}:{rid}", "details": details, "prev_hash": prev}
        rec["hash"] = hashlib.sha256((prev + json.dumps(rec, sort_keys=True, default=str)).encode()).hexdigest()
        self.audit.append(rec)

    def verify_audit_chain(self) -> bool:
        prev = "0" * 64
        for r in self.audit:
            body = {k: v for k, v in r.items() if k != "hash"}
            if r["prev_hash"] != prev or hashlib.sha256((prev + json.dumps(body, sort_keys=True, default=str)).encode()).hexdigest() != r["hash"]:
                return False
            prev = r["hash"]
        return True

    # ------------------------------------------------------------ authz
    def _require(self, actor, perm):
        u = USERS.get(actor)
        if not u or perm not in PERMS[u["role"]]:
            raise PermissionDenied(perm)
        return u

    def can(self, actor, perm):
        u = USERS.get(actor)
        return bool(u and perm in PERMS[u["role"]])

    # ------------------------------------------------------------ incidents
    def _sync_incident(self, g):
        if g.get("incident_id"):
            inc = self.incidents[g["incident_id"]]
        elif g.get("promote"):
            iid = next(self._inc_ids)
            inc = {"id": iid, "code": f"INC-{datetime.now().year}-{iid:04d}", "status": "new", "group_id": g["id"],
                   "created_at": self.ts(g["first_t"]), "notes": [], "decisions": [], "history": [],
                   "shares": defaultdict(set), "attack_confirmation": "unconfirmed", "owner": None,
                   "merged_into": None}
            g["incident_id"] = iid
            self.incidents[iid] = inc
            self._audit(None, "incident.created", "incident", inc["code"],
                        {"group": g["id"], "severity": g["severity"], "reason": "correlation threshold met"})
        else:
            return
        # absorbed groups' incidents are marked as duplicates of the fleet incident (dedupe)
        for other in self.corr.groups:
            if other.get("merged_into") == g["id"] and other.get("incident_id") and other["incident_id"] != inc["id"]:
                dup = self.incidents[other["incident_id"]]
                if dup["status"] != "duplicate":
                    dup["status"], dup["merged_into"] = "duplicate", inc["code"]
                    self._audit(None, "incident.deduplicated", "incident", dup["code"], {"merged_into": inc["code"]})

    def _group(self, inc):
        return next(g for g in self.corr.groups if g["id"] == inc["group_id"])

    def _impact(self, g):
        items = []
        for dev in sorted(g["devices"]):
            d = self.sim.devices.get(dev)
            if not d:
                continue
            samples = [s for s in self.hist[dev] if g["first_t"] - 10 <= s["t"] <= g["last_t"] + 60]
            powers = [s["measurements"].get("active_power_w") for s in samples if s["measurements"].get("active_power_w") is not None]
            entry = {"device": dev, "kind": d.kind, "vendor": VENDORS[d.vendor]["name"],
                     "functions_potentially_affected": FUNCTIONS[d.kind]}
            if powers:
                exported = sum(-p for p in powers if p < 0) / 3600
                entry["observed"] = f"power range {min(powers)/1000:.1f}…{max(powers)/1000:.1f} kW" + \
                    (f"; {exported/1000:.2f} kWh discharged/exported in window" if exported > 1 and d.kind == "charger" else "")
            socs = [s["measurements"].get("soc_estimate_pct", s["measurements"].get("soc_pct")) for s in samples]
            socs = [x for x in socs if x is not None]
            if socs and d.kind == "charger":
                entry["observed"] = entry.get("observed", "") + f"; EV SOC estimate {socs[0]:.1f}% → {socs[-1]:.1f}%"
            if d.id in self.engine.offline or any(x["rule"] == "TELEMETRY_SILENCE" and x["device_id"] == dev for x in g["detections"]):
                entry["observed"] = entry.get("observed", "") + "; telemetry gap observed"
            items.append(entry)
        return items

    def _exposure(self, g):
        out = []
        for dev in sorted(g["devices"]):
            d = self.sim.devices.get(dev)
            if d:
                for a in self.sc.applicability(d.model, d.firmware):
                    if a["status"] in ("potentially_affected", "confirmed_affected", "under_investigation"):
                        out.append({**a, "device": dev,
                                    "note": "Possible relevance only — not evidence that this vulnerability caused the anomaly."})
        # charger in the installation is always relevant context for gateway-originated activity
        for inst in g["installations"]:
            ch = self.sim.dev(inst, "charger")
            if ch.id not in g["devices"]:
                for a in self.sc.applicability(ch.model, ch.firmware):
                    if a["status"] in ("potentially_affected", "confirmed_affected"):
                        out.append({**a, "device": ch.id, "note": "Installation context — possible relevance only."})
        return out

    def _evidence(self, inc, g):
        ev = []
        for d in g["detections"]:
            ev.append({"id": f"det-{d['id']}", "type": "detection", "ts": d["ts"], "device": d["device_id"],
                       "vendor": d["vendor"], "category": d["category"], "severity": d["severity"],
                       "title": d["title"], "rule": f"{d['rule']} v{d['rule_version']}", "method": d["method"],
                       "explanation": d["explanation"], "occurrences": d["occurrences"],
                       "raw": d["evidence"][-6:], "provenance": d["provenance"], "kind": "model/rule output"})
        for i, x in enumerate(self._exposure(g)):
            ev.append({"id": f"exp-{x['vulnerability']}-{x['device']}", "type": "exposure", "device": x["device"],
                       "vendor": self.sim.devices[x["device"]].vendor, "title": f"{x['vulnerability']}: {x['title']}",
                       "explanation": f"{x['component']} — {x['status']} ({x['method']}). {x['note']}",
                       "severity": "info", "category": "supply_chain", "kind": "supply-chain context"})
        return ev

    def _recommended(self, g):
        top = g["hypotheses"][0]["key"] if g["hypotheses"] else None
        vendors = sorted({VENDORS[v]["name"] for v in g["vendors"]})
        rec = {
            "unauthorised_command": ["Contact resident to verify whether the discharge was requested",
                                     "Request VoltCharge review of charger command/OCPP logs (evidence share)",
                                     "Consider revoking the unrecognised client's gateway credentials (human-approved, executed by operator)",
                                     "Assess exposure to HYPO-2026-0001 / HYPO-2026-0002 on affected firmware"],
            "cybersecurity_compromise": ["Request OEM investigation of probed endpoint on shared firmware",
                                         "Prioritise firmware update planning for exposed devices",
                                         "Review gateway access logs across affected installations"],
            "communication_fault": ["Ask operator to check installation connectivity", "Monitor for recovery"],
            "sensor_fault": ["Request OEM diagnostics of the reporting sensor / metering", "Compare against meter before trusting device telemetry"],
            "legitimate_user_activity": ["Confirm change with resident; close as benign if confirmed"],
            "firmware_defect": ["Request OEM root-cause analysis across firmware cohort"],
            "integration_failure": ["Review HEMS ↔ device integration logs with operator"],
        }.get(top, ["Review evidence"])
        return {"actions": rec, "responsible_vendors": vendors,
                "note": "Recommendations only. EnergyOS never executes device control; humans approve and act."}

    def _visible_incidents(self, actor):
        u = USERS[actor]
        if self.can(actor, "view_incidents_all"):
            return list(self.incidents.values())
        if self.can(actor, "view_shared"):
            return [i for i in self.incidents.values()
                    if any(t["org"] == u["org"] for t in self.tasks.values() if t["incident_id"] == i["id"])
                    or i["shares"].get(u["org"])]
        raise PermissionDenied("incidents")

    def list_incidents(self, actor):
        with self.lock:
            out = []
            for inc in self._visible_incidents(actor):
                g = self._group(inc)
                full = self.can(actor, "view_incidents_all")
                out.append({"id": inc["id"], "code": inc["code"], "status": inc["status"], "severity": g["severity"],
                            "kind": g["kind"], "title": self._title(g) if full else "Shared investigation",
                            "installations": sorted(g["installations"]) if full else [],
                            "top_hypothesis": g["hypotheses"][0]["label"] if g["hypotheses"] and full else None,
                            "created_at": inc["created_at"], "last_activity": self.ts(g["last_t"]),
                            "vendors": sorted(g["vendors"]), "merged_into": inc["merged_into"],
                            "attack_confirmation": inc["attack_confirmation"]})
            return sorted(out, key=lambda x: -x["id"])

    def _title(self, g):
        top = g["hypotheses"][0]["label"] if g["hypotheses"] else "Anomaly"
        where = f"{len(g['installations'])} installations" if g["kind"] == "fleet" else next(iter(g["installations"]))
        return f"{top} — {where} ({len(g['devices'])} devices, {len(g['vendors'])} vendors)"

    def get_incident(self, actor, iid):
        with self.lock:
            inc = self.incidents.get(iid)
            if not inc or inc not in self._visible_incidents(actor):
                raise NotFound()
            g = self._group(inc)
            evidence = self._evidence(inc, g)
            tasks = [t for t in self.tasks.values() if t["incident_id"] == iid]
            if self.can(actor, "view_incidents_all"):
                summary = (f"{len(g['detections'])} findings across {len(g['devices'])} devices from "
                           f"{len(g['vendors'])} vendors in {', '.join(sorted(g['installations']))}. " +
                           (" ".join(l["text"] + "." for l in g["links"]) if g["links"] else ""))
                return {"id": iid, "code": inc["code"], "status": inc["status"], "severity": g["severity"],
                        "kind": g["kind"], "title": self._title(g), "summary": summary,
                        "fleet_key": g.get("fleet_key"), "merged_into": inc["merged_into"],
                        "installations": sorted(g["installations"]), "devices": sorted(g["devices"]),
                        "vendors": sorted(VENDORS[v]["name"] for v in g["vendors"]),
                        "clients": sorted(g["clients"]), "first_seen": self.ts(g["first_t"]),
                        "last_seen": self.ts(g["last_t"]), "hypotheses": g["hypotheses"],
                        "confidence": g.get("confidence"), "links": g["links"], "impact": self._impact(g),
                        "evidence": evidence, "recommended": self._recommended(g), "notes": inc["notes"],
                        "decisions": inc["decisions"], "history": inc["history"], "tasks": tasks,
                        "attack_confirmation": inc["attack_confirmation"], "owner": inc["owner"],
                        "shares": {o: sorted(s) for o, s in inc["shares"].items()},
                        "allowed_transitions": sorted(TRANSITIONS.get(inc["status"], [])),
                        "telemetry": self._incident_series(g),
                        "audit": [a for a in self.audit if a["resource"] == f"incident:{inc['code']}"]}
            org = USERS[actor]["org"]
            allowed = inc["shares"].get(org, set())
            return {"id": iid, "code": inc["code"], "status": inc["status"], "severity": g["severity"],
                    "title": "Shared investigation", "restricted_view": True,
                    "summary": "You can see only evidence explicitly shared with your organisation.",
                    "evidence": [e for e in evidence if e["id"] in allowed],
                    "tasks": [t for t in tasks if t["org"] == org],
                    "notes": [n for n in inc["notes"] if n.get("visibility") == "shared" or n["org"] == org]}

    def _incident_series(self, g):
        series = {}
        for dev in sorted(g["devices"]):
            d = self.sim.devices.get(dev)
            if d and d.kind in ("charger", "battery"):
                series[dev] = [{"ts": s["ts"][11:19], "power_w": s["measurements"].get("active_power_w")}
                               for s in self.hist[dev] if g["first_t"] - 20 <= s["t"] <= g["last_t"] + 60]
        for inst in g["installations"]:
            mid = f"met-{inst[-3:]}"
            series[mid + " (EV circuit)"] = [{"ts": s["ts"][11:19], "power_w": s["measurements"].get("ev_circuit_power_w")}
                                             for s in self.hist[mid] if g["first_t"] - 20 <= s["t"] <= g["last_t"] + 60]
        return series

    def _incident_for(self, actor, iid):
        inc = self.incidents.get(iid)
        if not inc or inc not in self._visible_incidents(actor):
            raise NotFound()
        return inc

    def transition(self, actor, iid, to, reason):
        with self.lock:
            self._require(actor, "triage")
            inc = self._incident_for(actor, iid)
            if to not in TRANSITIONS.get(inc["status"], set()):
                raise ValueError(f"Transition {inc['status']} → {to} not allowed")
            g = self._group(inc)
            if to == "closed" and g["severity"] in ("high", "critical") and not reason:
                raise ValueError("Closing a high/critical incident requires a recorded rationale")
            entry = {"from": inc["status"], "to": to, "by": USERS[actor]["name"], "reason": reason,
                     "at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
            inc["history"].append(entry)
            inc["status"] = to
            if inc["owner"] is None:
                inc["owner"] = USERS[actor]["name"]
            self._audit(actor, "incident.status_changed", "incident", inc["code"], entry)
            return entry

    def add_note(self, actor, iid, body, visibility="internal"):
        with self.lock:
            u = self._require(actor, "comment")
            inc = self._incident_for(actor, iid)
            if self.can(actor, "view_shared"):
                visibility = "shared"
            note = {"by": u["name"], "org": u["org"], "body": body[:4000], "visibility": visibility,
                    "at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
            inc["notes"].append(note)
            self._audit(actor, "incident.note_added", "incident", inc["code"], {"visibility": visibility})
            return note

    def decide(self, actor, iid, decision, rationale):
        """Human decisions the platform never makes autonomously."""
        with self.lock:
            u = self._require(actor, "decide")
            inc = self._incident_for(actor, iid)
            if decision not in ("confirm_attack", "reject_attack", "approve_remediation", "reportability_review_required", "not_reportable"):
                raise ValueError("unknown decision")
            if not rationale:
                raise ValueError("rationale required")
            rec = {"decision": decision, "by": u["name"], "rationale": rationale[:2000],
                   "at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
            inc["decisions"].append(rec)
            if decision == "confirm_attack":
                inc["attack_confirmation"] = "confirmed by human"
            elif decision == "reject_attack":
                inc["attack_confirmation"] = "rejected by human"
            self._audit(actor, f"incident.decision.{decision}", "incident", inc["code"], {"rationale": rationale[:200]})
            return rec

    # ------------------------------------------------------------ coordination
    def share_evidence(self, actor, iid, org, evidence_ids, purpose):
        with self.lock:
            self._require(actor, "share")
            inc = self._incident_for(actor, iid)
            if org not in ORGS or org == USERS[actor]["org"]:
                raise ValueError("unknown organisation")
            valid = {e["id"] for e in self._evidence(inc, self._group(inc))}
            ids = [e for e in evidence_ids if e in valid]
            if not ids:
                raise ValueError("no valid evidence selected")
            inc["shares"][org].update(ids)
            self._audit(actor, "evidence.shared", "incident", inc["code"],
                        {"grantee": ORGS[org]["name"], "evidence": ids, "purpose": purpose})
            return {"org": org, "evidence": sorted(inc["shares"][org])}

    def create_task(self, actor, iid, org, title, due_hours, evidence_ids, task_type="investigation"):
        with self.lock:
            u = self._require(actor, "assign")
            inc = self._incident_for(actor, iid)
            if org not in ORGS:
                raise ValueError("unknown organisation")
            tid = next(self._task_ids)
            due = datetime.now(timezone.utc) + timedelta(hours=float(due_hours))
            t = {"id": tid, "incident_id": iid, "incident_code": inc["code"], "org": org, "org_name": ORGS[org]["name"],
                 "title": title[:300], "type": task_type, "status": "open", "created_by": u["name"],
                 "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                 "due_at": due.isoformat(timespec="minutes"), "acknowledged_at": None, "comments": []}
            self.tasks[tid] = t
            self._audit(actor, "task.created", "incident", inc["code"], {"task": tid, "org": ORGS[org]["name"], "title": title})
            if evidence_ids:
                self.share_evidence(actor, iid, org, evidence_ids, f"task {tid}: {title}")
            if inc["status"] in ("new", "triaged", "investigating"):
                if "awaiting_oem" in TRANSITIONS[inc["status"]] and ORGS[org]["type"] == "oem":
                    self.transition(actor, iid, "awaiting_oem", f"Task {tid} assigned to {ORGS[org]['name']}")
            return t

    def list_tasks(self, actor):
        with self.lock:
            u = USERS.get(actor)
            if not u:
                raise PermissionDenied()
            now = datetime.now(timezone.utc)
            out = []
            for t in self.tasks.values():
                if self.can(actor, "view_incidents_all") or t["org"] == u["org"]:
                    out.append({**t, "overdue": t["status"] not in ("completed", "cancelled")
                                and datetime.fromisoformat(t["due_at"]) < now})
            return sorted(out, key=lambda t: -t["id"])

    def update_task(self, actor, tid, status, comment):
        with self.lock:
            u = self._require(actor, "task_respond") if not self.can(actor, "assign") else USERS[actor]
            t = self.tasks.get(tid)
            if not t or (not self.can(actor, "assign") and t["org"] != u["org"]):
                raise NotFound()
            if status not in ("acknowledged", "in_progress", "blocked", "completed", "cancelled", None, ""):
                raise ValueError("bad status")
            if status:
                if status == "acknowledged" and not t["acknowledged_at"]:
                    t["acknowledged_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
                t["status"] = status
            if comment:
                t["comments"].append({"by": u["name"], "org": u["org"], "body": comment[:4000],
                                      "at": datetime.now(timezone.utc).isoformat(timespec="seconds")})
            self._audit(actor, "task.updated", "incident", t["incident_code"], {"task": tid, "status": status, "comment": bool(comment)})
            return t

    # ------------------------------------------------------------ supply chain decisions (VEX-like)
    def vex_decision(self, actor, vuln, model, fw, status, note):
        with self.lock:
            u = self._require(actor, "vex")
            org_vendor = ORGS[u["org"]].get("vendor")
            dev = next((d for d in self.sim.devices.values() if d.model == model), None)
            if not dev or dev.vendor != org_vendor:
                raise PermissionDenied("not your product")
            if status not in ("confirmed_affected", "not_affected", "under_investigation"):
                raise ValueError("bad status")
            self.sc.decisions[(vuln, model, fw)] = {"status": status, "by": u["name"], "note": note[:500]}
            self._audit(actor, "vulnerability.applicability_decided", "vulnerability", vuln,
                        {"firmware": f"{model} {fw}", "status": status, "note": note[:200]})

    # ------------------------------------------------------------ simulation
    def start_scenario(self, actor, key):
        with self.lock:
            self._require(actor, "simulate")
            if key not in SCENARIOS:
                raise NotFound()
            info = self.sim.start_scenario(key)
            run = {"id": len(self.runs) + 1, "key": key, "name": SCENARIOS[key]["name"], "start_t": self.sim.t,
                   "started_at": self.ts(self.sim.t), "ground_truth": info["ground_truth"],
                   "expected": SCENARIOS[key]["expected"]}
            self.runs.append(run)
            self._audit(actor, "simulation.started", "scenario", key, {"run": run["id"]})
            return run

    def run_results(self):
        with self.lock:
            out = []
            for r in self.runs:
                end = r["start_t"] + SCENARIOS[r["key"]]["duration"]
                dets = [d for d in self.detections if d.get("scenario") == r["key"] and r["start_t"] <= d["t"] <= end + 5]
                gids = {d.get("group_id") for d in dets}
                groups = [g for g in self.corr.groups if g["id"] in gids and g["merged_into"] is None]
                incs = [self.incidents[g["incident_id"]]["code"] for g in groups if g.get("incident_id")]
                tops = [g["hypotheses"][0]["key"] for g in groups if g["hypotheses"] and g.get("incident_id")] or \
                       [g["hypotheses"][0]["key"] for g in groups if g["hypotheses"]]
                exp_h = r["ground_truth"].get("expected_hypothesis")
                first = min((d["t"] for d in dets), default=None)
                out.append({**r, "status": "running" if self.sim.t < end else "completed",
                            "detections": [{"rule": d["rule"], "severity": d["severity"], "device": d["device_id"],
                                            "at_s": round(d["t"] - r["start_t"], 1)} for d in dets],
                            "incidents": incs, "top_hypotheses": tops,
                            "hypothesis_match": (exp_h in tops[:1]) if exp_h else (not incs),
                            "first_detection_s": round(first - r["start_t"], 1) if first is not None else None})
            return list(reversed(out))
