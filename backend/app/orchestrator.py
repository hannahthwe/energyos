"""Orchestrator: question -> agents -> correlated, evidence-based diagnosis.

Offline mode (default, no API key) uses deterministic rules end-to-end.
Claude mode (`llm.py`) lets Claude choose agents and write the explanation,
over the same agents, canonical data and evidence, then validates the result.
"""
from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from . import agents as A
from . import canonical as C
from . import state
from . import tools as T
from .seed import DEMO_NOW_UTC, SCENARIOS
from .visibility import allowed_levels, filter_evidence

INTENTS = {
    "config_change": ["config", "setting", "changed", "change", "security", "hack", "attack", "audit", "who changed"],
    "which_system": ["which system", "which device", "who rejected", "rejected the command", "what rejected"],
    "reserve": ["reserve", "soc", "state of charge", "battery level"],
    "missing": ["missing", "unavailable", "not available", "don't know", "unknown", "evidence is missing"],
    "next_steps": ["what should", "next", "recommend", "do now", "what can i do"],
}


def classify(question: str) -> str:
    q = question.lower()
    for intent, words in INTENTS.items():
        if any(w in q for w in words):
            return intent
    return "why_failed"


def window_for(question: str, now: datetime = DEMO_NOW_UTC) -> T.Window:
    q = question.lower()
    if "week" in q:
        return T.Window(now - timedelta(days=7), now)
    # "last night", "yesterday", "tonight" and the default: the previous evening.
    evening = (now - timedelta(days=1)).replace(hour=17, minute=0, second=0, microsecond=0)
    return T.Window(evening, evening + timedelta(hours=13))


def incident_time(s: Session, installation_id: str, w: T.Window) -> datetime:
    cmds = T.correlate_command_response(s, installation_id, w)["commands"]
    if cmds:
        return T.parse(cmds[0]["timestamp_utc"])
    sched = T.get_energy_schedule(s, installation_id, w)["schedules"]
    if sched:
        return T.parse(sched[0]["start_time_utc"])
    return w.end


def make_ctx(s: Session, question: str) -> A.Ctx:
    w = window_for(question)
    iid = state.INSTALLATION_ID
    return A.Ctx(s=s, installation_id=iid, window=w, incident_at=incident_time(s, iid, w), customer_id=state.CUSTOMER_ID)


def kb_terms(energy: dict, tech: dict, sec: dict) -> list[str]:
    terms = ["v2h", "discharge"]
    if energy.get("reserve_blocks"):
        terms += ["reserve", "soc"]
    if tech.get("command_verdict", "").startswith("rejected"):
        terms += ["rejected", "charger", "voltara"]
    if sec.get("correlated_changes"):
        terms += ["configuration", "security", "audit"]
    return terms


# ------------------------------------------------------------------ offline
def run_agents_offline(ctx: A.Ctx, intent: str, role: str) -> dict[str, A.AgentResult]:
    res: dict[str, A.AgentResult] = {}
    res[A.ENERGY.key] = A.ENERGY.run(ctx)
    res[A.TECH.key] = A.TECH.run(ctx)
    e = res[A.ENERGY.key].signals
    sec_needed = (intent == "config_change") or (e.get("reserve_blocks") and e.get("reserve_recently_changed"))
    if sec_needed:
        res[A.SECURITY.key] = A.SECURITY.run(ctx, e.get("soc_pct"))
    else:
        since = e.get("reserve_valid_from") or "unknown"
        res[A.SECURITY.key] = A.not_used(
            A.SECURITY, f"Not needed: the active reserve has been unchanged since {since[:10]} and the question did not ask about settings.")
    res[A.SUPPORT.key] = A.SUPPORT.run(
        ctx, kb_terms(e, res[A.TECH.key].signals, res[A.SECURITY.key].signals), include_internal=role != "homeowner")
    return res


def _facts(results: dict[str, A.AgentResult], keys: list[str]) -> list[dict]:
    out = []
    for k in keys:
        r = results.get(k)
        if r and r.used:
            out += [{"text": o.text, "evidence_ids": o.evidence_ids, "agent": k} for o in r.observations if o.evidence_ids]
    return out


