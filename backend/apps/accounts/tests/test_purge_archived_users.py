from io import StringIO

from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from apps.accounts.models import AuditLog, User
from apps.companies.models import Company
from apps.tickets.models import Ticket


def make_client_user(email="client@example.com"):
    return User.objects.create_user(
        email=email, password="testpass123",
        first_name="Client", last_name="User",
        role=User.Role.CLIENT,
    )


class PurgeArchivedUsersTests(TestCase):
    def test_purges_users_past_retention_and_leaves_others(self):
        due = make_client_user("due@example.com")
        due.archive()
        due.purge_at = timezone.now() - timezone.timedelta(days=1)
        due.save(update_fields=["purge_at"])

        not_due = make_client_user("not_due@example.com")
        not_due.archive()  # purge_at is ~1 year out

        active = make_client_user("active@example.com")

        out = StringIO()
        call_command("purge_archived_users", stdout=out)

        self.assertFalse(User.objects.filter(email="due@example.com").exists())
        self.assertTrue(User.objects.filter(email="not_due@example.com").exists())
        self.assertTrue(User.objects.filter(email="active@example.com").exists())
        self.assertIn("Purged 1", out.getvalue())

    def test_purge_logs_audit_entry_and_preserves_tickets(self):
        company = Company.objects.create(name="Co")
        due = make_client_user("due2@example.com")
        ticket = Ticket.objects.create(
            company=company, created_by=due, subject="s", description="d",
        )
        due.archive()
        due.purge_at = timezone.now() - timezone.timedelta(days=1)
        due.save(update_fields=["purge_at"])

        call_command("purge_archived_users", stdout=StringIO())

        self.assertTrue(
            AuditLog.objects.filter(action=AuditLog.Action.USER_PURGE, target="due2@example.com").exists()
        )
        ticket.refresh_from_db()
        self.assertIsNone(ticket.created_by)
