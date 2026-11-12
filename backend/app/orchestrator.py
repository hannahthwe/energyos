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
    "savings": ["cost", "price", "cheap", "expensive", "saving", "save ", "could i save", "tariff", "paid", "bill", "€", "euro"],
    "grid_steering": ["grid", "steering", "paused", "pause", "home battery", "battery stop", "congestion", "operator",
                      "supplier"],
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
    steer = T.get_steering_events(s, installation_id, w)["events"]
    if steer:
        return T.parse(steer[0]["start_time_utc"])
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


def kb_terms(energy: dict, tech: dict, sec: dict, grid: dict | None = None, intent: str = "") -> list[str]:
    grid = grid or {}
    if grid.get("steering_events") or intent == "grid_steering":
        return ["grid", "steering", "battery", "paused"]
    if grid.get("peak_charging") or intent == "savings":
        return ["tariff", "dynamic", "price", "smart charging"]
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
    if intent in ("grid_steering", "savings"):
        res[A.GRID.key] = A.GRID.run(ctx)
        topic = "grid steering" if intent == "grid_steering" else "energy prices and charging cost"
        for a in (A.ENERGY, A.TECH, A.SECURITY):
            res[a.key] = A.not_used(a, f"Not needed: the question is about {topic}.")
        res[A.SUPPORT.key] = A.SUPPORT.run(ctx, kb_terms({}, {}, {}, res[A.GRID.key].signals, intent),
                                           include_internal=role != "homeowner")
        return res
    steering = T.get_steering_events(ctx.s, ctx.installation_id, ctx.window)["events"]
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
    if steering:
        res[A.GRID.key] = A.GRID.run(ctx)
    else:
        res[A.GRID.key] = A.not_used(A.GRID, "Not needed: no grid-steering event in the window and the question did not "
                                             "ask about prices or tariffs.")
    res[A.SUPPORT.key] = A.SUPPORT.run(
        ctx, kb_terms(e, res[A.TECH.key].signals, res[A.SECURITY.key].signals, res[A.GRID.key].signals),
        include_internal=role != "homeowner")
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
    e, t, sec, sup, g = get(A.ENERGY.key), get(A.TECH.key), get(A.SECURITY.key), get(A.SUPPORT.key), get(A.GRID.key)
    gaps = unavailable_of(results)
    gap_labels = ", ".join(x["plain_label"] for x in gaps)
    homeowner_headline = None
    flagged = sec.get("correlated_changes") or []
    reserve_change = next((c for c in flagged if c["setting_name"] == "v2h.reserve.min_soc_pct"), None)
    articles = {a["article_id"]: a for a in sup.get("articles", [])}
    kb_ids = [a["evidence_id"] for a in articles.values()]

    if g.get("steering_events") and intent != "savings":
        ev = g["steering_events"][0]
        con = g.get("contract") or {}
        dev = ev["device_type"].replace("_", " ")
        cause = "grid_steering"
        headline = (f"No fault: {ev['grid_operator']} asked the supplier to limit the {dev} to {ev['requested_max_kw']:.1f} kW "
                    f"from {ev['start_time_utc'][11:16]} to {ev['end_time_utc'][11:16]} UTC ({ev['reason']}). "
                    "The HEMS followed the grid-steering request.")
        interpretation = [{"text": f"The {dev} pause matches the supplier's grid-steering window, the HEMS measured the "
                                   "limit being followed and normal operation resumed when the window ended. This is grid "
                                   "steering, not a device fault.",
                           "evidence_ids": g["steering_evidence"][:1] + g["device_response_evidence"]}]
        if con.get("grid_steering_agreement"):
            interpretation.append({"text": f"The contract has a grid-steering agreement, so the household is compensated "
                                           f"({con['steering_compensation']}).", "evidence_ids": [con["evidence_id"]]})
        confidence = {"level": "high", "explanation": "Supplier steering record, contract and HEMS measurements agree."}
        escalation = {"required": False, "target": None, "reason": "Expected behaviour under the grid-steering agreement."}
        actions_support = ["Send the homeowner KB-GRID-005 and close the case as 'no fault: grid steering'.",
                           "No device action needed; the HEMS resumed automatically."]
        actions_home = [f"Nothing to do: your {dev} went back to normal at {ev['end_time_utc'][11:16]}.",
                        "Grid steering can happen again on busy evenings; your Hub handles it automatically.",
                        "Your compensation comes through your energy contract (lower network charges)."]
    elif intent == "savings" or g.get("peak_charging"):
        sv, sessions = g.get("savings") or {}, g.get("sessions") or []
        last = sessions[-1] if sessions else None
        if g.get("peak_charging") and last:
            cause = "peak_charging"
            hours = ", ".join(h["start_time_utc"][11:16] for h in last["cheapest_hours"])
            headline = (f"EV charging cost EUR {sv['total_actual_eur']:.2f}; charging in the cheapest hours before departure "
                        f"would have cost EUR {sv['total_cheapest_eur']:.2f} (EUR {sv['total_saving_eur']:.2f} less).")
            homeowner_headline = (f"You paid EUR {last['actual_cost_eur']:.2f} to charge. The cheapest hours would have cost "
                                  f"EUR {last['cheapest_cost_eur']:.2f}, and your car would still have been ready on time.")
            peak_ids = [h["evidence_id"] for h in last["actual_hours"] if h["evidence_id"]]
            cheap_ids = [h["evidence_id"] for h in last["cheapest_hours"] if h["evidence_id"]]
            interpretation = [
                {"text": f"The car charged {last['energy_kwh']:.1f} kWh from {last['start_time_utc'][11:16]} to "
                         f"{(last['end_time_utc'] or '')[11:16]} UTC, in the evening price peak, because smart charging was "
                         f"{'off' if last['smart_charging_on'] is False else 'not limiting the start time'}.",
                 "evidence_ids": last["evidence_ids"][:2] + peak_ids[:3]},
                {"text": f"The cheapest hours that still meet the {last['departure_utc'][11:16]} UTC departure were {hours} UTC.",
                 "evidence_ids": cheap_ids[:4]},
            ]
            confidence = {"level": "high", "explanation": "Deterministic calculation from charger meter values, the supplier "
                                                          "contract and published day-ahead prices."}
            escalation = {"required": False, "target": None, "reason": "Behaviour and settings question, no fault."}
            actions_support = ["Explain dynamic-tariff timing to the homeowner (KB-TAR-011).",
                               f"Suggest turning on smart charging with the {last['departure_utc'][11:16]} departure time."]
            actions_home = ["Turn on smart charging in the Helio app (Charging > Smart charging) and keep your departure time.",
                            f"Plug in as usual; the Hub will then charge in the cheapest hours (last night: {hours})."]
        elif gaps and (not sessions or sv.get("total_saving_eur") is None):
            cause = "data_unavailable"
        elif not sessions:
            cause = "no_savings"
            headline = "No EV charging session was recorded in this period, so there is nothing to compare."
            interpretation = [{"text": "No charging session in the window.", "evidence_ids": []}]
        elif sv.get("contract_type") == "fixed":
            cause = "no_savings"
            headline = (f"Your contract has a fixed price, so charging at another time would not change the cost "
                        f"(EUR {sv['total_actual_eur']:.2f} for {sv['total_energy_kwh']:.1f} kWh).")
            interpretation = [{"text": "Fixed-price contract: the price per kWh is the same in every hour.",
                               "evidence_ids": [g["contract"]["evidence_id"]] if g.get("contract") else []}]
        elif sv.get("total_saving_eur") is None:
            cause = "unknown"
            headline = "The charging cost could not be fully calculated from the available data."
            interpretation = [{"text": "Price, departure time or contract information is missing.", "evidence_ids": []}]
        else:
            cause = "no_savings"
            headline = (f"Charging already used cheap hours: EUR {sv['total_actual_eur']:.2f}, within "
                        f"EUR {sv['total_saving_eur']:.2f} of the cheapest plan.")
            interpretation = [{"text": "Charging happened in low-price hours.", "evidence_ids": []}]
        if cause in ("no_savings", "unknown"):
            confidence = {"level": "high" if cause == "no_savings" else "low", "explanation": "Based on supplier, charger and market records."}
            escalation = {"required": False, "target": None, "reason": "No fault."}
            actions_support = ["No action needed."]
            actions_home = ["Nothing to change."]
    elif intent == "grid_steering" and not gaps:
        cause = "no_grid_steering"
        headline = "No grid-steering event was recorded for this home in the period checked."
        interpretation = [{"text": "The supplier reported no grid-steering request for this installation.", "evidence_ids": []}]
        confidence = {"level": "high", "explanation": "Supplier records are available and contain no steering event."}
        escalation = {"required": False, "target": None, "reason": "No grid steering."}
        actions_support = ["Check the other agents' findings for the cause."]
        actions_home = ["Ask about the specific device that stopped, and we'll check it."]
    elif intent == "grid_steering":
        cause = "data_unavailable"
    elif e.get("reserve_blocks") and reserve_change:
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
        code = t.get("charger_error_code")
        headline = (f"The charger rejected the V2H session{f' ({code})' if code else ''}. The HEMS behaved correctly. "
                    "The rejection reason is not visible to HelioHEMS."
                    + (f" {gap_labels} records are unavailable (no consent)." if gaps else ""))
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
    elif gaps:
        cause = "data_unavailable"
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
        headline = ("V2H discharge was delivered as scheduled." if e.get("schedule_active")
                    else "No failure found in the records checked.")
        interpretation = [{"text": "No failure found.", "evidence_ids": []}]
        confidence = {"level": "high", "explanation": "Measured discharge matches the schedule."}
        escalation = {"required": False, "target": None, "reason": "No fault."}
        actions_support, actions_home = ["None."], ["None needed."]

    if cause == "data_unavailable":
        headline = (f"Cannot fully answer: {gap_labels} data is unavailable because there is no active consent to use it."
                    if gaps else "Cannot fully answer: the data needed is unavailable.")
        homeowner_headline = (f"We can't fully answer this: sharing of your {gap_labels.lower()} data is turned off."
                              if gaps else None)
        interpretation = [{"text": "ConnectedCare does not use a source without active consent, so the cause cannot be "
                                   "established from the remaining records.", "evidence_ids": []}]
        confidence = {"level": "low", "explanation": "Required data is unavailable."}
        escalation = {"required": False, "target": None, "reason": "Homeowner consent is needed before this can be diagnosed."}
        actions_support = [f"Ask the homeowner whether they want to turn sharing for {gap_labels} back on in Data & privacy.",
                           "Do not obtain the data through another channel without consent."]
        actions_home = [f"For a full answer, turn {gap_labels} back on in Data & privacy.", "You can turn it off again at any time."]

    if cause.startswith("config"):
        fact_keys = [A.SECURITY.key, A.ENERGY.key, A.TECH.key, A.GRID.key]
    elif cause in ("grid_steering", "peak_charging", "no_savings", "no_grid_steering"):
        fact_keys = [A.GRID.key, A.ENERGY.key, A.TECH.key]
    else:
        fact_keys = [A.ENERGY.key, A.TECH.key, A.SECURITY.key, A.GRID.key]
    facts = _facts(results, fact_keys)
    missing = []
    for k in (A.GRID.key, A.TECH.key, A.ENERGY.key, A.SECURITY.key):
        r = results.get(k)
        if r and r.used:
            missing += [m for m in r.missing_information if m not in missing]
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
        "unavailable_sources": gaps, "homeowner_headline": homeowner_headline,
    }


