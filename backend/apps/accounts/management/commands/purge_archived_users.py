"""
purge_archived_users — permanently deletes archived user accounts whose
one-year retention period has elapsed.

Tickets, messages, and time entries created by a purged user are NOT
deleted: those FKs are on_delete=SET_NULL, so the business records survive
with the "who" attribution cleared. Only the User row itself (their login,
name, email) is removed.

Run manually:
  docker compose exec web python manage.py purge_archived_users

Runs automatically via the user-purge Docker service (once a day).
"""
import logging

from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.accounts.models import AuditLog, User

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Permanently delete archived users past their 1-year retention period"

    def handle(self, *args, **options):
        due = User.objects.filter(status=User.Status.ARCHIVED, purge_at__lte=timezone.now())

        count = 0
        for user in due:
            email = user.email
            AuditLog.objects.create(
                actor=None,
                action=AuditLog.Action.USER_PURGE,
                target=email,
                detail=f"archived {user.archived_at:%Y-%m-%d}, purged after retention period",
                ip_address=None,
            )
            user.delete()
            count += 1
            logger.info("Purged archived user %s", email)

        self.stdout.write(self.style.SUCCESS(f"Purged {count} archived user(s)."))
