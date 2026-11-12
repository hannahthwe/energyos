"""Data-access consent manager (EU Data Act style).

One `ConsentRecord` with scope "data_access:<source>" per gated source says
whether ConnectedCare may use that source for this homeowner. Tools check
`active_sources()` and, for a source without active consent, return no rows
and report the source in `unavailable_sources` so answers can say the data is
unavailable instead of guessing.

CRM and the knowledge base are needed to run the service at all, and market
prices are public data, so those are not gated.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import canonical as C
from .adapters import AUDIT, CHG, HEMS, MKT, SOURCE_LABELS, SUP

DATA_ACT = "EU Data Act - user-designated recipient"
CONTRACT = "contract"
LEGAL_BASES = (DATA_ACT, CONTRACT)

# source -> (legal basis, what it covers, consent lifetime in days or None)
GATED: dict[str, tuple[str, str, int | None]] = {
    HEMS: (CONTRACT, "Energy system data: schedules, battery and EV charge levels, device events", None),
    CHG: (DATA_ACT, "Charger sessions and power readings from your Voltara charger", 365),
    SUP: (DATA_ACT, "Your tariff contract and grid-steering events from your energy supplier", 365),
    AUDIT: (CONTRACT, "History of changes to your system settings", None),
}
PLAIN_LABEL = {HEMS: "Home energy system", CHG: "EV charger", SUP: "Energy supplier", AUDIT: "Settings history"}


def _now() -> datetime:
    from .seed import DEMO_NOW_UTC  # demo clock keeps expiry deterministic

    return DEMO_NOW_UTC


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _is_active(r: C.ConsentRecord, now: datetime) -> bool:
    if r.revoked_at:
        return False
    return not r.expires_at or datetime.fromisoformat(r.expires_at.replace("Z", "+00:00")) > now


def _records(s: Session, customer_id: str) -> list[C.ConsentRecord]:
    return list(s.scalars(select(C.ConsentRecord).where(C.ConsentRecord.customer_id == customer_id,
                                                        C.ConsentRecord.scope.like("data_access:%"))))


def _latest(s: Session, customer_id: str) -> dict[str, C.ConsentRecord]:
    out: dict[str, C.ConsentRecord] = {}
    # Latest grant wins; on the demo clock a re-grant can share a timestamp with the revoked record.
    for r in sorted(_records(s, customer_id), key=lambda r: (r.approved_at, r.revoked_at is None)):
        out[r.source_system] = r
    return out


def _customer() -> str:
    from .state import CUSTOMER_ID

    return CUSTOMER_ID


def seed_defaults(s: Session, customer_id: str | None = None) -> None:
    """Homeowner gave every consent at onboarding (synthetic)."""
    customer_id = customer_id or _customer()
    if _records(s, customer_id):
        return
    for src in GATED:
        _add(s, customer_id, src, approved=datetime(2026, 3, 14, 12, 0, tzinfo=timezone.utc))


def _add(s: Session, customer_id: str, src: str, approved: datetime) -> C.ConsentRecord:
    basis, scope_text, days = GATED[src]
    r = C.ConsentRecord(consent_id=f"CON-{uuid.uuid4().hex[:8].upper()}", customer_id=customer_id, case_id=None,
                        evidence_scope="[]", approved_at=_iso(approved), source_system=src,
                        scope=f"data_access:{src}", legal_basis=basis,
                        expires_at=_iso(approved + timedelta(days=days)) if days else None, revoked_at=None)
    s.add(r)
    s.flush()
    return r


def active_sources(s: Session, customer_id: str | None = None) -> set[str]:
    now = _now()
    latest = _latest(s, customer_id or _customer())
    return {src for src, r in latest.items() if _is_active(r, now)}


def is_allowed(s: Session, source_system: str) -> bool:
    return source_system not in GATED or source_system in active_sources(s)


def unavailable(s: Session, sources) -> list[dict]:
    """Gated sources among `sources` without active consent, as missing-evidence entries."""
    active = active_sources(s)
    out = []
    for src in dict.fromkeys(sources):
        if src in GATED and src not in active:
            out.append({"source_system": src, "label": SOURCE_LABELS[src], "plain_label": PLAIN_LABEL[src],
                        "message": f"{SOURCE_LABELS[src]} data unavailable: no active homeowner consent "
                                   f"({GATED[src][0]}).",
                        "homeowner_message": f"{PLAIN_LABEL[src]} data isn't available because sharing it is "
                                             "turned off in Data & privacy."})
    return out


def _view(src: str, r: C.ConsentRecord | None, now: datetime) -> dict:
    basis, scope_text, _ = GATED[src]
    return {"source_system": src, "label": PLAIN_LABEL[src], "source_label": SOURCE_LABELS[src],
            "scope": scope_text, "legal_basis": r.legal_basis if r else basis, "toggleable": True,
            "active": bool(r and _is_active(r, now)), "consent_id": r.consent_id if r else None,
            "approved_at": r.approved_at if r else None, "expires_at": r.expires_at if r else None,
            "revoked_at": r.revoked_at if r else None}


def list_consents(s: Session, customer_id: str | None = None) -> list[dict]:
    now = _now()
    latest = _latest(s, customer_id or _customer())
    rows = [_view(src, latest.get(src), now) for src in GATED]
    rows.append({"source_system": MKT, "label": "Market prices", "source_label": SOURCE_LABELS[MKT],
                 "scope": "Public hourly day-ahead electricity prices", "legal_basis": "public data (no personal data)",
                 "toggleable": False, "active": True, "consent_id": None, "approved_at": None, "expires_at": None,
                 "revoked_at": None})
    return rows


class ConsentError(Exception):
    pass


def grant(s: Session, source_system: str, customer_id: str | None = None) -> dict:
    if source_system not in GATED:
        raise ConsentError(f"{source_system!r} is not a consent-managed source")
    customer_id = customer_id or _customer()
    now = _now()
    current = _latest(s, customer_id).get(source_system)
    if current and _is_active(current, now):
        return _view(source_system, current, now)
    return _view(source_system, _add(s, customer_id, source_system, approved=now), now)


def revoke(s: Session, consent_id: str, customer_id: str | None = None) -> dict:
    r = s.get(C.ConsentRecord, consent_id)
    if not r or r.customer_id != (customer_id or _customer()) or not r.scope.startswith("data_access:"):
        raise ConsentError(f"consent {consent_id} not found")
    if not r.revoked_at:
        r.revoked_at = _iso(_now())
    s.flush()
    return _view(r.source_system, r, _now())


def case_view(s: Session, customer_id: str) -> list[dict]:
    """What a support engineer sees: which sources the homeowner currently consents to."""
    return [{k: v for k, v in c.items() if k in ("source_system", "label", "source_label", "legal_basis", "active",
                                                  "expires_at", "revoked_at", "toggleable")}
            for c in list_consents(s, customer_id)]
