"""My Home - a small Home Assistant companion app (Flask backend).

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
import json
import mimetypes
import os
import secrets
import threading
import time
from pathlib import Path
from queue import Empty, Queue
from urllib.parse import urlencode

# Ensure PWA assets are served with the right Content-Type.
mimetypes.add_type("application/manifest+json", ".webmanifest")
mimetypes.add_type("text/javascript", ".js")

import jwt
import requests
import websocket  # websocket-client
from flask import Flask, Response, jsonify, redirect, request, send_from_directory
from flask_sock import Sock

try:
    # Load .env for standalone/dev runs. Optional - absent in the add-on.
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"

# --- Configuration -------------------------------------------------------

# Home Assistant writes add-on settings here and injects SUPERVISOR_TOKEN.
OPTIONS_FILE = Path(os.environ.get("OPTIONS_FILE", "/data/options.json"))
addon_options = {}
if OPTIONS_FILE.exists():
    try:
        addon_options = json.loads(OPTIONS_FILE.read_text())
    except (ValueError, OSError) as err:
        print(f"Could not read add-on options at {OPTIONS_FILE}: {err}")

SUPERVISOR_TOKEN = os.environ.get("SUPERVISOR_TOKEN")

# As an add-on, reach HA through the Supervisor proxy with the injected token.
# Standalone (dev), fall back to HA_URL / HA_TOKEN from the environment.
HA_URL = (
    os.environ.get("HA_URL")
    or ("http://supervisor/core" if SUPERVISOR_TOKEN else "")
).rstrip("/")
HA_TOKEN = os.environ.get("HA_TOKEN") or SUPERVISOR_TOKEN
JWT_SECRET = (
    addon_options.get("jwt_secret")
    or os.environ.get("JWT_SECRET")
    or "dev-secret-change-me"
)
# Display name + icon shown in the UI / browser tab. Configurable.
APP_NAME = addon_options.get("app_name") or os.environ.get("APP_NAME") or "My Home"
APP_ICON = addon_options.get("app_icon") or os.environ.get("APP_ICON") or "🏠"
# Entity domains assignable in Manage users (empty list = all domains).
_dt = addon_options.get("device_types")
if _dt is None:
    _dt = [d.strip() for d in os.environ.get("DEVICE_TYPES", "").split(",") if d.strip()]
DEVICE_TYPES = set(_dt) if _dt else set()


def _opt(key, default=""):
    return (addon_options.get(key) or os.environ.get(key.upper()) or default)


# --- OAuth (OpenID Connect) ----------------------------------------------
# Credentials live in the add-on config (not the UI). Defaults target Google,
# but any OIDC provider works by overriding the endpoints. Which login methods
# are actually OFFERED is chosen by the admin in the Settings tab.
OAUTH_CLIENT_ID = _opt("oauth_client_id")
OAUTH_CLIENT_SECRET = _opt("oauth_client_secret")
# External base URL of the published user dashboard (e.g.
# https://home.example.com). Used to build the OAuth redirect URI; the
# provider must have <base>/api/oauth/callback in its allowed redirect list.
OAUTH_REDIRECT_BASE = _opt("oauth_redirect_url").rstrip("/")
OAUTH_PROVIDER_NAME = _opt("oauth_provider_name", "Google")
OAUTH_AUTHORIZE_URL = _opt("oauth_authorize_url", "https://accounts.google.com/o/oauth2/v2/auth")
OAUTH_TOKEN_URL = _opt("oauth_token_url", "https://oauth2.googleapis.com/token")
OAUTH_USERINFO_URL = _opt("oauth_userinfo_url", "https://openidconnect.googleapis.com/v1/userinfo")
OAUTH_SCOPES = _opt("oauth_scopes", "openid email profile")
# Optional logo for the sign-in button on non-Google providers (a URL).
OAUTH_LOGO_URL = _opt("oauth_logo_url")


def _opt_list(key):
    v = addon_options.get(key)
    if v is None:
        v = [x.strip() for x in os.environ.get(key.upper(), "").split(",") if x.strip()]
    return [x.strip() for x in (v or []) if isinstance(x, str) and x.strip()]


# Restrict sign-in to these email domains (e.g. ["my.domain"]). Empty = any.
OAUTH_ALLOWED_DOMAINS = [d.lower().lstrip("@") for d in _opt_list("oauth_allowed_domains")]
# Always-allowed individual emails, even outside the allowed domains. A simple,
# revocable way to let a specific guest in without widening the domain rule.
OAUTH_ALLOWED_EMAILS = [e.lower() for e in _opt_list("oauth_allowed_emails")]


def oauth_configured():
    return bool(OAUTH_CLIENT_ID and OAUTH_CLIENT_SECRET and OAUTH_REDIRECT_BASE)


def oauth_is_google():
    return "accounts.google.com" in OAUTH_AUTHORIZE_URL or OAUTH_PROVIDER_NAME.strip().lower() == "google"

# The app listens on TWO ports:
#  - INGRESS_PORT: the management UI, reached only through HA's Ingress (the
#    sidebar tab). This port is never published, so a request arriving on it is
#    trusted as an authenticated HA admin (same model as the Terminal add-on).
#  - USER_PORT: the household-facing device dashboard, published to the network
#    and protected by the app's own per-user login.
INGRESS_PORT = int(os.environ.get("INGRESS_PORT") or os.environ.get("PORT") or 4000)
USER_PORT = int(os.environ.get("USER_PORT") or 8099)

# Real-time push (SSE) is on by default. Set STREAM=0 to make the dashboard
# poll instead - useful for local preview tools that wait for network-idle
# (an always-open stream never idles).
STREAM_ENABLED = os.environ.get("STREAM", "1") != "0"

if not HA_URL or not HA_TOKEN:
    raise SystemExit(
        "Missing Home Assistant connection. As an add-on this is automatic; "
        "standalone, set HA_URL and HA_TOKEN in your environment (.env)."
    )


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


def _load_settings():
    try:
        return json.loads(SETTINGS_FILE.read_text())
    except (OSError, ValueError):
        return {}


def _save_settings(data):
    SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS_FILE.write_text(json.dumps(data, indent=2))


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
        raise ApiError(f"Could not reach Home Assistant: {err}", 502)
    if not res.ok:
        raise ApiError(f"HA request failed ({res.status_code}): {res.text}", 502)
    # Some service calls return an empty body.
    return res.json() if res.text else None


def call_service(domain, service, entity_id, extra=None):
    body = {"entity_id": entity_id, **(extra or {})}
    return ha_request(f"/api/services/{domain}/{service}", "POST", body)


# --- App + auth ----------------------------------------------------------

app = Flask(__name__, static_folder=None)
app.config["MAX_CONTENT_LENGTH"] = 5 * 1024 * 1024  # 5 MB cap on uploads
sock = Sock(app)


class ApiError(Exception):
    def __init__(self, message, status=500):
        super().__init__(message)
        self.message = message
        self.status = status


@app.errorhandler(ApiError)
def handle_api_error(err):
    return jsonify(error=err.message), err.status


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
    """An 'all' user (and managers, who always have full access) owns every
    assignable entity; otherwise it's the explicit list."""
    if user.get("all") or user.get("manager"):
        return _domain_assignable(entity_id)
    return entity_id in user.get("entities", [])


