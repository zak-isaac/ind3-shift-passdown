"""Microsoft OAuth2 (PKCE) + Graph API email sender.

No passwords. No Azure portal admin access needed.
Just a free App Registration using any personal Microsoft account.

How to get a Client ID (one-time, 2 minutes):
  1. Go to https://portal.azure.com  (sign in with ANY Microsoft account —
     personal outlook.com/hotmail is fine, no Walmart IT needed)
  2. Azure Active Directory → App registrations → New registration
  3. Name: "IND3 Passdown"  |  Supported account types: "Any Azure AD + personal"
  4. Redirect URI → Web → http://<your-server-ip>:8765/admin/microsoft-callback
  5. Register → copy the Application (client) ID
  6. API permissions → Add → Microsoft Graph → Delegated → Mail.Send → Add
  7. Paste the Client ID into Admin → Email → Microsoft sign-in
"""
import base64
import hashlib
import json
import os
import secrets
from datetime import datetime, timezone, timedelta

import httpx

# common = accepts both personal Microsoft accounts AND work/school (Walmart) accounts
_AUTH_BASE  = "https://login.microsoftonline.com/common/oauth2/v2.0"
_GRAPH_ME   = "https://graph.microsoft.com/v1.0/me"
_GRAPH_MAIL = "https://graph.microsoft.com/v1.0/me/sendMail"
_SCOPES     = "openid profile email Mail.Send offline_access"
_SCOPES_DRAFT = "openid profile Mail.ReadWrite offline_access"

# In-memory PKCE state store  { state -> code_verifier }  — one entry per pending login
_pending: dict[str, str] = {}

# Per-user draft pending: { state -> {verifier, html_body, subject, to_addrs, expires} }
_draft_pending: dict[str, dict] = {}

# In-memory token store — cleared on server restart (no password ever on disk)
_session: dict = {"access_token": "", "refresh_token": "", "expires_at": "", "account": ""}


# ── PKCE ────────────────────────────────────────────────────────────────────────
def _pkce() -> tuple[str, str]:
    verifier  = secrets.token_urlsafe(64)
    digest    = hashlib.sha256(verifier.encode()).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return verifier, challenge


# ── Public API ──────────────────────────────────────────────────────────────────
def build_auth_url(client_id: str, redirect_uri: str) -> tuple[str, str]:
    """Return (auth_url, state). Call this to start the login redirect."""
    from urllib.parse import urlencode
    verifier, challenge = _pkce()
    state = secrets.token_urlsafe(16)
    _pending[state] = verifier
    params = urlencode({
        "client_id":             client_id,
        "response_type":         "code",
        "redirect_uri":          redirect_uri,
        "scope":                 _SCOPES,
        "state":                 state,
        "code_challenge":        challenge,
        "code_challenge_method": "S256",
        "prompt":                "select_account",
    })
    return f"{_AUTH_BASE}/authorize?{params}", state


async def handle_callback(client_id: str, code: str, state: str, redirect_uri: str) -> str:
    """Exchange auth code for tokens. Returns account email. Stores tokens in memory."""
    verifier = _pending.pop(state, None)
    if not verifier:
        raise ValueError("Login session expired or invalid — please try again.")

    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.post(f"{_AUTH_BASE}/token", data={
            "client_id":     client_id,
            "grant_type":    "authorization_code",
            "code":          code,
            "redirect_uri":  redirect_uri,
            "code_verifier": verifier,
            "scope":         _SCOPES,
        })
        r.raise_for_status()
        tok = r.json()

    # Grab email from /me
    account = ""
    async with httpx.AsyncClient(timeout=10) as c:
        me = await c.get(_GRAPH_ME, headers={"Authorization": f"Bearer {tok['access_token']}"})
        if me.is_success:
            d = me.json()
            account = d.get("mail") or d.get("userPrincipalName", "")

    expires_at = (
        datetime.now(timezone.utc) + timedelta(seconds=int(tok.get("expires_in", 3600)))
    ).isoformat()

    _session.update({
        "access_token":  tok["access_token"],
        "refresh_token": tok.get("refresh_token", ""),
        "expires_at":    expires_at,
        "account":       account,
    })
    return account


async def _ensure_token(client_id: str) -> str:
    """Return a valid access token, refreshing if needed."""
    if not _session["access_token"]:
        raise RuntimeError("Not signed in with Microsoft — go to Admin to sign in.")

    expires_at = datetime.fromisoformat(_session["expires_at"])
    if datetime.now(timezone.utc) < expires_at - timedelta(minutes=5):
        return _session["access_token"]   # still valid

    if not _session["refresh_token"]:
        _session.update({"access_token": "", "account": ""})
        raise RuntimeError("Session expired — please sign in again in Admin.")

    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.post(f"{_AUTH_BASE}/token", data={
            "client_id":     client_id,
            "grant_type":    "refresh_token",
            "refresh_token": _session["refresh_token"],
            "scope":         _SCOPES,
        })
        if not r.is_success:
            _session.update({"access_token": "", "account": ""})
            raise RuntimeError("Token refresh failed — please sign in again in Admin.")
        tok = r.json()

    expires_at = (
        datetime.now(timezone.utc) + timedelta(seconds=int(tok.get("expires_in", 3600)))
    ).isoformat()
    _session.update({
        "access_token":  tok["access_token"],
        "refresh_token": tok.get("refresh_token", _session["refresh_token"]),
        "expires_at":    expires_at,
    })
    return _session["access_token"]


