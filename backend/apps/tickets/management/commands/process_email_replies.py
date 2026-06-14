"""
process_email_replies — polls the support mailbox for replies to existing tickets
and pins them as Messages.

How it works:
  1. Fetches unread emails from SUPPORT_EMAIL mailbox via Microsoft Graph.
  2. Looks for a ticket number in the subject: [T-2026-34567]
  3. If found, creates a Message on that ticket from the email body.
  4. Marks the email as read so it isn't processed again.
  5. Emails without a ticket number are left untouched (handle manually).

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
from apps.accounts.models import User
from apps.tickets.models import Message, Ticket

logger = logging.getLogger(__name__)

_TICKET_RE = re.compile(r'\[T-(\d{4}-\d{5,6})\]', re.IGNORECASE)
_GRAPH_BASE = "https://graph.microsoft.com/v1.0"


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


class Command(BaseCommand):
    help = "Poll support mailbox for email replies and pin them to tickets."

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

        processed = skipped = errors = 0

        for email in emails:
            subject = email.get("subject", "")
            match = _TICKET_RE.search(subject)

            if not match:
                # Not a ticket reply — leave unread for manual handling
                skipped += 1
                continue

            ticket_number = f"T-{match.group(1)}"

            try:
                ticket = Ticket.objects.get(ticket_number=ticket_number)
            except Ticket.DoesNotExist:
                logger.warning("Email references unknown ticket %s — marking read", ticket_number)
                _mark_as_read(token, mailbox, email["id"])
                errors += 1
                continue

            # Try to match sender to a User in the system
            sender_email = (
                email.get("from", {})
                     .get("emailAddress", {})
                     .get("address", "")
                     .lower()
            )
            sender_name = (
                email.get("from", {})
                     .get("emailAddress", {})
                     .get("name", "")
            )
            author = User.objects.filter(email__iexact=sender_email).first()

            # Extract plain text from HTML body
            html_body = email.get("body", {}).get("content", "")
            plain_body = _strip_html(html_body) if html_body else ""

            if not plain_body.strip():
                plain_body = f"[Empty reply from {sender_name or sender_email}]"

            # Prepend sender info if we couldn't match to a user
            if not author:
                plain_body = f"From: {sender_name} <{sender_email}>\n\n{plain_body}"

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
            f"Done — processed={processed} skipped={skipped} errors={errors}"
        )
