"""Device routes: list devices, entity detail, service control, MDI/brand icons.

Registered as a blueprint on the app in core.py.
"""
import json
import re
import threading
import time

import requests
from flask import Blueprint, Response, jsonify, request

from access import assert_owned, user_can_access
from config import HA_TOKEN, HA_URL
from core import STATE_CACHE, _device_view
from errors import ApiError
from ha import _location_lookup, call_service, ha_registries_cached, ha_request
from security import current_user, is_management, user_from_token
from store import ICON_DIR, _append_activity

bp = Blueprint("devices", __name__)


@bp.get("/api/devices")
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


@bp.get("/api/entity/<path:entity_id>")
def entity_detail(entity_id):
    """Fresh full state for a single owned entity (for the detail panel)."""
    user = current_user()
    assert_owned(user, entity_id)
    return jsonify(_device_view(ha_request(f"/api/states/{entity_id}")))


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


@bp.get("/api/icon/mdi/<name>")
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


@bp.get("/api/icon/brand/<domain>")
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


@bp.post("/api/control")
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
