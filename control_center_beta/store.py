"""Persistence layer for Control Center.

Backed by SQLite (see db.py) since v2.10.0. The public functions keep the exact
same names, signatures and dict/list shapes they had in the JSON era, so routes,
the scheduler and security.py are unchanged - only the internals are SQL. The
uploaded icon stays a file under the data dir (ICON_DIR); db.py imports the
legacy JSON into the database once, on first boot.
"""
import json
import threading
import time

import db
from user import User
from config import (
    DEVICE_TYPES,
    APP_NAME,
    APP_ICON,
    addon_options,
    oauth_configured,
    BASE_DIR,
)

# The data dir (icon + db live together); kept for the icon helpers + callers
# that import ICON_DIR.
ICON_DIR = db.DATA_DIR
ACTIVITY_MAX = 1000  # keep the most recent N actions


# --- Settings (kv-of-JSON table) + brief read cache ------------------------
_SETTINGS_CACHE = {"ts": 0.0, "data": None}
_SETTINGS_TTL = 3.0


def _load_settings():
    now = time.time()
    c = _SETTINGS_CACHE
    if c["data"] is not None and now - c["ts"] < _SETTINGS_TTL:
        return dict(c["data"])  # a copy, so callers can mutate freely
    data = {}
    for row in db.get().execute("SELECT key, value FROM settings"):
        try:
            data[row["key"]] = json.loads(row["value"])
        except (ValueError, TypeError):
            pass
    c["data"] = data
    c["ts"] = now
    return dict(data)


def _save_settings(data):
    with db.transaction() as conn:
        conn.execute("DELETE FROM settings")
        conn.executemany(
            "INSERT INTO settings(key, value) VALUES(?, ?)",
            [(k, json.dumps(v)) for k, v in data.items()],
        )
    _SETTINGS_CACHE["data"] = dict(data)  # refresh the cache immediately
    _SETTINGS_CACHE["ts"] = time.time()


# --- Activity log ----------------------------------------------------------
_ACTIVITY_LOCK = threading.Lock()
_COALESCE_SERVICES = {"set_temperature", "set_percentage", "set_cover_position", "volume_set"}
_COALESCE_WINDOW = 120  # seconds

_ACTIVITY_COLS = ("ts", "username", "name", "entity_id", "entity",
                  "domain", "service", "verb", "source", "schedule")


def _row_to_entry(row):
    """Activity row -> dict, dropping NULL source/schedule so human entries keep
    their original (source-less) shape."""
    d = {k: row[k] for k in _ACTIVITY_COLS}
    if d.get("source") is None:
        d.pop("source", None)
    if d.get("schedule") is None:
        d.pop("schedule", None)
    return d


def _insert_activity(conn, entry):
    conn.execute(
        "INSERT INTO activity(ts,username,name,entity_id,entity,domain,service,"
        "verb,source,schedule) VALUES(?,?,?,?,?,?,?,?,?,?)",
        tuple(entry.get(k) for k in _ACTIVITY_COLS),
    )


def _load_activity():
    return [_row_to_entry(r) for r in
            db.get().execute("SELECT * FROM activity ORDER BY id")]


def _append_activity(entry):
    """Append one action, coalescing continuous-value runs by the same user on
    the same entity within the window (keep only the final value)."""
    with _ACTIVITY_LOCK, db.transaction() as conn:
        last = conn.execute("SELECT * FROM activity ORDER BY id DESC LIMIT 1").fetchone()
        coalesce = (
            last is not None
            and entry.get("service") in _COALESCE_SERVICES
            and last["service"] == entry.get("service")
            and last["entity_id"] == entry.get("entity_id")
            and last["username"] == entry.get("username")
            and (entry.get("ts", 0) - (last["ts"] or 0)) <= _COALESCE_WINDOW
        )
        if coalesce:
            conn.execute(
                "UPDATE activity SET ts=?,name=?,entity=?,domain=?,service=?,"
                "verb=?,source=?,schedule=? WHERE id=?",
                (entry.get("ts"), entry.get("name"), entry.get("entity"),
                 entry.get("domain"), entry.get("service"), entry.get("verb"),
                 entry.get("source"), entry.get("schedule"), last["id"]),
            )
        else:
            _insert_activity(conn, entry)
        # trim to the most recent ACTIVITY_MAX
        conn.execute(
            "DELETE FROM activity WHERE id NOT IN "
            "(SELECT id FROM activity ORDER BY id DESC LIMIT ?)", (ACTIVITY_MAX,))


