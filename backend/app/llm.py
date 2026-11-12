"""Claude-powered orchestration (optional; enabled when ANTHROPIC_API_KEY is set).

Claude sees the five specialist agents as tools, decides which to run, and
submits a structured diagnosis through `submit_diagnosis`. Everything Claude
returns is validated against the evidence the agents actually produced:
uncited or unknown evidence IDs are removed, and homeowner answers that leak
restricted detail are rejected. Any failure falls back to offline mode.
"""
from __future__ import annotations

import json
import os

from . import agents as A
from . import tools as T
from .visibility import allowed_levels

MODEL = os.environ.get("CONNECTEDCARE_MODEL", "claude-opus-5-5")
EFFORT = os.environ.get("CONNECTEDCARE_EFFORT", "low")  # fast enough for a live demo
MAX_TURNS = 8


# Substrings that must never reach the homeowner: evidence IDs, internal record refs, codes and actors.
HOMEOWNER_FORBIDDEN = {"EVD-", "api_client", "cli-", "audit", "V2H_SESSION_REJECTED", "CC-DEV", "VS-",
                       "CC-TAR", "CC-GSE", "CC-PRC", "GSR-", "DA-SYN", "NW-55120", "SP-1900"}


class LLMUnavailable(Exception):
    pass


def enabled() -> bool:
    if os.environ.get("CONNECTEDCARE_OFFLINE") == "1":
        return False
    return bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"))


SYSTEM = """You are the ConnectedCare AI orchestrator for HelioHEMS, a home energy management system manufacturer.
You diagnose connected home-energy problems (HEMS, home battery, bidirectional EV charger, vehicle-to-home) and
explain energy costs for one installation.

You have five specialist agents as tools. Each runs deterministic checks over HelioHEMS's unified data model
(CRM, HEMS backend, the authorised charger integration, the energy supplier, public market prices, knowledge base
and configuration audit log) and returns structured findings with evidence IDs. Run the agents that are relevant
to the question; for V2H failures usually Energy Behaviour and Technical Diagnostics first, then Support Resolution
with search terms that fit the findings. Run Security Correlation when a setting may have changed or the question
asks about settings or security. Run Grid & Tariff when a device was paused or limited, or the question is about
grid steering, prices, tariffs, charging cost or savings.

Rules:
- Use only what the agents return. Never invent data, causes, timestamps or tool results.
- Separate observed facts (what records show) from interpretation (what they suggest).
- If something cannot be established from the data, say so under missing information. HelioHEMS cannot see the
  third-party charger's internal logs; never guess a charger's internal rejection reason.
- A configuration change is an observation, not proof of an attack. Do not claim a cyberattack.
- Euro amounts, cheapest hours and savings come only from the Grid & Tariff agent's calculation; never compute
  or round your own figures differently. A grid-steering pause is not a fault; say whether compensation is on record.
- If an agent reports a source as unavailable (no active consent), say that data is unavailable. Never infer it.
- Cite evidence IDs exactly as the agents returned them.
- Finish by calling submit_diagnosis exactly once."""

HOMEOWNER_RULES = """
Audience: the homeowner, in the Helio mobile app. Use short, friendly, plain language with no jargon,
error codes, device IDs or evidence IDs in the text. Do not mention internal logs, audit records or API clients.
Recommend only safe actions a homeowner can take (no electrical work, no resetting more than once)."""

SUPPORT_RULES = """
Audience: a HelioHEMS support engineer. Be precise: include UTC times, device IDs, source systems and evidence IDs."""


