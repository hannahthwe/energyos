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

from . import cases, datamodel, db, llm, orchestrator, state
from .seed import SCENARIOS

app = FastAPI(title="ConnectedCare AI", version="0.1.0",
              description="AI technical support copilot for connected home energy. SYNTHETIC DEMO DATA.")
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
    except cases.Invalid as e:
        raise HTTPException(400, str(e))


class ScenarioIn(BaseModel):
    scenario: str = Field(pattern="^[ABCabc]$")


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


# ------------------------------------------------------------------- demo
@app.get("/api/demo")
def demo_state(s=Depends(session)):
    code = state.active_scenario(s)
    return {"scenario": code, "scenarios": list(SCENARIOS.values()), "ai_mode": "claude" if llm.enabled() else "offline",
            "model": llm.MODEL if llm.enabled() else None, "synthetic_data": True}


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
    return datamodel.data_model(s)


# ----------------------------------------------- built support dashboard
WEB = Path(__file__).resolve().parents[2] / "web" / "dist"
if WEB.exists():
    app.mount("/assets", StaticFiles(directory=WEB / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        return FileResponse(WEB / "index.html")
