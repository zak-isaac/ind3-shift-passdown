"""Microsoft OAuth2 + Graph API email sender.

Flow:
  1. Admin clicks "Sign in with Microsoft"
  2. Browser redirects to Microsoft SSO (Walmart's Azure AD)
  3. Microsoft redirects back to /admin/microsoft-callback with an auth code
  4. We exchange the code for access + refresh tokens (stored in DB)
  5. Emails sent via Graph API — no passwords ever stored

Requires an Azure AD App Registration:
  - Platform: Web  |  Redirect URI: http://<host>/admin/microsoft-callback
  - Delegated permissions: Mail.Send
  - Put the Application (client) ID in Admin → Microsoft settings
"""
import base64
import hashlib
import json
import os
import secrets
from datetime import datetime, timezone, timedelta

import httpx

# Walmart's Azure AD tenant
TENANT_ID = "3cbcc3d3-094d-4006-9849-0d11d61f484d"
_AUTHORITY = f"https://login.microsoftonline.com/{TENANT_ID}/oauth2/v2.0"
_GRAPH     = "https://graph.microsoft.com/v1.0"
_SCOPES    = "https://graph.microsoft.com/Mail.Send offline_access openid profile"

# In-memory PKCE state store  {state: code_verifier}  — cleared after use
_pending: dict[str, str] = {}


# ── PKCE helpers ───────────────────────────────────────────────────────────────
def _pkce_pair() -> tuple[str, str]:
    """Return (code_verifier, code_challenge) for S256 PKCE."""
    verifier   = secrets.token_urlsafe(64)
    digest     = hashlib.sha256(verifier.encode()).digest()
    challenge  = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return verifier, challenge


# ── Public API ─────────────────────────────────────────────────────────────────
def build_auth_url(client_id: str, redirect_uri: str) -> tuple[str, str]:
    """Return (auth_url, state).  Stores code_verifier internally for callback."""
    verifier, challenge = _pkce_pair()
    state = secrets.token_urlsafe(16)
    _pending[state] = verifier

    from urllib.parse import urlencode
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
    return f"{_AUTHORITY}/authorize?{params}", state


async def exchange_code(
    client_id: str, code: str, state: str, redirect_uri: str
) -> dict:
    """Exchange auth code for tokens.  Returns the full token response dict."""
    verifier = _pending.pop(state, None)
    if not verifier:
        raise ValueError("Unknown or expired OAuth state — please try again.")

    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.post(f"{_AUTHORITY}/token", data={
            "client_id":     client_id,
            "grant_type":    "authorization_code",
            "code":          code,
            "redirect_uri":  redirect_uri,
            "code_verifier": verifier,
            "scope":         _SCOPES,
        })
        r.raise_for_status()
        return r.json()


async def _refresh(client_id: str, refresh_tok: str) -> dict:
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.post(f"{_AUTHORITY}/token", data={
            "client_id":     client_id,
            "grant_type":    "refresh_token",
            "refresh_token": refresh_tok,
            "scope":         _SCOPES,
        })
        r.raise_for_status()
        return r.json()


def _token_record(raw: dict, existing_refresh: str = "") -> dict:
    """Normalise a token response into our DB record format."""
    expires_at = (
        datetime.now(timezone.utc) + timedelta(seconds=int(raw.get("expires_in", 3600)))
    ).isoformat()
    return {
        "access_token":  raw["access_token"],
        "refresh_token": raw.get("refresh_token") or existing_refresh,
        "expires_at":    expires_at,
        "account":       raw.get("id_token_claims", {}).get("preferred_username", ""),
    }


async def send_email(
    db_mod,           # db module — used to read/write stored tokens
    to_addrs: list[str],
    subject: str,
    body: str,
) -> str:
    """Send email via Graph API.  Returns the 'from' address on success.

    Raises RuntimeError if not connected or token refresh fails.
    """
    client_id = (os.environ.get("PASSDOWN_MS_CLIENT_ID") or "").strip()
    client_id = client_id or db_mod.get_setting("ms_client_id", "").strip()
    raw_rec   = db_mod.get_setting("ms_tokens", "")
    if not client_id or not raw_rec:
        raise RuntimeError("Microsoft account not connected — go to Admin to sign in.")

    rec = json.loads(raw_rec)

    # Refresh if within 5 minutes of expiry
    expires_at = datetime.fromisoformat(rec["expires_at"])
    if datetime.now(timezone.utc) >= expires_at - timedelta(minutes=5):
        try:
            refreshed = await _refresh(client_id, rec["refresh_token"])
            rec = _token_record(refreshed, rec["refresh_token"])
            db_mod.set_setting("ms_tokens", json.dumps(rec))
        except Exception as exc:
            db_mod.set_setting("ms_tokens", "")   # force re-auth
            raise RuntimeError(f"Token refresh failed — please reconnect: {exc}") from exc

    payload = {
        "message": {
            "subject": subject,
            "body": {"contentType": "Text", "content": body},
            "toRecipients": [{"emailAddress": {"address": a}} for a in to_addrs],
        },
        "saveToSentItems": True,
    }
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.post(
            f"{_GRAPH}/me/sendMail",
            json=payload,
            headers={"Authorization": f"Bearer {rec['access_token']}"},
        )
        if r.status_code == 401:
            db_mod.set_setting("ms_tokens", "")
            raise RuntimeError("Microsoft token expired — please reconnect in Admin.")
        r.raise_for_status()

    return rec.get("account", "")


def connection_status(db_mod) -> dict:
    """Return dict with 'connected' bool and 'account' string for the admin UI."""
    raw = db_mod.get_setting("ms_tokens", "")
    client_id = (os.environ.get("PASSDOWN_MS_CLIENT_ID") or "").strip()
    client_id = client_id or db_mod.get_setting("ms_client_id", "").strip()
    if not raw or not client_id:
        return {"connected": False, "account": "", "client_id": client_id}
    rec = json.loads(raw)
    return {"connected": True, "account": rec.get("account", ""), "client_id": client_id}