def _agent_tools() -> list[dict]:
    empty = {"type": "object", "properties": {}, "additionalProperties": False, "required": []}
    return [
        {"name": "run_technical_diagnostics_agent", "strict": True, "input_schema": empty,
         "description": "Technical Diagnostics Agent: charger status and connectivity, HEMS commands vs charger responses, "
                        "charging-session failures and technical events. Tools: get_device_status, get_charger_sessions, "
                        "get_device_events, correlate_command_response."},
        {"name": "run_energy_behaviour_agent", "strict": True, "input_schema": empty,
         "description": "Energy Behaviour Agent: V2H schedules, EV SOC history, minimum-reserve constraints, expected vs "
                        "measured power. Tools: get_energy_schedule, get_soc_history, get_operating_constraints, "
                        "compare_expected_actual_power."},
        {"name": "run_security_correlation_agent", "strict": True, "input_schema": empty,
         "description": "Security Correlation Agent: configuration changes from the audit log and their timing relative to "
                        "the incident. Distinguishes observed changes from security conclusions. Tools: "
                        "get_configuration_changes, correlate_changes_with_incident."},
        {"name": "run_grid_tariff_agent", "strict": True, "input_schema": empty,
         "description": "Grid & Tariff Agent: energy supplier contract (fixed or dynamic), grid-steering events from the "
                        "grid operator and the device's measured response, hourly day-ahead prices, and EV charging cost "
                        "vs the cheapest hours that still meet the departure time. Tools: get_tariff_contract, "
                        "get_steering_events, get_price_series, compare_actual_vs_cheapest."},
        {"name": "run_support_resolution_agent", "strict": True,
         "description": "Support Resolution Agent: CRM customer profile, previous cases, and approved knowledge-base "
                        "articles. Tools: get_customer_profile, get_previous_cases, search_knowledge_base.",
         "input_schema": {"type": "object", "additionalProperties": False, "required": ["search_terms"],
                          "properties": {"search_terms": {"type": "array", "items": {"type": "string"},
                                                          "description": "Lower-case keywords, e.g. v2h, reserve, rejected"}}}},
    ]


_cited = {"type": "array", "items": {"type": "object", "additionalProperties": False, "required": ["text", "evidence_ids"],
                                     "properties": {"text": {"type": "string"},
                                                    "evidence_ids": {"type": "array", "items": {"type": "string"}}}}}
_strs = {"type": "array", "items": {"type": "string"}}


def _submit_tool(role: str) -> dict:
    if role == "homeowner":
        props = {"headline": {"type": "string"}, "what_happened": {"type": "string"}, "why": _strs,
                 "unknown": _strs, "next_steps": _strs}
    else:
        props = {"answer": {"type": "string", "description": "Direct answer to the question"},
                 "observed_facts": _cited, "interpretation": _cited, "missing_information": _strs,
                 "recommended_actions": _strs}
    return {"name": "submit_diagnosis", "strict": True,
            "description": "Submit the final evidence-based diagnosis. Call exactly once, after running the agents.",
            "input_schema": {"type": "object", "additionalProperties": False, "required": list(props), "properties": props}}


def _view_for(result: A.AgentResult, role: str, s) -> dict:
    """What Claude is allowed to see from an agent, for this role."""
    d = result.to_dict()
    if role != "homeowner":
        d.pop("homeowner_notes", None)
        return d
    levels = allowed_levels(role)
    vis = {e["evidence_id"]: e["visibility_level"] for e in T.evidence_lookup(s, result.evidence_ids)}
    safe_obs = [o for o in d["observations"] if o["evidence_ids"] and all(vis.get(i) in levels for i in o["evidence_ids"])]
    return {"agent": d["name"], "status": d["status"], "plain_language_findings": d["homeowner_notes"],
            "homeowner_safe_observations": safe_obs,
            "evidence_ids": [i for i in d["evidence_ids"] if vis.get(i) in levels],
            "missing_information_count": len(d["missing_information"]),
            "unavailable_data": [u["homeowner_message"] for u in d["unavailable_sources"]]}


