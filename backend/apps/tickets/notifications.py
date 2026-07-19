"""
Ticket notification functions.

Each function is self-contained — call it from any view after the relevant
action completes. All functions are silent on failure (email_service handles logging).

Usage example:
    from apps.tickets.notifications import notify_ticket_created
    notify_ticket_created(ticket)
"""

import logging

from django.conf import settings
from django.template.loader import render_to_string

from apps.accounts.email_service import send_email

logger = logging.getLogger(__name__)


def _site_url() -> str:
    return getattr(settings, "SITE_URL", "").rstrip("/")


def _tech_url(ticket) -> str:
    return f"{_site_url()}/tech/tickets/{ticket.pk}/"


def _portal_url(ticket) -> str:
    return f"{_site_url()}/portal/{ticket.pk}/"


def _render(template: str, context: dict) -> str:
    return render_to_string(f"email/{template}", context)


# ── Client confirmation ───────────────────────────────────────────────────────

def notify_ticket_confirmed(ticket) -> None:
    """
    Send a confirmation to the client immediately after they submit a ticket.
    """
    client = ticket.created_by
    if not client or not client.email:
        return
    context = {
        "ticket": ticket,
        "ticket_url": _portal_url(ticket),
        "recipient_name": client.get_full_name(),
        "recipient_is_client": True,
    }
    send_email(
        client.email,
        client.get_full_name(),
        f"[{ticket.ticket_number}] We received your request: {ticket.subject}",
        _render("ticket_confirmed.html", context),
    )


# ── New ticket ────────────────────────────────────────────────────────────────

def notify_ticket_created(ticket) -> None:
    """
    Notify when a new ticket is submitted.
    - If assigned: notify that tech.
    - If unassigned: notify the support inbox.
    """
    context = {
        "ticket": ticket,
        "ticket_url": _tech_url(ticket),
        "recipient_is_client": False,
    }

    if ticket.assigned_to and ticket.assigned_to.email:
        recipient = ticket.assigned_to
        context["recipient_name"] = recipient.get_full_name()
        subject = f"[{ticket.ticket_number}] New ticket assigned to you: {ticket.subject}"
        send_email(recipient.email, recipient.get_full_name(), subject,
                   _render("ticket_created.html", context))
    else:
        context["recipient_name"] = "Support Team"
        subject = f"[{ticket.ticket_number}] New unassigned ticket: {ticket.subject}"
        send_email(settings.SUPPORT_EMAIL, "Support Team", subject,
                   _render("ticket_created.html", context))


# ── New reply ─────────────────────────────────────────────────────────────────

def notify_new_reply(message) -> None:
    """
    Notify the other party when a reply is posted.
    - Tech replied (non-internal) → notify the client who created the ticket.
    - Client replied → notify assigned tech or support inbox.
    Internal notes are silently ignored.
    """
    if message.is_internal:
        return

    ticket = message.ticket
    author = message.author
    author_is_tech = author and getattr(author, "is_tech", False)

    if author_is_tech:
        # Tech replied → notify client
        client = ticket.created_by
        if not client or not client.email:
            return
        context = {
            "ticket": ticket,
            "message": message,
            "ticket_url": _portal_url(ticket),
            "recipient_name": client.get_full_name(),
            "replied_by": "Canopy Support",
            "recipient_is_client": True,
        }
        send_email(
            client.email,
            client.get_full_name(),
            f"[{ticket.ticket_number}] Update on your ticket: {ticket.subject}",
            _render("new_reply.html", context),
        )
    else:
        # Client replied → notify tech
        if ticket.assigned_to and ticket.assigned_to.email:
            recipient = ticket.assigned_to
            to_email, to_name = recipient.email, recipient.get_full_name()
        else:
            to_email, to_name = settings.SUPPORT_EMAIL, "Support Team"

        client_name = author.get_full_name() if author else "Client"
        context = {
            "ticket": ticket,
            "message": message,
            "ticket_url": _tech_url(ticket),
            "recipient_name": to_name,
            "replied_by": client_name,
            "recipient_is_client": False,
        }
        send_email(
            to_email,
            to_name,
            f"[{ticket.ticket_number}] Client replied: {ticket.subject}",
            _render("new_reply.html", context),
        )


# ── Assignment ────────────────────────────────────────────────────────────────

def notify_ticket_assigned(ticket) -> None:
    """Notify a tech when a ticket is assigned to them."""
    if not ticket.assigned_to or not ticket.assigned_to.email:
        return
    tech = ticket.assigned_to
    context = {
        "ticket": ticket,
        "ticket_url": _tech_url(ticket),
        "recipient_name": tech.get_full_name(),
        "recipient_is_client": False,
    }
    send_email(
        tech.email,
        tech.get_full_name(),
        f"[{ticket.ticket_number}] Ticket assigned to you: {ticket.subject}",
        _render("ticket_assigned.html", context),
    )


# ── Status change ─────────────────────────────────────────────────────────────

def notify_status_changed(ticket, old_status: str) -> None:
    """
    Notify the client when a ticket's status changes to something meaningful.
    Fires on: RESOLVED, CLOSED, WAITING_CLIENT.
    Ignores internal status movements (OPEN ↔ IN_PROGRESS).
    """
    from .models import Ticket

    notify_on = {Ticket.Status.RESOLVED, Ticket.Status.CLOSED, Ticket.Status.WAITING_CLIENT}
    if ticket.status not in notify_on:
        return

    client = ticket.created_by
    if not client or not client.email:
        return

    status_labels = dict(Ticket.Status.choices)
    context = {
        "ticket": ticket,
        "ticket_url": _portal_url(ticket),
        "recipient_name": client.get_full_name(),
        "old_status_label": status_labels.get(old_status, old_status),
        "new_status_label": status_labels.get(ticket.status, ticket.status),
        "recipient_is_client": True,
    }
    send_email(
        client.email,
        client.get_full_name(),
        f"[{ticket.ticket_number}] Your ticket status has been updated",
        _render("status_changed.html", context),
    )
