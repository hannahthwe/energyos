"""ConnectedCare AI API.

Demo authentication: the caller's role comes from the `X-Demo-Role` header
("homeowner" or "support"). `/api/home/*` always runs as homeowner;
`/api/support/*` requires the support role. Visibility rules are enforced
here and in the orchestrator, not in the clients.
"""
from __future__ import annotations

import os
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import cases, consents, datamodel, db, llm, orchestrator, state
from .seed import SCENARIOS

# Presentation mode: hides internals (agent/tool panel, raw evidence IDs, data-model view, model name, API docs).
DEMO_MODE = os.environ.get("CONNECTEDCARE_DEMO_MODE") == "1"

app = FastAPI(title="ConnectedCare AI", version="0.2.0",
              description="AI technical support copilot for connected home energy. SYNTHETIC DEMO DATA.",
              docs_url=None if DEMO_MODE else "/docs", redoc_url=None if DEMO_MODE else "/redoc",
              openapi_url=None if DEMO_MODE else "/openapi.json")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


@app.on_event("startup")
def _startup() -> None:
    state.init_db(reset=os.environ.get("CONNECTEDCARE_RESET") == "1")


def session():
    with db.session_scope() as s:
        yield s


def support_role(x_demo_role: str = Header(default="")) -> str:
    if x_demo_role != "support":
        raise HTTPException(403, "support role required (send X-Demo-Role: support)")
    return "support"


def _wrap(fn, *a):
    try:
        return fn(*a)
    except cases.NotFound as e:
        raise HTTPException(404, str(e))
    except (cases.Invalid, consents.ConsentError) as e:
        raise HTTPException(404 if "not found" in str(e) else 400, str(e))


class ScenarioIn(BaseModel):
    scenario: str = Field(pattern="^[A-Ea-e]$")


class QuestionIn(BaseModel):
    question: str = Field(min_length=2, max_length=500)


class HandoffIn(BaseModel):
    run_id: str
    approved: bool
    note: str | None = None


class NoteIn(BaseModel):
    text: str = Field(min_length=1, max_length=4000)


class StatusIn(BaseModel):
    status: str


class ConsentIn(BaseModel):
    source_system: str


# ------------------------------------------------------------------- demo
@app.get("/api/demo")
def demo_state(s=Depends(session)):
    code = state.active_scenario(s)
    return {"scenario": code, "scenarios": list(SCENARIOS.values()), "ai_mode": "claude" if llm.enabled() else "offline",
            "model": llm.MODEL if llm.enabled() and not DEMO_MODE else None, "synthetic_data": True,
            "demo_mode": DEMO_MODE}


@app.post("/api/demo/scenario")
def demo_set(body: ScenarioIn, s=Depends(session)):
    state.set_scenario(s, body.scenario)
    return {"scenario": body.scenario.upper(), **SCENARIOS[body.scenario.upper()]}


# -------------------------------------------------------------- homeowner
@app.get("/api/home/overview")
def home_overview(s=Depends(session)):
    return datamodel.home_overview(s)


@app.post("/api/home/chat")
def home_chat(body: QuestionIn, s=Depends(session)):
    return orchestrator.diagnose(s, body.question, role="homeowner")


@app.get("/api/home/support-request/{run_id}/preview")
def home_share_preview(run_id: str, s=Depends(session)):
    return _wrap(cases.share_preview, s, run_id)


@app.post("/api/home/support-request")
def home_handoff(body: HandoffIn, s=Depends(session)):
    return _wrap(cases.create_case_from_homeowner, s, body.run_id, body.approved, body.note)


@app.get("/api/home/savings")
def home_savings(window: str = "7d", s=Depends(session)):
    if window not in datamodel.SAVINGS_WINDOWS:
        raise HTTPException(400, f"window must be one of {sorted(datamodel.SAVINGS_WINDOWS)}")
    return datamodel.home_savings(s, window)


@app.get("/api/home/consents")
def home_consents(s=Depends(session)):
    return consents.list_consents(s)


@app.post("/api/home/consents")
def home_consent_grant(body: ConsentIn, s=Depends(session)):
    return _wrap(consents.grant, s, body.source_system)


@app.post("/api/home/consents/{consent_id}/revoke")
def home_consent_revoke(consent_id: str, s=Depends(session)):
    return _wrap(consents.revoke, s, consent_id)


@app.get("/api/home/cases")
def home_cases(s=Depends(session)):
    return cases.homeowner_cases(s)


# ---------------------------------------------------------------- support
@app.get("/api/support/cases")
def support_cases(role=Depends(support_role), s=Depends(session)):
    return cases.list_cases(s)


@app.get("/api/support/cases/{case_id}")
def support_case(case_id: str, role=Depends(support_role), s=Depends(session)):
    return _wrap(cases.get_case, s, case_id, role)


@app.post("/api/support/cases/{case_id}/ask")
def support_ask(case_id: str, body: QuestionIn, role=Depends(support_role), s=Depends(session)):
    return _wrap(cases.ask, s, case_id, body.question)


@app.post("/api/support/cases/{case_id}/notes")
def support_note(case_id: str, body: NoteIn, role=Depends(support_role), s=Depends(session)):
    return _wrap(cases.add_note, s, case_id, body.text)


@app.post("/api/support/cases/{case_id}/draft-response")
def support_draft(case_id: str, role=Depends(support_role), s=Depends(session)):
    return _wrap(cases.draft_response, s, case_id)


@app.post("/api/support/cases/{case_id}/escalation")
def support_escalation(case_id: str, role=Depends(support_role), s=Depends(session)):
    return _wrap(cases.prepare_escalation, s, case_id)


@app.patch("/api/support/cases/{case_id}/status")
def support_status(case_id: str, body: StatusIn, role=Depends(support_role), s=Depends(session)):
    return _wrap(cases.set_status, s, case_id, body.status)


@app.post("/api/support/diagnose")
def support_diagnose(body: QuestionIn, role=Depends(support_role), s=Depends(session)):
    return orchestrator.diagnose(s, body.question, role="support")


@app.get("/api/support/data-model")
def support_data_model(role=Depends(support_role), s=Depends(session)):
    if DEMO_MODE:
        raise HTTPException(404, "not available in demo mode")
    return datamodel.data_model(s)


# ----------------------------------------------- built support dashboard
WEB = Path(__file__).resolve().parents[2] / "web" / "dist"
if WEB.exists():
    app.mount("/assets", StaticFiles(directory=WEB / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        return FileResponse(WEB / "index.html")