def synthesize(ctx: A.Ctx, results: dict[str, A.AgentResult], question: str, intent: str) -> dict:
    get = lambda k: results[k].signals if k in results and results[k].used else {}  # noqa: E731
    e, t, sec, sup = get(A.ENERGY.key), get(A.TECH.key), get(A.SECURITY.key), get(A.SUPPORT.key)
    flagged = sec.get("correlated_changes") or []
    reserve_change = next((c for c in flagged if c["setting_name"] == "v2h.reserve.min_soc_pct"), None)
    articles = {a["article_id"]: a for a in sup.get("articles", [])}
    kb_ids = [a["evidence_id"] for a in articles.values()]

    if e.get("reserve_blocks") and reserve_change:
        cause = "config_change_raised_reserve"
        headline = (f"Discharge was blocked by the minimum reserve, which was raised {reserve_change['old_value']}% -> "
                    f"{reserve_change['new_value']}% by an unregistered API client "
                    f"{reserve_change['minutes_before_incident']} min earlier. Human review required.")
        interpretation = [
            {"text": f"The HEMS applied the reserve rule correctly: SOC {e['soc_pct']:.0f}% was not above the "
                     f"{e['reserve_pct']:.0f}% reserve, so no command was sent to the charger.",
             "evidence_ids": [e["soc_evidence"], e["reserve_evidence"]]},
            {"text": f"At the previous {reserve_change['old_value']}% reserve, discharge would have been allowed. The "
                     "configuration change is therefore the direct reason the discharge did not happen.",
             "evidence_ids": [reserve_change["evidence_id"]]},
            {"text": "The change came from an API client that is not registered for this home. That is unusual and "
                     "needs human review, but it is not evidence of a cyberattack on its own: it could be an "
                     "integration or tool the homeowner or installer authorised.",
             "evidence_ids": [reserve_change["evidence_id"]]},
        ]
        confidence = {"level": "medium",
                      "explanation": "High confidence in the mechanism (reserve change -> blocked discharge). "
                                     "Authorisation and intent of the change are unknown."}
        escalation = {"required": True, "target": "HelioHEMS security review (human)",
                      "reason": "Unrecognised actor changed a setting that controls discharge."}
        actions_support = ["Confirm with the homeowner (and installer) whether they made or authorised the change.",
                           "Open a human security review following KB-SEC-003; preserve audit record "
                           f"{reserve_change['evidence_id']}.",
                           f"Identify the owner of API client '{reserve_change['actor_ref']}'; consider revoking its "
                           "credentials only after review.",
                           "Do not tell the customer an attack occurred unless the review confirms it.",
                           "Restore the reserve to the homeowner's chosen value once confirmed."]
        actions_home = ["Open the Helio app and check Settings > Vehicle > Minimum reserve.",
                        "If you did not change it, request support so a specialist can review the change.",
                        "Don't share your app login with anyone while this is reviewed."]
    elif e.get("reserve_blocks"):
        cause = "soc_reserve"
        headline = (f"No fault: EV SOC {e['soc_pct']:.0f}% was not above the {e['reserve_pct']:.0f}% minimum reserve, "
                    "so the HEMS correctly held back the discharge.")
        interpretation = [{"text": "The discharge did not happen because the EV was at or below the minimum reserve. "
                                   "This is expected protective behaviour, not a fault.",
                           "evidence_ids": [e["soc_evidence"], e["reserve_evidence"]] + kb_ids[:1]}]
        if t.get("command_verdict") == "not_sent_blocked_by_hems":
            interpretation.append({"text": "The charger was online and reported no session or fault, so there is no "
                                           "contradictory charger evidence.", "evidence_ids": []})
        confidence = {"level": "high",
                      "explanation": "SOC and reserve come directly from HEMS records, the HEMS logged the inhibit, "
                                     "and no charger fault contradicts it."}
        escalation = {"required": False, "target": None, "reason": "Expected behaviour; self-service explanation is sufficient."}
        actions_support = ["Send the homeowner KB-V2H-014 and close the case as 'no fault'."]
        actions_home = [f"Charge the car above {e['reserve_pct']:.0f}% before the evening window, or",
                        "lower the minimum reserve in the Helio app (Settings > Vehicle > Minimum reserve) if you "
                        "need less driving range kept back."]
    elif t.get("command_verdict", "").startswith("rejected_by_charger"):
        cause = "charger_rejection"
        headline = (f"The charger rejected the V2H session ({t.get('charger_error_code')}). The HEMS behaved correctly. "
                    "The rejection reason is not visible to HelioHEMS.")
        interpretation = [
            {"text": f"Energy rules were satisfied (SOC {e.get('soc_pct', 0):.0f}% > reserve {e.get('reserve_pct', 0):.0f}%), "
                     "the schedule was active and the HEMS sent the request, so the HEMS side behaved correctly.",
             "evidence_ids": [x for x in [e.get("soc_evidence"), e.get("reserve_evidence"), e.get("schedule_evidence")] if x]},
            {"text": "The failure occurred at the charger, which rejected the session. Why it rejected it is recorded only "
                     "in Voltara's internal logs, which this integration cannot access. ConnectedCare does not guess the reason.",
             "evidence_ids": results[A.TECH.key].evidence_ids[-1:] if A.TECH.key in results else []},
        ]
        if "KI-0412" in articles:
            interpretation.append({"text": "Known issue KI-0412 (BiDi 11 firmware 2.8.x) describes a similar pattern, and this "
                                           "charger runs 2.8.3. It is a lead for Voltara, not a confirmed cause.",
                                   "evidence_ids": ["EVD-KB-KI-0412"]})
        confidence = {"level": "medium",
                      "explanation": "High confidence about where it failed (charger rejection); the reason is unknown "
                                     "without third-party logs."}
        escalation = {"required": True, "target": "Voltara partner support (charger manufacturer)",
                      "reason": "Root cause requires charger-internal logs that HelioHEMS cannot access."}
        actions_support = ["Escalate to Voltara via the partner portal with session reference, timestamps, firmware 2.8.3 "
                           "and vehicle model (see the escalation package).",
                           "Mention known issue KI-0412 as a possible lead, without presenting it as the cause.",
                           "Tell the homeowner the HEMS side is fine and the charger maker is investigating.",
                           "Ask the homeowner for at most one charger restart (KB-V2H-022)."]
        actions_home = ["Leave the car plugged in. Your home system will try again at the next scheduled window.",
                        "Don't change your settings; they are correct.",
                        "Request support so the charger manufacturer can check the charger's internal logs."]
    elif e and not e.get("discharge_observed") and e.get("schedule_active"):
        cause = "unknown"
        headline = "No discharge was measured and no agent found a cause in the available data."
        interpretation = [{"text": "The available records do not explain the failure.", "evidence_ids": []}]
        confidence = {"level": "low", "explanation": "No supporting evidence for any cause."}
        escalation = {"required": True, "target": "HelioHEMS tier-2 support", "reason": "Unexplained failure."}
        actions_support = ["Escalate to tier-2 with the collected evidence."]
        actions_home = ["Request support."]
    else:
        cause = "no_fault"
        headline = "V2H discharge was delivered as scheduled."
        interpretation = [{"text": "No failure found.", "evidence_ids": []}]
        confidence = {"level": "high", "explanation": "Measured discharge matches the schedule."}
        escalation = {"required": False, "target": None, "reason": "No fault."}
        actions_support, actions_home = ["None."], ["None needed."]

    fact_keys = [A.SECURITY.key, A.ENERGY.key, A.TECH.key] if cause.startswith("config") else [A.ENERGY.key, A.TECH.key, A.SECURITY.key]
    facts = _facts(results, fact_keys)
    missing = []
    for k in (A.TECH.key, A.ENERGY.key, A.SECURITY.key):
        r = results.get(k)
        if r and r.used:
            missing += r.missing_information
    if not missing:
        missing = ["No material information is missing for this diagnosis."]

    evidence_ids = list(dict.fromkeys(
        [i for f in facts for i in f["evidence_ids"]] + [i for x in interpretation for i in x["evidence_ids"]] + kb_ids))
    return {
        "likely_cause": cause, "headline": headline, "intent": intent,
        "observed_facts": facts, "interpretation": interpretation, "confidence": confidence,
        "missing_information": missing,
        "recommended_actions": {"support": actions_support, "homeowner": actions_home},
        "evidence_ids": evidence_ids, "escalation": escalation,
    }


