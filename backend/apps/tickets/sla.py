from datetime import timedelta
from django.utils import timezone

# Clock-hour SLA targets per priority (can evolve to business-hours later)
SLA_POLICY = {
    "CRITICAL": {"response_hours": 1,  "resolve_hours": 4},
    "HIGH":     {"response_hours": 4,  "resolve_hours": 8},
    "MEDIUM":   {"response_hours": 8,  "resolve_hours": 24},
    "LOW":      {"response_hours": 24, "resolve_hours": 72},
}

# Warn when this fraction of SLA time remains
WARNING_THRESHOLD = 0.25


class SLAStatus:
    OK = "OK"
    WARNING = "WARNING"
    BREACHED = "BREACHED"
    MET = "MET"           # ticket resolved within SLA
    MISSED = "MISSED"     # ticket resolved after SLA


def deadlines_for(priority, created_at):
    """Return (response_deadline, resolve_deadline) for a ticket."""
    policy = SLA_POLICY.get(priority, SLA_POLICY["MEDIUM"])
    return (
        created_at + timedelta(hours=policy["response_hours"]),
        created_at + timedelta(hours=policy["resolve_hours"]),
    )


def response_status(ticket):
    """SLA status for first-response."""
    if not ticket.sla_response_deadline:
        return None
    if ticket.first_response_at:
        return SLAStatus.MET if ticket.first_response_at <= ticket.sla_response_deadline else SLAStatus.MISSED
    now = timezone.now()
    if now > ticket.sla_response_deadline:
        return SLAStatus.BREACHED
    remaining = ticket.sla_response_deadline - now
    total = ticket.sla_response_deadline - ticket.created_at
    if remaining / total <= WARNING_THRESHOLD:
        return SLAStatus.WARNING
    return SLAStatus.OK


def resolve_status(ticket):
    """SLA status for resolution."""
    if not ticket.sla_resolve_deadline:
        return None
    if ticket.resolved_at:
        return SLAStatus.MET if ticket.resolved_at <= ticket.sla_resolve_deadline else SLAStatus.MISSED
    now = timezone.now()
    if now > ticket.sla_resolve_deadline:
        return SLAStatus.BREACHED
    remaining = ticket.sla_resolve_deadline - now
    total = ticket.sla_resolve_deadline - ticket.created_at
    if remaining / total <= WARNING_THRESHOLD:
        return SLAStatus.WARNING
    return SLAStatus.OK


def time_remaining_display(deadline):
    """Human-readable countdown, e.g. '2h 14m' or 'Breached 3h ago'."""
    now = timezone.now()
    delta = deadline - now
    if delta.total_seconds() < 0:
        delta = -delta
        h, rem = divmod(int(delta.total_seconds()), 3600)
        m = rem // 60
        if h:
            return f"Breached {h}h {m}m ago"
        return f"Breached {m}m ago"
    h, rem = divmod(int(delta.total_seconds()), 3600)
    m = rem // 60
    if h:
        return f"{h}h {m}m"
    return f"{m}m"
