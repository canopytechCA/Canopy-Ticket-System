"""
Microsoft OAuth2 Authorization Code Flow for SSO login.

Uses the 'common' endpoint so users from any M365 tenant can sign in.
On callback, the user must already have an account in this system —
no auto-provisioning. Email matching is case-insensitive.

The same Azure AD app registration used for Graph email sending works here.
You only need to add the callback URL as a Web redirect URI in the app registration:
  https://support.canopytech.ca/auth/microsoft/callback/
"""

import logging
import secrets
from urllib.parse import urlencode

import httpx
from django.conf import settings

logger = logging.getLogger(__name__)

_AUTHORIZE_URL = "https://login.microsoftonline.com/common/oauth2/v2.0/authorize"
_TOKEN_URL = "https://login.microsoftonline.com/common/oauth2/v2.0/token"
_GRAPH_ME_URL = "https://graph.microsoft.com/v1.0/me"
_SCOPE = "openid email profile User.Read"
_SESSION_STATE_KEY = "ms_oauth_state"


def _redirect_uri(request) -> str:
    return request.build_absolute_uri("/auth/microsoft/callback/")


def get_authorize_url(request) -> str:
    """Generate the Microsoft OAuth2 authorization URL and store state in session."""
    state = secrets.token_urlsafe(24)
    request.session[_SESSION_STATE_KEY] = state
    params = {
        "client_id": settings.AZURE_CLIENT_ID,
        "response_type": "code",
        "redirect_uri": _redirect_uri(request),
        "scope": _SCOPE,
        "state": state,
        "response_mode": "query",
        "prompt": "select_account",
    }
    return f"{_AUTHORIZE_URL}?{urlencode(params)}"


def verify_state(request, state: str) -> bool:
    expected = request.session.pop(_SESSION_STATE_KEY, None)
    return expected is not None and state == expected


def get_user_info(request, code: str) -> dict:
    """
    Exchange authorization code for tokens, then fetch user profile from Graph.
    Returns {'email': str, 'first_name': str, 'last_name': str, 'display_name': str}
    Raises RuntimeError on any failure.
    """
    try:
        token_resp = httpx.post(
            _TOKEN_URL,
            data={
                "client_id": settings.AZURE_CLIENT_ID,
                "client_secret": settings.AZURE_CLIENT_SECRET,
                "code": code,
                "redirect_uri": _redirect_uri(request),
                "grant_type": "authorization_code",
                "scope": _SCOPE,
            },
            timeout=15.0,
        )
        token_resp.raise_for_status()
    except httpx.HTTPStatusError as e:
        raise RuntimeError(f"Token exchange failed: {e.response.status_code} {e.response.text[:200]}")
    except httpx.RequestError as e:
        raise RuntimeError(f"Token exchange request error: {e}")

    access_token = token_resp.json().get("access_token")
    if not access_token:
        raise RuntimeError("No access_token in Microsoft response")

    try:
        me_resp = httpx.get(
            _GRAPH_ME_URL,
            headers={"Authorization": f"Bearer {access_token}"},
            params={"$select": "mail,userPrincipalName,givenName,surname,displayName"},
            timeout=10.0,
        )
        me_resp.raise_for_status()
    except Exception as e:
        raise RuntimeError(f"Graph /me request failed: {e}")

    me = me_resp.json()
    email = (me.get("mail") or me.get("userPrincipalName") or "").lower().strip()
    if not email:
        raise RuntimeError("Could not retrieve email from Microsoft profile")

    return {
        "email": email,
        "first_name": me.get("givenName", ""),
        "last_name": me.get("surname", ""),
        "display_name": me.get("displayName", ""),
    }