# ------------------------------------------------------------- answers & views
def direct_answer(d: dict, results: dict[str, A.AgentResult], intent: str) -> str:
    sig = lambda k: results[k].signals if k in results and results[k].used else {}  # noqa: E731
    e, t, sec = sig(A.ENERGY.key), sig(A.TECH.key), sig(A.SECURITY.key)
    if intent == "which_system":
        if t.get("rejecting_system", "") and t["rejecting_system"].startswith("Charger"):
            return (f"The charger (Voltara BiDi 11) rejected the command: session reported '{t['charger_error_code']}' at "
                    f"{(t['charger_response_time'] or '')[11:16]} UTC, one minute after the HEMS request. The HEMS did not reject it.")
        if t.get("command_verdict") == "not_sent_blocked_by_hems":
            return ("No system rejected a command. The HEMS deliberately withheld the discharge request because of the "
                    "minimum-reserve rule, so the charger was never asked.")
        return "No rejecting system is identified in the available records."
    if intent == "reserve":
        if e.get("soc_pct") is None:
            return "No SOC data is available for this window."
        if e.get("reserve_blocks"):
            return (f"The reserve was not violated; it was enforced. SOC was {e['soc_pct']:.0f}% and the reserve was "
                    f"{e['reserve_pct']:.0f}%, so the HEMS correctly refused to discharge below it.")
        return (f"No. SOC was {e['soc_pct']:.0f}%, above the {e['reserve_pct']:.0f}% reserve, so the reserve did not block "
                "the discharge and was not violated.")
    if intent == "config_change":
        if not sec:
            return "The Security Correlation Agent was not run for this question."
        changes = sec.get("correlated_changes") or []
        if changes:
            c = changes[-1]
            return (f"Yes. {c['setting_name']} changed {c['old_value']} -> {c['new_value']} at {c['timestamp_utc'][11:16]} UTC by "
                    f"{c['actor_type']} '{c['actor_ref']}'"
                    + (" (not a registered client)" if not c["actor_recognised"] else "")
                    + f", {c['minutes_before_incident']} min before the discharge attempt. This is an observed change; "
                    "whether it was authorised is not established, and it is not a confirmed attack.")
        return ("No configuration change affecting V2H discharge occurred in the 24 hours before the incident. "
                + (results[A.SECURITY.key].observations[-1].text if results[A.SECURITY.key].observations else ""))
    if intent == "missing":
        return "Missing evidence: " + " ".join(f"({i + 1}) {m}" for i, m in enumerate(d["missing_information"]))
    if intent == "next_steps":
        return "Recommended next steps: " + " ".join(f"({i + 1}) {a}" for i, a in enumerate(d["recommended_actions"]["support"]))
    return d["headline"] + " " + " ".join(x["text"] for x in d["interpretation"])


