"""Authentication and security utilities for Control Center.

JWT secret management, password hashing (PBKDF2-HMAC-SHA256), login throttle,
account/entity expiry, session tokens, OAuth email allowlisting, password
complexity rules, and the Flask-request helpers (current_user, is_management,
require_admin). No Flask app object lives here - only per-request helpers that
read from flask.g / flask.request.
"""
import hashlib
import hmac
import os
import secrets
import threading
import time
from datetime import date, datetime
from urllib.parse import urlencode

import jwt
from flask import g, redirect, request

import config
from config import addon_options, INGRESS_PORT, OAUTH_REDIRECT_BASE
from errors import ApiError
from store import ICON_DIR, _load_settings, load_users, save_users
from user import date_expired, parse_date


# --- JWT secret management ------------------------------------------------
# An explicitly configured secret (add-on option or JWT_SECRET env) always
# wins. Otherwise we generate a random secret once and persist it to /data, so
# an install that never set one is NOT signing sessions with the guessable
# default shipped in config.yaml (which would let anyone forge a session).
JWT_SECRET_FILE = ICON_DIR / ".jwt_secret"
_KNOWN_DEFAULT_SECRETS = {
    "change-me-to-a-long-random-string",
    "dev-secret-change-me",
    "change-me",
}


def _resolve_jwt_secret():
    explicit = addon_options.get("jwt_secret") or os.environ.get("JWT_SECRET")
    if explicit and explicit not in _KNOWN_DEFAULT_SECRETS:
        return explicit, "config"
    try:
        existing = JWT_SECRET_FILE.read_text().strip()
        if existing:
            return existing, "managed"
    except OSError:
        pass
    generated = secrets.token_urlsafe(48)
    try:
        JWT_SECRET_FILE.parent.mkdir(parents=True, exist_ok=True)
        JWT_SECRET_FILE.write_text(generated)
    except OSError:
        pass  # in-memory fallback: still safe, just won't survive a restart
    return generated, "managed"


JWT_SECRET, JWT_SECRET_SOURCE = _resolve_jwt_secret()


def regenerate_jwt_secret():
    """Rotate the managed secret (invalidates all sessions). No-op when the
    secret is pinned via config/env."""
    global JWT_SECRET, JWT_SECRET_SOURCE
    if JWT_SECRET_SOURCE == "config":
        return False
    new_secret = secrets.token_urlsafe(48)
    JWT_SECRET_FILE.parent.mkdir(parents=True, exist_ok=True)
    JWT_SECRET_FILE.write_text(new_secret)
    JWT_SECRET = new_secret
    JWT_SECRET_SOURCE = "managed"
    return True


# --- Passwords ------------------------------------------------------------
# Stored hashed with PBKDF2-HMAC-SHA256 (stdlib - no extra dependency). Legacy
# plaintext passwords (pre-2.1, or restored from an old backup) are still
# accepted and transparently upgraded to a hash, so an upgrade never locks
# anyone out. Format: pbkdf2_sha256$<rounds>$<salt_hex>$<hash_hex>.
_PBKDF2_ROUNDS = 200_000


def hash_password(pw):
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", pw.encode("utf-8"), salt, _PBKDF2_ROUNDS)
    return f"pbkdf2_sha256${_PBKDF2_ROUNDS}${salt.hex()}${dk.hex()}"


def _is_hashed(stored):
    return isinstance(stored, str) and stored.startswith("pbkdf2_sha256$")


def verify_password(stored, pw):
    if not stored or pw is None:
        return False
    if _is_hashed(stored):
        try:
            _, rounds, salt_hex, hash_hex = stored.split("$", 3)
            dk = hashlib.pbkdf2_hmac("sha256", pw.encode("utf-8"), bytes.fromhex(salt_hex), int(rounds))
        except (ValueError, TypeError):
            return False
        return hmac.compare_digest(dk.hex(), hash_hex)
    return hmac.compare_digest(str(stored), str(pw))  # legacy plaintext