def assert_owned(user, entity_id):
    """Reject any entity the logged-in user is not allowed to see/control."""
    if not user_can_access(user, entity_id):
        raise ApiError("You do not have access to that device", 403)


def _issue_token(user):
    return jwt.encode(
        {"username": user["username"], "exp": int(time.time()) + 7 * 24 * 3600},
        JWT_SECRET,
        algorithm="HS256",
    )


@app.post("/api/login")
def login():
    if not cfg_providers()["local"]:
        raise ApiError("Password sign-in is disabled", 403)
    body = request.get_json(silent=True) or {}
    username = body.get("username")
    password = body.get("password")
    user = next((u for u in load_users() if u["username"] == username), None)
    if not user or not user.get("password") or user.get("password") != password:
        raise ApiError("Invalid username or password", 401)
    return jsonify(
        token=_issue_token(user),
        displayName=user.get("displayName") or user["username"],
    )


@app.get("/api/me")
def me():
    """The signed-in user's display name + role (so the dashboard can show the
    manager-only area organizer)."""
    user = current_user()
    return jsonify(
        username=user["username"],
        displayName=user.get("displayName") or user["username"],
        manager=bool(user.get("manager")),
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
    # No restriction configured -> any verified email is allowed.
    if not OAUTH_ALLOWED_DOMAINS and not OAUTH_ALLOWED_EMAILS:
        return True
    email = email.lower()
    domain = email.rsplit("@", 1)[-1] if "@" in email else ""
    return domain in OAUTH_ALLOWED_DOMAINS or email in OAUTH_ALLOWED_EMAILS


def _user_for_email(email, name=""):
    """Find the app user for an OAuth email, creating an un-onboarded one (no
    devices) on first sign-in so the admin can assign devices later. The display
    name is taken from the provider (e.g. the Google account name) on creation,
    falling back to the email's local part."""
    email = email.strip().lower()
    users = load_users()
    found = next(
        (u for u in users
         if (u.get("email") or "").lower() == email or u["username"].lower() == email),
        None,
    )
    if found:
        return found
    record = {
        "username": email,
        "email": email,
        "displayName": (name or "").strip() or email.split("@")[0],
        "provider": "oauth",
        "entities": [],
    }
    users.append(record)
    save_users(users)
    return record


def _oauth_error_page(message):
    html = (
        "<!doctype html><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        "<title>Sign-in failed</title>"
        "<body style='font-family:system-ui;background:#0f1419;color:#e7edf3;"
        "display:flex;min-height:100vh;align-items:center;justify-content:center;margin:0'>"
        f"<div style='max-width:420px;padding:24px;text-align:center'><h2>Sign-in failed</h2>"
        f"<p style='color:#9aa7b4'>{message}</p>"
        "<p><a href='./' style='color:#3b82f6'>Back to sign in</a></p></div></body>"
    )
    return Response(html, status=400, mimetype="text/html")


@app.get("/api/oauth/login")
def oauth_login():
    if not oauth_configured():
        return _oauth_error_page("OAuth is not configured on this server.")
    # Stateless CSRF token: a short-lived signed value echoed back as `state`.
    state = jwt.encode(
        {"n": secrets.token_urlsafe(8), "exp": int(time.time()) + 600},
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
    return redirect(f"{OAUTH_AUTHORIZE_URL}?{urlencode(params)}")


@app.get("/api/oauth/callback")
def oauth_callback():
    if not oauth_configured():
        return _oauth_error_page("OAuth is not configured on this server.")
    if request.args.get("error"):
        return _oauth_error_page("Access was denied at the provider.")
    code = request.args.get("code")
    state = request.args.get("state")
    try:
        jwt.decode(state, JWT_SECRET, algorithms=["HS256"])
    except jwt.PyJWTError:
        return _oauth_error_page("This sign-in link expired. Please try again.")
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
    if profile.get("email_verified") is False:
        return _oauth_error_page("Your email address is not verified.")
    if not _email_allowed(email):
        return _oauth_error_page("Your account isn't allowed to use this app.")

    user = _user_for_email(email, profile.get("name"))
    # Hand the session token + display name to the SPA via the URL fragment
    # (never sent to a server or written to logs); it stores and strips them.
    frag = urlencode({"oauth_token": _issue_token(user), "oauth_name": user.get("displayName") or ""})
    return redirect(f"{OAUTH_REDIRECT_BASE}/#{frag}")


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
    locate = _location_lookup(ha_registries_cached())
    result = []
    for s in ha_request("/api/states"):
        if not user_can_access(user, s["entity_id"]):
            continue
        view = _device_view(s)
        view["area"], view["floor"] = locate(s["entity_id"])
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


def require_manager():
    user = current_user()
    if not user.get("manager"):
        raise ApiError("Manager access required", 403)
    return user


@app.get("/api/manager/devices")
def manager_devices():
    """Every HA device with its current area, plus the list of areas - for the
    manager's area organizer."""
    require_manager()
    reg = ha_registries()
    floors = {f["floor_id"]: f.get("name") for f in reg.get("floors", [])}
    area_by_id = {a["area_id"]: a for a in reg.get("areas", [])}
    # Friendly names for a device's entities (from current states).
    names = {}
    try:
        for s in ha_request("/api/states"):
            names[s["entity_id"]] = s.get("attributes", {}).get("friendly_name") or s["entity_id"]
    except ApiError:
        pass
    ents_by_dev = {}
    for e in reg.get("entities", []):
        if e.get("device_id"):
            ents_by_dev.setdefault(e["device_id"], []).append(
                names.get(e["entity_id"], e["entity_id"])
            )

    devices = []
    for d in reg.get("devices", []):
        aid = d.get("area_id")
        area = area_by_id.get(aid)
        ents = sorted(ents_by_dev.get(d["id"], []))
        devices.append({
            "id": d["id"],
            "name": d.get("name_by_user") or d.get("name") or "Unnamed device",
            "manufacturer": d.get("manufacturer"),
            "model": d.get("model"),
            "area_id": aid,
            "area": area.get("name") if area else None,
            "floor": floors.get(area.get("floor_id")) if area else None,
            "entities": ents,
        })
    devices.sort(key=lambda x: (x["area"] or "￿", x["name"].lower()))
    areas = [
        {"area_id": a["area_id"], "name": a.get("name"), "floor": floors.get(a.get("floor_id"))}
        for a in reg.get("areas", [])
    ]
    areas.sort(key=lambda a: (a["name"] or "").lower())
    return jsonify(devices=devices, areas=areas)


@app.post("/api/manager/device-area")
def manager_set_device_area():
    """Move a device to an area (or unassign with area_id=null). Writes through
    to Home Assistant's device registry, so the change is reflected in HA."""
    require_manager()
    body = request.get_json(silent=True) or {}
    device_id = body.get("device_id")
    area_id = body.get("area_id") or None
    if not isinstance(device_id, str) or not device_id:
        raise ApiError("device_id is required", 400)
    ha_ws_command(
        {"type": "config/device_registry/update", "device_id": device_id, "area_id": area_id}
    )
    _invalidate_registries()  # so dashboards/picker pick up the new area
    return jsonify(ok=True)


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
        for sub in list(SUBSCRIBERS):
            if entity_id in sub["owned"]:
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
    owned = set(user.get("entities", []))
    sub = {"q": Queue(), "owned": owned}

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
    sub = {"q": Queue(), "owned": set(user.get("entities", []))}
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
        }
        for i, t in cmds.items():
            ws.send(json.dumps({"id": i, "type": t}))
        got = {}
        while len(got) < len(cmds):
            msg = json.loads(ws.recv())
            if msg.get("type") == "result" and msg.get("id") in cmds:
                got[msg["id"]] = msg.get("result") or []
        return {"floors": got[1], "areas": got[2], "entities": got[3], "devices": got[4]}
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
            return (None, None)
        return (area.get("name"), floors.get(area.get("floor_id")))

    return locate


@app.get("/api/admin/entities")
def admin_entities():
    """Entities for the device picker, annotated with their floor and room.
    Shows entities whose domain is enabled OR that are in the global
    included-entities list. Pass ?all=1 to return every entity unfiltered (for
    the Settings include-picker)."""
    require_admin()
    locate = _location_lookup(ha_registries_cached())
    show_all = request.args.get("all") in ("1", "true", "yes")
    allowed = enabled_domains()
    included = included_entities()
    items = []
    for s in ha_request("/api/states"):
        eid = s["entity_id"]
        domain = eid.split(".")[0]
        if not show_all and allowed is not None and domain not in allowed and eid not in included:
            continue  # hidden by the device-types setting (and not curated in)
        area, floor = locate(eid)
        items.append({
            "entity_id": eid,
            "name": s.get("attributes", {}).get("friendly_name") or eid,
            "domain": domain,
            "area": area,
            "floor": floor,
        })
    items.sort(key=lambda i: (i["domain"], i["name"].lower()))
    return jsonify(entities=items)


@app.get("/api/admin/settings")
def admin_get_settings():
    """Display + auth settings for the Settings tab."""
    require_admin()
    return jsonify(
        title=cfg_title(),
        name=cfg_name(),
        icon=cfg_emoji(),
        authProviders=(_load_settings().get("auth_providers") or "local"),
        oauthConfigured=oauth_configured(),
        oauthName=OAUTH_PROVIDER_NAME,
        includedEntities=sorted(included_entities()),
    )


@app.post("/api/admin/settings")
def admin_set_settings():
    require_admin()
    body = request.get_json(silent=True) or {}
    s = _load_settings()
    for key in ("title", "name", "icon"):
        if key in body and isinstance(body[key], str):
            s[key] = body[key].strip()
    if body.get("authProviders") in ("local", "oauth", "both"):
        s["auth_providers"] = body["authProviders"]
    if isinstance(body.get("includedEntities"), list):
        s["included_entities"] = [e for e in body["includedEntities"] if isinstance(e, str)]
    _save_settings(s)
    return jsonify(ok=True)


@app.get("/api/admin/device-types")
def admin_get_device_types():
    """All entity domains present in HA + which are currently enabled for the
    picker (so the admin can see what's available and add types back)."""
    require_admin()
    available = sorted({s["entity_id"].split(".")[0] for s in ha_request("/api/states")})
    allowed = enabled_domains()
    return jsonify(
        available=available,
        enabled=available if allowed is None else sorted(allowed),
    )


@app.post("/api/admin/device-types")
def admin_set_device_types():
    require_admin()
    body = request.get_json(silent=True) or {}
    types = body.get("types")
    if not isinstance(types, list):
        raise ApiError("types must be a list", 400)
    s = _load_settings()
    s["device_types"] = [t for t in types if isinstance(t, str)]
    _save_settings(s)
    return jsonify(ok=True)


@app.get("/api/admin/activity")
def admin_activity():
    """The app's own activity log - who controlled what, newest first."""
    require_admin()
    log = _load_activity()
    who = request.args.get("user")
    if who:
        log = [e for e in log if e.get("username") == who]
    try:
        limit = min(int(request.args.get("limit", 200)), ACTIVITY_MAX)
    except (TypeError, ValueError):
        limit = 200
    return jsonify(activity=list(reversed(log))[:limit])


@app.delete("/api/admin/activity")
def admin_clear_activity():
    """Clear the activity log."""
    require_admin()
    with _ACTIVITY_LOCK:
        try:
            ACTIVITY_FILE.unlink()
        except OSError:
            pass
    return jsonify(ok=True)


# Bumped if the backup format ever changes incompatibly.
BACKUP_TYPE = "my_home_backup"
BACKUP_VERSION = 1


@app.get("/api/admin/export")
def admin_export():
    """Download everything in /data as one JSON file: users (with passwords),
    device assignments, settings, the activity log and the uploaded app icon.
    Restoring it after a reinstall brings the add-on back exactly as it was."""
    require_admin()
    data = {
        "type": BACKUP_TYPE,
        "version": BACKUP_VERSION,
        "users": load_users(),
        "settings": _load_settings(),
        "activity": _load_activity(),
    }
    icon = _find_icon()
    if icon:
        try:
            data["icon"] = {
                "filename": icon.name,
                "data": base64.b64encode(icon.read_bytes()).decode("ascii"),
            }
        except OSError:
            pass
    resp = Response(json.dumps(data, indent=2), mimetype="application/json")
    resp.headers["Content-Disposition"] = 'attachment; filename="my-home-backup.json"'
    return resp


@app.post("/api/admin/import")
def admin_import():
    """Restore a backup produced by /api/admin/export. Replaces all current
    users, assignments and settings."""
    require_admin()
    body = request.get_json(silent=True)
    if not isinstance(body, dict) or body.get("type") != BACKUP_TYPE:
        raise ApiError("That doesn't look like a My Home backup file.", 400)

    users = body.get("users")
    if not isinstance(users, list):
        raise ApiError("The backup has no users.", 400)
    clean = [
        u for u in users
        if isinstance(u, dict) and isinstance(u.get("username"), str) and u["username"].strip()
    ]
    if not clean:
        raise ApiError("The backup contains no valid users.", 400)
    if not any(u.get("admin") for u in clean):
        clean[0]["admin"] = True  # never import a set with no admin
    save_users(clean)

    settings = body.get("settings")
    if isinstance(settings, dict):
        _save_settings(settings)

    activity = body.get("activity")
    if isinstance(activity, list):
        with _ACTIVITY_LOCK:
            try:
                ACTIVITY_FILE.parent.mkdir(parents=True, exist_ok=True)
                ACTIVITY_FILE.write_text(json.dumps(activity))
            except OSError:
                pass

    _remove_icons()
    icon = body.get("icon")
    if isinstance(icon, dict) and icon.get("data"):
        ext = str(icon.get("filename", "")).rsplit(".", 1)[-1].lower()
        if ext not in ICON_EXT.values():
            ext = "png"
        try:
            ICON_DIR.mkdir(parents=True, exist_ok=True)
            (ICON_DIR / f"app-icon.{ext}").write_bytes(base64.b64decode(icon["data"]))
        except (OSError, ValueError):
            pass

    return jsonify(ok=True, users=len(clean))


@app.get("/api/admin/users")
def admin_list_users():
    require_admin()
    safe = [
        {
            "username": u["username"],
            "displayName": u.get("displayName", ""),
            "entities": u.get("entities", []),
            "all": bool(u.get("all")),
            "manager": bool(u.get("manager")),
        }
        for u in load_users()
    ]
    return jsonify(users=safe)


@app.post("/api/admin/users")
def admin_save_user():
    """Create or update a user. Username is the key; blank password keeps the
    existing one (and is required when creating)."""
    require_admin()
    body = request.get_json(silent=True) or {}
    username = (body.get("username") or "").strip()
    if not username:
        raise ApiError("Username is required", 400)

    users = load_users()
    # Editing an existing user (possibly renaming): `original` is the old name.
    # Its presence is what distinguishes an edit from a create.
    original = (body.get("original") or "").strip()
    taken = any(u["username"] == username for u in users)

    if original:
        existing = next((u for u in users if u["username"] == original), None)
        if existing is None:
            raise ApiError(f"The user '{original}' no longer exists", 404)
        # Renaming onto a name another account already uses is not allowed.
        if username != original and taken:
            raise ApiError(f"The username '{username}' is already in use", 400)
    else:
        # Creating: never silently overwrite an existing account.
        if taken:
            raise ApiError(f"The username '{username}' is already in use", 400)
        existing = None

    password = body.get("password")
    if existing is None and not password:
        raise ApiError("A password is required for a new user", 400)

    record = existing if existing is not None else {"username": username}
    record["username"] = username  # apply rename
    record["displayName"] = body.get("displayName") or username
    record["manager"] = bool(body.get("manager"))  # can organize devices into HA areas
    # Managers always have full device access.
    record["all"] = bool(body.get("all")) or record["manager"]
    record["entities"] = [e for e in body.get("entities", []) if isinstance(e, str)]
    if password:
        record["password"] = password
    if existing is None:
        users.append(record)

    save_users(users)
    return jsonify(ok=True)


@app.delete("/api/admin/users/<username>")
def admin_delete_user(username):
    require_admin()
    users = load_users()
    remaining = [u for u in users if u["username"] != username]
    if len(remaining) == len(users):
        raise ApiError("No such user", 404)
    save_users(remaining)
    return jsonify(ok=True)


@app.post("/api/admin/icon")
def admin_upload_icon():
    """Upload a custom app icon (PWA / home-screen / favicon)."""
    require_admin()
    f = request.files.get("file")
    if not f or not (f.mimetype or "").startswith("image/"):
        raise ApiError("Please choose an image file", 400)
    ext = ICON_EXT.get(f.mimetype, "png")
    ICON_DIR.mkdir(parents=True, exist_ok=True)
    _remove_icons()
    f.save(str(ICON_DIR / f"app-icon.{ext}"))
    return jsonify(ok=True)


@app.delete("/api/admin/icon")
def admin_clear_icon():
    """Remove the custom icon and revert to the default."""
    require_admin()
    _remove_icons()
    return jsonify(ok=True)


# --- Static client (served by Flask; SPA fallback) -----------------------


@app.get("/manifest.webmanifest")
def manifest():
    """Serve the PWA manifest with the configured app name (and custom icon, if
    set), so the installed home-screen app uses them too."""
    data = json.loads((STATIC_DIR / "manifest.webmanifest").read_text())
    data["name"] = cfg_name()
    data["short_name"] = cfg_name()
    icon = _find_icon()
    if icon:
        src = _app_image_url()
        icon_type = mimetypes.guess_type(icon.name)[0] or "image/png"
        data["icons"] = [
            {"src": src, "sizes": "192x192", "type": icon_type, "purpose": "any"},
            {"src": src, "sizes": "512x512", "type": icon_type, "purpose": "any"},
            {"src": src, "sizes": "512x512", "type": icon_type, "purpose": "maskable"},
        ]
    return Response(json.dumps(data), mimetype="application/manifest+json")


@app.get("/app-icon")
def app_icon():
    """The custom uploaded PWA/home-screen icon, or the bundled default."""
    icon = _find_icon()
    if icon:
        return send_from_directory(icon.parent, icon.name)
    return send_from_directory(STATIC_DIR / "icons", "icon-512.png")


@app.get("/")
def index():
    return send_from_directory(STATIC_DIR, "index.html")


@app.get("/<path:filename>")
def static_files(filename):
    target = STATIC_DIR / filename
    if target.is_file():
        return send_from_directory(STATIC_DIR, filename)
    # SPA fallback: anything that isn't a real file returns index.html.
    return send_from_directory(STATIC_DIR, "index.html")


# Start the HA WebSocket listener as soon as the module is imported, so it runs
# under gunicorn (the add-on) as well as the dev server below.
ensure_realtime()


if __name__ == "__main__":
    # Dev: serve both ports so each mode is reachable. In the add-on, gunicorn
    # binds both (see Dockerfile). threaded=True so SSE streams don't block.
    from werkzeug.serving import make_server

    user_srv = make_server("0.0.0.0", USER_PORT, app, threaded=True)
    threading.Thread(target=user_srv.serve_forever, daemon=True).start()
    print(f"My Home -> HA at {HA_URL}")
    print(f"  management (Ingress): http://0.0.0.0:{INGRESS_PORT}")
    print(f"  user dashboard:       http://0.0.0.0:{USER_PORT}")
    make_server("0.0.0.0", INGRESS_PORT, app, threaded=True).serve_forever()
