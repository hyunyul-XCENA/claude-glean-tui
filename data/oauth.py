"""OAuth PKCE authentication and Anthropic usage API client.

Implements the same OAuth flow as claude-usage-mini:
1. PKCE code verifier/challenge generation
2. Browser-based authorization at claude.ai
3. Token exchange at platform.claude.com
4. Usage data from api.anthropic.com/api/oauth/usage

Credentials are stored in ~/.claude-glean-tui/auth.json with 0600 permissions.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import time
import webbrowser
from pathlib import Path
from typing import Any, Dict, Optional, Tuple
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

# ── OAuth endpoints ──────────────────────────────────────────────────────────

_CLIENT_ID = "9d1c250a-e61b-44d9-88ed-5944d1962f5e"
_REDIRECT_URI = "https://platform.claude.com/oauth/code/callback"
_SCOPES = "user:profile user:inference"

_AUTHORIZE_URL = "https://claude.ai/oauth/authorize"
_TOKEN_URL = "https://platform.claude.com/v1/oauth/token"
_USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
_USERINFO_URL = "https://api.anthropic.com/api/oauth/userinfo"
_USER_AGENT = "claude-glean-tui/1.0"

# ── Credential storage ──────────────────────────────────────────────────────
# Priority:
#   1. CLAUDE_OAUTH_TOKEN env var (user-managed, highest priority)
#   2. ~/.config/claude-glean-tui/token file (chmod 600, app-managed)
#
# The 'a' key OAuth flow saves to both env (current session) and file (persist).

_TOKEN_DIR = Path(os.environ.get(
    "XDG_CONFIG_HOME", str(Path.home() / ".config"),
)) / "claude-glean-tui"
_TOKEN_FILE = _TOKEN_DIR / "token"


def _save_credentials(creds: Dict[str, Any]) -> None:
    """Save full credential dict to env var (current session) + file (persist)."""
    token = creds.get("access_token", "")
    if not token:
        return
    # Current session
    os.environ["CLAUDE_OAUTH_TOKEN"] = token
    # Persist full creds (including refresh_token, expires_at) as JSON
    try:
        _TOKEN_DIR.mkdir(parents=True, exist_ok=True)
        _TOKEN_FILE.write_text(json.dumps({
            "access_token": token,
            "refresh_token": creds.get("refresh_token", ""),
            "expires_at": creds.get("expires_at", 0),
            "scopes": creds.get("scopes", []),
        }), encoding="utf-8")
        os.chmod(_TOKEN_FILE, 0o600)
    except OSError:
        pass


def _load_credentials() -> Optional[Dict[str, Any]]:
    # 1. Env var (highest priority — no refresh metadata)
    token = os.environ.get("CLAUDE_OAUTH_TOKEN", "").strip()
    if token:
        return {"access_token": token, "refresh_token": "", "expires_at": 0, "scopes": []}
    # 2. Token file (JSON with full metadata)
    try:
        if _TOKEN_FILE.is_file():
            raw = _TOKEN_FILE.read_text(encoding="utf-8").strip()
            if raw.startswith("{"):
                data = json.loads(raw)
                if isinstance(data, dict) and data.get("access_token"):
                    return data
            elif raw:
                # Legacy: plain token string
                return {"access_token": raw, "refresh_token": "", "expires_at": 0, "scopes": []}
    except (OSError, json.JSONDecodeError):
        pass
    return None


def delete_credentials() -> None:
    """Remove token from file and current env (sign out)."""
    try:
        _TOKEN_FILE.unlink(missing_ok=True)
    except OSError:
        pass
    os.environ.pop("CLAUDE_OAUTH_TOKEN", None)


def is_authenticated() -> bool:
    """Check if valid credentials exist."""
    return _load_credentials() is not None


# ── PKCE helpers ─────────────────────────────────────────────────────────────

def _generate_code_verifier() -> str:
    return _base64url(secrets.token_bytes(32))


def _generate_code_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return _base64url(digest)


def _base64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


# ── OAuth flow ───────────────────────────────────────────────────────────────

# Module-level state for in-progress OAuth flow
_pending_verifier: Optional[str] = None
_pending_state: Optional[str] = None


def start_oauth_flow() -> str:
    """Open browser for OAuth authorization. Returns the authorize URL."""
    global _pending_verifier, _pending_state

    verifier = _generate_code_verifier()
    challenge = _generate_code_challenge(verifier)
    state = _generate_code_verifier()

    _pending_verifier = verifier
    _pending_state = state

    params = (
        f"?code=true"
        f"&client_id={_CLIENT_ID}"
        f"&response_type=code"
        f"&redirect_uri={_REDIRECT_URI}"
        f"&scope={_SCOPES.replace(' ', '%20')}"
        f"&code_challenge={challenge}"
        f"&code_challenge_method=S256"
        f"&state={state}"
    )
    url = _AUTHORIZE_URL + params
    webbrowser.open(url)
    return url


def complete_oauth_flow(raw_code: str) -> Dict[str, Any]:
    """Exchange authorization code for tokens.

    Args:
        raw_code: The code from the callback, optionally with ``#state`` suffix.

    Returns:
        ``{"ok": True}`` on success, ``{"error": str}`` on failure.
    """
    global _pending_verifier, _pending_state

    if not _pending_verifier:
        return {"error": "No OAuth flow in progress. Press 'a' first."}

    # Parse code#state format
    parts = raw_code.strip().split("#", 1)
    code = parts[0]
    if len(parts) > 1 and _pending_state:
        if parts[1] != _pending_state:
            _pending_verifier = None
            _pending_state = None
            return {"error": "OAuth state mismatch."}

    body = json.dumps({
        "grant_type": "authorization_code",
        "code": code,
        "state": _pending_state or "",
        "client_id": _CLIENT_ID,
        "redirect_uri": _REDIRECT_URI,
        "code_verifier": _pending_verifier,
    }).encode("utf-8")

    req = Request(
        _TOKEN_URL,
        data=body,
        headers={
            "Content-Type": "application/json",
            "User-Agent": _USER_AGENT,
        },
        method="POST",
    )

    try:
        with urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except HTTPError as e:
        _pending_verifier = None
        _pending_state = None
        detail = ""
        try:
            detail = e.read().decode("utf-8", errors="replace")[:200]
        except Exception:
            pass
        return {"error": f"Token exchange failed: HTTP {e.code} {detail}"}
    except (URLError, OSError) as e:
        _pending_verifier = None
        _pending_state = None
        return {"error": f"Network error: {e}"}

    access_token = data.get("access_token", "")
    if not access_token:
        _pending_verifier = None
        _pending_state = None
        return {"error": "No access token in response."}

    expires_in = data.get("expires_in", 3600)
    creds = {
        "access_token": access_token,
        "refresh_token": data.get("refresh_token", ""),
        "expires_at": time.time() + (expires_in if isinstance(expires_in, (int, float)) else 3600),
        "scopes": (data.get("scope", "") or _SCOPES).split(),
    }
    _save_credentials(creds)
    _pending_verifier = None
    _pending_state = None
    return {"ok": True, "access_token": access_token}


# ── Token refresh ────────────────────────────────────────────────────────────

def _refresh_token() -> bool:
    """Refresh the access token using the stored refresh token. Returns True on success."""
    creds = _load_credentials()
    if not creds or not creds.get("refresh_token"):
        return False

    body = json.dumps({
        "grant_type": "refresh_token",
        "refresh_token": creds["refresh_token"],
        "client_id": _CLIENT_ID,
    }).encode("utf-8")

    req = Request(
        _TOKEN_URL,
        data=body,
        headers={"Content-Type": "application/json", "User-Agent": _USER_AGENT},
        method="POST",
    )

    try:
        with urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except (HTTPError, URLError, OSError):
        return False

    new_token = data.get("access_token", "")
    if not new_token:
        return False

    expires_in = data.get("expires_in", 3600)
    creds["access_token"] = new_token
    if data.get("refresh_token"):
        creds["refresh_token"] = data["refresh_token"]
    creds["expires_at"] = time.time() + (expires_in if isinstance(expires_in, (int, float)) else 3600)
    _save_credentials(creds)
    return True


def _needs_refresh() -> bool:
    # Env var tokens don't expire (user manages them)
    if os.environ.get("CLAUDE_OAUTH_TOKEN", "").strip():
        return False
    creds = _load_credentials()
    if not creds:
        return False
    expires_at = creds.get("expires_at", 0)
    if expires_at == 0:
        return False  # no expiry info
    return time.time() > (expires_at - 60)


# ── API calls ────────────────────────────────────────────────────────────────

def _authorized_request(url: str) -> Optional[Dict[str, Any]]:
    """Make an authorized GET request. Handles token refresh automatically."""
    if _needs_refresh():
        if not _refresh_token():
            return None  # don't delete user's env var token

    creds = _load_credentials()
    if not creds:
        return None

    req = Request(
        url,
        headers={
            "Authorization": f"Bearer {creds['access_token']}",
            "anthropic-beta": "oauth-2025-04-20",
            "User-Agent": _USER_AGENT,
        },
    )

    try:
        with urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except HTTPError as e:
        if e.code == 401:
            # Try refresh once
            if _refresh_token():
                creds = _load_credentials()
                if creds:
                    req.remove_header("Authorization")
                    req.add_header("Authorization", f"Bearer {creds['access_token']}")
                    try:
                        with urlopen(req, timeout=15) as resp:
                            return json.loads(resp.read().decode("utf-8"))
                    except (HTTPError, URLError, OSError):
                        pass
            # Don't delete user's env var — just return None to fallback
        return None
    except (URLError, OSError):
        return None


def fetch_usage() -> Optional[Dict[str, Any]]:
    """Fetch usage data from Anthropic API.

    Returns parsed response::

        {
            "five_hour": {"utilization": 45.2, "resets_at": "..."},
            "seven_day": {"utilization": 23.1, "resets_at": "..."},
            "seven_day_opus": {...},
            "seven_day_sonnet": {...},
            "extra_usage": {"is_enabled": true, "used_credits": 450, "monthly_limit": 5000},
        }

    Returns None if not authenticated or request fails.
    """
    return _authorized_request(_USAGE_URL)


def fetch_userinfo() -> Optional[Dict[str, Any]]:
    """Fetch user profile (email, name) from Anthropic API."""
    return _authorized_request(_USERINFO_URL)
