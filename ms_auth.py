"""Microsoft OAuth2 (PKCE) + Graph API — admin sign-in for the server account.

Used by the Admin panel to sign in once and send automated emails.
The per-user draft flow was removed (Walmart IT requires admin consent
for Mail.ReadWrite which cannot be self-granted).
"""
import base64
import hashlib
import json
import os
import secrets
from datetime import datetime, timezone, timedelta

import httpx

# Walmart tenant
_WALMART_TENANT = "3cbcc3d3-094d-4006-9849-0d11d61f484d"
_AUTH_BASE  = f"https://login.microsoftonline.com/{_WALMART_TENANT}/oauth2/v2.0"
_GRAPH_ME   = "https://graph.microsoft.com/v1.0/me"
_GRAPH_MAIL = "https://graph.microsoft.com/v1.0/me/sendMail"
_SCOPES     = "openid profile email Mail.Send offline_access"

# In-memory PKCE state store  { state -> code_verifier }
_pending: dict[str, str] = {}

# In-memory token store — cleared on server restart
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
    """Exchange auth code for tokens. Returns account email."""
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
        return _session["access_token"]

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