def _migrate_passwords(users):
    """Hash any legacy plaintext passwords in place. Returns True if changed."""
    changed = False
    for u in users:
        pw = u.get("password")
        if isinstance(pw, str) and pw and not _is_hashed(pw):
            u["password"] = hash_password(pw)
            changed = True
    return changed


def _migrate_store_passwords():
    """One-time on boot: hash any plaintext passwords already on disk (after an
    upgrade or a restored old backup) so plaintext never lingers in the store."""
    try:
        users = load_users()
    except (OSError, ValueError):
        return
    if _migrate_passwords(users):
        save_users(users)


_migrate_store_passwords()


# --- Account / entity expiry ----------------------------------------------
# An admin can set a "valid through" date; the account (or a user's access to
# a specific entity) works for the whole of that day and is cut off from the
# day after. Blank/absent = never expires.
_EXPIRED_MSG = "Your account has expired. Please contact your administrator for help."

# Aliases kept for callers that already import these names from security.
_parse_date = parse_date
_date_expired = date_expired


def _user_expired(user):
    return date_expired(user.get("expires"))


def _entity_expired_for(user, entity_id):
    """True if this user's access to entity_id has a passed expiry date."""
    return date_expired((user.get("entity_expires") or {}).get(entity_id))


# --- Session tokens -------------------------------------------------------


def _issue_token(user):
    return jwt.encode(
        {"username": user["username"], "exp": int(time.time()) + 7 * 24 * 3600},
        JWT_SECRET,
        algorithm="HS256",
    )


def user_from_token(token):
    """Resolve a session token to a user, or raise ApiError."""
    if not token:
        raise ApiError("Not authenticated", 401)
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
    except jwt.PyJWTError:
        raise ApiError("Session expired", 401)
    user = next(
        (u for u in load_users() if u["username"] == payload.get("username")), None
    )
    if not user:
        raise ApiError("Unknown user", 401)
    # Cut off an already-signed-in session the moment the account expires.
    if _user_expired(user):
        raise ApiError(_EXPIRED_MSG, 403, {"expired": True})
    return user


def current_user():
    """The authenticated user for this request. Reuses the one the auth gate
    already resolved (stashed on flask.g) so we don't validate the token twice;
    falls back to validating the Bearer header when called outside that path."""
    cached = getattr(g, "current_user", None)
    if cached is not None:
        return cached
    header = request.headers.get("Authorization", "")
    token = header[7:] if header.startswith("Bearer ") else None
    g.current_user = user_from_token(token)
    return g.current_user


def is_management():
    """True when the request arrived on the (unpublished) Ingress port - i.e.
    through HA's sidebar, where the user is already an authenticated HA admin."""
    return str(request.environ.get("SERVER_PORT")) == str(INGRESS_PORT)


def require_admin():
    """Admin endpoints are served ONLY on the management (Ingress) port. A
    request there is trusted as the HA admin, so no app login is required."""
    if not is_management():
        raise ApiError("Admin access required", 403)
    return {"username": None, "admin": True}


# --- Login throttle -------------------------------------------------------
# Simple in-memory brute-force throttle, keyed by USERNAME only. One gunicorn
# worker, so a module-level dict shared across threads is enough; nothing
# persists across a restart. We deliberately do NOT key on the client IP: the
# X-Forwarded-For header is spoofable (a rotating value would bypass the limit),
# and the real peer IP behind a shared proxy/tunnel is identical for everyone
# (so per-IP would lock all users out at once). Username keying needs no IP and
# can't be defeated by a header.
_LOGIN_FAILS = {}
_LOGIN_LOCK = threading.Lock()
_LOGIN_MAX_FAILS = 8
_LOGIN_WINDOW = 300  # seconds; failures older than this are forgotten
_LOGIN_MAX_KEYS = 5000  # cap the dict so spamming random usernames can't exhaust memory
# A throwaway hash so a missing user still costs one PBKDF2 verify (constant-time
# login: the response doesn't reveal whether the username exists).
_DUMMY_PW_HASH = hash_password("\x00 no-such-user \x00")


