"""Transport-independent API router. Used by the FastAPI server (run.py) and by the
in-browser Pyodide build, so both demos run identical backend logic and authorisation."""
from __future__ import annotations

import json
import re
import secrets

from .platform import ORGS, PERMS, USERS, NotFound, PermissionDenied, Platform
from .scenarios import SCENARIOS
from .simulator import INSTALLATIONS, VENDORS

TOKENS: dict[str, str] = {}
ROUTES = []


def route(method, pattern):
    def deco(fn):
        ROUTES.append((method, re.compile("^" + pattern + "$"), fn))
        return fn
    return deco


class HTTPError(Exception):
    def __init__(self, status, msg):
        self.status, self.msg = status, msg


def dispatch(p: Platform, method: str, path: str, query: dict, body, token: str | None):
    actor = TOKENS.get(token or "")
    for m, rx, fn in ROUTES:
        mt = rx.match(path)
        if m == method and mt:
            try:
                if fn.__name__ not in ("login", "users") and actor is None:
                    raise HTTPError(401, "not signed in")
                return 200, fn(p, actor, query or {}, body or {}, *mt.groups())
            except HTTPError as e:
                return e.status, {"error": e.msg}
            except PermissionDenied as e:
                return 403, {"error": f"permission denied ({e})"}
            except NotFound:
                return 404, {"error": "not found"}
            except (ValueError, KeyError, TypeError) as e:
                return 400, {"error": str(e)}
    return 404, {"error": "no such route"}


def dispatch_json(p, method, path, query_json, body_json, token):
    """String-in/string-out entry point for Pyodide."""
    status, data = dispatch(p, method, path, json.loads(query_json or "{}"), json.loads(body_json or "{}"), token)
    return json.dumps({"status": status, "data": data}, default=str)


# ---------------------------------------------------------------- identity
@route("GET", "/api/users")
def users(p, a, q, b):
    return [{"id": k, **v, "org_name": ORGS[v["org"]]["name"]} for k, v in USERS.items()]


@route("POST", "/api/login")
def login(p, a, q, b):
    uid = b.get("user_id")
    if uid not in USERS:
        raise HTTPError(401, "unknown user")
    tok = secrets.token_urlsafe(24)
    TOKENS[tok] = uid
    with p.lock:
        p._audit(uid, "auth.login", "user", uid, {"method": "demo identity selector"})
    return {"token": tok}


@route("GET", "/api/me")
def me(p, a, q, b):
    u = USERS[a]
    return {"id": a, **u, "org_name": ORGS[u["org"]]["name"], "permissions": sorted(PERMS[u["role"]])}


