"""Schedule API routes for Control Center.

User-facing endpoints (any authenticated user, served on the user port):
  GET/POST   /api/schedules           - list / create own schedules
  PUT/DELETE /api/schedules/<id>      - update / delete own schedule
  GET        /api/schedule-perms      - which climate entities this user may schedule
  GET        /api/schedule-entities   - permitted entities with live HA state

Admin endpoints (management port only):
  GET/POST   /api/admin/schedule-perms        - view / set per-user climate scheduling perms
  GET        /api/admin/schedules             - all schedules across all users
  PATCH      /api/admin/schedules/<id>        - update any schedule (enable/disable, rename)
  DELETE     /api/admin/schedules/<id>        - delete any schedule
"""
import uuid

from flask import Blueprint, jsonify, request

from access import user_can_access
from errors import ApiError
from security import current_user, require_admin
from store import (
    load_schedule_perms,
    load_schedules,
    save_schedule_perms,
    save_schedules,
)

bp = Blueprint("schedules", __name__)

# Sentinel stored in a user's schedule_perms list to mean "all climate devices
# this user can control, present and future". Resolved live (see _resolve_allowed)
# so the admin never has to add each new device by hand. It is deliberately not a
# valid entity id, so it can't collide with a real one.
ALL_CLIMATE = "*"


def _all_climate_ids():
    """Every climate entity currently known in STATE_CACHE (namespaced ids for
    remote instances included)."""
    from core import STATE_CACHE
    return [
        eid for eid in STATE_CACHE
        if (eid.split(":", 1)[1] if ":" in eid else eid).startswith("climate.")
    ]


def _resolve_allowed(user, perms=None):
    """The set of climate entity ids this user may schedule. If their perms hold
    the ALL_CLIMATE sentinel, that expands to every climate device they can
    control right now (so new devices are covered automatically); otherwise it's
    the explicit stored list. Access is always re-checked, so a broadened "all"
    grant can never exceed the devices the user is actually allowed to control."""
    raw = (perms if perms is not None else load_schedule_perms()).get(user["username"], [])
    if ALL_CLIMATE in raw:
        return {eid for eid in _all_climate_ids() if user_can_access(user, eid)}
    return set(raw)


# ---------------------------------------------------------------------------
# User-facing endpoints
# ---------------------------------------------------------------------------

@bp.get("/api/schedules")
def get_my_schedules():
    username = current_user()["username"]
    schedules = [s for s in load_schedules() if s.get("owner") == username]
    return jsonify(schedules)


@bp.post("/api/schedules")
def create_schedule():
    user = current_user()
    username = user["username"]
    data = request.get_json(force=True) or {}
    allowed = _resolve_allowed(user)
    sched = {
        "id": str(uuid.uuid4()),
        "owner": username,
        "name": str(data.get("name", "New Schedule"))[:80],
        "enabled": True,
        "targets": [t for t in data.get("targets", []) if t in allowed],
        "entries": _validated_entries(data.get("entries", [])),
    }
    schedules = load_schedules()
    schedules.append(sched)
    save_schedules(schedules)
    return jsonify(sched), 201


@bp.put("/api/schedules/<sched_id>")
def update_schedule(sched_id):
    user = current_user()
    username = user["username"]
    schedules = load_schedules()
    idx = _find_owned(schedules, sched_id, username)
    data = request.get_json(force=True) or {}
    allowed = _resolve_allowed(user)
    sched = schedules[idx]
    if "name" in data:
        sched["name"] = str(data["name"])[:80]
    if "enabled" in data:
        sched["enabled"] = bool(data["enabled"])
    if "targets" in data:
        sched["targets"] = [t for t in data["targets"] if t in allowed]
    if "entries" in data:
        sched["entries"] = _validated_entries(data["entries"])
    save_schedules(schedules)
    return jsonify(sched)


@bp.delete("/api/schedules/<sched_id>")
def delete_schedule(sched_id):
    username = current_user()["username"]
    schedules = load_schedules()
    idx = _find_owned(schedules, sched_id, username)
    schedules.pop(idx)
    save_schedules(schedules)
    return jsonify({"ok": True})


@bp.get("/api/schedule-perms")
def get_schedule_perms():
    user = current_user()
    perms = load_schedule_perms()
    raw = perms.get(user["username"], [])
    return jsonify({
        "entity_ids": sorted(_resolve_allowed(user, perms)),
        "all": ALL_CLIMATE in raw,
    })