def build_timeline(ctx: A.Ctx, role: str) -> list[dict]:
    s, iid, w = ctx.s, ctx.installation_id, ctx.window
    items: list[dict] = []

    def add(ts, label, ev_id, source, kind):
        items.append({"timestamp_utc": ts, "time": ts[11:16], "label": label, "evidence_id": ev_id,
                      "source_system": source, "kind": kind})

    for x in T.get_energy_schedule(s, iid, w)["schedules"]:
        add(x["start_time_utc"], f"HEMS V2H discharge schedule becomes active ({abs(x['requested_power_kw']):.1f} kW)",
            x["evidence_id"], "hems_backend", "schedule")
    for x in T.get_operating_constraints(s, iid, ctx.incident_at)["constraints"]:
        if T.parse(x["valid_from_utc"]) >= ctx.incident_at - timedelta(hours=24):
            add(x["valid_from_utc"], f"Minimum reserve {x['threshold_value']:.0f}% takes effect", x["evidence_id"],
                "hems_backend", "constraint")
    for x in T.get_configuration_changes(s, iid, ctx.incident_at - timedelta(hours=24))["changes"]:
        add(x["timestamp_utc"], f"Audit: {x['setting_name']} {x['old_value']} -> {x['new_value']} by {x['actor_type']} "
            f"'{x['actor_ref']}'", x["evidence_id"], "config_audit", "config_change")
    soc = T.soc_at(s, iid, ctx.incident_at)
    if soc:
        add(ctx.incident_at.strftime("%Y-%m-%dT%H:%M:%SZ"), f"EV SOC {soc['soc_pct']:.0f}%", soc["evidence_id"],
            "hems_backend", "measurement")
    for x in T.correlate_command_response(s, iid, w)["commands"]:
        label = {"blocked_by_hems": "HEMS withholds 3.0 kW discharge request (reserve rule)",
                 "rejected_by_device": "HEMS requests 3.0 kW discharge from charger"}.get(
            x["hems_status"], f"HEMS requests {abs(x['requested_kw']):.1f} kW discharge")
        add(x["timestamp_utc"], label, x["evidence_ids"][0], "hems_backend", "command")
    for x in T.get_charger_sessions(s, iid, w)["sessions"]:
        add(x["start_time_utc"], f"Charger reports V2H session {x['session_status']}"
            + (f" ({x['reported_error_code']})" if x["reported_error_code"] else ""), x["evidence_id"],
            "charger_integration", "session")
    for x in T.get_device_events(s, iid, w)["events"]:
        if x["event_type"] in ("discharge_inhibited", "discharge_not_observed", "evse_nack"):
            add(x["timestamp_utc"], f"HEMS event: {x['message']}", x["evidence_id"], "hems_backend", "event")
    p = T.compare_expected_actual_power(s, iid, w)["samples"]
    after = [x for x in p if T.parse(x["timestamp_utc"]) >= ctx.incident_at + timedelta(minutes=5)]
    if after:
        x = after[0]
        add(x["timestamp_utc"], f"Measured charger power remains {x['measured_kw']:.1f} kW (expected {x['expected_kw']:.1f} kW)",
            x["evidence_id"], "charger_integration", "measurement")

    vis = {e["evidence_id"]: e["visibility_level"] for e in T.evidence_lookup(s, [i["evidence_id"] for i in items])}
    allowed = allowed_levels(role)
    out = []
    for i in sorted(items, key=lambda r: (r["timestamp_utc"], r["kind"] != "schedule")):
        i["visibility_level"] = vis.get(i["evidence_id"], "support_internal")
        if i["visibility_level"] in allowed:
            out.append(i)
    return out