def _login_key(username):
    return (username or "").strip().lower() or "?"


def _login_blocked(key):
    now = time.time()
    with _LOGIN_LOCK:
        fails = [t for t in _LOGIN_FAILS.get(key, []) if now - t < _LOGIN_WINDOW]
        if fails:
            _LOGIN_FAILS[key] = fails
        else:
            _LOGIN_FAILS.pop(key, None)
        return len(fails) >= _LOGIN_MAX_FAILS


def _login_note_fail(key):
    now = time.time()
    with _LOGIN_LOCK:
        _LOGIN_FAILS.setdefault(key, []).append(now)
        if len(_LOGIN_FAILS) > _LOGIN_MAX_KEYS:  # sweep keys whose fails all expired
            for k in [k for k, v in list(_LOGIN_FAILS.items())
                      if not any(now - t < _LOGIN_WINDOW for t in v)]:
                _LOGIN_FAILS.pop(k, None)


def _login_clear(key):
    with _LOGIN_LOCK:
        _LOGIN_FAILS.pop(key, None)


# --- Password complexity rules --------------------------------------------


def _password_rules():
    """Admin-configured complexity rules for self-service password changes."""
    r = _load_settings().get("password_rules") or {}
    return {
        "min": int(r.get("min") or 0),
        "max": int(r.get("max") or 0),
        "upper": bool(r.get("upper")),
        "lower": bool(r.get("lower")),
        "number": bool(r.get("number")),
        "special": bool(r.get("special")),
    }


def _join_natural(items):
    if len(items) <= 1:
        return "".join(items)
    if len(items) == 2:
        return f"{items[0]} and {items[1]}"
    return ", ".join(items[:-1]) + ", and " + items[-1]


def _password_problems(pw, rules=None):
    """List of unmet password requirements (empty list = OK)."""
    r = rules or _password_rules()
    out = []
    if r["min"] and len(pw) < r["min"]:
        out.append(f"at least {r['min']} characters")
    if r["max"] and len(pw) > r["max"]:
        out.append(f"at most {r['max']} characters")
    if r["upper"] and not any(c.isupper() for c in pw):
        out.append("an uppercase letter")
    if r["lower"] and not any(c.islower() for c in pw):
        out.append("a lowercase letter")
    if r["number"] and not any(c.isdigit() for c in pw):
        out.append("a number")
    if r["special"] and not any((not c.isalnum()) and (not c.isspace()) for c in pw):
        out.append("a special character")
    return out


# --- OAuth helpers --------------------------------------------------------


def _oauth_redirect_uri():
    return f"{OAUTH_REDIRECT_BASE}/api/oauth/callback"


def _email_allowed(email):
    # Read via the config module so the values are a single source of truth
    # (the admin sets these at boot; tests toggle them on `config`).
    # No allow-list configured -> refuse unless the admin opted into allow-any.
    if not config.OAUTH_ALLOWED_DOMAINS and not config.OAUTH_ALLOWED_EMAILS:
        return config.OAUTH_ALLOW_ANY
    email = email.lower()
    domain = email.rsplit("@", 1)[-1] if "@" in email else ""
    return domain in config.OAUTH_ALLOWED_DOMAINS or email in config.OAUTH_ALLOWED_EMAILS


def _oauth_error_page(message, code="error"):
    """Send an OAuth sign-in failure back to the dashboard login page so it
    renders inside the app (themed, consistent with password sign-in) rather
    than a separate page. `code='expired'` triggers the dedicated "account
    expired" prompt; any other failure shows `message` inline on the login
    screen. The values ride in the URL fragment (never sent to a server)."""
    base = OAUTH_REDIRECT_BASE or ""
    frag = urlencode({"auth_error": code, "auth_msg": message})
    return redirect(f"{base}/#{frag}")
