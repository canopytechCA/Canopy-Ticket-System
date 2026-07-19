from unittest.mock import patch

from django.core.cache import cache
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from apps.accounts.models import AuditLog, User
from apps.companies.models import Company


def make_tech(email="tech@example.com", password="testpass123", is_superuser=False):
    return User.objects.create_user(
        email=email, password=password,
        first_name="Tech", last_name="User",
        role=User.Role.TECH,
        is_superuser=is_superuser,
    )


def make_client_user(email="client@example.com", password="testpass123", company=None):
    return User.objects.create_user(
        email=email, password=password,
        first_name="Client", last_name="User",
        role=User.Role.CLIENT,
        company=company,
    )


class _MarkLimitedMiddleware:
    """Test-only middleware that pre-sets request.limited=True before the view."""
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.limited = True
        return self.get_response(request)


_BASE_MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django_htmx.middleware.HtmxMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]


class LoginViewTests(TestCase):
    def setUp(self):
        cache.clear()  # django-ratelimit's counter lives in cache, not the DB — isolate per test
        self.c = Client()
        self.url = reverse("accounts:login")

    def test_get_returns_200(self):
        r = self.c.get(self.url)
        self.assertEqual(r.status_code, 200)

    def test_valid_tech_login_redirects_to_tech_portal(self):
        make_tech()
        r = self.c.post(self.url, {"username": "tech@example.com", "password": "testpass123"})
        self.assertRedirects(r, "/tech/", fetch_redirect_response=False)

    def test_valid_client_login_redirects_to_client_portal(self):
        co = Company.objects.create(name="Co")
        make_client_user(company=co)
        r = self.c.post(self.url, {"username": "client@example.com", "password": "testpass123"})
        self.assertRedirects(r, "/portal/", fetch_redirect_response=False)

    def test_wrong_password_returns_200(self):
        make_tech()
        r = self.c.post(self.url, {"username": "tech@example.com", "password": "wrongpass"})
        self.assertEqual(r.status_code, 200)
        self.assertFalse(r.wsgi_request.user.is_authenticated)

    def test_unknown_email_returns_200(self):
        r = self.c.post(self.url, {"username": "nobody@example.com", "password": "anything"})
        self.assertEqual(r.status_code, 200)

    @override_settings(MIDDLEWARE=_BASE_MIDDLEWARE + ["apps.accounts.tests.test_views._MarkLimitedMiddleware"])
    def test_rate_limited_request_shows_friendly_error(self):
        """When request.limited is True the view shows a rate limit message, not a 403."""
        make_tech()
        r = self.c.post(self.url, {"username": "tech@example.com", "password": "testpass123"})
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Too many login attempts")

    def test_blocked_user_correct_password_shows_generic_error(self):
        """A blocked account must not leak its status, even to someone with the right password."""
        user = make_client_user()
        user.block()
        r = self.c.post(self.url, {"username": "client@example.com", "password": "testpass123"})
        self.assertEqual(r.status_code, 200)
        self.assertNotContains(r, "647-478-8449")
        self.assertFalse(r.wsgi_request.user.is_authenticated)

    def test_blocked_user_wrong_password_shows_generic_error(self):
        """A blocked account must not leak its status to someone without the password."""
        user = make_client_user()
        user.block()
        r = self.c.post(self.url, {"username": "client@example.com", "password": "wrongpass"})
        self.assertEqual(r.status_code, 200)
        self.assertNotContains(r, "647-478-8449")
        self.assertFalse(r.wsgi_request.user.is_authenticated)

    def test_blocked_and_wrong_password_show_identical_error(self):
        """The whole point: a blocked account's error text must be byte-identical
        to a plain wrong-password error, so login responses can't be used to
        distinguish 'blocked' from 'wrong password' from 'no such account'."""
        make_client_user()
        User.objects.get(email="client@example.com").block()
        blocked_r = self.c.post(self.url, {"username": "client@example.com", "password": "testpass123"})

        make_client_user(email="other@example.com")
        wrong_pw_r = self.c.post(self.url, {"username": "other@example.com", "password": "wrongpass"})

        self.assertEqual(
            blocked_r.context["form"].non_field_errors(),
            wrong_pw_r.context["form"].non_field_errors(),
        )

    def test_archived_user_cannot_log_in(self):
        user = make_client_user()
        user.archive()
        r = self.c.post(self.url, {"username": "client@example.com", "password": "testpass123"})
        self.assertEqual(r.status_code, 200)
        self.assertFalse(r.wsgi_request.user.is_authenticated)
        self.assertNotContains(r, "647-478-8449")


class LogoutViewTests(TestCase):
    def setUp(self):
        self.c = Client()

    def test_logout_redirects_to_login(self):
        tech = make_tech()
        self.c.force_login(tech)
        r = self.c.post(reverse("accounts:logout"))
        self.assertRedirects(r, reverse("accounts:login"), fetch_redirect_response=False)

    def test_already_logged_out_redirects(self):
        r = self.c.post(reverse("accounts:logout"))
        self.assertEqual(r.status_code, 302)


