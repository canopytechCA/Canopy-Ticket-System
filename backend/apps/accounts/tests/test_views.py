from django.test import Client, TestCase, override_settings
from django.urls import reverse

from apps.accounts.models import User
from apps.companies.models import Company


def make_tech(email="tech@example.com", password="testpass123"):
    return User.objects.create_user(
        email=email, password=password,
        first_name="Tech", last_name="User",
        role=User.Role.TECH,
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
