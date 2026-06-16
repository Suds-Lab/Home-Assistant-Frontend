"""Admin API routes (served on the management / Ingress port).

Manage users, settings, device types, the activity log, the HA logbook/history
views, backup export/import, and the custom app icon. All gated by
require_admin() (which trusts the management port). Registered as a blueprint on
the app in core.py.
"""
from flask import Blueprint

from core import *  # noqa: F403 - helpers, flask names, and stdlib re-exports
from core import (  # underscore-prefixed helpers that `import *` does not carry
    _ACTIVITY_LOCK,
    _find_icon,
    _load_activity,
    _load_settings,
    _location_lookup,
    _migrate_passwords,
    _parse_date,
    _password_rules,
    _remove_icons,
    _safe_ts,
    _save_settings,
)

bp = Blueprint("admin", __name__)


@bp.get("/api/admin/entities")
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
        area, floor, _area_icon = locate(eid)
        items.append({
            "entity_id": eid,
            "name": s.get("attributes", {}).get("friendly_name") or eid,
            "domain": domain,
            "area": area,
            "floor": floor,
        })
    items.sort(key=lambda i: (i["domain"], i["name"].lower()))
    return jsonify(entities=items)


@bp.get("/api/admin/settings")
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
        oauthOpenWarning=bool(
            oauth_configured()
            and not OAUTH_ALLOWED_DOMAINS
            and not OAUTH_ALLOWED_EMAILS
            and not OAUTH_ALLOW_ANY
        ),
        secretSource=JWT_SECRET_SOURCE,
        passwordRules=_password_rules(),
        includedEntities=sorted(included_entities()),
    )


@bp.post("/api/admin/settings")
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
    if isinstance(body.get("passwordRules"), dict):
        pr = body["passwordRules"]
        s["password_rules"] = {
            "min": max(0, min(128, int(pr.get("min") or 0))),
            "max": max(0, min(256, int(pr.get("max") or 0))),
            "upper": bool(pr.get("upper")),
            "lower": bool(pr.get("lower")),
            "number": bool(pr.get("number")),
            "special": bool(pr.get("special")),
        }
    _save_settings(s)
    return jsonify(ok=True)


@bp.post("/api/admin/regenerate-secret")
def admin_regenerate_secret():
    """Rotate the session-signing secret (logs everyone out). No-op when the
    secret is pinned via add-on config / env."""
    require_admin()
    if not regenerate_jwt_secret():
        raise ApiError("The session secret is set via add-on configuration; change it there.", 400)
    return jsonify(ok=True)


@bp.get("/api/admin/device-types")
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


@bp.post("/api/admin/device-types")
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


@bp.get("/api/admin/activity")
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


@bp.delete("/api/admin/activity")
def admin_clear_activity():
    """Clear the activity log."""
    require_admin()
    with _ACTIVITY_LOCK:
        try:
            ACTIVITY_FILE.unlink()
        except OSError:
            pass
    return jsonify(ok=True)


def _iso_to_epoch(s):
    """Parse an HA ISO timestamp to epoch seconds (0 on failure)."""
    if not s:
        return 0
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp()
    except (ValueError, TypeError):
        return 0


def _logbook_verb(e):
    """Human action text for an HA logbook entry."""
    msg = e.get("message")
    if msg:
        return msg
    st = e.get("state")
    if st in ("on", "off", "locked", "unlocked", "open", "closed", "home", "not_home"):
        return {"on": "turned on", "off": "turned off"}.get(st, st)
    return f"changed to {st}" if st is not None else "changed"


