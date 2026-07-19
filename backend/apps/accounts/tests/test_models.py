from django.test import TestCase
from django.utils import timezone

from apps.accounts.models import ARCHIVE_RETENTION_DAYS, User


def make_user(email="user@example.com"):
    return User.objects.create_user(
        email=email, password="testpass123",
        first_name="Test", last_name="User",
        role=User.Role.CLIENT,
    )


class UserBlockTests(TestCase):
    def test_block_sets_status_and_deactivates(self):
        user = make_user()
        user.block()
        user.refresh_from_db()
        self.assertEqual(user.status, User.Status.BLOCKED)
        self.assertFalse(user.is_active)
        self.assertIsNotNone(user.blocked_at)
        self.assertTrue(user.is_blocked)

    def test_unblock_restores_active(self):
        user = make_user()
        user.block()
        user.unblock()
        user.refresh_from_db()
        self.assertEqual(user.status, User.Status.ACTIVE)
        self.assertTrue(user.is_active)
        self.assertIsNone(user.blocked_at)


class UserArchiveTests(TestCase):
    def test_archive_sets_status_and_purge_date(self):
        user = make_user()
        before = timezone.now()
        user.archive()
        user.refresh_from_db()
        self.assertEqual(user.status, User.Status.ARCHIVED)
        self.assertFalse(user.is_active)
        self.assertIsNotNone(user.archived_at)
        self.assertTrue(user.is_archived)
        expected_purge = before + timezone.timedelta(days=ARCHIVE_RETENTION_DAYS)
        self.assertAlmostEqual(user.purge_at, expected_purge, delta=timezone.timedelta(seconds=5))

    def test_restore_clears_archive_fields(self):
        user = make_user()
        user.archive()
        user.restore()
        user.refresh_from_db()
        self.assertEqual(user.status, User.Status.ACTIVE)
        self.assertTrue(user.is_active)
        self.assertIsNone(user.archived_at)
        self.assertIsNone(user.purge_at)

    def test_archive_clears_any_prior_block(self):
        user = make_user()
        user.block()
        user.archive()
        user.refresh_from_db()
        self.assertEqual(user.status, User.Status.ARCHIVED)
        self.assertIsNone(user.blocked_at)
