from .base import *

DEBUG = True
ALLOWED_HOSTS = ["*"]

# Use SQLite locally for easy dev without Docker
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
    }
}

SESSION_COOKIE_SECURE = False
CSRF_COOKIE_SECURE = False