async def send_email(
    client_id: str,
    to_addrs: list[str],
    subject: str,
    body: str,
    from_name: str = "IND3 Shift Passdown",
) -> None:
    """Send email via Microsoft Graph API using the stored OAuth session."""
    token   = await _ensure_token(client_id)
    account = _session.get("account", "")
    payload = {
        "message": {
            "subject": subject,
            "body":    {"contentType": "Text", "content": body},
            "from":    {"emailAddress": {"name": from_name, "address": account}},
            "toRecipients": [{"emailAddress": {"address": a}} for a in to_addrs],
        },
        "saveToSentItems": True,
    }
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.post(_GRAPH_MAIL, json=payload,
                         headers={"Authorization": f"Bearer {token}"})
        r.raise_for_status()


def sign_out() -> None:
    _session.update({"access_token": "", "refresh_token": "", "expires_at": "", "account": ""})


def signed_in_as() -> str:
    """Return account email if signed in, else empty string."""
    return _session.get("account", "") if _session.get("access_token") else ""


# ── Per-user draft creation (each lead uses their own Microsoft account) ────────

def build_draft_auth_url(
    client_id: str,
    redirect_uri: str,
    html_body: str,
    subject: str,
    to_addrs: str,
) -> str:
    """Start a per-user OAuth flow scoped to Mail.ReadWrite (draft creation).

    The email content is stored server-side keyed by state so it survives
    the OAuth redirect without bloating the URL.
    Returns the Microsoft login URL to open in a new tab.
    """
    from urllib.parse import urlencode
    from datetime import datetime, timezone, timedelta

    verifier, challenge = _pkce()
    state = secrets.token_urlsafe(24)

    # Lazy-expire old draft entries (>15 min)
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=15)
    expired = [k for k, v in _draft_pending.items()
               if datetime.fromisoformat(v["expires"]) < cutoff]
    for k in expired:
        _draft_pending.pop(k, None)

    _draft_pending[state] = {
        "verifier":  verifier,
        "html_body": html_body,
        "subject":   subject,
        "to_addrs":  to_addrs,
        "expires":   (datetime.now(timezone.utc) + timedelta(minutes=15)).isoformat(),
    }

    params = urlencode({
        "client_id":             client_id,
        "response_type":         "code",
        "redirect_uri":          redirect_uri,
        "scope":                 _SCOPES_DRAFT,
        "state":                 state,
        "code_challenge":        challenge,
        "code_challenge_method": "S256",
        "prompt":                "select_account",
    })
    return f"{_AUTH_BASE}/authorize?{params}"


async def create_user_draft(
    client_id: str,
    code: str,
    state: str,
    redirect_uri: str,
) -> str:
    """Exchange auth code for a per-user token, create a draft, return OWA draft URL.

    The token is used once and discarded — never stored server-side.
    """
    entry = _draft_pending.pop(state, None)
    if not entry:
        raise ValueError("Draft session expired or invalid — please try again.")

    # Exchange code → token (one-time, user-specific)
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.post(f"{_AUTH_BASE}/token", data={
            "client_id":     client_id,
            "grant_type":    "authorization_code",
            "code":          code,
            "redirect_uri":  redirect_uri,
            "code_verifier": entry["verifier"],
            "scope":         _SCOPES_DRAFT,
        })
        r.raise_for_status()
        token = r.json()["access_token"]

    # Build draft message
    to_list = [a.strip() for a in entry["to_addrs"].split(",") if a.strip()]
    payload = {
        "subject": entry["subject"],
        "body":    {"contentType": "HTML", "content": entry["html_body"]},
        "toRecipients": [{"emailAddress": {"address": a}} for a in to_list],
    }

    # Create draft via Graph API
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.post(
            "https://graph.microsoft.com/v1.0/me/messages",
            json=payload,
            headers={"Authorization": f"Bearer {token}"},
        )
        r.raise_for_status()
        draft = r.json()

    # Return the OWA compose deep-link for this specific draft
    draft_id  = draft.get("id", "")
    web_link  = draft.get("webLink", "")
    if web_link:
        return web_link
    # Fallback: generic OWA drafts folder
    return "https://outlook.office.com/mail/drafts"