@bp.get("/api/admin/ha-logbook")
def admin_ha_logbook():
    """Live pull of Home Assistant's own logbook for a time range (never stored).
    Our app's own changes - which HA records as the Supervisor / "by system" -
    are dropped, since the app's activity log already shows them with the real
    user's name. Pass ?entity= to scope to one entity."""
    require_admin()
    start, end = request.args.get("start"), request.args.get("end")
    entity = request.args.get("entity")
    if not start:
        raise ApiError("start is required", 400)
    if not _safe_ts(start) or (end and not _safe_ts(end)):
        raise ApiError("Invalid time range", 400)
    if entity and not valid_entity_id(entity):
        raise ApiError("Invalid entity id", 400)
    path = f"/api/logbook/{quote(start)}"
    qs = []
    if end:
        qs.append("end_time=" + quote(end))
    if entity:
        qs.append("entity=" + quote(entity))
    if qs:
        path += "?" + "&".join(qs)
    try:
        raw = ha_request(path) or []
    except ApiError:
        raw = []

    # Index our own actions to drop HA's duplicate "by system" entries (and keep
    # the user's name so a match can be attributed to the real app user). The
    # window is generous so a slightly-delayed logbook record still matches.
    ours = {}
    for a in _load_activity():
        eid = a.get("entity_id")
        if eid:
            ours.setdefault(eid, []).append((a.get("ts") or 0, a.get("name")))

    def app_match(eid, when_ts):
        for t, name in ours.get(eid, []):
            if abs(t - when_ts) <= 90:
                return name or True
        return None

    out = []
    for e in raw if isinstance(raw, list) else []:
        eid = e.get("entity_id")
        ts = _iso_to_epoch(e.get("when"))
        # Our own change: skip it - the app's log shows it with the real name.
        if eid and app_match(eid, ts) is not None:
            continue
        out.append({
            "ts": ts,
            "entity_id": eid,
            "entity": e.get("name") or eid or "",
            "domain": (eid.split(".")[0] if eid else e.get("domain")) or "",
            "verb": _logbook_verb(e),
            "name": e.get("context_name") or None,  # who/what caused it, if known
            "source": "ha",
        })
    out.sort(key=lambda x: x["ts"], reverse=True)

    # Cap the payload so a busy day can't flood the client. Newest kept.
    try:
        limit = min(int(request.args.get("limit", 4000)), 10000)
    except (TypeError, ValueError):
        limit = 4000
    total = len(out)
    if total > limit:
        out = out[:limit]
    return jsonify(entries=out, total=total, truncated=max(0, total - limit))


# Numeric attributes worth charting per domain, like Home Assistant's own
# entity history (a climate's current + target temperature, a light's
# brightness, etc.). Each is (attribute, label, unit-or-None - None means take
# the unit from the entity's own attributes when present).
_HISTORY_ATTRS = {
    "climate": [("current_temperature", "Current", None), ("temperature", "Target", None)],
    "water_heater": [("current_temperature", "Current", None), ("temperature", "Target", None)],
    "humidifier": [("current_humidity", "Current", "%"), ("humidity", "Target", "%")],
    "light": [("brightness", "Brightness", None)],
    "fan": [("percentage", "Speed", "%")],
    "media_player": [("volume_level", "Volume", None)],
    "cover": [("current_position", "Position", "%")],
}


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


@bp.get("/api/admin/ha-history")
def admin_ha_history():
    """Live pull of one entity's state history for the chart (never stored).

    Returns one or more numeric series - like HA's own entity history. For a
    climate entity that's its current + target temperature; for a light its
    brightness, etc. Entities with no chartable number (locks, switches) fall
    back to a stepped chart of their discrete state."""
    require_admin()
    entity = request.args.get("entity")
    start, end = request.args.get("start"), request.args.get("end")
    if not entity or not start:
        raise ApiError("entity and start are required", 400)
    if not valid_entity_id(entity):
        raise ApiError("Invalid entity id", 400)
    if not _safe_ts(start) or (end and not _safe_ts(end)):
        raise ApiError("Invalid time range", 400)
    domain = entity.split(".")[0]
    # NOTE: no minimal_response - we need each snapshot's attributes over time.
    path = f"/api/history/period/{quote(start)}?filter_entity_id={quote(entity)}"
    if end:
        path += "&end_time=" + quote(end)
    path += "&significant_changes_only"
    try:
        raw = ha_request(path) or []
    except ApiError:
        raw = []
    states = raw[0] if (isinstance(raw, list) and raw) else []
    # Every snapshot for one entity shares a timeline, so all series align to it.
    times = [_iso_to_epoch(s.get("last_changed") or s.get("last_updated")) for s in states]

    def unit_from_states(attr, fallback):
        if fallback is not None:
            return fallback
        for s in states:
            a = s.get("attributes") or {}
            u = a.get("unit_of_measurement") or (a.get("temperature_unit")
                                                 if "temp" in attr else None)
            if u:
                return u
        return None

    series = []
    unit = None
    for attr, label, u_default in _HISTORY_ATTRS.get(domain, []):
        vals = [_num((s.get("attributes") or {}).get(attr)) for s in states]
        if any(v is not None for v in vals):
            u = unit_from_states(attr, u_default)
            series.append({"label": label, "unit": u, "values": vals})
            unit = unit or u

    if not series:
        # The entity's own state may itself be numeric (a sensor / number).
        state_vals = [_num(s.get("state")) for s in states]
        if any(v is not None for v in state_vals):
            u = unit_from_states("unit_of_measurement", None)
            series.append({"label": "Value", "unit": u, "values": state_vals})
            unit = u

    if series:
        return jsonify(entity=entity, numeric=True, unit=unit, times=times, series=series)

    # Discrete fallback: a stepped chart over the entity's distinct states.
    levels = sorted({str(s.get("state")) for s in states if s.get("state") is not None})
    idx = {lvl: i for i, lvl in enumerate(levels)}
    vals = [idx.get(str(s.get("state"))) for s in states]
    return jsonify(entity=entity, numeric=False, levels=levels, times=times,
                   series=[{"label": entity, "values": vals}])