def replace_activity(entries):
    """Replace the whole log (used by the importer and backup restore)."""
    rows = entries if isinstance(entries, list) else []
    with _ACTIVITY_LOCK, db.transaction() as conn:
        conn.execute("DELETE FROM activity")
        for e in rows[-ACTIVITY_MAX:]:
            if isinstance(e, dict):
                _insert_activity(conn, e)


def clear_activity():
    with _ACTIVITY_LOCK, db.transaction() as conn:
        conn.execute("DELETE FROM activity")


# --- User store ------------------------------------------------------------


def _seed_users():
    """Initial accounts used the first time the store is created."""
    users = addon_options.get("users")
    if isinstance(users, list) and users:
        return [dict(u) for u in users]
    bundled = BASE_DIR / "users.json"
    return json.loads(bundled.read_text())["users"] if bundled.exists() else []


def save_users(users):
    with db.transaction() as conn:
        conn.execute("DELETE FROM users")  # cascades to entity child tables
        for seq, u in enumerate(users):
            usr = User.from_dict(u)
            conn.execute(
                "INSERT INTO users(username,display_name,admin,manager,all_devices,"
                "password,email,provider,picture,expires,seq) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (usr.username, usr.display_name, int(usr.admin), int(usr.manager),
                 int(usr.all_devices), usr.password, usr.email, usr.provider,
                 usr.picture, usr.expires, seq),
            )
            for i, e in enumerate(usr.entities):
                conn.execute(
                    "INSERT OR IGNORE INTO user_entities(username,entity_id,ordinal) VALUES(?,?,?)",
                    (usr.username, e, i))
            for e, exp in usr.entity_expires.items():
                conn.execute(
                    "INSERT OR IGNORE INTO user_entity_expires(username,entity_id,expires) VALUES(?,?,?)",
                    (usr.username, e, exp))


def load_users():
    """User list in the same dict shape User.to_dict produces (so callers that do
    User.from_dict are unaffected). Seeds the default admin on an empty store."""
    conn = db.get()
    rows = conn.execute("SELECT * FROM users ORDER BY seq").fetchall()
    if not rows:
        save_users(_seed_users())
        rows = conn.execute("SELECT * FROM users ORDER BY seq").fetchall()

    ents = {}
    for r in conn.execute("SELECT username,entity_id FROM user_entities ORDER BY username,ordinal"):
        ents.setdefault(r["username"], []).append(r["entity_id"])
    exps = {}
    for r in conn.execute("SELECT username,entity_id,expires FROM user_entity_expires"):
        exps.setdefault(r["username"], {})[r["entity_id"]] = r["expires"]

    out = []
    for r in rows:
        d = {
            "username": r["username"],
            "displayName": r["display_name"] or r["username"],
            "entities": ents.get(r["username"], []),
            "admin": bool(r["admin"]),
            "manager": bool(r["manager"]),
            "all": bool(r["all_devices"]),
            "expires": r["expires"],
            "entity_expires": exps.get(r["username"], {}),
        }
        if r["password"] is not None:
            d["password"] = r["password"]
        if r["email"]:
            d["email"] = r["email"]
        if r["provider"]:
            d["provider"] = r["provider"]
        if r["picture"]:
            d["picture"] = r["picture"]
        out.append(d)
    return out


# --- Config accessors (read live display/auth settings) --------------------


def enabled_domains():
    s = _load_settings()
    if isinstance(s.get("device_types"), list):
        return set(s["device_types"])
    return DEVICE_TYPES or None


def included_entities():
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
    choice = (_load_settings().get("auth_providers") or "local").lower()
    oauth = oauth_configured() and choice in ("oauth", "both")
    local = choice in ("local", "both") or not oauth
    return {"local": local, "oauth": oauth}


# --- Uploaded custom icon (still a file under ICON_DIR) ---------------------

ICON_EXT = {
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/webp": "webp",
    "image/svg+xml": "svg",
    "image/gif": "gif",
}


def _find_icon():
    for p in sorted(ICON_DIR.glob("app-icon.*")):
        return p
    return None


def _remove_icons():
    for p in ICON_DIR.glob("app-icon.*"):
        try:
            p.unlink()
        except OSError:
            pass


def _app_image_url():
    p = _find_icon()
    return f"./app-icon?v={int(p.stat().st_mtime)}" if p else None


# --- Schedules -------------------------------------------------------------


