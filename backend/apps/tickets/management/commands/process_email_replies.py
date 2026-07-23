"""
process_email_replies — polls the support mailbox for replies to existing
tickets (pins them as Messages) and for unrecognized emails (creates new
tickets from them).

Only ever processes mail received after a persistent cutoff (EmailPollState,
one row, DB-backed so it survives restarts/redeploys) - never a backlog of
old unread mail. The very first time this command ever runs, it just
records "now" as the cutoff and processes nothing; every run after that
only looks at mail received since the last run.

Launch gate: nothing is processed before _LAUNCH_AT (Aug 3, 2026 12:01am
Eastern) regardless of the above - remove this block once that date has
passed and it's no longer needed. This also clamps the stored cutoff up to
_LAUNCH_AT the first time it runs after that instant, so the backlog that
piled up before launch (including while Graph mailbox permissions were
missing) is never swept in as a wave of tickets.

How it works:
  1. Fetches unread emails received since the last poll from SUPPORT_EMAIL
     mailbox via Microsoft Graph.
  2. Looks for a ticket number in the subject: [T-2026-34567]
  3. If found, creates a Message on that ticket from the email body.
  4. If not found, creates a brand new ticket from the email instead:
       - assigned_to is always Marc Gullo (marc.gullo@canopytech.ca).
       - company is matched from the sender's email domain against
         Company.email_domain; left blank if nothing matches, for a
         human to set later.
       - the sender is skipped entirely if it's the support mailbox
         itself, to avoid looping on our own notifications.
       - the sender gets a "we received your request" confirmation
         email, same as tickets submitted through the client portal -
         sent to the raw sender address, whether or not it matches an
         existing User account.
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
from datetime import datetime
from html.parser import HTMLParser
from zoneinfo import ZoneInfo

import httpx
from django.conf import settings
from django.core.cache import cache
from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.accounts.email_service import _TOKEN_CACHE_KEY, _get_token
from apps.accounts.models import AuditLog, User
from apps.companies.models import Company
from apps.tickets.models import EmailPollState, Message, Ticket
from apps.tickets.notifications import notify_ticket_confirmed, notify_ticket_created

logger = logging.getLogger(__name__)

_TICKET_RE = re.compile(r'\[T-(\d{4}-\d{5,6})\]', re.IGNORECASE)
_GRAPH_BASE = "https://graph.microsoft.com/v1.0"
_DEFAULT_ASSIGNEE_EMAIL = "marc.gullo@canopytech.ca"

# Launch gate: this app goes live Aug 3, 2026 12:01am Eastern. Nothing —
# not a single email, not the pre-existing unread backlog that piled up
# while Graph mailbox permissions were missing — should become a ticket
# before that instant. zoneinfo resolves this to the correct UTC offset
# for the date (EDT, UTC-4, since Eastern DST is in effect in August).
_LAUNCH_AT = datetime(2026, 8, 3, 0, 1, tzinfo=ZoneInfo("America/Toronto"))


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


def _get_unread_messages(token: str, mailbox: str, since=None) -> list[dict]:
    """Fetch unread messages, optionally only those received after `since`
    (a datetime). The `since` cutoff is what stops a backlog of old unread
    mail from being swept into tickets/replies on every poll."""
    filt = "isRead eq false"
    if since is not None:
        filt += f" and receivedDateTime gt {since.strftime('%Y-%m-%dT%H:%M:%SZ')}"
    url = (
        f"{_GRAPH_BASE}/users/{mailbox}/messages"
        f"?$filter={filt}"
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

        # Cutoff is captured before fetching, not after processing - if it
        # were set to "now" after the loop finishes, an email that arrives
        # in the gap between fetching and saving would fall before that
        # timestamp and never get picked up by a later poll.
        poll_started_at = timezone.now()

        if poll_started_at < _LAUNCH_AT:
            self.stdout.write(
                f"Before launch ({_LAUNCH_AT.isoformat()}) — not processing any mail yet."
            )
            return

        state, is_new_state = EmailPollState.objects.get_or_create(pk=1)
        if is_new_state or state.last_received_at is None or state.last_received_at < _LAUNCH_AT:
            # Either genuinely the first run ever, or the stored cutoff
            # predates launch (e.g. it was set back when the mailbox
            # permission was still broken/missing) - clamp to the launch
            # instant rather than "now", so the backlog that piled up before
            # launch is never swept in as tickets on the next poll.
            state.last_received_at = _LAUNCH_AT
            state.save(update_fields=["last_received_at"])
            self.stdout.write(
                "Cutoff established at launch time — not processing any "
                "pre-launch backlog. Future polls only see mail received "
                "after launch."
            )
            return

        cutoff = state.last_received_at

        try:
            token = _get_token()
            emails = _get_unread_messages(token, mailbox, since=cutoff)
        except httpx.HTTPStatusError as e:
            # A stale/bad cached token would otherwise keep getting reused
            # for up to 55 minutes (11 polls) before naturally expiring.
            if e.response.status_code in (401, 403):
                cache.delete(_TOKEN_CACHE_KEY)
            logger.error("Failed to fetch emails: %s", e)
            self.stderr.write(f"Failed to fetch emails: {e}")
            return
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

        state.last_received_at = poll_started_at
        state.save(update_fields=["last_received_at"])

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
            notify_ticket_confirmed(ticket, to_email=sender_email, to_name=sender_name or sender_email)
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