def unavailable_of(results: dict[str, A.AgentResult]) -> list[dict]:
    out: dict[str, dict] = {}
    for r in results.values():
        if r.used:
            for u in r.unavailable_sources:
                out.setdefault(u["source_system"], u)
    return list(out.values())


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
        label = {"v2h_discharge": f"HEMS V2H discharge schedule becomes active ({abs(x['requested_power_kw']):.1f} kW)",
                 "battery_discharge": f"HEMS home battery discharge schedule active ({abs(x['requested_power_kw']):.1f} kW)",
                 "smart_charge": f"EV plugged in; departure {x['end_time_utc'][11:16]} UTC, smart charging "
                                 f"{'on' if x['enabled'] else 'off'}"}.get(x["action"], x["action"])
        add(x["start_time_utc"], label, x["evidence_id"], "hems_backend", "schedule")
    for x in T.get_steering_events(s, iid, w)["events"]:
        add(x["start_time_utc"], f"Grid steering starts: {x['grid_operator']} limits {x['device_type'].replace('_', ' ')} "
            f"to {x['requested_max_kw']:.1f} kW ({x['reason']})", x["evidence_id"], "energy_supplier", "steering")
        add(x["end_time_utc"], "Grid steering ends", x["evidence_id"], "energy_supplier", "steering")
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
        if x["session_type"] == "charge":
            add(x["start_time_utc"], f"EV charging session starts ({x['session_status']})", x["evidence_id"],
                "charger_integration", "charging")
            continue
        add(x["start_time_utc"], f"Charger reports V2H session {x['session_status']}"
            + (f" ({x['reported_error_code']})" if x["reported_error_code"] else ""), x["evidence_id"],
            "charger_integration", "session")
    for x in T.get_device_events(s, iid, w)["events"]:
        if x["event_type"] in ("discharge_inhibited", "discharge_not_observed", "evse_nack", "grid_steering_applied",
                               "grid_steering_ended"):
            add(x["timestamp_utc"], f"HEMS event: {x['message']}", x["evidence_id"], "hems_backend", "event")
    p = T.compare_expected_actual_power(s, iid, w)["samples"]
    p = p if any(x["expected_kw"] for x in p) else []  # only meaningful when a discharge was scheduled
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
    for k in (A.GRID.key, A.ENERGY.key, A.TECH.key, A.SECURITY.key):
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
        "grid_steering": "Last night your home battery stopped powering your home for a while, then started again.",
        "peak_charging": "Your car charged as soon as it was plugged in, during the most expensive hours of the evening.",
        "no_savings": "We compared what your charging cost with the cheapest possible hours.",
        "no_grid_steering": "We checked your energy supplier's records for grid-steering requests.",
        "data_unavailable": "We tried to check your records, but some of the data needed isn't shared with us.",
    }.get(cause, "We checked last night's records for your home.")
    unknown = {
        "soc_reserve": ["Nothing important is missing. We can't see why the car was at that level (for example, your driving)."],
        "charger_rejection": ["Why the charger declined. Only the charger manufacturer can see that information.",
                              "Whether it will happen again tonight."],
        "config_change_raised_reserve": ["Who changed the reserve setting and why. A specialist needs to check this."],
        "grid_steering": ["When the grid operator will need to steer again. That depends on how busy the local grid is."],
        "peak_charging": ["Prices further ahead than tomorrow. They are published one day in advance."],
        "no_savings": [],
        "no_grid_steering": [],
        "data_unavailable": [],
    }.get(cause, ["We could not find a clear reason in the data we can access."])
    unknown = unknown + [u["homeowner_message"] for u in d.get("unavailable_sources", [])]
    if cause in ("no_savings", "no_grid_steering", "data_unavailable"):
        notes = notes or [d["headline"]]
    return {
        "headline": {
            "soc_reserve": "Your car was below your minimum reserve, so it kept its charge. Nothing is broken.",
            "charger_rejection": "The charger didn't accept the request. Your home system worked correctly.",
            "config_change_raised_reserve": "Your minimum reserve was set higher than your car's charge. The setting "
                                            "was changed shortly before, so we recommend a quick check by support.",
            "grid_steering": "Your battery was paused by a grid-steering request, not a fault. You're compensated for it.",
        }.get(cause, d.get("homeowner_headline") or d["headline"]),
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
            gap_msgs = [u["homeowner_message"] for u in d.get("unavailable_sources", [])]
            view["unknown"] = list(view.get("unknown") or []) + [m for m in gap_msgs if m not in (view.get("unknown") or [])]
        out = {
            "run_id": out["run_id"], "role": role, "question": question, "mode": mode, "mode_note": llm_note,
            "scenario": out["scenario"], "installation_id": ctx.installation_id, "window": out["window"],
            "likely_cause": d["likely_cause"], **view,
            "unavailable_sources": [u["plain_label"] for u in d.get("unavailable_sources", [])],
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
    if intent in ("grid_steering", "savings"):
        return agents.get("grid_tariff", {}).get("finding") or snapshot.get("headline", "")
    if intent == "reserve":
        return agents.get("energy_behaviour", {}).get("finding", "")
    return snapshot.get("headline", "")


def strip_ids(text: str) -> str:
    return re.sub(r"\bEVD-[A-Z]+-[\w-]+\b", "", text)