@bp.get("/api/schedule-entities")
def get_schedule_entities():
    """Permitted climate entities with their live HA state from STATE_CACHE."""
    user = current_user()
    allowed = _resolve_allowed(user)
    if not allowed:
        return jsonify({"entities": []})
    from core import STATE_CACHE
    entities = []
    for eid in sorted(allowed):
        state = STATE_CACHE.get(eid, {})
        entities.append({
            "entity_id": eid,
            "name": (state.get("attributes") or {}).get("friendly_name") or eid,
            "state": state.get("state", "unknown"),
            "attributes": state.get("attributes") or {},
        })
    return jsonify({"entities": entities})


# ---------------------------------------------------------------------------
# Admin endpoints
# ---------------------------------------------------------------------------

@bp.get("/api/admin/climate-entities")
def admin_climate_entities():
    """All climate entities currently known in STATE_CACHE.

    Used by the admin UserEditor to populate the per-user scheduling
    permission picker. Reads the live cache so it works with real HA and
    with the MOCK_HA dev mode - no separate HA round-trip needed.
    """
    require_admin()
    from core import STATE_CACHE
    entities = []
    for eid, state in STATE_CACHE.items():
        real = eid.split(":", 1)[1] if ":" in eid else eid
        if not real.startswith("climate."):
            continue
        attrs = state.get("attributes") or {}
        entities.append({
            "entity_id": eid,
            "name": attrs.get("friendly_name") or real,
        })
    entities.sort(key=lambda e: e["name"])
    return jsonify(entities=entities)


@bp.get("/api/admin/schedule-perms")
def admin_get_schedule_perms():
    require_admin()
    return jsonify(load_schedule_perms())


@bp.post("/api/admin/schedule-perms")
def admin_set_schedule_perms():
    require_admin()
    data = request.get_json(force=True) or {}
    username = (data.get("username") or "").strip()
    entity_ids = data.get("entity_ids") or []
    if not username:
        raise ApiError("username required", 400)
    perms = load_schedule_perms()
    # "Allow all" is stored as the ALL_CLIMATE sentinel (present + future); the
    # frontend signals it via all=true or by sending ["*"]. Otherwise store the
    # explicit list, or drop the user entirely when nothing is selected.
    if data.get("all") or ALL_CLIMATE in entity_ids:
        perms[username] = [ALL_CLIMATE]
    elif entity_ids:
        perms[username] = [str(e) for e in entity_ids]
    else:
        perms.pop(username, None)
    save_schedule_perms(perms)
    return jsonify({"ok": True})


@bp.get("/api/admin/schedules")
def admin_get_all_schedules():
    require_admin()
    return jsonify(load_schedules())


@bp.patch("/api/admin/schedules/<sched_id>")
def admin_patch_schedule(sched_id):
    require_admin()
    schedules = load_schedules()
    idx = _find_any(schedules, sched_id)
    data = request.get_json(force=True) or {}
    sched = schedules[idx]
    if "enabled" in data:
        sched["enabled"] = bool(data["enabled"])
    if "name" in data:
        sched["name"] = str(data["name"])[:80]
    save_schedules(schedules)
    return jsonify(sched)


@bp.delete("/api/admin/schedules/<sched_id>")
def admin_delete_schedule(sched_id):
    require_admin()
    schedules = load_schedules()
    idx = _find_any(schedules, sched_id)
    schedules.pop(idx)
    save_schedules(schedules)
    return jsonify({"ok": True})


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_VALID_MODES = {"off", "cool", "heat", "auto", "dry", "fan"}
_VALID_DAYS = set(range(7))  # 0=Mon..6=Sun


def _validated_entries(raw):
    entries = []
    for e in raw:
        if not isinstance(e, dict):
            continue
        time_val = e.get("time", "")
        if not (isinstance(time_val, str) and len(time_val) == 5 and time_val[2] == ":"):
            continue
        mode = e.get("mode", "heat")
        if mode not in _VALID_MODES:
            mode = "heat"
        entry = {
            "id": e.get("id") or str(uuid.uuid4()),
            "days": [int(d) for d in e.get("days", []) if int(d) in _VALID_DAYS],
            "time": time_val,
            "mode": mode,
            "temp": float(e["temp"]) if e.get("temp") is not None else None,
            "fan": e.get("fan") or None,
        }
        entries.append(entry)
    return entries


def _find_owned(schedules, sched_id, username):
    for i, s in enumerate(schedules):
        if s.get("id") == sched_id:
            if s.get("owner") != username:
                raise ApiError("Not found", 404)
            return i
    raise ApiError("Not found", 404)


def _find_any(schedules, sched_id):
    for i, s in enumerate(schedules):
        if s.get("id") == sched_id:
            return i
    raise ApiError("Not found", 404)
