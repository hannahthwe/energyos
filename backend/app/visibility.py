"""Role-based visibility, enforced on the server.

    homeowner  -> homeowner_safe only
    support    -> homeowner_safe + support_internal + restricted

Restricted records (security audit data) are visible to support staff but are
never shared with the homeowner, never included in customer-facing drafts, and
never sent to third-party partners in an escalation package.
"""
from __future__ import annotations

ROLE_LEVELS = {
    "homeowner": {"homeowner_safe"},
    "support": {"homeowner_safe", "support_internal", "restricted"},
    "partner": {"homeowner_safe", "support_internal"},
}


def allowed_levels(role: str) -> set[str]:
    if role not in ROLE_LEVELS:
        raise PermissionError(f"unknown role {role!r}")
    return ROLE_LEVELS[role]


def filter_evidence(items: list[dict], role: str) -> list[dict]:
    allowed = allowed_levels(role)
    return [i for i in items if i.get("visibility_level") in allowed]


def can_see(level: str, role: str) -> bool:
    return level in allowed_levels(role)
