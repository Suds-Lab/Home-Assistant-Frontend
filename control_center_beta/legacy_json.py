"""Readers for the pre-2.10 JSON files, used once by db.py to import existing
installs into SQLite. These are the OLD store loader bodies lifted verbatim, so
they already tolerate every historical shape (plaintext passwords, `fan` as
string-or-list, missing files, schedules with no `type`, minimal 1.0 user
records, `"*"` wildcards, instance-prefixed ids). Each returns the same shape the
old loader returned, or the empty default when the file is absent.

Path resolution mirrors the old `store._store_path()` exactly, so it finds the
files wherever an upgrading install left them. After a successful, verified
import db.py renames each file to `*.imported`; this module is not used again.
"""
import base64
import json
import os
from pathlib import Path

from config import SUPERVISOR_TOKEN, BASE_DIR


def _data_dir():
    if os.environ.get("USERS_STORE"):
        return Path(os.environ["USERS_STORE"]).parent
    if SUPERVISOR_TOKEN and Path("/data").is_dir():
        return Path("/data")
    return BASE_DIR


_DIR = _data_dir()
USERS_FILE = (Path(os.environ["USERS_STORE"]) if os.environ.get("USERS_STORE")
              else _DIR / "users.json")
SETTINGS_FILE = _DIR / "settings.json"
ACTIVITY_FILE = _DIR / "activity.json"
SCHEDULES_FILE = _DIR / "schedules.json"
SCHEDULE_PERMS_FILE = _DIR / "schedule_perms.json"
LISTS_FILE = _DIR / "lists.json"
TELEGRAM_CONFIG_FILE = _DIR / "telegram_config.json"
TELEGRAM_PERMS_FILE = _DIR / "telegram_perms.json"
TELEGRAM_READ_FILE = _DIR / "telegram_read.json"


def all_files():
    """Every legacy JSON file, for presence checks and post-import archiving."""
    return [USERS_FILE, SETTINGS_FILE, ACTIVITY_FILE, SCHEDULES_FILE,
            SCHEDULE_PERMS_FILE, LISTS_FILE, TELEGRAM_CONFIG_FILE,
            TELEGRAM_PERMS_FILE, TELEGRAM_READ_FILE]


def read_users():
    """Users list, or [] when absent. Does NOT seed - the importer seeds on empty
    so the verification compares like-for-like."""
    try:
        return json.loads(USERS_FILE.read_text())["users"]
    except (OSError, ValueError, KeyError, TypeError):
        return []


def read_settings():
    try:
        data = json.loads(SETTINGS_FILE.read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def read_activity():
    try:
        data = json.loads(ACTIVITY_FILE.read_text())
        return data if isinstance(data, list) else []
    except (OSError, ValueError):
        return []


def read_schedules():
    try:
        data = json.loads(SCHEDULES_FILE.read_text())
        if not isinstance(data, list):
            return []
        data.sort(key=lambda s: (s.get("name") or "").lower())  # old load_schedules sorted
        return data
    except (OSError, ValueError):
        return []


def read_schedule_perms():
    try:
        data = json.loads(SCHEDULE_PERMS_FILE.read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def read_lists():
    try:
        data = json.loads(LISTS_FILE.read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def read_telegram_config():
    try:
        cfg = json.loads(TELEGRAM_CONFIG_FILE.read_text())
        if not isinstance(cfg, dict):
            cfg = {}
    except (OSError, ValueError):
        cfg = {}
    cfg.setdefault("creds", {})
    cfg.setdefault("channels", [])
    return cfg


def read_telegram_perms():
    try:
        data = json.loads(TELEGRAM_PERMS_FILE.read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def read_telegram_read():
    try:
        data = json.loads(TELEGRAM_READ_FILE.read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def read_icon():
    """{'filename','data'(base64)} for the uploaded app icon, or None - so the
    pre-migration backup captures it the same way admin_export does."""
    for p in sorted(_DIR.glob("app-icon.*")):
        try:
            return {"filename": p.name,
                    "data": base64.b64encode(p.read_bytes()).decode("ascii")}
        except OSError:
            return None
    return None