def homeowner_view(d: dict, results: dict[str, A.AgentResult], safe_evidence: list[dict]) -> dict:
    notes = []
    for k in (A.ENERGY.key, A.TECH.key, A.SECURITY.key):
        r = results.get(k)
        if r and r.used:
            notes += r.homeowner_notes
    cause = d["likely_cause"]
    what = {
        "soc_reserve": "Last night your car was plugged in and your evening schedule started at 18:55, but your home "
                       "did not use the car's battery.",
        "charger_rejection": "Last night at 19:00 your home system asked the charger to power your house from the car, "
                             "but no power came from the car.",
        "config_change_raised_reserve": "Last night your evening schedule started at 18:55, but your home did not use "
                                        "the car's battery.",
    }.get(cause, "We checked last night's records for your home.")
    unknown = {
        "soc_reserve": ["Nothing important is missing. We can't see why the car was at that level (for example, your driving)."],
        "charger_rejection": ["Why the charger declined. Only the charger manufacturer can see that information.",
                              "Whether it will happen again tonight."],
        "config_change_raised_reserve": ["Who changed the reserve setting and why. A specialist needs to check this."],
    }.get(cause, ["We could not find a clear reason in the data we can access."])
    return {
        "headline": {
            "soc_reserve": "Your car was below your minimum reserve, so it kept its charge. Nothing is broken.",
            "charger_rejection": "The charger didn't accept the request. Your home system worked correctly.",
            "config_change_raised_reserve": "Your minimum reserve was set higher than your car's charge. The setting "
                                            "was changed shortly before, so we recommend a quick check by support.",
        }.get(cause, d["headline"]),
        "what_happened": what,
        "why": notes,
        "unknown": unknown,
        "next_steps": d["recommended_actions"]["homeowner"],
        "support_recommended": d["escalation"]["required"],
        "evidence": safe_evidence,
        "checks_run": [r.name for r in results.values() if r.used],
    }


