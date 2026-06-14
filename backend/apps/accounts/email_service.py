"""
Microsoft Graph API email sender.

Requires an Azure AD app registration with the Mail.Send *application*
permission (not delegated) granted and admin-consented.

Token is cached for 55 minutes (tokens expire in 60 min).
All failures are logged and swallowed — email errors never break a request.
"""

import logging

import httpx
from django.conf import settings
from django.core.cache import cache

logger = logging.getLogger(__name__)

_TOKEN_CACHE_KEY = "graph_access_token"
_TOKEN_URL = "https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token"
_SEND_URL = "https://graph.microsoft.com/v1.0/users/{from_email}/sendMail"


def _configured() -> bool:
    return all([
        getattr(settings, "GRAPH_CLIENT_ID", ""),
        getattr(settings, "GRAPH_CLIENT_SECRET", ""),
        getattr(settings, "GRAPH_TENANT_ID", ""),
    ])


def _get_token() -> str:
    token = cache.get(_TOKEN_CACHE_KEY)
    if token:
        return token

    resp = httpx.post(
        _TOKEN_URL.format(tenant_id=settings.GRAPH_TENANT_ID),
        data={
            "grant_type": "client_credentials",
            "client_id": settings.GRAPH_CLIENT_ID,
            "client_secret": settings.GRAPH_CLIENT_SECRET,
            "scope": "https://graph.microsoft.com/.default",
        },
        timeout=10.0,
    )
    resp.raise_for_status()
    data = resp.json()
    token = data["access_token"]
    expires_in = int(data.get("expires_in", 3600))
    cache.set(_TOKEN_CACHE_KEY, token, timeout=expires_in - 60)
    return token


def send_email(
    to_email: str,
    to_name: str,
    subject: str,
    html_body: str,
    from_name: str = "Canopy Support",
) -> bool:
    """
    Send an HTML email via Microsoft Graph.
    Returns True on success, False on any failure.
    Never raises — all errors are logged.
    """
    if not _configured():
        logger.warning(
            "Email skipped — GRAPH_CLIENT_ID/SECRET/TENANT_ID not configured. "
            "Would have sent '%s' to %s", subject, to_email
        )
        return False

    if not to_email:
        logger.warning("Email skipped — no recipient address for: %s", subject)
        return False

    from_email = settings.SUPPORT_EMAIL

    try:
        token = _get_token()
        resp = httpx.post(
            _SEND_URL.format(from_email=from_email),
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            json={
                "message": {
                    "subject": subject,
                    "body": {"contentType": "HTML", "content": html_body},
                    "from": {
                        "emailAddress": {"address": from_email, "name": from_name}
                    },
                    "toRecipients": [
                        {"emailAddress": {"address": to_email, "name": to_name}}
                    ],
                }
            },
            timeout=15.0,
        )
        resp.raise_for_status()
        logger.info("Email sent — to=%s subject=%s", to_email, subject)
        return True

    except httpx.HTTPStatusError as e:
        # Token may be stale — clear cache so next call refreshes
        if e.response.status_code in (401, 403):
            cache.delete(_TOKEN_CACHE_KEY)
        logger.error(
            "Graph API error sending to %s: %s %s",
            to_email, e.response.status_code, e.response.text[:200],
        )
        return False
    except Exception as e:
        logger.error("Unexpected error sending email to %s: %s", to_email, e)
        return False
