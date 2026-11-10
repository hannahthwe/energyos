"""Four specialised diagnostic agents.

Each agent owns a small set of deterministic tools from `tools.py`, runs them
against the canonical model, and returns a structured `AgentResult`. Agents
do not call Claude; the orchestrator (offline rules or Claude) interprets and
combines their results.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from . import tools as T


@dataclass
class Ctx:
    s: Session
    installation_id: str
    window: T.Window
    incident_at: datetime
    customer_id: str


@dataclass
class Observation:
    text: str
    evidence_ids: list[str]


@dataclass
class AgentResult:
    key: str
    name: str
    responsibility: str
    used: bool = False
    reason_not_used: str | None = None
    tools_called: list[str] = field(default_factory=list)
    investigated: list[str] = field(default_factory=list)
    status: str = "not_run"  # finding | no_issue | inconclusive | context | not_run
    finding: str = ""
    observations: list[Observation] = field(default_factory=list)
    evidence_ids: list[str] = field(default_factory=list)
    missing_information: list[str] = field(default_factory=list)
    signals: dict = field(default_factory=dict)
    homeowner_notes: list[str] = field(default_factory=list)

    def obs(self, text: str, ids: list[str]) -> None:
        self.observations.append(Observation(text, ids))
        for i in ids:
            if i not in self.evidence_ids:
                self.evidence_ids.append(i)

    def call(self, name: str, fn, *args, **kw):
        self.tools_called.append(name)
        return fn(*args, **kw)

    def to_dict(self) -> dict:
        return asdict(self)


def hhmm(ts: str) -> str:
    return ts[11:16]


# =================================================================== Agent 1
class TechnicalDiagnosticsAgent:
    key, name = "technical_diagnostics", "Technical Diagnostics Agent"
    responsibility = "Charger status, connectivity, command execution, session failures, technical events"

    def run(self, c: Ctx) -> AgentResult:
        r = AgentResult(self.key, self.name, self.responsibility, used=True)
        status = r.call("get_device_status", T.get_device_status, c.s, c.installation_id)
        sessions = r.call("get_charger_sessions", T.get_charger_sessions, c.s, c.installation_id, c.window)
        events = r.call("get_device_events", T.get_device_events, c.s, c.installation_id, c.window)
        corr = r.call("correlate_command_response", T.correlate_command_response, c.s, c.installation_id, c.window)
        r.investigated = ["Device connectivity (CRM + HEMS + charger integration)",
                          f"{len(sessions['sessions'])} charger session(s) in window",
                          f"{len(events['events'])} HEMS technical event(s)",
                          f"{len(corr['commands'])} HEMS command(s) matched against charger responses"]

        charger = next((d for d in status["devices"] if d["device_type"] == "ev_charger"), None)
        online = bool(charger and charger["connectivity_status"] == "online")
        if charger:
            r.obs(f"Charger {charger['device_id']} ({charger['manufacturer']} {charger['model']}, fw {charger['firmware_version']}) "
                  f"is {charger['connectivity_status']} via the authorised integration.", [])
        cmd = corr["commands"][0] if corr["commands"] else None
        r.signals = {"charger_online": online, "faults": events["faults"],
                     "command_verdict": cmd["verdict"] if cmd else "no_command",
                     "rejecting_system": cmd["rejecting_system"] if cmd else None,
                     "charger_error_code": cmd["charger_error_code"] if cmd else None,
                     "charger_error_detail_available": cmd["charger_error_detail_available"] if cmd else None,
                     "command_time": cmd["timestamp_utc"] if cmd else None,
                     "charger_response_time": cmd["charger_response_utc"] if cmd else None}

        for e in events["events"]:
            r.obs(f"{hhmm(e['timestamp_utc'])} HEMS event {e['event_type'].upper()} ({e['severity']}): {e['message']}",
                  [e["evidence_id"]])

        if not cmd:
            r.status, r.finding = "inconclusive", "No discharge command was recorded in the time window."
            r.missing_information.append("No HEMS command record in the selected window.")
        elif cmd["verdict"] == "not_sent_blocked_by_hems":
            r.status = "no_issue"
            r.obs(f"{hhmm(cmd['timestamp_utc'])} HEMS discharge request {cmd['requested_kw']:.1f} kW was withheld by the HEMS "
                  "and never sent to the charger.", cmd["evidence_ids"])
            r.finding = ("No command reached the charger: the HEMS withheld it. The charger was online and reported no "
                         "session and no fault, so there is no contradictory charger evidence.")
            r.homeowner_notes.append("Your charger was online and working normally; it was never asked to start.")
        elif cmd["verdict"].startswith("rejected_by_charger"):
            r.status = "finding"
            r.obs(f"{hhmm(cmd['timestamp_utc'])} HEMS sent a {cmd['requested_kw']:.1f} kW discharge request to the charger.",
                  cmd["evidence_ids"][:1])
            if cmd["charger_session_id"]:
                lag = int((T.parse(cmd["charger_response_utc"]) - T.parse(cmd["timestamp_utc"])).total_seconds() // 60)
                r.obs(f"{hhmm(cmd['charger_response_utc'])} Charger reported V2H session {cmd['charger_session_id']} "
                      f"rejected with code {cmd['charger_error_code']} ({lag} min after the request).", cmd["evidence_ids"][1:])
            r.finding = (f"The charger rejected the V2H session with code {cmd['charger_error_code']}. "
                         "The HEMS request itself was valid. The integration does not expose why the charger rejected it.")
            if not cmd["charger_error_detail_available"]:
                r.missing_information.append(
                    "Internal Voltara charger logs (actual rejection reason): not accessible through the authorised integration.")
                r.missing_information.append("Vehicle-to-charger communication logs (EV side).")
            r.homeowner_notes.append("Your home system asked the charger to start powering the house, but the charger "
                                     "did not accept the request.")
        else:
            r.status = "no_issue"
            r.finding = f"Command verdict: {cmd['verdict'].replace('_', ' ')}."
        if events["faults"]:
            r.obs(f"Device faults reported: {', '.join(events['faults'])}", [])
        return r


# =================================================================== Agent 2
class EnergyBehaviourAgent:
    key, name = "energy_behaviour", "Energy Behaviour Agent"
    responsibility = "SOC constraints, energy schedules, power measurements, charge/discharge behaviour"

    def run(self, c: Ctx) -> AgentResult:
        r = AgentResult(self.key, self.name, self.responsibility, used=True)
        sched = r.call("get_energy_schedule", T.get_energy_schedule, c.s, c.installation_id, c.window)
        soc = r.call("get_soc_history", T.get_soc_history, c.s, c.installation_id, c.window)
        cons = r.call("get_operating_constraints", T.get_operating_constraints, c.s, c.installation_id, c.incident_at)
        power = r.call("compare_expected_actual_power", T.compare_expected_actual_power, c.s, c.installation_id, c.window)
        r.investigated = [f"{len(sched['schedules'])} V2H schedule(s)", f"{len(soc['samples'])} EV SOC sample(s)",
                          f"{len(cons['constraints'])} operating constraint(s)",
                          f"{len(power['samples'])} charger power sample(s) vs schedule"]

        s0 = next((x for x in sched["schedules"] if x["action"] == "v2h_discharge"), None)
        if s0:
            r.obs(f"V2H discharge schedule active {hhmm(s0['start_time_utc'])}-{hhmm(s0['end_time_utc'])} UTC, requesting "
                  f"{s0['requested_power_kw']:.1f} kW (negative = supplying the home), enabled={s0['enabled']}.", [s0["evidence_id"]])
        at = T.soc_at(c.s, c.installation_id, c.incident_at)
        reserve = next((x for x in cons["constraints"] if x["constraint_type"] == "min_reserve_soc"), None)
        if at:
            r.obs(f"EV SOC at {hhmm(c.incident_at.isoformat())} was {at['soc_pct']:.0f}% (sample {hhmm(at['timestamp_utc'])}).",
                  [at["evidence_id"]])
        if reserve:
            r.obs(f"Minimum reserve SOC is {reserve['threshold_value']:.0f}%, valid from {reserve['valid_from_utc']}.",
                  [reserve["evidence_id"]])
        r.obs(f"Measured charger power stayed at {power['max_discharge_kw']:.1f} kW in "
              f"{power['samples_in_discharge_window']} sample(s) inside the discharge window "
              f"(expected {s0['requested_power_kw']:.1f} kW)." if s0 else "No discharge schedule found.",
              power["evidence_ids"][-3:])

        blocks = bool(at and reserve and at["soc_pct"] <= reserve["threshold_value"])
        recent = bool(reserve and c.incident_at - T.parse(reserve["valid_from_utc"]) <= timedelta(hours=24))
        r.signals = {"schedule_active": bool(s0 and s0["enabled"]), "soc_pct": at["soc_pct"] if at else None,
                     "reserve_pct": reserve["threshold_value"] if reserve else None,
                     "reserve_valid_from": reserve["valid_from_utc"] if reserve else None,
                     "reserve_blocks": blocks, "reserve_recently_changed": recent,
                     "discharge_observed": power["discharge_observed"],
                     "requested_kw": s0["requested_power_kw"] if s0 else None,
                     "schedule_evidence": s0["evidence_id"] if s0 else None,
                     "soc_evidence": at["evidence_id"] if at else None,
                     "reserve_evidence": reserve["evidence_id"] if reserve else None,
                     "power_evidence": power["evidence_ids"][-3:]}

        if blocks:
            r.status = "finding"
            r.finding = (f"EV SOC {at['soc_pct']:.0f}% was not above the {reserve['threshold_value']:.0f}% minimum reserve "
                         "when the discharge window started, so the HEMS correctly withheld discharge. 0 kW is the expected result.")
            if recent:
                r.finding += f" The reserve value only took effect at {hhmm(reserve['valid_from_utc'])} UTC, shortly before the window."
            r.homeowner_notes.append(f"Your car was at {at['soc_pct']:.0f}% and your minimum reserve is "
                                     f"{reserve['threshold_value']:.0f}%. The system keeps that reserve for driving, so it did not "
                                     "use the car to power the house.")
        elif s0 and not power["discharge_observed"]:
            r.status = "no_issue"
            r.finding = (f"Energy constraints were satisfied (SOC {at['soc_pct']:.0f}% > reserve {reserve['threshold_value']:.0f}%) "
                         "and the schedule was active, yet no discharge was measured. The energy rules do not explain the failure.")
            r.missing_information.append("Vehicle battery-management limits are not reported to the HEMS.")
            r.homeowner_notes.append(f"Your car had enough charge ({at['soc_pct']:.0f}%) and your schedule was on.")
        else:
            r.status = "no_issue"
            r.finding = "Discharge was delivered as scheduled."
        return r


# =================================================================== Agent 3
class SecurityCorrelationAgent:
    key, name = "security_correlation", "Security Correlation Agent"
    responsibility = "Configuration changes, unusual setting changes, audit-event correlation"

    def run(self, c: Ctx, soc_pct: float | None) -> AgentResult:
        r = AgentResult(self.key, self.name, self.responsibility, used=True)
        r.call("get_configuration_changes", T.get_configuration_changes, c.s, c.installation_id,
               c.incident_at - timedelta(days=30))
        corr = r.call("correlate_changes_with_incident", T.correlate_changes_with_incident, c.s, c.installation_id,
                      c.incident_at, 24, soc_pct)
        r.investigated = [f"{len(corr['changes'])} configuration change(s) in the last 30 days (audit log)",
                          "Correlation window: 24 h before the incident",
                          "Actor identity against registered clients"]
        flagged = corr["correlated_changes"]
        r.signals = {"correlated_changes": flagged, "needs_human_review": corr["needs_human_review"],
                     "security_conclusion": corr["security_conclusion"]}
        for ch in corr["changes"]:
            who = f"{ch['actor_type']} client '{ch['actor_ref']}'" + ("" if ch["actor_recognised"] else " (NOT a registered client)")
            r.obs(f"{ch['timestamp_utc'][:16].replace('T', ' ')} {ch['setting_name']}: {ch['old_value']} -> {ch['new_value']} by {who}.",
                  [ch["evidence_id"]])

        if flagged:
            ch = flagged[-1]
            r.status = "finding"
            r.finding = (f"Observed: {ch['setting_name']} changed {ch['old_value']} -> {ch['new_value']} by "
                         f"{ch['actor_type']} '{ch['actor_ref']}' {ch['minutes_before_incident']} min before the discharge attempt. ")
            if ch["effect_on_incident"]:
                r.finding += f"Effect: {ch['effect_on_incident']}. "
            if ch["needs_human_review"]:
                r.finding += ("The actor is not a registered client, so authorisation cannot be confirmed. This is an "
                              "unexplained change requiring human review, not a confirmed cyberattack.")
                r.missing_information += ["Who owns API client credential '" + ch["actor_ref"] + "' and whether the homeowner or installer authorised it.",
                                          "Intent behind the change (cannot be inferred from the audit log).",
                                          "API gateway / authentication logs for this client."]
                r.homeowner_notes.append("A settings change shortly before the discharge window could not be matched to "
                                         "you or your installer. A support specialist should review it.")
            else:
                r.finding += "The actor is a registered client."
        else:
            r.status = "no_issue"
            r.finding = "No configuration change affecting V2H discharge in the 24 h before the incident."
        return r


# =================================================================== Agent 4
class SupportResolutionAgent:
    key, name = "support_resolution", "Support Resolution Agent"
    responsibility = "CRM history, technical manuals, troubleshooting, case summaries, escalation preparation"

    def run(self, c: Ctx, terms: list[str], include_internal: bool) -> AgentResult:
        r = AgentResult(self.key, self.name, self.responsibility, used=True)
        prof = r.call("get_customer_profile", T.get_customer_profile, c.s, c.installation_id)
        prev = r.call("get_previous_cases", T.get_previous_cases, c.s, prof["customer"]["customer_id"])
        kb = r.call("search_knowledge_base", T.search_knowledge_base, c.s, terms, include_internal)
        r.investigated = [f"CRM profile {prof['customer']['crm_ref']} / site {prof['installation']['crm_ref']}",
                          f"{len(prev['cases'])} previous support interaction(s)",
                          f"Knowledge base search: {', '.join(terms)}"]
        r.status = "context"
        for a in kb["articles"]:
            r.obs(f"{a['article_id']} ({a['doc_type']}): {a['title']}", [a["evidence_id"]])
        related = [p for p in prev["cases"] if any(t in p["subject"].lower() for t in ("reserve", "v2h", "charger"))]
        for p in related:
            r.obs(f"Previous case {p['crm_ref']} ({p['opened']}, {p['status']}): {p['subject']}. {p['resolution']}", [])
        r.signals = {"articles": kb["articles"], "previous_cases": prev["cases"], "profile": prof}
        r.finding = (f"Retrieved {len(kb['articles'])} approved article(s)"
                     + (f" ({', '.join(a['article_id'] for a in kb['articles'])})" if kb["articles"] else "")
                     + f" and {len(prev['cases'])} previous CRM interaction(s) for {prof['customer']['name']}.")
        return r


TECH, ENERGY, SECURITY, SUPPORT = (TechnicalDiagnosticsAgent(), EnergyBehaviourAgent(),
                                   SecurityCorrelationAgent(), SupportResolutionAgent())
ALL_AGENTS = [TECH, ENERGY, SECURITY, SUPPORT]


def not_used(agent, reason: str) -> AgentResult:
    return AgentResult(agent.key, agent.name, agent.responsibility, used=False, reason_not_used=reason)