def orchestrate(ctx: A.Ctx, question: str, role: str) -> tuple[dict, dict]:
    import anthropic

    from .orchestrator import kb_terms

    client = anthropic.Anthropic()
    results: dict[str, A.AgentResult] = {}
    seen_ids: set[str] = set()
    system = SYSTEM + (HOMEOWNER_RULES if role == "homeowner" else SUPPORT_RULES)
    tools = _agent_tools() + [_submit_tool(role)]
    messages = [{"role": "user", "content":
                 f"Installation {ctx.installation_id}. Time window {ctx.window.as_dict()} (installation timezone "
                 f"Europe/London = UTC in November). Discharge attempt at {T.fmt(ctx.incident_at)}.\n\nQuestion: {question}"}]

    def run(name: str, args: dict) -> dict:
        if name == "run_technical_diagnostics_agent":
            r = A.TECH.run(ctx)
        elif name == "run_energy_behaviour_agent":
            r = A.ENERGY.run(ctx)
        elif name == "run_security_correlation_agent":
            soc = (results[A.ENERGY.key].signals.get("soc_pct") if A.ENERGY.key in results
                   else (T.soc_at(ctx.s, ctx.installation_id, ctx.incident_at) or {}).get("soc_pct"))
            r = A.SECURITY.run(ctx, soc)
        elif name == "run_grid_tariff_agent":
            r = A.GRID.run(ctx)
        elif name == "run_support_resolution_agent":
            terms = [t.lower() for t in args.get("search_terms") or []] or kb_terms(
                results.get(A.ENERGY.key, A.not_used(A.ENERGY, "")).signals,
                results.get(A.TECH.key, A.not_used(A.TECH, "")).signals,
                results.get(A.SECURITY.key, A.not_used(A.SECURITY, "")).signals,
                results.get(A.GRID.key, A.not_used(A.GRID, "")).signals)
            r = A.SUPPORT.run(ctx, terms, include_internal=role != "homeowner")
        else:
            raise KeyError(name)
        results[r.key] = r
        view = _view_for(r, role, ctx.s)
        seen_ids.update(view.get("evidence_ids", []))
        return view

    submitted = None
    try:
        for _ in range(MAX_TURNS):
            resp = client.beta.messages.create(
                model=MODEL, max_tokens=16000, system=system, tools=tools, messages=messages,
                thinking={"type": "adaptive"}, output_config={"effort": EFFORT},
                betas=["server-side-fallback-2026-07-01"], fallbacks="default",
            )
            if resp.stop_reason == "refusal":
                raise LLMUnavailable("request declined")
            messages.append({"role": "assistant", "content": resp.content})
            calls = [b for b in resp.content if b.type == "tool_use"]
            if not calls:
                break
            outputs = []
            for b in calls:
                args = b.input if isinstance(b.input, dict) else json.loads(b.input or "{}")
                if b.name == "submit_diagnosis":
                    submitted = args
                    outputs.append({"type": "tool_result", "tool_use_id": b.id, "content": "received"})
                    continue
                try:
                    outputs.append({"type": "tool_result", "tool_use_id": b.id, "content": json.dumps(run(b.name, args))})
                except Exception as exc:  # noqa: BLE001 - report tool errors back to Claude
                    outputs.append({"type": "tool_result", "tool_use_id": b.id, "content": str(exc), "is_error": True})
            messages.append({"role": "user", "content": outputs})
            if submitted:
                break
    except anthropic.APIConnectionError as exc:
        raise LLMUnavailable(f"connection error: {exc}") from exc
    except anthropic.RateLimitError as exc:
        raise LLMUnavailable("rate limited") from exc
    except anthropic.APIStatusError as exc:
        raise LLMUnavailable(f"API error {exc.status_code}") from exc

    if not results:
        raise LLMUnavailable("Claude did not run any agent")
    if not submitted:
        raise LLMUnavailable("Claude did not submit a diagnosis")
    return results, validate(submitted, role, seen_ids, ctx)


def validate(sub: dict, role: str, seen_ids: set[str], ctx: A.Ctx) -> dict:
    if role == "homeowner":
        forbidden = HOMEOWNER_FORBIDDEN
        blob = json.dumps(sub)
        hits = [f for f in forbidden if f.lower() in blob.lower()]
        if hits:
            raise LLMUnavailable(f"homeowner answer leaked restricted detail ({', '.join(hits)})")
        return {"homeowner": sub}

    def clean(items):
        out = []
        for it in items or []:
            ids = [i for i in it.get("evidence_ids", []) if i in seen_ids]
            if ids:
                out.append({"text": it["text"], "evidence_ids": ids, "agent": None})
        return out

    facts, interp = clean(sub.get("observed_facts")), clean(sub.get("interpretation"))
    if not facts:
        raise LLMUnavailable("no observed fact was backed by agent evidence")
    return {"answer": sub.get("answer", ""), "observed_facts": facts, "interpretation": interp,
            "missing_information": sub.get("missing_information") or [], "recommended_actions": sub.get("recommended_actions") or []}


def apply_text(d: dict, text: dict, role: str) -> dict:
    if role == "homeowner":
        if text["homeowner"].get("headline"):
            d["answer"] = text["homeowner"]["headline"]
        return d
    d = dict(d)
    d["answer"] = text["answer"] or d["answer"]
    d["observed_facts"] = text["observed_facts"]
    if text["interpretation"]:
        d["interpretation"] = text["interpretation"]
    if text["missing_information"]:
        d["missing_information"] = text["missing_information"]
    if text["recommended_actions"]:
        d["recommended_actions"] = {**d["recommended_actions"], "support": text["recommended_actions"]}
    d["evidence_ids"] = list(dict.fromkeys(d["evidence_ids"] + [i for f in d["observed_facts"] for i in f["evidence_ids"]]))
    return d