# ---------------------------------------------------------------- overview / live
@route("GET", "/api/overview")
def overview(p, a, q, b):
    with p.lock:
        incs = [i for i in p.incidents.values() if i["status"] != "duplicate"]
        groups = {g["id"]: g for g in p.corr.groups}
        active = [i for i in incs if i["status"] not in ("resolved", "closed")]
        sev = {}
        for i in active:
            s = groups[i["group_id"]]["severity"]
            sev[s] = sev.get(s, 0) + 1
        exposure = p.sc.exposure(p.sim.devices.values())
        buckets = {}
        for d in p.detections:
            k = p.ts(d["t"] // 30 * 30)[11:19]
            buckets.setdefault(k, {"time": k, "cyber": 0, "physical": 0})
            if d["category"] in ("cyber", "physical"):
                buckets[k][d["category"]] += 1
        online = sum(1 for d in p.sim.devices.values() if d.id not in p.engine.offline)
        return {"installations": len(INSTALLATIONS), "devices": len(p.sim.devices), "devices_online": online,
                "vendors": len(VENDORS), "active_incidents": len(active), "by_severity": sev,
                "awaiting_review": sum(1 for i in incs if i["status"] == "new"),
                "awaiting_oem": sum(1 for i in incs if i["status"] == "awaiting_oem"),
                "open_tasks": sum(1 for t in p.tasks.values() if t["status"] not in ("completed", "cancelled")),
                "detections_total": len(p.detections),
                "exposed_devices": len({x["device_id"] for v in exposure for x in v["affected_devices"]}),
                "trend": list(buckets.values())[-20:], "sim_time_s": p.sim.t,
                "recent_audit": [{"at": r["at"], "who": r["actor_name"], "action": r["action"], "resource": r["resource"]}
                                 for r in p.audit[-8:]][::-1]}


@route("GET", "/api/live")
def live(p, a, q, b):
    p._require(a, "view_fleet")
    inst = q.get("installation", "inst-001")
    with p.lock:
        n = inst[-3:]
        def series(dev, key):
            return [{"ts": s["ts"][11:19], "v": s["measurements"].get(key)} for s in list(p.hist[dev])[-120:]]
        ch, mt, bat, inv = f"cha-{n}", f"met-{n}", f"bat-{n}", f"inv-{n}"
        rows = {}
        for name, dev, key in [("charger_reported_w", ch, "active_power_w"), ("meter_ev_circuit_w", mt, "ev_circuit_power_w"),
                               ("battery_w", bat, "active_power_w"), ("pv_w", inv, "active_power_w"),
                               ("grid_w", mt, "grid_power_w"), ("ev_soc", ch, "soc_estimate_pct"), ("battery_soc", bat, "soc_pct")]:
            for pt in series(dev, key):
                rows.setdefault(pt["ts"], {"ts": pt["ts"]})[name] = pt["v"]
        evs = [e for e in p.events if e["installation_id"] == inst][-40:][::-1]
        dets = [d for d in p.detections if d["installation_id"] == inst][-30:][::-1]
        devices = [{"id": d.id, "kind": d.kind, "vendor": VENDORS[d.vendor]["name"], "model": d.model,
                    "firmware": d.firmware, "online": d.id not in p.engine.offline,
                    "last": (p.hist[d.id][-1]["measurements"] if p.hist[d.id] else {}),
                    "state": (p.hist[d.id][-1]["state"] if p.hist[d.id] else "unknown")}
                   for d in p.sim.devices.values() if d.installation_id == inst]
        return {"installation": inst, "series": sorted(rows.values(), key=lambda r: r["ts"]),
                "events": [{k: e[k] for k in ("ts", "device_id", "vendor", "event_type", "severity", "client_id", "details")} for e in evs],
                "detections": [_det(d) for d in dets], "devices": devices,
                "installations": [{"id": k, **v} for k, v in INSTALLATIONS.items()]}


def _det(d):
    return {k: d.get(k) for k in ("id", "ts", "rule", "title", "category", "method", "severity", "score", "device_id",
                                  "vendor", "client_id", "explanation", "occurrences", "group_id", "provenance")}


@route("GET", "/api/correlations")
def correlations(p, a, q, b):
    p._require(a, "view_incidents_all")
    with p.lock:
        out = []
        for g in reversed(p.corr.groups[-60:]):
            if g["merged_into"]:
                continue
            out.append({"id": g["id"], "kind": g["kind"], "status": g["status"], "severity": g["severity"],
                        "score": g["score"], "installations": sorted(g["installations"]),
                        "devices": sorted(g["devices"]), "vendors": sorted(g["vendors"]),
                        "hypotheses": g["hypotheses"][:3], "links": g["links"], "confidence": g.get("confidence"),
                        "incident": p.incidents[g["incident_id"]]["code"] if g.get("incident_id") else None,
                        "promoted": bool(g.get("incident_id")), "detections": [_det(d) for d in g["detections"][-12:]],
                        "first_seen": p.ts(g["first_t"]), "last_seen": p.ts(g["last_t"])})
        return out


# ---------------------------------------------------------------- fleet & supply chain
@route("GET", "/api/fleet")
def fleet(p, a, q, b):
    u = USERS[a]
    with p.lock:
        if p.can(a, "view_fleet"):
            devs = list(p.sim.devices.values())
        elif p.can(a, "vex"):  # OEM: only its own products, aggregate installed base
            devs = [d for d in p.sim.devices.values() if d.vendor == ORGS[u["org"]].get("vendor")]
        else:
            raise PermissionDenied("fleet")
        full = p.can(a, "view_fleet")
        return {"devices": [{"id": d.id if full else "(withheld)", "installation": d.installation_id if full else "(withheld)",
                             "kind": d.kind, "vendor": VENDORS[d.vendor]["name"], "model": d.model, "firmware": d.firmware,
                             "online": d.id not in p.engine.offline} for d in devs],
                "vendors": [{"id": k, **v} for k, v in VENDORS.items()]}


@route("GET", "/api/supply-chain")
def supply_chain(p, a, q, b):
    u = USERS[a]
    with p.lock:
        own_vendor = ORGS[u["org"]].get("vendor") if p.can(a, "vex") else None
        sboms = []
        for (model, ver), s in p.sc.sboms.items():
            vendor = next((d.vendor for d in p.sim.devices.values() if d.model == model), None)
            if own_vendor and vendor != own_vendor:
                continue
            installed = sum(1 for d in p.sim.devices.values() if d.model == model and d.firmware == ver)
            sboms.append({"firmware": model, "version": ver, "file": s["filename"], "spec": s["spec_version"],
                          "supplier": s["supplier"], "installed_devices": installed,
                          "components": s["components"], "applicability": p.sc.applicability(model, ver)})
        exposure = p.sc.exposure(p.sim.devices.values())
        if not p.can(a, "view_fleet"):
            for v in exposure:
                v["affected_devices"] = [{"firmware": x["firmware"], "status": x["status"]} for x in v["affected_devices"]
                                         if own_vendor and any(s["firmware"] in x["firmware"] for s in sboms)]
        return {"sboms": sboms, "vulnerabilities": exposure, "can_decide": p.can(a, "vex")}


@route("POST", "/api/supply-chain/decision")
def vex(p, a, q, b):
    p.vex_decision(a, b["vulnerability"], b["model"], b["version"], b["status"], b.get("note", ""))
    return {"ok": True}


@route("POST", "/api/supply-chain/sbom")
def upload_sbom(p, a, q, b):
    if not (p.can(a, "vex") or p.can(a, "triage")):
        raise PermissionDenied("sbom upload")
    raw = b.get("content", "")
    parsed = p.sc.add_sbom(raw.encode() if isinstance(raw, str) else raw, b.get("filename", "upload.json")[:100])
    with p.lock:
        p._audit(a, "sbom.uploaded", "sbom", f"{parsed['firmware_name']} {parsed['firmware_version']}",
                 {"components": len(parsed["components"])})
    return {"firmware": parsed["firmware_name"], "version": parsed["firmware_version"], "components": len(parsed["components"])}


# ---------------------------------------------------------------- incidents & workflow
@route("GET", "/api/incidents")
def incidents(p, a, q, b):
    return p.list_incidents(a)


@route("GET", r"/api/incidents/(\d+)")
def incident(p, a, q, b, iid):
    return p.get_incident(a, int(iid))


@route("POST", r"/api/incidents/(\d+)/status")
def status(p, a, q, b, iid):
    return p.transition(a, int(iid), b["status"], b.get("reason", ""))


@route("POST", r"/api/incidents/(\d+)/notes")
def notes(p, a, q, b, iid):
    return p.add_note(a, int(iid), b["body"], b.get("visibility", "internal"))


@route("POST", r"/api/incidents/(\d+)/decision")
def decision(p, a, q, b, iid):
    return p.decide(a, int(iid), b["decision"], b.get("rationale", ""))


@route("POST", r"/api/incidents/(\d+)/tasks")
def new_task(p, a, q, b, iid):
    return p.create_task(a, int(iid), b["org"], b["title"], b.get("due_hours", 48), b.get("evidence_ids", []),
                         b.get("type", "investigation"))


@route("POST", r"/api/incidents/(\d+)/share")
def share(p, a, q, b, iid):
    return p.share_evidence(a, int(iid), b["org"], b.get("evidence_ids", []), b.get("purpose", ""))


@route("GET", r"/api/incidents/(\d+)/export")
def export(p, a, q, b, iid):
    inc = p.get_incident(a, int(iid))
    with p.lock:
        p._audit(a, "incident.exported", "incident", inc["code"], {"format": "json"})
    return {"export_note": "Observed facts = telemetry/events; detections = rule/model output; hypotheses = "
                           "machine-ranked explanations; decisions = human records. Simulated data.", **inc}


@route("GET", "/api/tasks")
def tasks(p, a, q, b):
    return p.list_tasks(a)


@route("POST", r"/api/tasks/(\d+)")
def update_task(p, a, q, b, tid):
    return p.update_task(a, int(tid), b.get("status"), b.get("comment", ""))


@route("GET", "/api/orgs")
def orgs(p, a, q, b):
    return [{"id": k, **v} for k, v in ORGS.items()]


# ---------------------------------------------------------------- simulation & audit
@route("GET", "/api/scenarios")
def scenarios(p, a, q, b):
    return {"scenarios": [{"key": k, "name": v["name"], "description": v["description"], "expected": v["expected"],
                           "duration": v["duration"]} for k, v in SCENARIOS.items()],
            "runs": p.run_results(), "sim_time_s": p.sim.t}


@route("POST", r"/api/scenarios/(\w+)/start")
def start(p, a, q, b, key):
    return p.start_scenario(a, key)


@route("POST", "/api/simulation/reset")
def reset(p, a, q, b):
    p.reset(a)
    return {"ok": True}


@route("GET", "/api/audit")
def audit(p, a, q, b):
    p._require(a, "audit")
    with p.lock:
        return {"chain_valid": p.verify_audit_chain(), "records": p.audit[-200:][::-1]}
