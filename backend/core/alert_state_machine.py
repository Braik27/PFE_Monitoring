"""
Alert State Machine — Enforces valid transitions with RBAC guards and audit logging.

States: NEW → ACKNOWLEDGED → IN_PROGRESS → RESOLVED → CLOSED
        (can escalate at any stage before CLOSED)
"""

from datetime import datetime
from typing import Dict, List, Optional
from enum import Enum

from core.sla_policy import (
    compute_sla_deadline as compute_canonical_sla_deadline,
    compute_sla_status,
)


# ─── State Definitions ──────────────────────────────────────────────────
class AlertStatus(str, Enum):
    """Valid alert statuses."""
    NEW = "NEW"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    IN_PROGRESS = "IN_PROGRESS"
    ESCALATED = "ESCALATED"
    RESOLVED = "RESOLVED"
    CLOSED = "CLOSED"
    IGNORED = "IGNORED"


# ─── Transition Rules ──────────────────────────────────────────────────
TRANSITIONS = {
    "NEW":          ["ACKNOWLEDGED", "IN_PROGRESS", "RESOLVED", "ESCALATED", "IGNORED"],
    "ACKNOWLEDGED": ["IN_PROGRESS", "RESOLVED", "ESCALATED", "IGNORED"],
    "IN_PROGRESS":  ["RESOLVED", "ESCALATED", "IGNORED"],
    "ESCALATED":    ["ACKNOWLEDGED", "IN_PROGRESS", "RESOLVED", "IGNORED"],
    "RESOLVED":     ["CLOSED"],
    "CLOSED":       [],
    "IGNORED":      ["ACKNOWLEDGED", "IN_PROGRESS", "ESCALATED", "RESOLVED"],
}

# ─── Role Guards per Status ────────────────────────────────────────────
ROLE_GUARDS = {
    "ACKNOWLEDGED": ["analyst", "consultant", "team_leader", "admin"],
    "IN_PROGRESS":  ["analyst", "consultant", "team_leader", "admin"],
    "ESCALATED":    ["analyst", "consultant", "team_leader", "admin"],
    "RESOLVED":     ["analyst", "consultant", "team_leader", "admin"],
    "CLOSED":       ["system", "admin"],
    "IGNORED":      ["analyst", "consultant", "team_leader", "admin"],
}

# ─── Exceptions ────────────────────────────────────────────────────────
class InvalidTransitionError(Exception):
    """Attempted an invalid state transition."""
    pass


class ValidationError(Exception):
    """Data validation failed."""
    pass


class PermissionError(Exception):
    """RBAC check failed."""
    pass


# ─── Validation & Transition ────────────────────────────────────────────
def validate_transition(
    current_status: str,
    new_status: str,
    actor_role: str,
    comment: str = "",
) -> None:
    """
    Validate a state transition against rules, RBAC, and field requirements.

    Args:
        current_status: Current alert status
        new_status: Desired new status
        actor_role: Role of the user initiating transition
        comment: Optional comment (required for RESOLVED)

    Raises:
        InvalidTransitionError: if transition is not allowed
        PermissionError: if actor lacks required role
        ValidationError: if required fields are missing
    """
    # Guard 1: Valid transition?
    allowed = TRANSITIONS.get(current_status, [])
    if new_status not in allowed:
        raise InvalidTransitionError(
            f"Transition {current_status} → {new_status} is not allowed. "
            f"Allowed: {allowed}"
        )

    # Guard 2: RBAC
    required_roles = ROLE_GUARDS.get(new_status, [])
    if required_roles and actor_role not in required_roles:
        raise PermissionError(
            f"Role '{actor_role}' cannot set status '{new_status}'. "
            f"Required roles: {required_roles}"
        )

    # Guard 3: Required fields
    if new_status == "RESOLVED" and not comment.strip():
        raise ValidationError(
            "Status 'RESOLVED' requires a comment. Cannot proceed without evidence."
        )


