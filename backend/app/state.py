"""Database bootstrap and demo scenario switching."""
from __future__ import annotations

import json

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import adapters, canonical as C, db, seed
from . import sources as S

INSTALLATION_ID = "CC-INST-20931"
CUSTOMER_ID = "CC-CUS-88412"
TENANT_ID = "helio"


def _set(s: Session, key: str, value: str) -> None:
    row = s.get(C.DemoState, key)
    if row:
        row.value = value
    else:
        s.add(C.DemoState(key=key, value=value))


def get_value(s: Session, key: str, default: str = "") -> str:
    row = s.get(C.DemoState, key)
    return row.value if row else default


def active_scenario(s: Session) -> str:
    return get_value(s, "scenario", "A")


def unmapped_records(s: Session) -> list[dict]:
    return json.loads(get_value(s, "unmapped", "[]"))


def set_scenario(s: Session, code: str) -> dict:
    seed.seed_scenario(s, code)
    s.flush()
    report = adapters.ingest(s)
    _set(s, "scenario", code.upper())
    _set(s, "unmapped", json.dumps(report["unmapped"]))
    return report


def _seed_historic_case(s: Session) -> None:
    """One resolved case from CRM history so the queue is never empty."""
    if s.get(C.SupportCase, "CASE-1001"):
        return
    s.add(C.SupportCase(
        case_id="CASE-1001", tenant_id=TENANT_ID, customer_id=CUSTOMER_ID, installation_id=INSTALLATION_ID,
        title="Question about the V2H minimum reserve", status="Resolved", created_at="2026-09-18T09:12:00Z",
        description="Homeowner asked what the minimum reserve setting does.",
        diagnostic_summary="No fault. Reserve explained; homeowner kept 40%.", escalation_required=False,
        scenario="-", origin="crm_import", snapshot_json="{}"))
    s.add(C.SupportMessage(message_id="MSG-1001-1", case_id="CASE-1001", sender_role="support",
                           message_type="note", message="Imported from CRM-10871.",
                           created_at="2026-09-18T09:30:00Z", visibility_level="support_internal"))


def init_db(reset: bool = False) -> None:
    if reset:
        db.Base.metadata.drop_all(db.engine)
    db.Base.metadata.create_all(db.engine)
    with db.session_scope() as s:
        if s.scalar(select(func.count()).select_from(S.CrmAccount)) == 0:
            seed.seed_static(s)
            s.flush()
        set_scenario(s, active_scenario(s))
        _seed_historic_case(s)