# -------------------------------------------------------------------- entry
def diagnose(s: Session, question: str, role: str = "support", use_llm: bool | None = None) -> dict:
    from . import llm  # local import to avoid a cycle

    intent = classify(question)
    ctx = make_ctx(s, question)
    mode, llm_note, results, llm_text = "offline", None, None, None
    if llm.enabled() if use_llm is None else use_llm:
        try:
            results, llm_text = llm.orchestrate(ctx, question, role)
            mode = "claude"
        except llm.LLMUnavailable as exc:
            llm_note = f"Claude unavailable, deterministic fallback used ({exc})."
            results = None
    if results is None:
        results = run_agents_offline(ctx, intent, role)
    for a in A.ALL_AGENTS:
        if a.key not in results:
            results[a.key] = A.not_used(a, "Not selected by the orchestrator for this question.")

    d = synthesize(ctx, results, question, intent)
    d["answer"] = direct_answer(d, results, intent)
    if llm_text:
        d = llm.apply_text(d, llm_text, role)

    evidence = T.evidence_lookup(s, d["evidence_ids"])
    scenario = state.active_scenario(s)
    out = {
        "run_id": f"RUN-{uuid.uuid4().hex[:8].upper()}",
        "role": role, "question": question, "mode": mode, "mode_note": llm_note,
        "scenario": {"code": scenario, "name": SCENARIOS[scenario]["name"]},
        "installation_id": ctx.installation_id, "window": ctx.window.as_dict(),
        "incident_utc": ctx.incident_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
        **d,
        "agents": [results[a.key].to_dict() for a in A.ALL_AGENTS],
        "evidence": evidence,
        "timeline": build_timeline(ctx, role),
        "power_series": [{"time": p["timestamp_utc"][11:16], "expected_kw": p["expected_kw"], "measured_kw": p["measured_kw"]}
                         for p in T.compare_expected_actual_power(s, ctx.installation_id, ctx.window)["samples"]],
        "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    if role == "homeowner":
        safe = filter_evidence(evidence, "homeowner")
        view = homeowner_view(d, results, safe)
        if llm_text and llm_text.get("homeowner"):
            view.update({k: v for k, v in llm_text["homeowner"].items() if v})
        out = {
            "run_id": out["run_id"], "role": role, "question": question, "mode": mode, "mode_note": llm_note,
            "scenario": out["scenario"], "installation_id": ctx.installation_id, "window": out["window"],
            "likely_cause": d["likely_cause"], **view,
            "timeline": out["timeline"], "created_at": out["created_at"],
        }
    s.add(C.DiagnosisRun(run_id=out["run_id"], role=role, question=question, scenario=scenario,
                         created_at=out["created_at"], result_json=json.dumps(out)))
    return out


def offline_followup_from_snapshot(snapshot: dict, question: str) -> str:
    """Answer from a stored case snapshot (used when live data has moved on)."""
    intent = classify(question)
    agents = {a["key"]: a for a in snapshot.get("agents", [])}
    if intent == "missing":
        return "Missing evidence: " + " ".join(f"({i + 1}) {m}" for i, m in enumerate(snapshot.get("missing_information", [])))
    if intent == "next_steps":
        return "Recommended next steps: " + " ".join(
            f"({i + 1}) {a}" for i, a in enumerate(snapshot.get("recommended_actions", {}).get("support", [])))
    if intent == "config_change":
        sec = agents.get("security_correlation", {})
        return sec.get("finding") or sec.get("reason_not_used") or "No security correlation result in the snapshot."
    if intent == "which_system":
        return agents.get("technical_diagnostics", {}).get("finding", "")
    if intent == "reserve":
        return agents.get("energy_behaviour", {}).get("finding", "")
    return snapshot.get("headline", "")


def strip_ids(text: str) -> str:
    return re.sub(r"\bEVD-[A-Z]+-[\w-]+\b", "", text)
