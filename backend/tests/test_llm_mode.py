"""Claude-mode orchestration with a scripted fake client (no network)."""
import os
from types import SimpleNamespace as NS

import anthropic
import pytest

from app import db, llm, orchestrator, state

Q = "Why didn't my EV discharge last night?"


def block(name, inp, i):
    return NS(type="tool_use", name=name, input=inp, id=f"tu_{i}")


class FakeClient:
    def __init__(self, script):
        self.script = list(script)
        self.beta = NS(messages=NS(create=self._create))
        self.calls = []

    def _create(self, **kw):
        self.calls.append(kw)
        content = self.script.pop(0)
        return NS(stop_reason="tool_use" if any(b.type == "tool_use" for b in content) else "end_turn", content=content)


@pytest.fixture()
def s(tmp_path, monkeypatch):
    monkeypatch.delenv("CONNECTEDCARE_OFFLINE", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    db.configure(f"sqlite:///{tmp_path / 'llm.db'}")
    state.init_db(reset=True)
    with db.session_scope() as sess:
        state.set_scenario(sess, "B")
        yield sess


def install(monkeypatch, script):
    fake = FakeClient(script)
    monkeypatch.setattr(anthropic, "Anthropic", lambda: fake)
    return fake


def test_claude_mode_runs_selected_agents_and_validates_evidence(s, monkeypatch):
    fake = install(monkeypatch, [
        [block("run_energy_behaviour_agent", {}, 1), block("run_technical_diagnostics_agent", {}, 2)],
        [block("run_support_resolution_agent", {"search_terms": ["rejected", "charger"]}, 3)],
        [block("submit_diagnosis", {
            "answer": "The charger rejected the session.",
            "observed_facts": [
                {"text": "Charger rejected session at 19:01.", "evidence_ids": ["EVD-CHG-VS-77120"]},
                {"text": "Invented fact", "evidence_ids": ["EVD-FAKE-1"]},
            ],
            "interpretation": [{"text": "Reason unknown.", "evidence_ids": ["EVD-CHG-VS-77120"]}],
            "missing_information": ["Voltara internal logs"],
            "recommended_actions": ["Escalate to Voltara"]}, 4)],
    ])
    d = orchestrator.diagnose(s, Q, "support")
    assert d["mode"] == "claude"
    used = {a["key"] for a in d["agents"] if a["used"]}
    assert used == {"energy_behaviour", "technical_diagnostics", "support_resolution"}
    assert [f["text"] for f in d["observed_facts"]] == ["Charger rejected session at 19:01."]  # invented one dropped
    assert d["likely_cause"] == "charger_rejection" and d["escalation"]["required"]
    req = fake.calls[0]
    assert req["model"] == "claude-opus-5-5" and req["thinking"] == {"type": "adaptive"}
    assert req["fallbacks"] == "default"


def test_claude_mode_falls_back_when_no_evidence(s, monkeypatch):
    install(monkeypatch, [
        [block("submit_diagnosis", {"answer": "x", "observed_facts": [{"text": "x", "evidence_ids": ["EVD-NOPE"]}],
                                    "interpretation": [], "missing_information": [], "recommended_actions": []}, 1)],
    ])
    d = orchestrator.diagnose(s, Q, "support")
    assert d["mode"] == "offline" and "fallback" in d["mode_note"]
    assert d["likely_cause"] == "charger_rejection"


def test_homeowner_claude_answer_with_leak_is_rejected(s, monkeypatch):
    install(monkeypatch, [
        [block("run_technical_diagnostics_agent", {}, 1)],
        [block("submit_diagnosis", {"headline": "Charger said V2H_SESSION_REJECTED", "what_happened": "x",
                                    "why": [], "unknown": [], "next_steps": []}, 2)],
    ])
    h = orchestrator.diagnose(s, Q, "homeowner")
    assert h["mode"] == "offline" and "V2H_SESSION_REJECTED" not in h["headline"]


def test_homeowner_agent_view_hides_internal_evidence(s):
    from app import agents as A
    ctx = orchestrator.make_ctx(s, Q)
    view = llm._view_for(A.TECH.run(ctx), "homeowner", s)
    assert "signals" not in view and "observations" not in view
    assert all(not i.startswith("EVD-CHG-VS") for i in view["evidence_ids"])