class TechUserBlockArchiveTests(TestCase):
    def setUp(self):
        self.superuser = make_tech(email="super@example.com", is_superuser=True)
        self.regular_tech = make_tech(email="regular@example.com")
        self.target = make_client_user()
        self.detail_url = reverse("tickets:tech_user_detail", kwargs={"pk": self.target.pk})
        self.archive_url = reverse("tickets:tech_user_archive", kwargs={"pk": self.target.pk})

    def test_non_superuser_cannot_block(self):
        c = Client()
        c.force_login(self.regular_tech)
        r = c.post(self.detail_url, {"action": "block"})
        self.assertEqual(r.status_code, 403)
        self.target.refresh_from_db()
        self.assertEqual(self.target.status, User.Status.ACTIVE)

    def test_non_superuser_cannot_archive(self):
        c = Client()
        c.force_login(self.regular_tech)
        r = c.get(self.archive_url)
        self.assertEqual(r.status_code, 403)

    def test_superuser_can_block_and_unblock(self):
        c = Client()
        c.force_login(self.superuser)
        c.post(self.detail_url, {"action": "block"})
        self.target.refresh_from_db()
        self.assertEqual(self.target.status, User.Status.BLOCKED)
        self.assertTrue(
            AuditLog.objects.filter(action=AuditLog.Action.USER_BLOCK, target=self.target.email).exists()
        )

        c.post(self.detail_url, {"action": "unblock"})
        self.target.refresh_from_db()
        self.assertEqual(self.target.status, User.Status.ACTIVE)
        self.assertTrue(
            AuditLog.objects.filter(action=AuditLog.Action.USER_UNBLOCK, target=self.target.email).exists()
        )

    def test_superuser_can_archive_via_confirm_page(self):
        c = Client()
        c.force_login(self.superuser)
        get_r = c.get(self.archive_url)
        self.assertEqual(get_r.status_code, 200)

        post_r = c.post(self.archive_url)
        self.assertRedirects(post_r, self.detail_url)
        self.target.refresh_from_db()
        self.assertEqual(self.target.status, User.Status.ARCHIVED)
        self.assertIsNotNone(self.target.purge_at)
        self.assertTrue(
            AuditLog.objects.filter(action=AuditLog.Action.USER_ARCHIVE, target=self.target.email).exists()
        )

    def test_superuser_can_restore_archived_user(self):
        self.target.archive()
        c = Client()
        c.force_login(self.superuser)
        c.post(self.detail_url, {"action": "restore"})
        self.target.refresh_from_db()
        self.assertEqual(self.target.status, User.Status.ACTIVE)
        self.assertIsNone(self.target.purge_at)

    def test_superuser_cannot_block_own_account(self):
        c = Client()
        c.force_login(self.superuser)
        own_url = reverse("tickets:tech_user_detail", kwargs={"pk": self.superuser.pk})
        c.post(own_url, {"action": "block"})
        self.superuser.refresh_from_db()
        self.assertEqual(self.superuser.status, User.Status.ACTIVE)

    def test_superuser_cannot_archive_own_account(self):
        c = Client()
        c.force_login(self.superuser)
        own_archive_url = reverse("tickets:tech_user_archive", kwargs={"pk": self.superuser.pk})
        c.post(own_archive_url)
        self.superuser.refresh_from_db()
        self.assertEqual(self.superuser.status, User.Status.ACTIVE)


class MicrosoftCallbackBlockedArchivedTests(TestCase):
    """The SSO callback looked up users with is_active=True only, which for a
    blocked/archived user (is_active=False) fell through to auto-provisioning
    and crashed on the unique email constraint. Covers the fix."""

    def setUp(self):
        self.co = Company.objects.create(name="Co", email_domain="example.com")
        self.c = Client()

    def _get_with_state(self, email):
        session = self.c.session
        session["ms_oauth_state"] = "state123"
        session.save()
        with patch("apps.accounts.microsoft_auth.verify_state", return_value=True), \
             patch("apps.accounts.microsoft_auth.get_user_info", return_value={
                 "email": email, "first_name": "A", "last_name": "B", "display_name": "A B",
             }):
            return self.c.get(reverse("accounts:microsoft_callback"), {"code": "abc", "state": "state123"})

    def test_blocked_user_sso_shows_support_message_no_crash(self):
        user = make_client_user(email="blocked@example.com", company=self.co)
        user.block()
        r = self._get_with_state("blocked@example.com")
        self.assertEqual(r.status_code, 302)
        self.assertEqual(User.objects.filter(email__iexact="blocked@example.com").count(), 1)

    def test_archived_user_sso_does_not_crash_or_duplicate(self):
        user = make_client_user(email="archived@example.com", company=self.co)
        user.archive()
        r = self._get_with_state("archived@example.com")
        self.assertEqual(r.status_code, 302)
        self.assertEqual(User.objects.filter(email__iexact="archived@example.com").count(), 1)
