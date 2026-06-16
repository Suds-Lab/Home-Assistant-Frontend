"""Control Center - a small Home Assistant companion app (Flask backend).

Shows each logged-in person only their own lights and AC and lets them control
them, talking to Home Assistant through this backend so the HA token never
reaches the browser.

Runs two ways:
  * Standalone (dev): reads HA_URL / HA_TOKEN from the environment (.env).
  * Home Assistant add-on: reads settings from /data/options.json and reaches
    HA through the Supervisor proxy using the auto-injected SUPERVISOR_TOKEN,
    so no long-lived token is needed.
"""

import base64
import hashlib
import hmac
import json
import mimetypes
import os
import re
import secrets
import threading
import time
from datetime import date, datetime
from pathlib import Path
from queue import Empty, Queue
from urllib.parse import quote, urlencode

# Ensure PWA assets are served with the right Content-Type.
mimetypes.add_type("application/manifest+json", ".webmanifest")
mimetypes.add_type("text/javascript", ".js")

import jwt
import requests
import websocket  # websocket-client
from flask import Flask, Response, jsonify, redirect, request, send_from_directory
from flask_sock import Sock

# Configuration (add-on options + env) lives in config.py. Re-exported here via
# `import *` so existing bare references (and `app.<name>` access in tests) keep
# working unchanged.
import config  # the live module, for values that may be toggled at runtime/tests
from config import *  # noqa: F401,F403


def _store_path():
    """Where accounts are read/written.

    In the add-on this lives under /data (persisted across restarts and
    updates); standalone it's users.json next to this file.
    """
    if os.environ.get("USERS_STORE"):
        return Path(os.environ["USERS_STORE"])
    if SUPERVISOR_TOKEN and Path("/data").is_dir():
        return Path("/data/users.json")
    return BASE_DIR / "users.json"


STORE_FILE = _store_path()

# Uploaded custom app icon lives next to the user store (persistent /data).
ICON_DIR = STORE_FILE.parent
SETTINGS_FILE = ICON_DIR / "settings.json"
ACTIVITY_FILE = ICON_DIR / "activity.json"
ACTIVITY_MAX = 1000  # keep the most recent N actions

# --- Session-signing secret ---------------------------------------------
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


# Settings are read on hot paths (including the live-broadcast access check, once
# per state change), so cache them briefly to avoid a disk read every time.
_SETTINGS_CACHE = {"ts": 0.0, "data": None}
_SETTINGS_TTL = 3.0


def _load_settings():
    now = time.time()
    c = _SETTINGS_CACHE
    if c["data"] is not None and now - c["ts"] < _SETTINGS_TTL:
        return dict(c["data"])  # a copy, so callers can mutate freely
    try:
        data = json.loads(SETTINGS_FILE.read_text())
    except (OSError, ValueError):
        data = {}
    c["data"] = data
    c["ts"] = now
    return dict(data)


def _save_settings(data):
    SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS_FILE.write_text(json.dumps(data, indent=2))
    _SETTINGS_CACHE["data"] = dict(data)  # refresh the cache immediately
    _SETTINGS_CACHE["ts"] = time.time()


_ACTIVITY_LOCK = threading.Lock()

# Services that adjust a continuous value: stepping the AC up/down (or a dimmer,
# cover position, volume) fires one call per press. Rather than logging every
# step, collapse a run of them by the same user on the same entity into a single
# entry showing the FINAL value, as long as they're close together in time.
_COALESCE_SERVICES = {"set_temperature", "set_percentage", "set_cover_position", "volume_set"}
_COALESCE_WINDOW = 120  # seconds


def _load_activity():
    try:
        data = json.loads(ACTIVITY_FILE.read_text())
        return data if isinstance(data, list) else []
    except (OSError, ValueError):
        return []


def _append_activity(entry):
    """Append one action to our own activity log (newest last on disk).

    This is the app's own record of who did what - app users aren't HA users,
    so HA's native logbook can only credit the Supervisor. This log always
    shows the real person.
    """
    with _ACTIVITY_LOCK:
        log = _load_activity()
        last = log[-1] if log else None
        coalesce = (
            last is not None
            and entry.get("service") in _COALESCE_SERVICES
            and last.get("service") == entry.get("service")
            and last.get("entity_id") == entry.get("entity_id")
            and last.get("username") == entry.get("username")
            and (entry.get("ts", 0) - last.get("ts", 0)) <= _COALESCE_WINDOW
        )
        if coalesce:
            log[-1] = entry  # keep only the latest (final) value of the run
        else:
            log.append(entry)
        if len(log) > ACTIVITY_MAX:
            log = log[-ACTIVITY_MAX:]
        try:
            ACTIVITY_FILE.parent.mkdir(parents=True, exist_ok=True)
            tmp = ACTIVITY_FILE.with_name(ACTIVITY_FILE.name + ".tmp")
            tmp.write_text(json.dumps(log))
            tmp.replace(ACTIVITY_FILE)
        except OSError:
            pass  # best-effort; never block a control action on logging


def enabled_domains():
    """Domains the picker is allowed to show. None = all. The in-app setting
    wins; otherwise the `device_types` config seeds it."""
    s = _load_settings()
    if isinstance(s.get("device_types"), list):
        return set(s["device_types"])
    return DEVICE_TYPES or None


