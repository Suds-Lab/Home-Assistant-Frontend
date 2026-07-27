"""Persistence layer for Control Center.

Handles the user store (users.json), app settings (settings.json), activity log
(activity.json), and the uploaded custom icon -- all under the same /data
directory. Also provides the cfg_* accessors that read live display settings.
No dependency on Flask, ha.py, access.py, or security.py.
"""
import json
import os
import threading
import time
from pathlib import Path

import config
from config import (
    SUPERVISOR_TOKEN,
    BASE_DIR,
    DEVICE_TYPES,
    APP_NAME,
    APP_ICON,
    addon_options,
    oauth_configured,
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


# --- Settings cache --------------------------------------------------------
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


# --- Activity log ----------------------------------------------------------
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
    """Append one action to the activity log, coalescing continuous-value runs.

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


# --- User store ------------------------------------------------------------


def _seed_users():
    """Initial accounts used the first time the store is created."""
    users = addon_options.get("users")
    if isinstance(users, list) and users:
        return [dict(u) for u in users]
    bundled = BASE_DIR / "users.json"
    return json.loads(bundled.read_text())["users"] if bundled.exists() else []


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


# --- Config accessors (read live display/auth settings) --------------------


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


# --- Uploaded custom icon --------------------------------------------------

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


# --- Schedule persistence --------------------------------------------------

SCHEDULES_FILE = ICON_DIR / "schedules.json"
SCHEDULE_PERMS_FILE = ICON_DIR / "schedule_perms.json"


def load_schedules():
    try:
        data = json.loads(SCHEDULES_FILE.read_text())
        return data if isinstance(data, list) else []
    except (OSError, ValueError):
        return []


def save_schedules(schedules):
    SCHEDULES_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = SCHEDULES_FILE.with_name(SCHEDULES_FILE.name + ".tmp")
    tmp.write_text(json.dumps(schedules, indent=2))
    tmp.replace(SCHEDULES_FILE)


def load_schedule_perms():
    try:
        data = json.loads(SCHEDULE_PERMS_FILE.read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_schedule_perms(perms):
    SCHEDULE_PERMS_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = SCHEDULE_PERMS_FILE.with_name(SCHEDULE_PERMS_FILE.name + ".tmp")
    tmp.write_text(json.dumps(perms, indent=2))
    tmp.replace(SCHEDULE_PERMS_FILE)