def load_schedules():
    conn = db.get()
    scheds = conn.execute("SELECT * FROM schedules ORDER BY lower(name)").fetchall()

    targets = {}
    for r in conn.execute("SELECT schedule_id,entity_id FROM schedule_targets ORDER BY schedule_id,ordinal"):
        targets.setdefault(r["schedule_id"], []).append(r["entity_id"])

    days = {}
    for r in conn.execute("SELECT entry_id,day FROM schedule_entry_days ORDER BY entry_id,ordinal"):
        days.setdefault(r["entry_id"], []).append(r["day"])

    entries = {}
    for r in conn.execute("SELECT * FROM schedule_entries ORDER BY schedule_id,ordinal"):
        entries.setdefault(r["schedule_id"], []).append({
            "id": r["id"],
            "days": days.get(r["id"], []),
            "time": r["time"],
            "mode": r["mode"],
            "temp": r["temp"],
            "fan": json.loads(r["fan"]) if r["fan"] is not None else None,
        })

    out = []
    for s in scheds:
        d = {
            "id": s["id"],
            "owner": s["owner"],
            "name": s["name"],
            "enabled": bool(s["enabled"]),
            "targets": targets.get(s["id"], []),
            "entries": entries.get(s["id"], []),
        }
        if s["type"] is not None:
            d["type"] = s["type"]
        if s["start"] is not None:
            d["start"] = s["start"]
        if s["end"] is not None:
            d["end"] = s["end"]
        out.append(d)
    return out


def save_schedules(schedules):
    with db.transaction() as conn:
        conn.execute("DELETE FROM schedules")  # cascades targets/entries/days
        for si, s in enumerate(schedules):
            sid = s.get("id") or f"sched-{si}"
            conn.execute(
                "INSERT INTO schedules(id,owner,name,enabled,type,start,end) VALUES(?,?,?,?,?,?,?)",
                (sid, s.get("owner") or "", s.get("name") or "",
                 1 if s.get("enabled", True) else 0,
                 s.get("type"), s.get("start"), s.get("end")),
            )
            for ti, t in enumerate(s.get("targets") or []):
                conn.execute(
                    "INSERT OR IGNORE INTO schedule_targets(schedule_id,entity_id,ordinal) VALUES(?,?,?)",
                    (sid, t, ti))
            for ei, e in enumerate(s.get("entries") or []):
                eid = e.get("id") or f"{sid}-entry-{ei}"
                fan = e.get("fan")
                conn.execute(
                    "INSERT INTO schedule_entries(id,schedule_id,ordinal,time,mode,temp,fan) "
                    "VALUES(?,?,?,?,?,?,?)",
                    (eid, sid, ei, e.get("time") or "", e.get("mode") or "heat",
                     e.get("temp"), json.dumps(fan) if fan is not None else None),
                )
                for di, day in enumerate(e.get("days") or []):
                    try:
                        dv = int(day)
                    except (TypeError, ValueError):
                        continue
                    conn.execute(
                        "INSERT OR IGNORE INTO schedule_entry_days(entry_id,day,ordinal) VALUES(?,?,?)",
                        (eid, dv, di))


def load_schedule_perms():
    perms = {}
    for r in db.get().execute("SELECT username,entity_id FROM schedule_perms ORDER BY rowid"):
        perms.setdefault(r["username"], []).append(r["entity_id"])
    return perms


def save_schedule_perms(perms):
    with db.transaction() as conn:
        conn.execute("DELETE FROM schedule_perms")
        for username, ids in (perms or {}).items():
            for e in (ids or []):
                conn.execute(
                    "INSERT OR IGNORE INTO schedule_perms(username,entity_id) VALUES(?,?)",
                    (username, e))


# --- Per-user device lists -------------------------------------------------


def load_lists():
    conn = db.get()
    ents = {}
    for r in conn.execute("SELECT list_id,entity_id FROM list_entities ORDER BY list_id,ordinal"):
        ents.setdefault(r["list_id"], []).append(r["entity_id"])
    out = {}
    for r in conn.execute("SELECT * FROM lists ORDER BY username,ordinal"):
        out.setdefault(r["username"], []).append({
            "id": r["id"], "name": r["name"], "entities": ents.get(r["id"], []),
        })
    return out


def save_lists(lists):
    with db.transaction() as conn:
        conn.execute("DELETE FROM lists")  # cascades list_entities
        for username, items in (lists or {}).items():
            for ordinal, lst in enumerate(items or []):
                if not isinstance(lst, dict):
                    continue
                lid = lst.get("id") or f"{username}-list-{ordinal}"
                conn.execute(
                    "INSERT INTO lists(id,username,name,ordinal) VALUES(?,?,?,?)",
                    (lid, username, lst.get("name") or "", ordinal))
                for i, e in enumerate(lst.get("entities") or []):
                    conn.execute(
                        "INSERT OR IGNORE INTO list_entities(list_id,entity_id,ordinal) VALUES(?,?,?)",
                        (lid, e, i))


# Create/upgrade the database and import any legacy JSON on first import of this
# module - before any route, the scheduler, or security.py reads a thing.
db.bootstrap()