def included_entities():
    """Specific entity_ids always available regardless of device-type filter -
    so a noisy domain (e.g. switch) can be disabled while a few hand-picked
    entities are still shown in the picker and granted to 'All devices' users."""
    s = _load_settings()
    inc = s.get("included_entities")
    return set(e for e in inc if isinstance(e, str)) if isinstance(inc, list) else set()


# Resolve display settings (set in the Settings tab, stored in /data).
#  * App name  -> browser-tab <title>, PWA / installed-app name. cfg_name().
#  * Title     -> the heading people see on the login page and dashboard.
#                 Falls back to the app name when unset. cfg_title().
def cfg_name():
    return (_load_settings().get("name") or "").strip() or APP_NAME


def cfg_title():
    s = _load_settings()
    return (s.get("title") or "").strip() or cfg_name()


def cfg_emoji():
    return (_load_settings().get("icon") or "").strip() or APP_ICON


def cfg_providers():
    """Which login methods are offered on the user dashboard. The admin's
    choice (Settings tab: 'local' | 'oauth' | 'both') intersected with what's
    actually configured. Always leaves at least local enabled as a fallback."""
    choice = (_load_settings().get("auth_providers") or "local").lower()
    oauth = oauth_configured() and choice in ("oauth", "both")
    local = choice in ("local", "both") or not oauth
    return {"local": local, "oauth": oauth}


ICON_EXT = {
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/webp": "webp",
    "image/svg+xml": "svg",
    "image/gif": "gif",
}


def _find_icon():
    """Path to the uploaded custom icon, if any."""
    for p in sorted(ICON_DIR.glob("app-icon.*")):
        return p
    return None


def _remove_icons():
    for p in ICON_DIR.glob("app-icon.*"):
        try:
            p.unlink()
        except OSError:
            pass  # best-effort (e.g. transient file lock on Windows)


def _app_image_url():
    """Cache-busted URL for the custom icon, or None when using the default."""
    p = _find_icon()
    return f"./app-icon?v={int(p.stat().st_mtime)}" if p else None


def _seed_users():
    """Initial accounts used the first time the store is created."""
    users = addon_options.get("users")
    if isinstance(users, list) and users:
        seed = [dict(u) for u in users]
    else:
        bundled = BASE_DIR / "users.json"
        seed = json.loads(bundled.read_text())["users"] if bundled.exists() else []
    return seed


def save_users(users):
    STORE_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STORE_FILE.with_name(STORE_FILE.name + ".tmp")
    tmp.write_text(json.dumps({"users": users}, indent=2))
    tmp.replace(STORE_FILE)


def load_users():
    """User -> entities mapping (read fresh each call, so edits are live)."""
    if not STORE_FILE.exists():
        save_users(_seed_users())
    return json.loads(STORE_FILE.read_text())["users"]


# --- Passwords -----------------------------------------------------------
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


# --- Home Assistant helpers ----------------------------------------------