# Bumped if the backup format ever changes incompatibly. The literal is kept as
# "my_home_backup" (the pre-2.0 name) on purpose: it's the compatibility marker
# stamped into exported backups, so changing it would reject backups that existing
# installs already exported. Same reasoning as keeping the add-on slug.
BACKUP_TYPE = "my_home_backup"
BACKUP_VERSION = 1


@bp.get("/api/admin/export")
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
    }
    # The activity log can be large and is disposable - allow excluding it
    # with ?activity=0 (omitted entirely so restoring won't clear an existing log).
    if request.args.get("activity") not in ("0", "false", "no"):
        data["activity"] = _load_activity()
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
    resp.headers["Content-Disposition"] = 'attachment; filename="control-center-backup.json"'
    return resp


@bp.post("/api/admin/import")
def admin_import():
    """Restore a backup produced by /api/admin/export. Replaces all current
    users, assignments and settings."""
    require_admin()
    body = request.get_json(silent=True)
    if not isinstance(body, dict) or body.get("type") != BACKUP_TYPE:
        raise ApiError("That doesn't look like a Control Center backup file.", 400)

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
    _migrate_passwords(clean)  # hash any plaintext from an old backup
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


@bp.get("/api/admin/users")
def admin_list_users():
    require_admin()
    safe = [
        {
            "username": u["username"],
            "displayName": u.get("displayName", ""),
            "entities": u.get("entities", []),
            "all": bool(u.get("all")),
            "manager": bool(u.get("manager")),
            "expires": u.get("expires", ""),
            "entityExpires": u.get("entity_expires", {}),
        }
        for u in load_users()
    ]
    return jsonify(users=safe)


@bp.post("/api/admin/users")
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
    record["manager"] = bool(body.get("manager"))  # can organize devices/areas in HA
    # Store "all" as-is. Managers already get full device access via the manager
    # flag (see user_can_access), so we don't force all=true for them - doing so
    # left "All devices" stuck on after un-managing someone.
    record["all"] = bool(body.get("all"))
    record["entities"] = [e for e in body.get("entities", []) if isinstance(e, str)]
    # Optional account expiry (stored normalised as YYYY-MM-DD, or "" for never).
    exp = _parse_date(body.get("expires"))
    record["expires"] = exp.isoformat() if exp else ""
    # Optional per-entity expiry: { entity_id: "YYYY-MM-DD" }. Keep only valid
    # entity ids with valid dates; an entity dropping off the list clears it.
    raw_ee = body.get("entityExpires") or {}
    entity_expires = {}
    if isinstance(raw_ee, dict):
        for eid, ds in raw_ee.items():
            d = _parse_date(ds)
            if valid_entity_id(eid) and d:
                entity_expires[eid] = d.isoformat()
    record["entity_expires"] = entity_expires
    if password:
        record["password"] = hash_password(password)
    if existing is None:
        users.append(record)

    save_users(users)
    return jsonify(ok=True)


@bp.delete("/api/admin/users/<username>")
def admin_delete_user(username):
    require_admin()
    users = load_users()
    remaining = [u for u in users if u["username"] != username]
    if len(remaining) == len(users):
        raise ApiError("No such user", 404)
    save_users(remaining)
    return jsonify(ok=True)


@bp.post("/api/admin/icon")
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


@bp.delete("/api/admin/icon")
def admin_clear_icon():
    """Remove the custom icon and revert to the default."""
    require_admin()
    _remove_icons()
    return jsonify(ok=True)
