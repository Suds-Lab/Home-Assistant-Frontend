"""Runtime configuration for Control Center.

All add-on options / environment parsing lives here so the rest of the app can
import ready-to-use constants. Two sources, in order: Home Assistant add-on
options (/data/options.json) then environment variables (.env in dev).
"""

import json
import os
from pathlib import Path

try:
    # Load .env for standalone/dev runs. Optional - absent in the add-on.
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"


def _read_version():
    """The add-on version from config.yaml - the single source of truth used to
    cache-bust the front-end assets (?v=) so a new build always loads fresh."""
    try:
        for line in (BASE_DIR / "config.yaml").read_text().splitlines():
            s = line.strip()
            if s.startswith("version:"):
                return s.split(":", 1)[1].strip().strip("\"'") or "dev"
    except OSError:
        pass
    return "dev"


APP_VERSION = _read_version()

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
# JWT_SECRET is resolved in security.py (after the /data paths are known): an
# explicitly configured secret wins, otherwise a random one is generated and
# persisted so we never sign sessions with a guessable default.
# Display name + icon shown in the UI / browser tab. Configurable.
APP_NAME = addon_options.get("app_name") or os.environ.get("APP_NAME") or "Control Center"
APP_ICON = addon_options.get("app_icon") or os.environ.get("APP_ICON") or ""
# Entity domains assignable in Manage users (empty list = all domains).
_dt = addon_options.get("device_types")
if _dt is None:
    _dt = [d.strip() for d in os.environ.get("DEVICE_TYPES", "").split(",") if d.strip()]
DEVICE_TYPES = set(_dt) if _dt else set()


def _opt(key, default=""):
    return addon_options.get(key) or os.environ.get(key.upper()) or default


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


def _opt_bool(key):
    v = addon_options.get(key)
    if v is None:
        v = os.environ.get(key.upper())
    return str(v).strip().lower() in ("true", "1", "yes", "on")


# Fail closed: with no domain/email allow-list set, OAuth sign-in is refused
# unless the admin explicitly opts into "anyone with a verified email" here.
OAUTH_ALLOW_ANY = _opt_bool("oauth_allow_any")

# Opt-in clickjacking protection: send X-Frame-Options: DENY on the user
# dashboard. Off by default so it never breaks a panel_iframe embed; the
# management UI (which runs inside HA's Ingress iframe) is always exempt.
BLOCK_IFRAME = _opt_bool("block_iframe_embedding")


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
# Standalone only: which interface the (unauthenticated, port-trusted) management
# port binds to. Defaults to localhost so it isn't exposed on the LAN; set
# INGRESS_BIND=0.0.0.0 to opt back in. The add-on uses gunicorn, not this path.
INGRESS_BIND = os.environ.get("INGRESS_BIND", "127.0.0.1")

# Real-time push (SSE) is on by default. Set STREAM=0 to make the dashboard
# poll instead - useful for local preview tools that wait for network-idle
# (an always-open stream never idles).
STREAM_ENABLED = os.environ.get("STREAM", "1") != "0"

if not HA_URL or not HA_TOKEN:
    raise SystemExit(
        "Missing Home Assistant connection. As an add-on this is automatic; "
        "standalone, set HA_URL and HA_TOKEN in your environment (.env)."
    )
