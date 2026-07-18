"""
process_email_replies — polls the support mailbox for replies to existing
tickets (pins them as Messages) and for unrecognized emails (creates new
tickets from them).

How it works:
  1. Fetches unread emails from SUPPORT_EMAIL mailbox via Microsoft Graph.
  2. Looks for a ticket number in the subject: [T-2026-34567]
  3. If found, creates a Message on that ticket from the email body.
  4. If not found, creates a brand new ticket from the email instead:
       - assigned_to is always Marc Gullo (marc.gullo@canopytech.ca).
       - company is matched from the sender's email domain against
         Company.email_domain; left blank if nothing matches, for a
         human to set later.
       - the sender is skipped entirely if it's the support mailbox
         itself, to avoid looping on our own notifications.
  5. Marks the email as read so it isn't processed again either way.

Requires:
  - GRAPH_CLIENT_ID, GRAPH_CLIENT_SECRET, GRAPH_TENANT_ID in settings.
  - The app registration needs Mail.Read + Mail.ReadWrite application permissions.

Run manually:
  docker compose exec web python manage.py process_email_replies

Runs automatically via the email-inbound Docker service (every 5 minutes).
"""

import logging
import re
from html.parser import HTMLParser

import httpx
from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.accounts.email_service import _get_token
from apps.accounts.models import AuditLog, User
from apps.companies.models import Company
from apps.tickets.models import Message, Ticket
from apps.tickets.notifications import notify_ticket_created

logger = logging.getLogger(__name__)

_TICKET_RE = re.compile(r'\[T-(\d{4}-\d{5,6})\]', re.IGNORECASE)
_GRAPH_BASE = "https://graph.microsoft.com/v1.0"
_DEFAULT_ASSIGNEE_EMAIL = "marc.gullo@canopytech.ca"


class _HTMLStripper(HTMLParser):
    def __init__(self):
        super().__init__()
        self.reset()
        self._parts = []

    def handle_data(self, d):
        self._parts.append(d)

    def get_text(self):
        return "\n".join(
            line for line in "".join(self._parts).splitlines()
            if line.strip()
        )


def _strip_html(html: str) -> str:
    stripper = _HTMLStripper()
    stripper.feed(html)
    return stripper.get_text().strip()


def _get_unread_messages(token: str, mailbox: str) -> list[dict]:
    url = (
        f"{_GRAPH_BASE}/users/{mailbox}/messages"
        "?$filter=isRead eq false"
        "&$orderby=receivedDateTime asc"
        "&$top=25"
        "&$select=id,subject,body,from,receivedDateTime"
    )
    resp = httpx.get(
        url,
        headers={"Authorization": f"Bearer {token}"},
        timeout=15.0,
    )
    resp.raise_for_status()
    return resp.json().get("value", [])


def _mark_as_read(token: str, mailbox: str, message_id: str) -> None:
    httpx.patch(
        f"{_GRAPH_BASE}/users/{mailbox}/messages/{message_id}",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        json={"isRead": True},
        timeout=10.0,
    ).raise_for_status()


def _extract_sender(email: dict) -> tuple[str, str]:
    """Returns (sender_email, sender_name), lowercasing the address."""
    frm = email.get("from", {}).get("emailAddress", {})
    return frm.get("address", "").lower(), frm.get("name", "")


def _extract_plain_body(email: dict, sender_email: str, sender_name: str, author) -> str:
    html_body = email.get("body", {}).get("content", "")
    plain_body = _strip_html(html_body) if html_body else ""
    if not plain_body.strip():
        plain_body = f"[Empty email from {sender_name or sender_email}]"
    # Prepend sender info if we couldn't match to a user, so identity isn't lost.
    if not author:
        plain_body = f"From: {sender_name} <{sender_email}>\n\n{plain_body}"
    return plain_body