def transition_alert(storage, alert_token, new_status, actor_user, comment=""):
    """
    Transition an alert to a new status, enforcing guards.
    
    Args:
        storage: storage instance with methods get_alert_by_token and update_alert_status
        alert_token: the token of the alert
        new_status: the desired new status (string)
        actor_user: dict with keys 'username', 'role', 'sub' (from Keycloak)
        comment: optional comment (required for RESOLVED)
    
    Returns:
        Updated alert dict
    
    Raises:
        InvalidTransitionError, ValidationError, PermissionError
    """
    # Get current alert
    alert = storage.get_alert_by_token(alert_token)
    if not alert:
        raise ValueError("Alert not found")
    
    # Use workflow_status as source of truth (dual-status model)
    current_status = alert.get("workflow_status") or alert["status"]
    
    # Validate transition
    validate_transition(current_status, new_status, actor_user["role"], comment)
    
    # Apply transition + audit trail (alert_history via update_alert_status)
    storage.update_alert_status(
        alert_token,
        new_status,
        audit_username=actor_user.get("username", "system"),
        audit_comment=comment or f"{current_status} → {new_status}",
    )

    # Set resolved_by/resolved_at or escalated_by/escalated_to
    if new_status == "RESOLVED":
        storage.set_resolved(alert_token, actor_user.get("username", "system"))
    elif new_status == "ESCALATED":
        # escalated_to will be set separately by the escalation endpoint
        storage.set_escalated(alert_token, actor_user.get("username", "system"), "")

    return storage.get_alert_by_token(alert_token)


# ─── Dynamic SLA Computation ────────────────────────────────────────────
def compute_sla_deadline(
    alert: Dict,
    open_alert_count: int = 0,
) -> Dict:
    """
    Compute dynamic SLA deadline based on alert severity, backlog, and current state.

    Args:
        alert: Alert record with 'created_at', 'flux_type', 'n_critiques', 
               'n_warnings', 'concordance', 'status'
        open_alert_count: Number of open (non-CLOSED) alerts in system

    Returns:
        dict with 'sla_deadline', 'sla_hours', 'remaining_pct', 'breached'
    """
    # Base SLA hours per flux type (configurable per customer)
    BASE_HOURS = {
        "comptabilite": 24,
        "tresorerie": 12,
        "paie": 8,
        "default": 24,
    }

    def severity_weight(n_crit, n_warn, concordance):
        """Higher severity → tighter SLA."""
        if n_crit > 10 or concordance < 30:
            return 1.5
        if n_crit > 5 or concordance < 50:
            return 1.3
        if n_warn > 20:
            return 1.1
        return 1.0

    def backlog_factor(open_count):
        """Higher backlog → looser SLA (allow time for queue)."""
        if open_count > 50:
            return 1.3
        if open_count > 20:
            return 1.1
        return 1.0

    def escalation_modifier(status):
        """Escalated alert gets fresh window (shorter deadline)."""
        return 0.6 if status == "ESCALATED" else 1.0

    # Compute multiplied SLA
    base = BASE_HOURS.get(alert.get("flux_type", "default"), 24)
    weight = severity_weight(
        alert.get("n_critiques", 0),
        alert.get("n_warnings", 0),
        alert.get("concordance", 100),
    )
    backlog = backlog_factor(open_alert_count)
    modifier = escalation_modifier(alert.get("status", "NEW"))

    sla_hours = base * weight * backlog * modifier
    created = datetime.fromisoformat(alert["created_at"]) if isinstance(alert["created_at"], str) else alert["created_at"]
    deadline = compute_canonical_sla_deadline(created, sla_hours)
    status = compute_sla_status(sla_hours, deadline, now=datetime.utcnow())

    return {
        "sla_deadline": status["sla_deadline"],
        "sla_hours": round(sla_hours, 1),
        "remaining_pct": status["remaining_pct"],
        "breached": status["breached"],
    }