def ha_request(path, method="GET", payload=None):
    try:
        res = requests.request(
            method,
            f"{HA_URL}{path}",
            headers={
                "Authorization": f"Bearer {HA_TOKEN}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=15,
        )
    except requests.RequestException as err:
        print(f"HA connection error for {method} {path}: {err}")
        raise ApiError("Couldn't reach Home Assistant.", 502)
    if not res.ok:
        print(f"HA request failed ({res.status_code}) for {method} {path}: {res.text[:500]}")
        raise ApiError("Couldn't reach Home Assistant. Check the add-on logs for details.", 502)
    # Some service calls return an empty body.
    return res.json() if res.text else None


def call_service(domain, service, entity_id, extra=None):
    body = {"entity_id": entity_id, **(extra or {})}
    return ha_request(f"/api/services/{domain}/{service}", "POST", body)


# --- App + auth ----------------------------------------------------------

app = Flask(__name__, static_folder=None)
app.config["MAX_CONTENT_LENGTH"] = 5 * 1024 * 1024  # 5 MB cap on uploads
sock = Sock(app)


from errors import ApiError  # noqa: E402 (shared type; see errors.py)


@app.errorhandler(ApiError)
def handle_api_error(err):
    payload = {"error": err.message}
    payload.update(err.extra)
    return jsonify(payload), err.status


# Optional account/entity expiry. An admin can set a "valid through" date; the
# account (or a user's access to a specific entity) works for the whole of that
# day and is cut off from the day after. Blank/absent = never expires.
_EXPIRED_MSG = "Your account has expired. Please contact your administrator for help."


def _parse_date(s):
    """Parse a 'YYYY-MM-DD' date string; None if blank/invalid."""
    if not isinstance(s, str) or not s.strip():
        return None
    try:
        return datetime.strptime(s.strip()[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def _date_expired(s):
    """True once the 'valid through' date has fully passed (the day after it)."""
    d = _parse_date(s)
    return d is not None and date.today() > d


def _user_expired(user):
    return _date_expired(user.get("expires"))


def _entity_expired_for(user, entity_id):
    """True if this user's access to entity_id has a passed expiry date."""
    return _date_expired((user.get("entity_expires") or {}).get(entity_id))


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
    """Validate the Bearer session token and return the matching user."""
    header = request.headers.get("Authorization", "")
    token = header[7:] if header.startswith("Bearer ") else None
    return user_from_token(token)


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


def _domain_assignable(entity_id):
    """Whether this entity is offered/granted: within the allowed device types,
    or explicitly added to the global included-entities list."""
    allowed = enabled_domains()
    if allowed is None or entity_id.split(".")[0] in allowed:
        return True
    return entity_id in included_entities()


def user_can_access(user, entity_id):
    """Explicitly-assigned entities are always owned (any type - that's the
    per-user 'add a specific device' override). Beyond that, an 'all' user (and
    managers, who get all devices) owns every assignable entity."""
    # An admin-set per-user expiry on this entity makes it disappear for this
    # user once the date passes, even if they'd otherwise own it.
    if _entity_expired_for(user, entity_id):
        return False
    if entity_id in user.get("entities", []):
        return True
    if user.get("all") or user.get("manager"):
        return _domain_assignable(entity_id)
    return False


_ENTITY_ID_RE = re.compile(r"^[a-z_]+\.[a-z0-9_]+$")
_SAFE_TS_RE = re.compile(r"^[0-9T:.+\- Zz]+$")


def valid_entity_id(entity_id):
    """A real HA entity id is `domain.object_id`, lowercase a-z/0-9/_ only.
    Reject anything else so it can't be smuggled into an HA API URL (e.g.
    `light.x/../../config` traversing out of /api/states/)."""
    return isinstance(entity_id, str) and bool(_ENTITY_ID_RE.match(entity_id))


def _safe_ts(s):
    """Accept only timestamp-ish characters (ISO 8601 / epoch); never a path."""
    return isinstance(s, str) and bool(_SAFE_TS_RE.match(s)) and ".." not in s


def assert_owned(user, entity_id):
    """Reject any entity the logged-in user is not allowed to see/control."""
    if not valid_entity_id(entity_id):
        raise ApiError("Invalid entity id", 400)
    if not user_can_access(user, entity_id):
        raise ApiError("You do not have access to that device", 403)


def _issue_token(user):
    return jwt.encode(
        {"username": user["username"], "exp": int(time.time()) + 7 * 24 * 3600},
        JWT_SECRET,
        algorithm="HS256",
    )


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


@app.post("/api/login")
def login():
    if not cfg_providers()["local"]:
        raise ApiError("Password sign-in is disabled", 403)
    body = request.get_json(silent=True) or {}
    username = body.get("username")
    password = body.get("password")
    key = _login_key(username)
    if _login_blocked(key):
        raise ApiError("Too many attempts. Please wait a few minutes and try again.", 429)
    user = next((u for u in load_users() if u["username"] == username), None)
    # Always run a hash check (a dummy when the user is missing) so the response
    # time doesn't reveal whether the username exists.
    stored = user.get("password") if user else _DUMMY_PW_HASH
    if not verify_password(stored, password) or not user:
        _login_note_fail(key)
        raise ApiError("Invalid username or password", 401)
    _login_clear(key)
    if _user_expired(user):
        raise ApiError(_EXPIRED_MSG, 403, {"expired": True})
    # Lazy upgrade: if this account still had a plaintext password, hash it now.
    if not _is_hashed(user.get("password")):
        users = load_users()
        for u in users:
            if u["username"] == user["username"]:
                u["password"] = hash_password(password)
        save_users(users)
    return jsonify(
        token=_issue_token(user),
        displayName=user.get("displayName") or user["username"],
    )


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


@app.post("/api/me/password")
def change_my_password():
    """Let a signed-in local user change their own password: verifies the current
    one first (throttled), then enforces the admin's complexity rules."""
    user = current_user()
    if not cfg_providers()["local"]:
        raise ApiError("Password sign-in is disabled.", 403)
    if not user.get("password"):
        raise ApiError("This account signs in with OAuth and has no password.", 400)
    body = request.get_json(silent=True) or {}
    current = body.get("current") or ""
    new = body.get("new") or ""
    key = _login_key(user["username"])
    if _login_blocked(key):
        raise ApiError("Too many attempts. Please wait a few minutes and try again.", 429)
    if not verify_password(user.get("password"), current):
        _login_note_fail(key)
        raise ApiError("Current password is incorrect.", 401)
    _login_clear(key)
    problems = _password_problems(new)
    if problems:
        raise ApiError("Password must have " + _join_natural(problems) + ".", 400)
    users = load_users()
    for u in users:
        if u["username"] == user["username"]:
            u["password"] = hash_password(new)
    save_users(users)
    return jsonify(ok=True)


@app.get("/api/me")
def me():
    """The signed-in user's display name + role (so the dashboard can show the
    manager-only area organizer) plus avatar and password-change availability."""
    user = current_user()
    return jsonify(
        username=user["username"],
        displayName=user.get("displayName") or user["username"],
        manager=bool(user.get("manager")),
        picture=user.get("picture") or None,
        canChangePassword=bool(cfg_providers()["local"] and user.get("password")),
        passwordRules=_password_rules(),
    )


@app.get("/api/session")
def session():
    """Tells the UI which experience to render based on the port it arrived on:
    'manage' (Ingress/sidebar) or 'user' (published dashboard)."""
    providers = cfg_providers()
    return jsonify(
        mode="manage" if is_management() else "user",
        stream=STREAM_ENABLED,
        appName=cfg_name(),   # browser tab + installed-app (PWA) name
        title=cfg_title(),    # heading shown on the login page + dashboard
        appIcon=cfg_emoji(),
        appImage=_app_image_url(),
        providers=providers,            # which login methods to show
        oauthName=OAUTH_PROVIDER_NAME,  # label for the OAuth button
        oauthIsGoogle=oauth_is_google(),  # show the Google logo
        oauthLogo=OAUTH_LOGO_URL,         # custom provider logo (non-Google)
    )


# --- OAuth sign-in (user dashboard only) ---------------------------------


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


def _user_for_email(email, name="", picture=""):
    """Find the app user for an OAuth email, creating an un-onboarded one (no
    devices) on first sign-in so the admin can assign devices later. The display
    name is taken from the provider (e.g. the Google account name) on creation,
    falling back to the email's local part. The provider avatar URL, if any, is
    stored and refreshed on each sign-in for the account menu."""
    email = email.strip().lower()
    users = load_users()
    found = next(
        (u for u in users
         if (u.get("email") or "").lower() == email or u["username"].lower() == email),
        None,
    )
    if found:
        if picture and found.get("picture") != picture:
            found["picture"] = picture
            save_users(users)
        return found
    record = {
        "username": email,
        "email": email,
        "displayName": (name or "").strip() or email.split("@")[0],
        "provider": "oauth",
        "picture": picture or "",
        "entities": [],
    }
    users.append(record)
    save_users(users)
    return record


def _oauth_error_page(message, code="error"):
    """Send an OAuth sign-in failure back to the dashboard login page so it
    renders inside the app (themed, consistent with password sign-in) rather
    than a separate page. `code='expired'` triggers the dedicated "account
    expired" prompt; any other failure shows `message` inline on the login
    screen. The values ride in the URL fragment (never sent to a server)."""
    base = OAUTH_REDIRECT_BASE or ""
    frag = urlencode({"auth_error": code, "auth_msg": message})
    return redirect(f"{base}/#{frag}")


@app.get("/api/oauth/login")
def oauth_login():
    if not oauth_configured():
        return _oauth_error_page("OAuth is not configured on this server.")
    # CSRF: a short-lived signed `state` bound to a matching HttpOnly cookie, so
    # only the browser that started the flow can complete it (stops an attacker
    # pre-minting a state and logging a victim into the attacker's account).
    nonce = secrets.token_urlsafe(16)
    state = jwt.encode(
        {"n": nonce, "exp": int(time.time()) + 600},
        JWT_SECRET,
        algorithm="HS256",
    )
    params = {
        "client_id": OAUTH_CLIENT_ID,
        "redirect_uri": _oauth_redirect_uri(),
        "response_type": "code",
        "scope": OAUTH_SCOPES,
        "state": state,
        "access_type": "online",
        "prompt": "select_account",
    }
    if len(OAUTH_ALLOWED_DOMAINS) == 1:
        params["hd"] = OAUTH_ALLOWED_DOMAINS[0]  # Google domain hint
    resp = redirect(f"{OAUTH_AUTHORIZE_URL}?{urlencode(params)}")
    resp.set_cookie(
        "cc_oauth_state", nonce, max_age=600, httponly=True,
        secure=True, samesite="Lax", path="/api/oauth/",
    )
    return resp


@app.get("/api/oauth/callback")
def oauth_callback():
    if not oauth_configured():
        return _oauth_error_page("OAuth is not configured on this server.")
    if request.args.get("error"):
        return _oauth_error_page("Access was denied at the provider.")
    code = request.args.get("code")
    state = request.args.get("state")
    try:
        claims = jwt.decode(state, JWT_SECRET, algorithms=["HS256"])
    except jwt.PyJWTError:
        return _oauth_error_page("This sign-in link expired. Please try again.")
    cookie_nonce = request.cookies.get("cc_oauth_state") or ""
    if not cookie_nonce or not hmac.compare_digest(cookie_nonce, claims.get("n") or ""):
        return _oauth_error_page("This sign-in couldn't be verified. Please start again.")
    if not code:
        return _oauth_error_page("No authorization code was returned.")

    try:
        tok = requests.post(
            OAUTH_TOKEN_URL,
            data={
                "code": code,
                "client_id": OAUTH_CLIENT_ID,
                "client_secret": OAUTH_CLIENT_SECRET,
                "redirect_uri": _oauth_redirect_uri(),
                "grant_type": "authorization_code",
            },
            headers={"Accept": "application/json"},
            timeout=15,
        )
        tok.raise_for_status()
        access_token = tok.json().get("access_token")
        info = requests.get(
            OAUTH_USERINFO_URL,
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=15,
        )
        info.raise_for_status()
        profile = info.json()
    except requests.RequestException:
        return _oauth_error_page("Could not reach the identity provider.")

    email = (profile.get("email") or "").strip()
    if not email:
        return _oauth_error_page("The provider did not return an email address.")
    if not profile.get("email_verified"):
        return _oauth_error_page("Your email address is not verified by the provider.")
    if not _email_allowed(email):
        if not OAUTH_ALLOWED_DOMAINS and not OAUTH_ALLOWED_EMAILS and not OAUTH_ALLOW_ANY:
            print("OAuth sign-in refused: enabled but no allowed emails/domains are "
                  "configured (set oauth_allowed_emails / oauth_allowed_domains, or "
                  "oauth_allow_any: true).")
            return _oauth_error_page(
                "Sign-in isn't configured: no allowed emails or domains are set. "
                "Ask your administrator."
            )
        return _oauth_error_page("Your account isn't allowed to use this app.")

    user = _user_for_email(email, profile.get("name"), profile.get("picture") or "")
    if _user_expired(user):
        return _oauth_error_page(_EXPIRED_MSG, "expired")
    # Hand the session token + display name to the SPA via the URL fragment
    # (never sent to a server or written to logs); it stores and strips them.
    frag = urlencode({"oauth_token": _issue_token(user), "oauth_name": user.get("displayName") or ""})
    resp = redirect(f"{OAUTH_REDIRECT_BASE}/#{frag}")
    resp.delete_cookie("cc_oauth_state", path="/api/oauth/")
    return resp


# --- Device routes -------------------------------------------------------


def _device_view(s):
    attrs = s.get("attributes", {})
    return {
        "entity_id": s["entity_id"],
        "domain": s["entity_id"].split(".")[0],
        "name": attrs.get("friendly_name") or s["entity_id"],
        "state": s.get("state"),
        "attributes": attrs,
        "last_changed": s.get("last_changed"),
        "last_updated": s.get("last_updated"),
    }


@app.get("/api/devices")
def devices():
    """Every entity assigned to the user, with full state + attributes, plus
    the room/floor it's in (so the dashboard can group by room)."""
    user = current_user()
    reg = ha_registries_cached()
    locate = _location_lookup(reg)
    ent_dev = {e["entity_id"]: e.get("device_id") for e in reg.get("entities", [])}
    result = []
    for s in ha_request("/api/states"):
        if not user_can_access(user, s["entity_id"]):
            continue
        view = _device_view(s)
        view["area"], view["floor"], view["area_icon"] = locate(s["entity_id"])
        view["device_id"] = ent_dev.get(s["entity_id"])  # for the manager edit popup
        result.append(view)
    result.sort(key=lambda d: (d["domain"], d["name"].lower()))
    return jsonify(devices=result)


@app.get("/api/entity/<path:entity_id>")
def entity_detail(entity_id):
    """Fresh full state for a single owned entity (for the detail panel)."""
    user = current_user()
    assert_owned(user, entity_id)
    return jsonify(_device_view(ha_request(f"/api/states/{entity_id}")))


# --- Manager: organize devices into Home Assistant areas -----------------




# --- Material Design Icons (fetched once from Iconify, then cached locally) ---
# Areas (and other things) can carry an arbitrary mdi:* icon. There are ~7000
# of them, so instead of bundling them we fetch each icon's SVG body once from
# Iconify and cache it in /data, then serve it ourselves - browsers never call
# an external service, and it works offline after the first fetch.
ICON_CACHE_FILE = ICON_DIR / "mdi-icons.json"
_ICON_CACHE = None
_ICON_LOCK = threading.Lock()
_ICON_NAME_CHARS = set("abcdefghijklmnopqrstuvwxyz0123456789-")


def _valid_icon_name(name):
    return bool(name) and len(name) <= 64 and all(c in _ICON_NAME_CHARS for c in name)


def _get_mdi_icon(name):
    """Return {body,width,height} for an mdi icon, fetching+caching on first use."""
    global _ICON_CACHE
    with _ICON_LOCK:
        if _ICON_CACHE is None:
            try:
                _ICON_CACHE = json.loads(ICON_CACHE_FILE.read_text())
            except (OSError, ValueError):
                _ICON_CACHE = {}
        if name in _ICON_CACHE:
            return _ICON_CACHE[name]
    try:
        r = requests.get("https://api.iconify.design/mdi.json",
                         params={"icons": name}, timeout=8)
        if r.status_code != 200:
            return None
        j = r.json()
        ic = (j.get("icons") or {}).get(name)
        if not ic or not ic.get("body"):
            return None
        data = {
            "body": ic["body"],
            "width": ic.get("width") or j.get("width") or 24,
            "height": ic.get("height") or j.get("height") or 24,
        }
    except (requests.RequestException, ValueError):
        return None
    with _ICON_LOCK:
        _ICON_CACHE[name] = data
        try:
            ICON_CACHE_FILE.write_text(json.dumps(_ICON_CACHE))
        except OSError:
            pass
    return data


@app.get("/api/icon/mdi/<name>")
def mdi_icon(name):
    """The SVG body for one mdi:* icon (e.g. an area's icon). Any signed-in user
    may read these; they're just public icon shapes."""
    if not is_management():
        current_user()  # requires a valid session token otherwise
    if not _valid_icon_name(name):
        raise ApiError("Invalid icon name", 400)
    data = _get_mdi_icon(name)
    if not data:
        raise ApiError("Icon not found", 404)
    return jsonify(data)


_BRAND_DOMAIN_RE = re.compile(r"^[a-z0-9_]+$")
_BRAND_ICON_CACHE = {}   # domain -> (content_type, bytes); only successes cached
_BRAND_ICON_LOCK = threading.Lock()


@app.get("/api/icon/brand/<domain>")
def brand_icon(domain):
    """Proxy an integration's brand icon. Home Assistant's brands API (2026.3+)
    serves a custom integration's OWN logo and falls back to the CDN for core
    ones; for older HA we hit the brands CDN core path directly. A 404 tells the
    UI to show a generic puzzle glyph. With the domain locked to [a-z0-9_] it
    only ever returns a brand image (no SSRF)."""
    # On the published dashboard port, require a valid session (passed as ?token=
    # since an <img> can't send the Authorization header) so the set of installed
    # integrations isn't enumerable by anyone. The Ingress port is trusted by port.
    if not is_management():
        user_from_token(request.args.get("token"))
    if not _BRAND_DOMAIN_RE.match(domain or ""):
        raise ApiError("Invalid integration", 400)
    with _BRAND_ICON_LOCK:
        hit = _BRAND_ICON_CACHE.get(domain)
    if hit:
        return Response(hit[1], mimetype=hit[0], headers={"Cache-Control": "public, max-age=86400"})
    found = None
    for url, headers in (
        (f"{HA_URL}/api/brands/integration/{domain}/icon.png", {"Authorization": f"Bearer {HA_TOKEN}"}),
        (f"https://brands.home-assistant.io/{domain}/icon.png", {}),
    ):
        try:
            r = requests.get(url, headers=headers, timeout=8)
        except requests.RequestException:
            continue
        if r.ok and r.headers.get("content-type", "").startswith("image/"):
            found = (r.headers["content-type"], r.content)
            break
    if not found:
        raise ApiError("No brand icon", 404)  # transient/missing: not cached, retries later
    with _BRAND_ICON_LOCK:
        _BRAND_ICON_CACHE[domain] = found
    return Response(found[1], mimetype=found[0], headers={"Cache-Control": "public, max-age=86400"})


# Services the app may call, per domain. Calls are always scoped to an entity
# the user owns and to that entity's own domain - never an arbitrary HA service.
ALLOWED_SERVICES = {
    "light": {"turn_on", "turn_off", "toggle"},
    "switch": {"turn_on", "turn_off", "toggle"},
    "input_boolean": {"turn_on", "turn_off", "toggle"},
    "fan": {"turn_on", "turn_off", "toggle", "set_percentage", "oscillate"},
    "climate": {
        "turn_on", "turn_off", "set_hvac_mode", "set_temperature",
        "set_fan_mode", "set_swing_mode",
    },
    "cover": {
        "open_cover", "close_cover", "stop_cover", "toggle", "set_cover_position"
    },
    "lock": {"lock", "unlock", "open"},
    "media_player": {
        "turn_on", "turn_off", "media_play", "media_pause", "media_stop",
        "media_play_pause", "media_next_track", "media_previous_track",
        "volume_set", "volume_up", "volume_down", "volume_mute",
    },
    "scene": {"turn_on"},
    "script": {"turn_on", "turn_off", "toggle"},
    "automation": {"turn_on", "turn_off", "toggle", "trigger"},
    "button": {"press"},
    "input_button": {"press"},
    "vacuum": {"start", "pause", "stop", "return_to_base", "locate"},
}


@app.post("/api/control")
def control():
    """Call a whitelisted service on an entity the user owns."""
    user = current_user()
    body = request.get_json(silent=True) or {}
    entity_id = body.get("entity_id")
    service = body.get("service")
    data = body.get("data") or {}
    if not isinstance(entity_id, str) or not isinstance(service, str):
        raise ApiError("entity_id and service are required", 400)
    if not isinstance(data, dict):
        raise ApiError("data must be an object", 400)
    assert_owned(user, entity_id)
    domain = entity_id.split(".")[0]
    if service not in ALLOWED_SERVICES.get(domain, set()):
        raise ApiError(f"Service '{service}' is not allowed for {domain} entities", 400)
    call_service(domain, service, entity_id, data)
    _log_action(user, domain, service, entity_id, data)
    return jsonify(ok=True)


# Human-readable verbs for the logbook attribution.
_ACTION_VERBS = {
    "turn_on": "turned on", "turn_off": "turned off", "toggle": "toggled",
    "open_cover": "opened", "close_cover": "closed", "stop_cover": "stopped",
    "set_cover_position": "set the position of", "lock": "locked", "unlock": "unlocked",
    "set_percentage": "set the speed of", "media_play": "played", "media_pause": "paused",
    "media_play_pause": "play/paused", "volume_set": "set the volume of",
    "press": "pressed", "start": "started", "pause": "paused",
}


def _action_verb(service, data):
    if service == "set_hvac_mode":
        return f"set the mode to {data.get('hvac_mode')}"
    if service == "set_fan_mode":
        return f"set the fan to {data.get('fan_mode')}"
    if service == "set_swing_mode":
        return f"set swing to {data.get('swing_mode')}"
    if service == "set_temperature":
        return f"set the temperature to {data.get('temperature')}°"
    if service == "set_percentage":
        return f"set the speed to {data.get('percentage')}%"
    if service == "set_cover_position":
        return f"set the position to {data.get('position')}%"
    if service == "volume_set":
        return f"set the volume to {round((data.get('volume_level') or 0) * 100)}%"
    return _ACTION_VERBS.get(service, service.replace("_", " "))


def _entity_name(entity_id):
    state = STATE_CACHE.get(entity_id)
    if state:
        return state.get("attributes", {}).get("friendly_name") or entity_id
    return entity_id


def _log_action(user, domain, service, entity_id, data):
    """Record who did what in the app's own activity log. App users aren't HA
    users, so HA's native logbook can only credit the Supervisor - this log is
    the source of truth that always names the real person."""
    _append_activity({
        "ts": time.time(),
        "username": user.get("username"),
        "name": user.get("displayName") or user.get("username") or "A user",
        "entity_id": entity_id,
        "entity": _entity_name(entity_id),
        "domain": domain,
        "service": service,
        "verb": _action_verb(service, data),
    })


# --- Real-time updates (HA WebSocket -> cache -> SSE) ---------------------

# A single background WebSocket to Home Assistant keeps this live cache of
# entity states; connected browsers get pushed only the entities they own.
STATE_CACHE = {}  # entity_id -> raw HA state dict
SUBSCRIBERS = []  # each item: {"q": Queue, "owned": set(entity_id)}
_SUB_LOCK = threading.Lock()
_CACHE_READY = threading.Event()


def _ws_url():
    base = HA_URL.replace("https://", "wss://").replace("http://", "ws://")
    return f"{base}/api/websocket"


def _broadcast(entity_id, new_state):
    view = _device_view(new_state) if new_state else None
    payload = json.dumps({"entity_id": entity_id, "state": view})
    with _SUB_LOCK:
        subs = list(SUBSCRIBERS)
    for sub in subs:
        # Push to anyone who can see this entity - which for "All devices" users
        # and managers is everything they're allowed, not a fixed list. (Using
        # a static owned-set missed them, since they own devices via the flag,
        # not an explicit entity list.)
        if user_can_access(sub["user"], entity_id):
            sub["q"].put(payload)


def _ws_loop():
    """Maintain the HA WebSocket connection, refilling the cache and fanning
    state_changed events out to subscribers. Reconnects forever on failure."""
    while True:
        try:
            ws = websocket.create_connection(_ws_url(), timeout=30)
            json.loads(ws.recv())  # auth_required
            ws.send(json.dumps({"type": "auth", "access_token": HA_TOKEN}))
            if json.loads(ws.recv()).get("type") != "auth_ok":
                print("HA WebSocket auth failed")
                ws.close()
                time.sleep(15)
                continue

            # Seed the cache with a full snapshot, then subscribe to changes.
            try:
                for s in ha_request("/api/states"):
                    STATE_CACHE[s["entity_id"]] = s
            except Exception as err:  # noqa: BLE001
                print("State snapshot failed:", err)
            ws.send(json.dumps(
                {"id": 1, "type": "subscribe_events", "event_type": "state_changed"}
            ))
            ws.recv()  # subscribe ack
            _CACHE_READY.set()
            print("HA WebSocket connected; streaming state changes")

            while True:
                msg = json.loads(ws.recv())
                if msg.get("type") != "event":
                    continue
                data = msg["event"]["data"]
                eid = data["entity_id"]
                new = data.get("new_state")
                if new is None:
                    STATE_CACHE.pop(eid, None)
                else:
                    STATE_CACHE[eid] = new
                # A brand-new entity (no prior state) may belong to a device/area
                # we haven't cached - refresh the registry so its room/floor is
                # right on the next fetch.
                if data.get("old_state") is None and new is not None:
                    _invalidate_registries()
                _broadcast(eid, new)
        except Exception as err:  # noqa: BLE001
            _CACHE_READY.clear()
            print("HA WebSocket error, reconnecting in 5s:", err)
            time.sleep(5)


_ws_started = False


def ensure_realtime():
    """Start the HA WebSocket thread once per process."""
    global _ws_started
    if not _ws_started:
        _ws_started = True
        threading.Thread(target=_ws_loop, daemon=True).start()


@app.get("/api/stream")
def stream():
    """Server-Sent Events: an initial snapshot of the user's devices, then a
    live update whenever one changes. EventSource can't set headers, so the
    session token comes as a query param."""
    user = user_from_token(request.args.get("token"))
    sub = {"q": Queue(), "user": user}

    def events():
        with _SUB_LOCK:
            SUBSCRIBERS.append(sub)
        try:
            # Flush headers immediately so the client's onopen fires and it can
            # (re)sync the full list via REST. We only stream incremental
            # updates here, so a down WebSocket can never blank the dashboard.
            yield ": connected\n\n"
            while True:
                try:
                    payload = sub["q"].get(timeout=20)
                    yield f"event: update\ndata: {payload}\n\n"
                except Empty:
                    yield ": keepalive\n\n"  # comment line keeps the socket warm
        finally:
            with _SUB_LOCK:
                SUBSCRIBERS[:] = [s for s in SUBSCRIBERS if s is not sub]

    return Response(
        events(),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@sock.route("/api/ws")
def ws_stream(ws):
    """Live updates over a WebSocket (proxy-friendly: traverses reverse proxies
    and Cloudflare better than SSE). Pushes the user's owned-entity changes; the
    client re-syncs the full list over REST when it (re)connects."""
    try:
        user = user_from_token(request.args.get("token"))
    except ApiError:
        ws.close()
        return
    sub = {"q": Queue(), "user": user}
    with _SUB_LOCK:
        SUBSCRIBERS.append(sub)
    try:
        while True:
            try:
                ws.send(sub["q"].get(timeout=25))
            except Empty:
                ws.send('{"type":"ping"}')  # keepalive + detect a closed socket
    except Exception:  # noqa: BLE001  (client went away)
        pass
    finally:
        with _SUB_LOCK:
            SUBSCRIBERS[:] = [s for s in SUBSCRIBERS if s is not sub]


# --- Admin: manage users (admin-only) ------------------------------------


def ha_registries():
    """One-shot WebSocket query for HA's floor/area/entity/device registries, so
    the picker can group devices by floor and room. Returns {} on any failure."""
    try:
        ws = websocket.create_connection(_ws_url(), timeout=10)
    except Exception:  # noqa: BLE001
        return {}
    try:
        json.loads(ws.recv())  # auth_required
        ws.send(json.dumps({"type": "auth", "access_token": HA_TOKEN}))
        if json.loads(ws.recv()).get("type") != "auth_ok":
            return {}
        cmds = {
            1: "config/floor_registry/list",
            2: "config/area_registry/list",
            3: "config/entity_registry/list",
            4: "config/device_registry/list",
            5: "manifest/list",  # integration domain -> friendly name (incl. custom)
        }
        for i, t in cmds.items():
            ws.send(json.dumps({"id": i, "type": t}))
        got = {}
        while len(got) < len(cmds):
            msg = json.loads(ws.recv())
            if msg.get("type") == "result" and msg.get("id") in cmds:
                got[msg["id"]] = msg.get("result") or []
        return {
            "floors": got[1], "areas": got[2], "entities": got[3], "devices": got[4],
            "integrations": {m.get("domain"): m.get("name")
                             for m in (got.get(5) or []) if m.get("domain") and m.get("name")},
        }
    except Exception:  # noqa: BLE001
        return {}
    finally:
        try:
            ws.close()
        except Exception:  # noqa: BLE001
            pass


def ha_ws_command(payload):
    """Send one WebSocket command to HA and return its result, or raise ApiError.
    Used for registry writes (e.g. moving a device to an area)."""
    try:
        ws = websocket.create_connection(_ws_url(), timeout=10)
    except Exception:  # noqa: BLE001
        raise ApiError("Could not reach Home Assistant", 502)
    try:
        json.loads(ws.recv())  # auth_required
        ws.send(json.dumps({"type": "auth", "access_token": HA_TOKEN}))
        if json.loads(ws.recv()).get("type") != "auth_ok":
            raise ApiError("Home Assistant rejected the connection", 502)
        msg = dict(payload)
        msg["id"] = 1
        ws.send(json.dumps(msg))
        while True:
            res = json.loads(ws.recv())
            if res.get("type") == "result" and res.get("id") == 1:
                if not res.get("success"):
                    raise ApiError(
                        (res.get("error") or {}).get("message")
                        or "Home Assistant rejected the change",
                        502,
                    )
                return res.get("result")
    finally:
        try:
            ws.close()
        except Exception:  # noqa: BLE001
            pass


_REG_CACHE = {"ts": 0.0, "data": None}
_REG_LOCK = threading.Lock()


def _invalidate_registries():
    with _REG_LOCK:
        _REG_CACHE["ts"] = 0.0


def ha_registries_cached(ttl=300):
    """ha_registries() with a short TTL cache - the registries rarely change,
    and this avoids opening a WebSocket on every dashboard refresh."""
    now = time.time()
    with _REG_LOCK:
        cached = _REG_CACHE["data"]
        if cached is not None and (now - _REG_CACHE["ts"]) < ttl:
            return cached
    reg = ha_registries()
    if reg:  # only cache a successful query
        with _REG_LOCK:
            _REG_CACHE["data"] = reg
            _REG_CACHE["ts"] = now
    return reg


def _location_lookup(reg):
    """Build an entity_id -> (room, floor) resolver from HA's registries."""
    floors = {f["floor_id"]: f.get("name") for f in reg.get("floors", [])}
    areas = {a["area_id"]: a for a in reg.get("areas", [])}
    dev_area = {d["id"]: d.get("area_id") for d in reg.get("devices", [])}
    ent_area = {}
    for e in reg.get("entities", []):
        aid = e.get("area_id") or dev_area.get(e.get("device_id"))
        if aid:
            ent_area[e["entity_id"]] = aid

    def locate(entity_id):
        aid = ent_area.get(entity_id)
        area = areas.get(aid) if aid else None
        if not area:
            return (None, None, None)
        return (area.get("name"), floors.get(area.get("floor_id")), area.get("icon"))

    return locate




# --- Static client (served by Flask; SPA fallback) -----------------------




# Start the HA WebSocket listener as soon as the module is imported, so it runs
# under gunicorn (the add-on) as well as the dev server below.
ensure_realtime()


# App-level middleware (security headers) and the dev-server entry point live in
# app.py, the thin composition/entry module that imports this one.


# --- Route blueprints (split out of this module by feature area) ----------
from routes.admin import bp as _admin_bp  # noqa: E402
from routes.manager import bp as _manager_bp  # noqa: E402
from routes.pwa import bp as _pwa_bp  # noqa: E402
app.register_blueprint(_admin_bp)
app.register_blueprint(_manager_bp)
app.register_blueprint(_pwa_bp)