class Command(BaseCommand):
    help = "Poll support mailbox for email replies and pin them to tickets, or create new tickets from unrecognized emails."

    def handle(self, *args, **options):
        if not all([
            getattr(settings, "GRAPH_CLIENT_ID", ""),
            getattr(settings, "GRAPH_CLIENT_SECRET", ""),
            getattr(settings, "GRAPH_TENANT_ID", ""),
        ]):
            self.stderr.write("GRAPH_* settings not configured — skipping.")
            return

        mailbox = settings.SUPPORT_EMAIL

        try:
            token = _get_token()
            emails = _get_unread_messages(token, mailbox)
        except Exception as e:
            logger.error("Failed to fetch emails: %s", e)
            self.stderr.write(f"Failed to fetch emails: {e}")
            return

        processed = created = skipped = errors = 0

        for email in emails:
            subject = email.get("subject", "")
            match = _TICKET_RE.search(subject)

            if not match:
                outcome = self._create_ticket_from_email(email, token, mailbox)
                if outcome == "created":
                    created += 1
                elif outcome == "skipped":
                    skipped += 1
                else:
                    errors += 1
                continue

            ticket_number = f"T-{match.group(1)}"

            try:
                ticket = Ticket.objects.get(ticket_number=ticket_number)
            except Ticket.DoesNotExist:
                logger.warning("Email references unknown ticket %s — marking read", ticket_number)
                _mark_as_read(token, mailbox, email["id"])
                errors += 1
                continue

            sender_email, sender_name = _extract_sender(email)
            author = User.objects.filter(email__iexact=sender_email).first()
            plain_body = _extract_plain_body(email, sender_email, sender_name, author)

            try:
                Message.objects.create(
                    ticket=ticket,
                    author=author,
                    body=plain_body,
                    is_internal=False,
                )

                # Auto-reopen if client replied to a waiting ticket
                if (ticket.status == Ticket.Status.WAITING_CLIENT
                        and author and not getattr(author, "is_tech", False)):
                    ticket.status = Ticket.Status.OPEN
                    ticket.save(update_fields=["status", "updated_at"])

                _mark_as_read(token, mailbox, email["id"])
                logger.info("Pinned email reply to %s from %s", ticket_number, sender_email)
                processed += 1

            except Exception as e:
                logger.error("Error pinning reply to %s: %s", ticket_number, e)
                errors += 1

        self.stdout.write(
            f"Done — processed={processed} created={created} skipped={skipped} errors={errors}"
        )

    def _create_ticket_from_email(self, email: dict, token: str, mailbox: str) -> str:
        """Creates a new ticket from an email with no recognized ticket tag
        in its subject. Returns "created", "skipped" (self-loop guard), or
        "error" (left unread, will be retried next poll)."""
        sender_email, sender_name = _extract_sender(email)

        # Don't let the mailbox create tickets from its own notifications.
        if sender_email and sender_email == mailbox.lower():
            return "skipped"

        try:
            subject = (email.get("subject") or "(no subject)")[:255]
            author = User.objects.filter(email__iexact=sender_email).first() if sender_email else None
            plain_body = _extract_plain_body(email, sender_email, sender_name, author)

            company = None
            if "@" in sender_email:
                domain = sender_email.split("@")[-1]
                company = Company.objects.filter(email_domain__iexact=domain, is_active=True).first()

            assignee = User.objects.filter(email__iexact=_DEFAULT_ASSIGNEE_EMAIL, is_active=True).first()
            if not assignee:
                logger.warning(
                    "Default assignee %s not found or inactive — leaving new ticket unassigned",
                    _DEFAULT_ASSIGNEE_EMAIL,
                )

            ticket = Ticket.objects.create(
                company=company,
                created_by=author,
                assigned_to=assignee,
                subject=subject,
                description=plain_body,
                priority=Ticket.Priority.MEDIUM,
            )
            Message.objects.create(
                ticket=ticket,
                author=author,
                body=plain_body,
                is_internal=False,
            )
            notify_ticket_created(ticket)
            AuditLog.objects.create(
                actor=None,
                action=AuditLog.Action.EMAIL_TICKET_CREATE,
                target=ticket.ticket_number,
                detail=f"From {sender_email}; company={company.name if company else 'none matched'}",
                ip_address=None,
            )

            _mark_as_read(token, mailbox, email["id"])
            logger.info(
                "Created %s from email by %s (company=%s)",
                ticket.ticket_number, sender_email, company.name if company else "none",
            )
            return "created"

        except Exception as e:
            logger.error("Error creating ticket from email: %s", e)
            return "error"
